"""Execute one mock attempt until its first actual human interrupt."""
import logging
import os
from pathlib import Path
from uuid import UUID
from sqlalchemy import select

from dotenv import dotenv_values
from app.core.config import ENV_FILE
from app.graph.factory import build_graph
from app.graph.checkpointer import sqlite_checkpointer
from app.infrastructure import database
from app.models.workflow_approval_decision import WorkflowApprovalDecision
from app.schemas.workflow_event import ApprovalPayload, ErrorPayload, ProgressPayload
from app.services.workflow_events import EventService
from app.services.workflow_run import WorkflowRunService, runnable_config_for_attempt

logger = logging.getLogger(__name__)


def checkpoint_path() -> Path:
    value = os.environ.get("LANGGRAPH_SQLITE_PATH") or dotenv_values(ENV_FILE, interpolate=False, encoding="utf-8-sig").get("LANGGRAPH_SQLITE_PATH") or "./data/langgraph-checkpoints.sqlite3"
    path = Path(value)
    return path if path.is_absolute() else ENV_FILE.parent / path


def run_mock_attempt(project_id: UUID, run_id: UUID) -> None:
    try:
        _run_mock_attempt(project_id, run_id)
    except Exception:
        logger.error("Mock workflow service unavailable; reconciliation required")


def _run_mock_attempt(project_id: UUID, run_id: UUID) -> None:
    """Own a fresh session; log only fixed messages on unrecoverable failures."""
    checkpoint_may_exist = False
    with database.SessionLocal() as session:
        runs, events = WorkflowRunService(session), EventService(session)
        try:
            # The parent lock serializes all service-created attempts for a project.
            runs.scope.require_project(project_id, action="edit")
            run = runs.repository.get(project_id, run_id, for_update=True)
            if run is None or run.status != "queued":
                logger.warning("Initial run unavailable or already claimed: project_id=%s run_id=%s", project_id, run_id)
                return
            latest = runs.repository.latest(project_id, run.thread_id, for_update=True)
            approval = session.scalar(select(WorkflowApprovalDecision.previous_run_id).where(
                WorkflowApprovalDecision.run_id == run_id).with_for_update())
            queued = events.since(project_id, run_id, 0, limit=2)
            if (run.attempt_no != 1 or run.current_stage != "planning" or latest is None
                    or latest.run_id != run_id or approval is not None or len(queued) != 1
                    or queued[0].sequence != 1 or queued[0].event_type != "progress"
                    or queued[0].status != "queued" or queued[0].stage != "planning"
                    or queued[0].payload.message != "Workflow queued"):
                logger.error("Initial run intent inconsistent; reconciliation required: project_id=%s run_id=%s", project_id, run_id)
                return
            path = checkpoint_path()
            try:
                path.stat()
            except FileNotFoundError:
                pass  # No file means this thread cannot have a checkpoint.
            else:
                # Existing files must be readable and show no checkpoint for this thread.
                with sqlite_checkpointer(path, read_only=True) as saver:
                    graph = build_graph(run.graph_version).compile(checkpointer=saver)
                    old = graph.get_state({"configurable": {"thread_id": str(run.thread_id)}})
                    if old.values or old.next or old.interrupts or (old.config or {}).get("configurable", {}).get("checkpoint_id"):
                        logger.error("Initial run checkpoint exists; reconciliation required: project_id=%s run_id=%s", project_id, run_id)
                        return
            run = runs.transition(project_id, run_id, "running", current_stage="planning")
            evt = events.prepare(project_id, run_id, "progress", stage="planning", agent_name="Planner", status="running",
                payload=ProgressPayload(stage="planning", agent_name="Planner", status="running", message="Workflow running"))
            config = runnable_config_for_attempt(run)
            events.commit_and_notify(evt)
            path.parent.mkdir(parents=True, exist_ok=True)
            with sqlite_checkpointer(path) as saver:
                graph = build_graph(run.graph_version).compile(checkpointer=saver)
                checkpoint_may_exist = True
                graph.invoke({"project_id": str(project_id), "current_stage": "planning"}, config)
                snapshot = graph.get_state({"configurable": {"thread_id": str(run.thread_id)}})
                if snapshot.next != ("task_approval",) or not snapshot.interrupts or snapshot.interrupts[0].value.get("type") != "task_approval":
                    raise RuntimeError("Expected task approval")
                checkpoint_id = UUID(snapshot.config["configurable"]["checkpoint_id"])
            runs.transition(project_id, run_id, "waiting_human", current_stage="planning")
            evt = events.prepare(project_id, run_id, "approval_required", stage="planning", agent_name="Planner", status="waiting_human",
                payload=ApprovalPayload(checkpoint_id=checkpoint_id, approval_type="task_approval", payload_ref=run_id))
            events.commit_and_notify(evt)
        except Exception:
            session.rollback()
            if checkpoint_may_exist:
                # A recorded failed/uncertain Planner call is never replayed; make
                # the attempt visible without claiming that SQLite can be rolled back.
                try:
                    from app.repositories.planner import PlannerRepository
                    run = runs.repository.get(project_id, run_id)
                    gen = PlannerRepository(session).for_run(project_id, run_id)
                    if (run is not None and run.graph_version == "planner_real_v1" and run.status == "running"
                            and gen is not None and gen.status in {"failed", "needs_review"}):
                        safe_code = "timeout" if gen.error_code == "timeout" else "other"
                        run = runs.transition(project_id, run_id, "failed", current_stage="planning", error_code=safe_code, retryable=False)
                        evt = events.prepare(project_id, run_id, "error", stage="planning", agent_name="Planner", status="failed",
                            payload=ErrorPayload(error_code=safe_code, safe_message="Workflow execution failed", retryable=False))
                        events.commit_and_notify(evt)
                        return
                except Exception:
                    session.rollback()
                # SQLite may have advanced: do not rerun or claim recovery.
                logger.error("Mock workflow checkpoint may exist; status/event reconciliation required")
                return
            try:
                run = runs.repository.get(project_id, run_id)
                if run is not None and run.status == "running":
                    runs.transition(project_id, run_id, "failed", current_stage="planning", error_code="internal_error", retryable=False)
                    evt = events.prepare(project_id, run_id, "error", stage="planning", agent_name="Planner", status="failed",
                        payload=ErrorPayload(error_code="internal_error", safe_message="Workflow execution failed", retryable=False))
                    events.commit_and_notify(evt)
                else:
                    logger.error("Mock workflow start failed; reconciliation required")
            except Exception:
                session.rollback()
                logger.error("Mock workflow failure could not be recorded; reconciliation required")
