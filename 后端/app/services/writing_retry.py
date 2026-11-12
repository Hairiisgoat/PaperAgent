"""Explicit, bounded retry of a known failed outline; never replay unknown calls."""
import logging
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.graph.checkpointer import sqlite_checkpointer
from app.graph.factory import build_graph
from app.infrastructure import database
from app.models.workflow_approval_decision import WorkflowApprovalDecision
from app.models.workflow_run import WorkflowRun
from app.models.writing import WritingCall, WritingChapter, WritingDraft
from app.schemas.workflow_event import CompletedPayload, ErrorPayload, ProgressPayload
from app.services.mock_start import checkpoint_path
from app.services.project_scope import ProjectConflict, ResourceNotFound
from app.services.workflow_events import EventService
from app.services.workflow_run import WorkflowRunService, runnable_config_for_attempt

logger = logging.getLogger(__name__)
_KNOWN_OUTLINE_ERRORS = {"invalid_output", "outline_json", "outline_type", "outline_missing", "outline_schema", "output_truncated"}


def source_decision(session: Session, project_id: UUID, run: WorkflowRun) -> WorkflowApprovalDecision:
    records = list(session.scalars(select(WorkflowApprovalDecision).where(
        WorkflowApprovalDecision.project_id == project_id,
        WorkflowApprovalDecision.thread_id == run.thread_id,
        WorkflowApprovalDecision.approval_type == "source_approval",
        WorkflowApprovalDecision.decision == "approve")))
    if len(records) != 1:
        raise ProjectConflict("Writing approval unavailable")
    original = session.get(WorkflowRun, records[0].run_id)
    if original is None or original.project_id != project_id or original.thread_id != run.thread_id or original.attempt_no > run.attempt_no:
        raise ProjectConflict("Writing approval unavailable")
    return records[0]


def _checkpoint(run: WorkflowRun, decision: WorkflowApprovalDecision, draft: WritingDraft):
    with sqlite_checkpointer(checkpoint_path(), read_only=True, require_existing=True) as saver:
        graph = build_graph(run.graph_version).compile(checkpointer=saver)
        snapshot = graph.get_state({"configurable": {"thread_id": str(run.thread_id)}})
    values = snapshot.values
    if (snapshot.next != ("writing",) or snapshot.interrupts or
        values.get("project_id") != str(run.project_id) or
        values.get("planner_generation_id") != str(draft.generation_id) or
        set(values.get("approved_source_ids", [])) != set(decision.approved_ids) or
        not values.get("approved_task_ids")):
        raise ProjectConflict("Writing checkpoint unavailable or inconsistent")
    return snapshot


def _failed_outline(session: Session, project_id: UUID, old: WorkflowRun) -> WritingDraft:
    if old.status != "failed" or old.current_stage not in {"research", "writing"} or old.graph_version != "planner_research_writing_v3":
        raise ProjectConflict("Writing retry unavailable")
    draft = session.scalar(select(WritingDraft).where(WritingDraft.project_id == project_id, WritingDraft.run_id == old.run_id).with_for_update())
    if not draft or draft.status not in {"failed", "needs_review"} or draft.thread_id != old.thread_id:
        raise ProjectConflict("Writing draft requires reconciliation")
    calls = list(session.scalars(select(WritingCall).where(WritingCall.draft_id == draft.draft_id).with_for_update()))
    chapters = session.scalar(select(WritingChapter.chapter_id).where(WritingChapter.draft_id == draft.draft_id).limit(1))
    if len(calls) != 1 or calls[0].ordinal != 0 or calls[0].kind != "outline" or calls[0].status != "failed" or calls[0].error_code not in _KNOWN_OUTLINE_ERRORS or chapters is not None:
        raise ProjectConflict("Writing call requires reconciliation")
    return draft


def queue_writing_retry(session: Session, project_id: UUID, failed_run_id: UUID):
    runs = WorkflowRunService(session)
    parent = runs.scope.require_project(project_id, action="edit")
    session.expire(parent)
    if runs.scope.repository.get(project_id, exclude_status="deleted", for_update=True) is None:
        raise ResourceNotFound("Resource not found")
    old = runs.repository.get(project_id, failed_run_id, for_update=True)
    if old is None:
        raise ResourceNotFound("Resource not found")
    draft = _failed_outline(session, project_id, old)
    latest = runs.repository.latest(project_id, old.thread_id, for_update=True)
    if latest and latest.run_id != old.run_id:
        # Repeating the same explicit request returns the already created attempt.
        retry_draft = session.scalar(select(WritingDraft).where(WritingDraft.project_id == project_id, WritingDraft.run_id == latest.run_id))
        if (latest.attempt_no == old.attempt_no + 1 and latest.graph_version == old.graph_version and retry_draft
                and retry_draft.thread_id == old.thread_id and retry_draft.generation_id == draft.generation_id):
            return latest, None
        raise ProjectConflict("Writing attempt is no longer latest")
    decision = source_decision(session, project_id, old)
    _checkpoint(old, decision, draft)
    new = runs.create(project_id, resume_thread_id=old.thread_id, manual_writing_retry=True)
    new.current_stage = "writing"
    new_draft = WritingDraft(draft_id=uuid4(), project_id=project_id, thread_id=old.thread_id,
        run_id=new.run_id, generation_id=draft.generation_id, status="queued")
    session.add(new_draft)
    session.flush()
    session.add(WritingCall(call_id=uuid4(), draft_id=new_draft.draft_id, ordinal=0, kind="outline", status="prepared", usage_complete=False))
    event = EventService(session).prepare(project_id, new.run_id, "progress", stage="writing", agent_name="Writing", status="queued",
        payload=ProgressPayload(stage="writing", agent_name="Writing", status="queued", message="Workflow queued"))
    return new, event


