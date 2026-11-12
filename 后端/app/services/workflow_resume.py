"""Accept an approval once and resume its existing checkpoint in the background."""
import logging
from uuid import UUID

from langgraph.types import Command
from sqlalchemy import select
from sqlalchemy.orm import Session
from app.models.workflow_approval_decision import WorkflowApprovalDecision
from app.services.project_scope import ResourceNotFound

from app.graph.factory import build_graph
from app.graph.checkpointer import sqlite_checkpointer
from app.infrastructure import database
from app.schemas.workflow_approval import ApprovalSubmission, ApprovalSubmissionResponse
from app.schemas.workflow_event import ApprovalPayload, CompletedPayload, ErrorPayload, ProgressPayload
from app.services.mock_start import checkpoint_path
from app.services.project_scope import ProjectConflict
from app.services.workflow_approval import WorkflowApprovalService
from app.services.workflow_events import EventService, from_record
from app.services.workflow_run import WorkflowRunService, runnable_config_for_attempt

logger = logging.getLogger(__name__)


class ResumeConsistencyError(Exception):
    """A committed intent cannot be safely reconciled with its run."""


class WritingStorageMissing(Exception):
    """Writing schema is absent; the approval has not been accepted."""


def _compatible_versions(previous, new, approval_type, decision):
    return previous == new or (previous == "planner_real_v1" and new == "planner_research_v2"
                               and approval_type == "task_approval" and decision == "approve") or (
                               previous == "planner_research_v2" and new == "planner_research_writing_v3"
                               and approval_type == "source_approval" and decision == "approve") or (
                               previous == "planner_research_v2" and new == "planner_research_writing_v4"
                               and approval_type == "source_approval" and decision == "approve")


def _require_research_storage(session: Session) -> None:
    from sqlalchemy.exc import SQLAlchemyError
    from app.infrastructure.database import DatabaseAccessError
    from app.models.research_source import ResearchSource, ResearchSourceTask
    try:
        session.execute(select(ResearchSource).limit(0))
        session.execute(select(ResearchSourceTask).limit(0))
    except SQLAlchemyError:
        raise DatabaseAccessError("Research storage unavailable") from None


