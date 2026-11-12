"""Project-scoped workflow attempt lifecycle; callers own the transaction."""
from typing import Literal, get_args
from uuid import UUID, uuid4

from langchain_core.runnables import RunnableConfig
from sqlalchemy.orm import Session

from app.graph.state import ErrorCode, WorkflowStage
from app.models.paper_project import utc_now
from app.models.workflow_run import WorkflowRun
from app.repositories.workflow_run import WorkflowRunRepository
from app.services.project_scope import ProjectConflict, ProjectScope, ResourceNotFound

RunStatus = Literal["queued", "running", "waiting_human", "paused", "failed", "completed", "cancelled"]
_TRANSITIONS = {
    "queued": {"running", "cancelled"},
    "running": {"waiting_human", "paused", "failed", "completed", "cancelled"},
    "waiting_human": set(), "paused": set(), "failed": set(), "completed": set(), "cancelled": set(),
}


def runnable_config_for_attempt(run: WorkflowRun) -> RunnableConfig:
    """Map the application UUID to the root LangChain run and expose it as metadata."""
    return {"run_id": run.run_id, "configurable": {"thread_id": str(run.thread_id)},
            "metadata": {"paperagent_run_id": str(run.run_id), "paperagent_project_id": str(run.project_id)}}


class WorkflowRunService:
    """All writes require an active project lock; commit/rollback belongs to caller."""
    def __init__(self, session: Session):
        self.session = session
        self.scope = ProjectScope(session)
        self.repository = WorkflowRunRepository(session)

    def create(self, project_id: UUID, *, resume_thread_id: UUID | None = None, graph_version: str = "mock_v1", continue_research: bool = False, continue_writing: bool = False, manual_writing_retry: bool = False) -> WorkflowRun:
        self.scope.require_project(project_id, action="edit")
        if resume_thread_id is None:
            if continue_research or continue_writing or manual_writing_retry:
                raise ProjectConflict("Research transition unavailable")
            thread_id, attempt_no, stage = uuid4(), 1, None
        else:
            previous = self.repository.latest(project_id, resume_thread_id, for_update=True)
            if previous is None:
                raise ResourceNotFound("Resource not found")
            if manual_writing_retry and (previous.status != "failed" or previous.graph_version != "planner_research_writing_v3" or previous.current_stage not in {"research", "writing"}):
                raise ProjectConflict("Writing retry unavailable")
            if previous.status not in {"waiting_human", "paused"} and not (previous.status == "failed" and (previous.retryable is True or manual_writing_retry)):
                raise ProjectConflict("Workflow attempt cannot be resumed")
            thread_id, attempt_no, stage = previous.thread_id, previous.attempt_no + 1, previous.current_stage
            graph_version = previous.graph_version
            if continue_research:
                if graph_version != "planner_real_v1" or previous.status != "waiting_human":
                    raise ProjectConflict("Research transition unavailable")
                graph_version = "planner_research_v2"
            if continue_writing:
                if graph_version != "planner_research_v2" or previous.status != "waiting_human":
                    raise ProjectConflict("Writing transition unavailable")
                graph_version = "planner_research_writing_v3"
        if graph_version not in {"mock_v1", "planner_real_v1", "planner_research_v2", "planner_research_writing_v3"}:
            raise ValueError("Unknown workflow graph")
        run = WorkflowRun(run_id=uuid4(), thread_id=thread_id, project_id=project_id, attempt_no=attempt_no, status="queued", current_stage=stage, graph_version=graph_version)
        self.repository.add(run)
        return run

    def get(self, project_id: UUID, run_id: UUID) -> WorkflowRun:
        self.scope.require_project(project_id, action="read")
        run = self.repository.get(project_id, run_id)
        if run is None:
            raise ResourceNotFound("Resource not found")
        return run

    def list_project(self, project_id: UUID, *, limit: int = 50) -> list[WorkflowRun]:
        self.scope.require_project(project_id, action="read")
        return self.repository.list_project(project_id, limit=limit)

    def latest(self, project_id: UUID, thread_id: UUID) -> WorkflowRun:
        self.scope.require_project(project_id, action="read")
        run = self.repository.latest(project_id, thread_id)
        if run is None:
            raise ResourceNotFound("Resource not found")
        return run

    def transition(self, project_id: UUID, run_id: UUID, status: RunStatus, *,
                   current_stage: WorkflowStage | None = None,
                   error_code: ErrorCode | None = None, retryable: bool | None = None) -> WorkflowRun:
        # Validate before looking up or changing persistent objects. Never store exception strings.
        if status not in _TRANSITIONS or (current_stage is not None and current_stage not in get_args(WorkflowStage)):
            raise ValueError("Invalid workflow status or stage")
        if status == "failed":
            if error_code not in get_args(ErrorCode) or type(retryable) is not bool:
                raise ValueError("Invalid safe error summary")
        elif error_code is not None or retryable is not None:
            raise ValueError("Error summary is only valid for failed attempts")
        self.scope.require_project(project_id, action="edit")
        run = self.repository.get(project_id, run_id, for_update=True)
        if run is None:
            raise ResourceNotFound("Resource not found")
        if run.status == status:
            if ((current_stage is not None and run.current_stage != current_stage)
                    or run.error_code != error_code or run.retryable != retryable):
                raise ProjectConflict("Repeated workflow update differs")
            return run
        if status not in _TRANSITIONS.get(run.status, set()):
            raise ProjectConflict("Invalid workflow status transition")
        now = utc_now()
        run.status = status
        if current_stage is not None:
            run.current_stage = current_stage
        if status == "running":
            run.started_at = now
        else:
            run.finished_at = now
        run.error_code, run.retryable = error_code, retryable
        self.session.flush()
        return run
