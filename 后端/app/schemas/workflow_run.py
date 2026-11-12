"""Read-only workflow status response contracts."""
from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict

from app.graph.state import ErrorCode, WorkflowStage
from app.services.workflow_run import RunStatus


class WorkflowStatusResponse(BaseModel):
    project_id: UUID
    thread_id: UUID
    latest_run_id: UUID
    status: RunStatus
    current_stage: WorkflowStage | None
    updated_at: datetime
    error_code: ErrorCode | None
    retryable: bool | None


class WorkflowRunResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    run_id: UUID
    thread_id: UUID
    project_id: UUID
    attempt_no: int
    status: RunStatus
    current_stage: WorkflowStage | None
    started_at: datetime | None
    finished_at: datetime | None
    created_at: datetime
    updated_at: datetime
    error_code: ErrorCode | None
    retryable: bool | None