def queue_approval(session: Session, project_id: UUID, previous_run_id: UUID, submission: ApprovalSubmission):
    """Validate under existing row locks; caller registers task and commits."""
    approvals = WorkflowApprovalService(session)
    # New source approvals use segmented writing by default; an explicit false
    # preserves the legacy graph for existing integrations and regression tests.
    use_segmented = submission.continue_writing and ('segmented_writing' not in submission.model_fields_set or submission.segmented_writing)
    runs, events = approvals.runs, EventService(session)
    # Serialize concurrent submissions before checking the unique decision.
    parent = runs.scope.require_project(project_id, action="read")
    session.expire(parent)
    parent = runs.scope.repository.get(project_id, exclude_status="deleted", for_update=True)
    if parent is None or parent.lifecycle_status == "deleted":
        from app.services.project_scope import ProjectNotFound
        raise ProjectNotFound("Project not found")
    previous = runs.repository.get(project_id, previous_run_id, for_update=True)
    if previous is None:
        raise ResourceNotFound("Resource not found")
    existing = session.scalar(select(WorkflowApprovalDecision).where(
        WorkflowApprovalDecision.project_id == project_id,
        WorkflowApprovalDecision.previous_run_id == previous_run_id).with_for_update())
    ids = sorted(str(item) for item in submission.approved_ids or [])
    if existing is not None:
        if existing.decision != submission.decision or sorted(existing.approved_ids) != ids:
            raise ProjectConflict("A different approval was already submitted")
        new_run = runs.repository.get(project_id, existing.run_id, for_update=True)
        if (new_run is None or new_run.thread_id != existing.thread_id or previous.thread_id != existing.thread_id
                or not _compatible_versions(previous.graph_version, new_run.graph_version, existing.approval_type, existing.decision)
                or submission.continue_research != (previous.graph_version == "planner_real_v1" and new_run.graph_version == "planner_research_v2")
                or submission.continue_writing != (previous.graph_version == "planner_research_v2" and new_run.graph_version in {"planner_research_writing_v3", "planner_research_writing_v4"})
                or ('segmented_writing' in submission.model_fields_set and submission.segmented_writing != (new_run.graph_version == "planner_research_writing_v4"))
                or new_run.attempt_no != previous.attempt_no + 1):
            raise ProjectConflict("Approval is unavailable or inconsistent")
        return ApprovalSubmissionResponse(project_id=project_id, previous_run_id=previous_run_id,
            run_id=new_run.run_id, thread_id=new_run.thread_id, attempt_no=new_run.attempt_no), None, None
    detail = approvals.get(project_id, previous_run_id)
    if submission.decision == "approve" and not set(submission.approved_ids or []).issubset(set(detail.item_ids)):
        raise ProjectConflict("Approval contains unavailable items")
    if submission.continue_research and (detail.approval_type != "task_approval" or previous.graph_version != "planner_real_v1"):
        raise ProjectConflict("Research transition unavailable")
    if submission.continue_research:
        # Fail before consuming the decision if the new schema is not deployed.
        _require_research_storage(session)
    if submission.continue_writing:
        if detail.approval_type != "source_approval" or previous.graph_version != "planner_research_v2":
            raise ProjectConflict("Writing transition unavailable")
        selected = set(submission.approved_ids or [])
        if not detail.sources or not any(item.source_id in selected and item.abstract and len(item.abstract.strip()) >= 8 for item in detail.sources):
            raise ProjectConflict("At least one approved source with an abstract is required")
        from sqlalchemy.exc import DBAPIError
        from app.models.writing import WritingDraft, WritingChapter, WritingCitation, WritingCall
        try:
            models = [WritingDraft, WritingChapter, WritingCitation, WritingCall]
            if use_segmented:
                from app.models.writing_segments import WritingManuscript, WritingManuscriptAttempt, WritingUnit, WritingUnitCall, WritingChapterVersion, WritingRevision
                models.extend([WritingManuscript, WritingManuscriptAttempt, WritingUnit, WritingUnitCall, WritingChapterVersion, WritingRevision])
            for model in models:
                session.execute(select(model).limit(0))
        except DBAPIError as error:
            args = getattr(error.orig, "args", ())
            if args and args[0] == 1146:
                raise WritingStorageMissing("Writing storage unavailable") from None
            raise
    new_run = runs.create(project_id, resume_thread_id=detail.thread_id, continue_research=submission.continue_research,
                          continue_writing=submission.continue_writing)
    if use_segmented:
        new_run.graph_version = 'planner_research_writing_v4'
    record = WorkflowApprovalDecision(previous_run_id=previous_run_id, run_id=new_run.run_id,
        project_id=project_id, thread_id=detail.thread_id, checkpoint_id=detail.checkpoint_id,
        approval_type=detail.approval_type, decision=submission.decision, approved_ids=ids, execution_status="queued")
    session.add(record)
    session.flush()
    if submission.continue_writing:
        from uuid import uuid4
        from app.models.research_source import ResearchSource
        from app.models.writing import WritingDraft, WritingCall
        source = session.scalar(select(ResearchSource).where(ResearchSource.project_id == project_id,
            ResearchSource.run_id == previous_run_id, ResearchSource.source_id.in_(submission.approved_ids or [])).limit(1))
        if source is None:
            raise ProjectConflict("Writing sources unavailable")
        draft = WritingDraft(draft_id=uuid4(), project_id=project_id, thread_id=detail.thread_id,
            run_id=new_run.run_id, generation_id=source.generation_id, status="queued")
        session.add(draft)
        session.flush()
        session.add(WritingCall(call_id=uuid4(), draft_id=draft.draft_id, ordinal=0, kind="outline",
            status="prepared", usage_complete=False))
        session.flush()
        if use_segmented:
            from app.models.writing_segments import WritingManuscript, WritingManuscriptAttempt
            manuscript = WritingManuscript(manuscript_id=uuid4(), project_id=project_id, thread_id=detail.thread_id,
                source_draft_id=draft.draft_id, generation_id=source.generation_id, status='queued')
            session.add(manuscript)
            session.flush()
            session.add(WritingManuscriptAttempt(run_id=new_run.run_id, manuscript_id=manuscript.manuscript_id,
                previous_run_id=previous_run_id, checkpoint_id=detail.checkpoint_id))
    event = events.prepare(project_id, new_run.run_id, "progress", stage=detail.current_stage,
        agent_name=None, status="queued", payload=ProgressPayload(stage=detail.current_stage,
        agent_name=None, status="queued", message="Workflow queued"))
    response = ApprovalSubmissionResponse(project_id=project_id, previous_run_id=previous_run_id,
        run_id=new_run.run_id, thread_id=new_run.thread_id, attempt_no=new_run.attempt_no)
    return response, event, (project_id, new_run.run_id)