def run_writing_retry(project_id: UUID, run_id: UUID) -> None:
    with database.SessionLocal() as session:
        runs, events = WorkflowRunService(session), EventService(session)
        try:
            runs.scope.require_project(project_id, action="edit")
            run = runs.repository.get(project_id, run_id, for_update=True)
            if run is None or run.status != "queued":
                return
            latest = runs.repository.latest(project_id, run.thread_id, for_update=True)
            draft = session.scalar(select(WritingDraft).where(WritingDraft.project_id == project_id, WritingDraft.run_id == run_id).with_for_update())
            if latest is None or latest.run_id != run_id or not draft or draft.status != "queued" or draft.thread_id != run.thread_id:
                raise ProjectConflict("Writing intent inconsistent")
            previous = session.scalar(select(WorkflowRun).where(WorkflowRun.project_id == project_id, WorkflowRun.thread_id == run.thread_id, WorkflowRun.attempt_no == run.attempt_no - 1))
            if not previous or previous.status != "failed" or previous.graph_version != run.graph_version:
                raise ProjectConflict("Writing previous attempt inconsistent")
            previous_draft = _failed_outline(session, project_id, previous)
            if previous_draft.generation_id != draft.generation_id:
                raise ProjectConflict("Writing generation inconsistent")
            decision = source_decision(session, project_id, run)
            _checkpoint(run, decision, draft)
            call = session.scalar(select(WritingCall).where(WritingCall.draft_id == draft.draft_id, WritingCall.ordinal == 0))
            if not call or call.status != "prepared":
                raise ProjectConflict("Writing call inconsistent")
            runs.transition(project_id, run_id, "running", current_stage="writing")
            evt = events.prepare(project_id, run_id, "progress", stage="writing", agent_name="Writing", status="running",
                payload=ProgressPayload(stage="writing", agent_name="Writing", status="running", message="Workflow running"))
            config = runnable_config_for_attempt(run)
            events.commit_and_notify(evt)
            with sqlite_checkpointer(checkpoint_path(), require_existing=True) as saver:
                graph = build_graph(run.graph_version).compile(checkpointer=saver)
                graph.invoke(None, config)
                snapshot = graph.get_state({"configurable": {"thread_id": str(run.thread_id)}})
                if snapshot.next or snapshot.interrupts or snapshot.values.get("draft_id") != str(draft.draft_id):
                    raise ProjectConflict("Writing checkpoint inconsistent")
            session.expire_all()
            run = runs.transition(project_id, run_id, "completed", current_stage="writing")
            evt = events.prepare(project_id, run_id, "completed", stage="writing", agent_name="Writing", status="completed",
                payload=CompletedPayload(result_ref=draft.draft_id, finished_at=run.finished_at))
            events.commit_and_notify(evt)
        except Exception:
            session.rollback()
            # The writing node commits through separate sessions. Refresh cached
            # identities before deciding whether its failure was uncertain.
            session.expire_all()
            logger.error("Writing retry failed; reconciliation required")
            try:
                run = runs.repository.get(project_id, run_id)
                if run and run.status == "running":
                    draft = session.scalar(select(WritingDraft).where(WritingDraft.project_id == project_id, WritingDraft.run_id == run_id).with_for_update())
                    if draft and draft.status in {"queued", "writing"}:
                        draft.status = "needs_review"
                        for call in session.scalars(select(WritingCall).where(WritingCall.draft_id == draft.draft_id, WritingCall.status == "invoking").with_for_update()):
                            call.status = "needs_review"
                    run = runs.transition(project_id, run_id, "failed", error_code="internal_error", retryable=False)
                    evt = events.prepare(project_id, run_id, "error", stage="writing", agent_name="Writing", status="failed",
                        payload=ErrorPayload(error_code="internal_error", safe_message="Workflow execution failed", retryable=False))
                    events.commit_and_notify(evt)
            except Exception:
                session.rollback()
                logger.error("Writing retry failure record unavailable")
