"""Verify and expose a pending approval without resuming the graph."""
import logging
import sqlite3
import stat
from typing import Literal, NoReturn
from uuid import UUID

from langgraph.types import StateSnapshot
from sqlalchemy.orm import Session

from app.graph.factory import build_graph
from app.graph.checkpointer import sqlite_checkpointer
from app.agents.mock_common import canonical_id
from app.repositories.workflow_event import WorkflowEventRepository
from app.schemas.workflow_approval import WorkflowApprovalResponse
from app.schemas.workflow_event import ApprovalEvent
from app.services.mock_start import checkpoint_path
from app.services.project_scope import ProjectConflict, ProjectNotFound, ResourceNotFound
from app.services.workflow_events import from_record
from app.services.workflow_run import WorkflowRunService

logger = logging.getLogger(__name__)


class ApprovalUnavailable(Exception):
    """Checkpoint storage could not be read safely."""


class WorkflowApprovalService:
    def __init__(self, session: Session):
        self.runs = WorkflowRunService(session)
        self.events = WorkflowEventRepository(session)

    def _inconsistent(self, reason: str, project_id: UUID, run_id: UUID) -> NoReturn:
        # Only fixed reason codes and UUIDs are logged, never checkpoint content.
        logger.warning("Approval reconciliation required: reason=%s project_id=%s run_id=%s", reason, project_id, run_id)
        raise ProjectConflict("Approval is unavailable or inconsistent")

    def get(self, project_id: UUID, run_id: UUID) -> WorkflowApprovalResponse:
        # Match write-side lock order (project, then run) so a concurrent
        # service-driven resume cannot make this response refer to an old run.
        parent = self.runs.scope.require_project(project_id, action="read")
        self.runs.session.expire(parent)
        parent = self.runs.scope.repository.get(project_id, exclude_status="deleted", for_update=True)
        if parent is None or parent.lifecycle_status.casefold() == "deleted":
            raise ProjectNotFound("Project not found")
        if parent.lifecycle_status.casefold() not in {"active", "archived"}:
            raise ProjectConflict("Project state does not permit reading")
        run = self.runs.repository.get(project_id, run_id, for_update=True)
        if run is None:
            raise ResourceNotFound("Resource not found")
        if run.status != "waiting_human":
            raise ProjectConflict("Run is not waiting for human approval")
        latest = self.runs.repository.latest(project_id, run.thread_id, for_update=True)
        if latest is None or latest.run_id != run_id:
            self._inconsistent("not_latest_attempt", project_id, run_id)

        record = self.events.latest_approval(project_id, run_id, for_update=True)
        if record is None:
            self._inconsistent("missing_approval_event", project_id, run_id)
        try:
            event = from_record(record)
        except Exception:
            self._inconsistent("invalid_approval_event", project_id, run_id)
        if not isinstance(event, ApprovalEvent) or event.payload.payload_ref != run_id or event.stage != run.current_stage:
            self._inconsistent("approval_event_mismatch", project_id, run_id)

        try:
            path = checkpoint_path()
            try:
                file_status = path.stat()
            except FileNotFoundError:
                self._inconsistent("checkpoint_file_missing", project_id, run_id)
            if not stat.S_ISREG(file_status.st_mode):
                self._inconsistent("checkpoint_file_missing", project_id, run_id)
            with sqlite_checkpointer(path, read_only=True) as saver:
                graph = build_graph(run.graph_version).compile(checkpointer=saver)
                snapshot = graph.get_state({"configurable": {"thread_id": str(run.thread_id)}})
                detail = self._extract(snapshot, project_id, run_id, run.thread_id, run.current_stage, event)
                if run.graph_version == "planner_real_v1":
                    detail.items = self._real_tasks(snapshot, detail, project_id, run_id, run.thread_id)
                elif run.graph_version == "planner_research_v2" and detail.approval_type == "source_approval":
                    detail.sources = self._real_sources(snapshot, detail, project_id, run_id, run.thread_id)
                return detail
        except ProjectConflict:
            raise
        except (sqlite3.Error, OSError):
            logger.error("Approval checkpoint storage unavailable: project_id=%s run_id=%s", project_id, run_id)
            raise ApprovalUnavailable("Approval storage unavailable") from None
        except Exception:
            # Includes serializer or corrupt checkpoint errors; do not leak internals.
            logger.error("Approval checkpoint could not be read: project_id=%s run_id=%s", project_id, run_id)
            raise ApprovalUnavailable("Approval storage unavailable") from None

    def _real_tasks(self, snapshot, detail, project_id, run_id, thread_id):
        from app.repositories.planner import PlannerRepository
        from app.schemas.workflow_approval import ApprovalTask
        try:
            generation_id = UUID(snapshot.values["planner_generation_id"])
        except (ValueError, TypeError, KeyError):
            self._inconsistent("missing_generation", project_id, run_id)
        repo = PlannerRepository(self.runs.session)
        gen = repo.generation(project_id, thread_id, generation_id)
        tasks = repo.tasks(project_id, thread_id, generation_id)
        if (gen is None or gen.planner_run_id != run_id or gen.status != "tasks_persisted"
                or gen.task_count != len(tasks) or [t.task_id for t in tasks] != detail.item_ids
                or [t.position for t in tasks] != list(range(1, len(tasks) + 1))):
            self._inconsistent("task_batch_mismatch", project_id, run_id)
        return [ApprovalTask(task_id=t.task_id, title=t.title, description=t.description,
                             expected_output=t.expected_output) for t in tasks]

    def _real_sources(self, snapshot, detail, project_id, run_id, thread_id):
        from sqlalchemy import select
        from app.models.research_source import ResearchSource, ResearchSourceTask
        from app.models.workflow_approval_decision import WorkflowApprovalDecision
        from app.schemas.workflow_approval import ApprovalSource, ApprovalSourceTask
        from app.repositories.planner import PlannerRepository
        from app.services.research_quality import duplicate_title_counts, relevance_hint, relevance_score, title_key
        try:
            generation_id = UUID(snapshot.values["planner_generation_id"])
            approved = {UUID(value) for value in snapshot.values["approved_task_ids"]}
        except (ValueError, TypeError, KeyError):
            self._inconsistent("research_state_invalid", project_id, run_id)
        decision = self.runs.session.scalar(select(WorkflowApprovalDecision).where(
            WorkflowApprovalDecision.project_id == project_id, WorkflowApprovalDecision.run_id == run_id))
        if (not decision or decision.thread_id != thread_id or decision.approval_type != "task_approval"
                or decision.decision != "approve" or set(decision.approved_ids) != {str(item) for item in approved}):
            self._inconsistent("research_approval_mismatch", project_id, run_id)
        sources = list(self.runs.session.scalars(select(ResearchSource).where(
            ResearchSource.project_id == project_id, ResearchSource.thread_id == thread_id,
            ResearchSource.run_id == run_id, ResearchSource.generation_id == generation_id).order_by(ResearchSource.doi)))
        if [source.source_id for source in sources] != detail.item_ids:
            self._inconsistent("research_batch_mismatch", project_id, run_id)
        links = self.runs.session.execute(select(ResearchSourceTask.source_id, ResearchSourceTask.task_id).join(
            ResearchSource, ResearchSource.source_id == ResearchSourceTask.source_id).where(
            ResearchSource.project_id == project_id, ResearchSource.run_id == run_id)).all()
        grouped = {source.source_id: [] for source in sources}
        for source_id, task_id in links:
            if source_id not in grouped or task_id not in approved:
                self._inconsistent("research_link_mismatch", project_id, run_id)
            grouped[source_id].append(task_id)
        if any(not grouped[source.source_id] for source in sources):
            self._inconsistent("research_link_missing", project_id, run_id)
        tasks = {t.task_id: t for t in PlannerRepository(self.runs.session).tasks(project_id, thread_id, generation_id)}
        if not approved.issubset(tasks):
            self._inconsistent("research_tasks_missing", project_id, run_id)
        duplicate_counts = duplicate_title_counts([source.title for source in sources])
        return [ApprovalSource(source_id=s.source_id, title=s.title, authors=s.authors, year=s.year,
            journal=s.journal, doi=s.doi, url=s.url, abstract=s.abstract,
            task_ids=grouped[s.source_id], tasks=[ApprovalSourceTask(task_id=task_id, title=tasks[task_id].title)
                for task_id in grouped[s.source_id]],
            relevance_hint=relevance_hint([relevance_score(tasks[task_id].title, tasks[task_id].description,
                tasks[task_id].expected_output, s.title, s.abstract) for task_id in grouped[s.source_id]]),
            same_title_count=duplicate_counts.get(title_key(s.title), 1)) for s in sources]
    def _extract(self, snapshot: StateSnapshot, project_id: UUID, run_id: UUID, thread_id: UUID,
                 stage: str | None, event: ApprovalEvent) -> WorkflowApprovalResponse:
        config = snapshot.config or {}
        metadata = snapshot.metadata or {}
        if (not isinstance(config, dict) or not isinstance(config.get("configurable"), dict)
                or not isinstance(metadata, dict) or not isinstance(snapshot.values, dict)
                or not isinstance(snapshot.interrupts, (list, tuple))):
            self._inconsistent("invalid_checkpoint_shape", project_id, run_id)
        details = config["configurable"]
        if (details.get("thread_id") != str(thread_id)
                or details.get("checkpoint_id") != str(event.payload.checkpoint_id)
                or metadata.get("paperagent_run_id") != str(run_id)
                or metadata.get("paperagent_project_id") != str(project_id)
                or snapshot.values.get("project_id") != str(project_id)
                or snapshot.values.get("current_stage") != stage):
            self._inconsistent("checkpoint_identity_mismatch", project_id, run_id)
        if len(snapshot.interrupts) != 1:
            self._inconsistent("missing_or_multiple_interrupts", project_id, run_id)
        interrupt = snapshot.interrupts[0].value
        if not isinstance(interrupt, dict):
            self._inconsistent("invalid_interrupt", project_id, run_id)
        kind = interrupt.get("type")
        if kind == "task_approval":
            node, field, expected_stage = "task_approval", "task_ids", "planning"
        elif kind == "source_approval":
            node, field, expected_stage = "source_approval", "source_ids", "research"
        else:
            self._inconsistent("invalid_approval_type", project_id, run_id)
        if (snapshot.next != (node,) or stage != expected_stage or event.payload.approval_type != kind
                or set(interrupt) - {"type", field, "error"}
                or ("error" in interrupt and interrupt["error"] != "invalid_approval")):
            self._inconsistent("interrupt_mismatch", project_id, run_id)
        ids = interrupt.get(field)
        state_ids = snapshot.values.get("tasks" if kind == "task_approval" else "source_ids")
        if (not isinstance(ids, list) or not 1 <= len(ids) <= 1000
                or not all(canonical_id(value) for value in ids)
                or len(ids) != len(set(ids)) or ids != state_ids):
            self._inconsistent("invalid_review_ids", project_id, run_id)
        approval_type: Literal["task_approval", "source_approval"] = kind
        return WorkflowApprovalResponse(project_id=project_id, run_id=run_id, thread_id=thread_id,
            approval_type=approval_type, checkpoint_id=event.payload.checkpoint_id,
            current_stage=expected_stage, item_ids=[UUID(value) for value in ids])