def run_resume_attempt(project_id: UUID, run_id: UUID) -> None:
    """Read the committed decision and claim a queued attempt once."""
    try:
        _run_resume_attempt(project_id, run_id)
    except Exception:
        logger.error("Workflow resume service unavailable; reconciliation required")


def _run_resume_attempt(project_id: UUID, run_id: UUID) -> None:
    with database.SessionLocal() as session:
        runs, events = WorkflowRunService(session), EventService(session)
        invoked = False
        try:
            runs.scope.require_project(project_id, action="edit")
            run = runs.repository.get(project_id, run_id, for_update=True)
            if run is None or run.status != "queued":
                logger.warning("Resume attempt not queued; skipping duplicate execution")
                return
            record = session.scalar(select(WorkflowApprovalDecision).where(
                WorkflowApprovalDecision.project_id == project_id,
                WorkflowApprovalDecision.run_id == run_id).with_for_update())
            if record is None or record.execution_status != "queued" or record.thread_id != run.thread_id:
                raise ResumeConsistencyError("Approval intent mismatch")
            previous_id, checkpoint_id = record.previous_run_id, record.checkpoint_id
            decision: dict[str, str | list[str]] = {"decision": record.decision}
            if record.decision == "approve":
                decision["approved_ids"] = list(record.approved_ids)
            elif record.decision != "reject" or record.approved_ids:
                raise ResumeConsistencyError("Invalid approval intent")
            latest = runs.repository.latest(project_id, run.thread_id, for_update=True)
            if latest is None or latest.run_id != run_id:
                raise ResumeConsistencyError("Invalid attempt")
            previous = runs.repository.get(project_id, previous_id, for_update=True)
            if (previous is None or previous.thread_id != run.thread_id
                    or not _compatible_versions(previous.graph_version, run.graph_version, record.approval_type, record.decision)
                    or previous.status != "waiting_human" or previous.attempt_no + 1 != run.attempt_no):
                raise ResumeConsistencyError("Invalid previous attempt")
            stage = run.current_stage
            record.execution_status = "running"
            run = runs.transition(project_id, run_id, "running", current_stage=stage)
            evt = events.prepare(project_id, run_id, "progress", stage=stage, agent_name=None, status="running",
                payload=ProgressPayload(stage=stage, agent_name=None, status="running", message="Workflow running"))
            config = runnable_config_for_attempt(run)
            thread_id = run.thread_id
            events.commit_and_notify(evt)
            with sqlite_checkpointer(checkpoint_path(), require_existing=True) as saver:
                graph = build_graph(run.graph_version).compile(checkpointer=saver)
                snapshot = graph.get_state({"configurable": {"thread_id": str(thread_id)}})
                # Revalidate immediately before execution against the old durable event.
                approval_service = WorkflowApprovalService(session)
                event_record = approval_service.events.latest_approval(project_id, previous_id)
                if event_record is None:
                    raise RuntimeError("Approval event missing")
                event = from_record(event_record)
                if event.payload.checkpoint_id != checkpoint_id:
                    raise RuntimeError("Checkpoint changed")
                detail = approval_service._extract(snapshot, project_id, previous_id, thread_id, stage, event)
                if previous.graph_version == "planner_real_v1":
                    approval_service._real_tasks(snapshot, detail, project_id, previous_id, thread_id)
                elif previous.graph_version == "planner_research_v2" and detail.approval_type == "source_approval":
                    approval_service._real_sources(snapshot, detail, project_id, previous_id, thread_id)
                ids = decision.get("approved_ids")
                if detail.approval_type != record.approval_type or (record.decision == "approve" and
                    (not isinstance(ids, list) or not ids or len(ids) != len(set(ids)) or not set(ids).issubset({str(item) for item in detail.item_ids}))):
                    raise RuntimeError("Approval intent mismatch")
                invoked = True
                graph.invoke(Command(resume=decision), config)
                # The Writing node commits status/progress with its own sessions.
                session.expire_all()
                run = runs.repository.get(project_id, run_id)
                record = session.scalar(select(WorkflowApprovalDecision).where(
                    WorkflowApprovalDecision.project_id == project_id, WorkflowApprovalDecision.run_id == run_id))
                if run is None or record is None:
                    raise RuntimeError("Workflow attempt unavailable")
                snapshot = graph.get_state({"configurable": {"thread_id": str(thread_id)}})
                if (run.graph_version == "planner_real_v1" and decision["decision"] == "approve"
                        and snapshot.next == ("planner_boundary",) and len(snapshot.interrupts) == 1
                        and snapshot.interrupts[0].value == {"type": "planner_paused"}):
                    run = runs.transition(project_id, run_id, "paused", current_stage="planning")
                    evt = events.prepare(project_id, run_id, "progress", stage="planning", agent_name=None,
                        status="paused", payload=ProgressPayload(stage="planning", agent_name=None,
                        status="paused", message="Planner approval complete; workflow paused"))
                elif (run.graph_version == "planner_research_v2" and record.approval_type == "source_approval"
                        and decision["decision"] == "approve" and snapshot.next == ("research_boundary",)
                        and len(snapshot.interrupts) == 1 and snapshot.interrupts[0].value == {"type": "research_paused"}):
                    run = runs.transition(project_id, run_id, "paused", current_stage="research")
                    evt = events.prepare(project_id, run_id, "progress", stage="research", agent_name=None,
                        status="paused", payload=ProgressPayload(stage="research", agent_name=None,
                        status="paused", message="Source approval complete; workflow paused"))
                elif snapshot.next == ("source_approval",) and len(snapshot.interrupts) == 1 and run.graph_version in {"mock_v1", "planner_research_v2"}:
                    value = snapshot.interrupts[0].value
                    if value.get("type") != "source_approval" or not value.get("source_ids"):
                        raise RuntimeError("Invalid interrupt")
                    new_checkpoint = UUID(snapshot.config["configurable"]["checkpoint_id"])
                    run = runs.transition(project_id, run_id, "waiting_human", current_stage="research")
                    evt = events.prepare(project_id, run_id, "approval_required", stage="research", agent_name="Research",
                        status="waiting_human", payload=ApprovalPayload(checkpoint_id=new_checkpoint,
                        approval_type="source_approval", payload_ref=run_id))
                elif not snapshot.next and not snapshot.interrupts:
                    if decision["decision"] == "reject":
                        run = runs.transition(project_id, run_id, "cancelled", current_stage=stage)
                        evt = events.prepare(project_id, run_id, "progress", stage=stage, agent_name=None, status="cancelled",
                            payload=ProgressPayload(stage=stage, agent_name=None, status="cancelled", message="Approval rejected"))
                    elif run.graph_version in {"planner_research_writing_v3", "planner_research_writing_v4"}:
                        result = UUID(snapshot.values["draft_id"])
                        run = runs.transition(project_id, run_id, "completed", current_stage="writing")
                        evt = events.prepare(project_id, run_id, "completed", stage="writing", agent_name="Writing", status="completed",
                            payload=CompletedPayload(result_ref=result, finished_at=run.finished_at))
                    else:
                        result = UUID(snapshot.values["review_id"])
                        run = runs.transition(project_id, run_id, "completed", current_stage="review")
                        evt = events.prepare(project_id, run_id, "completed", stage="review", agent_name="Review", status="completed",
                            payload=CompletedPayload(result_ref=result, finished_at=run.finished_at))
                else:
                    raise RuntimeError("Unexpected workflow result")
            record.execution_status = "finished"
            review_id = None
            if run.graph_version == 'planner_research_writing_v4' and run.status == 'completed':
                from app.services.review import prepare_automatic_review
                review_id = prepare_automatic_review(session, project_id, run_id)
            events.commit_and_notify(evt)
            if review_id:
                from app.services.review import run_review
                run_review(project_id, run_id, review_id)
        except Exception as error:
            session.rollback()
            # The writing node uses independent sessions and may already have
            # committed a definitive failed call and draft status.
            session.expire_all()
            if invoked:
                logger.error("Resume checkpoint may have advanced; reconciliation required")
            try:
                run = runs.repository.get(project_id, run_id)
                if run is not None and run.status == "running":
                    record = session.scalar(select(WorkflowApprovalDecision).where(
                        WorkflowApprovalDecision.project_id == project_id,
                        WorkflowApprovalDecision.run_id == run_id).with_for_update())
                    if record is not None:
                        record.execution_status = "needs_review"
                    if run.graph_version == "planner_research_writing_v4":
                        from app.models.writing_segments import WritingManuscript, WritingManuscriptAttempt, WritingUnit, WritingUnitCall
                        attempt = session.get(WritingManuscriptAttempt, run_id)
                        manuscript = session.get(WritingManuscript, attempt.manuscript_id, with_for_update=True) if attempt else None
                        if manuscript and manuscript.status in {'queued', 'writing'}:
                            manuscript.status = 'needs_review'
                            for call in session.scalars(select(WritingUnitCall).where(WritingUnitCall.run_id == run_id,
                                    WritingUnitCall.status == 'invoking').with_for_update()):
                                call.status = 'needs_review'
                                unit = session.get(WritingUnit, call.unit_id, with_for_update=True)
                                if unit and unit.status == 'writing':
                                    unit.status = 'needs_review'
                    if run.graph_version in {"planner_research_writing_v3", "planner_research_writing_v4"}:
                        from app.models.writing import WritingCall, WritingChapter, WritingDraft
                        draft = session.scalar(select(WritingDraft).where(WritingDraft.project_id == project_id,
                            WritingDraft.run_id == run_id).with_for_update())
                        if draft is not None and draft.status in {"queued", "writing"}:
                            draft.status = "needs_review"
                            calls = session.scalars(select(WritingCall).where(WritingCall.draft_id == draft.draft_id,
                                WritingCall.status == "invoking").with_for_update())
                            for call in calls:
                                call.status = "needs_review"
                                if call.chapter_id is not None:
                                    chapter = session.get(WritingChapter, call.chapter_id, with_for_update=True)
                                    if chapter is not None and chapter.status == "writing":
                                        chapter.status = "needs_review"
                    run = runs.transition(project_id, run_id, "failed", error_code="internal_error", retryable=False)
                    evt = events.prepare(project_id, run_id, "error", stage=run.current_stage, agent_name=None, status="failed",
                        payload=ErrorPayload(error_code="internal_error", safe_message="Workflow execution failed", retryable=False))
                    events.commit_and_notify(evt)
                elif run is not None and run.status == "queued" and isinstance(error, ResumeConsistencyError):
                    record = session.scalar(select(WorkflowApprovalDecision).where(
                        WorkflowApprovalDecision.project_id == project_id,
                        WorkflowApprovalDecision.run_id == run_id).with_for_update())
                    if record is not None:
                        record.execution_status = "needs_review"
                        session.commit()
                    logger.error("Queued resume inconsistent; reconciliation required: project_id=%s run_id=%s", project_id, run_id)
                else:
                    logger.error("Resume attempt could not be marked failed; reconciliation required")
            except Exception:
                session.rollback()
                logger.error("Resume failure record unavailable; reconciliation required")
