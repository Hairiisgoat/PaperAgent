"""Typed SSE event envelope. Ephemeral events have no durable cursor."""
from datetime import UTC, datetime
import re
from typing import Annotated, Literal, Union
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, field_validator, model_validator
from app.graph.state import ErrorCode, WorkflowStage
from app.services.workflow_run import RunStatus


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ProgressPayload(StrictModel):
    stage: WorkflowStage
    agent_name: Literal["Planner", "Research", "Writing", "Review"] | None
    status: RunStatus
    message: Literal["Workflow queued", "Workflow running", "Planner completed", "Waiting for approval", "Workflow completed", "Approval rejected", "Planner approval complete; workflow paused", "Source approval complete; workflow paused"]


class ApprovalPayload(StrictModel):
    checkpoint_id: UUID
    approval_type: Literal["task_approval", "source_approval"]
    payload_ref: UUID


class TextDeltaPayload(StrictModel):
    chapter_id: UUID
    delta: str = Field(min_length=1, max_length=2048)
    delta_sequence: int = Field(ge=1)
    @field_validator("delta")
    @classmethod
    def reject_obvious_secrets(cls, value: str) -> str:
        if re.search(r"(?i)(api[_-]?key|mysql_password|password\s*[=:]|bearer\s+|-----begin [a-z ]*private key)", value):
            raise ValueError("Sensitive text is not allowed")
        return value


class CompletedPayload(StrictModel):
    result_ref: UUID
    finished_at: datetime
    @field_validator("finished_at")
    @classmethod
    def normalize_time(cls, value: datetime) -> datetime:
        if value.utcoffset() is None:
            raise ValueError("Timezone required")
        return value.astimezone(UTC)


class ErrorPayload(StrictModel):
    error_code: ErrorCode
    safe_message: Literal["Workflow execution failed", "Workflow persistence failed"]
    retryable: bool


class HeartbeatPayload(StrictModel):
    timestamp: datetime
    @field_validator("timestamp")
    @classmethod
    def normalize_time(cls, value: datetime) -> datetime:
        if value.utcoffset() is None:
            raise ValueError("Timezone required")
        return value.astimezone(UTC)


class EventBase(StrictModel):
    event_id: UUID
    sequence: int | None = Field(default=None, ge=1)
    run_id: UUID
    project_id: UUID
    stage: WorkflowStage | None
    agent_name: Literal["Planner", "Research", "Writing", "Review"] | None
    status: RunStatus
    timestamp: datetime

    @field_validator("timestamp")
    @classmethod
    def utc_timestamp(cls, value: datetime) -> datetime:
        if value.utcoffset() is None:
            raise ValueError("Timezone required")
        return value.astimezone(UTC)


class ProgressEvent(EventBase):
    event_type: Literal["progress"] = "progress"
    payload: ProgressPayload
    @model_validator(mode="after")
    def consistent(self):
        if self.sequence is None or (self.stage, self.agent_name, self.status) != (self.payload.stage, self.payload.agent_name, self.payload.status):
            raise ValueError("Invalid progress event")
        return self


class ApprovalEvent(EventBase):
    event_type: Literal["approval_required"] = "approval_required"
    payload: ApprovalPayload
    @model_validator(mode="after")
    def cursor(self):
        if self.sequence is None or self.status != "waiting_human":
            raise ValueError("Durable cursor required")
        return self


class CompletedEvent(EventBase):
    event_type: Literal["completed"] = "completed"
    payload: CompletedPayload
    @model_validator(mode="after")
    def cursor(self):
        if self.sequence is None or self.status != "completed" or self.payload.finished_at.utcoffset() is None:
            raise ValueError("Durable cursor and timezone required")
        return self


class ErrorEvent(EventBase):
    event_type: Literal["error"] = "error"
    payload: ErrorPayload
    @model_validator(mode="after")
    def cursor(self):
        if self.sequence is None or self.status != "failed":
            raise ValueError("Durable cursor required")
        return self


class TextDeltaEvent(EventBase):
    event_type: Literal["text_delta"] = "text_delta"
    payload: TextDeltaPayload
    @model_validator(mode="after")
    def transient(self):
        if self.sequence is not None:
            raise ValueError("Transient event cannot have durable cursor")
        return self


class HeartbeatEvent(EventBase):
    event_type: Literal["heartbeat"] = "heartbeat"
    payload: HeartbeatPayload
    @model_validator(mode="after")
    def transient(self):
        if self.sequence is not None or self.payload.timestamp.utcoffset() is None:
            raise ValueError("Invalid heartbeat")
        return self


WorkflowEvent = Annotated[Union[ProgressEvent, ApprovalEvent, TextDeltaEvent, CompletedEvent, ErrorEvent, HeartbeatEvent], Field(discriminator="event_type")]
event_adapter = TypeAdapter(WorkflowEvent)
