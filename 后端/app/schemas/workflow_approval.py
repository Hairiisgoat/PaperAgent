"""Only public, reference-only information from a pending human review."""
from typing import Literal
from uuid import UUID
from pydantic import BaseModel, ConfigDict, Field, model_validator
from app.graph.state import WorkflowStage


class ApprovalTask(BaseModel):
    task_id: UUID
    title: str
    description: str
    expected_output: str


class ApprovalSourceTask(BaseModel):
    task_id: UUID
    title: str


class ApprovalSource(BaseModel):
    source_id: UUID
    title: str
    authors: list[str]
    year: int | None
    journal: str | None
    doi: str
    url: str
    abstract: str | None
    task_ids: list[UUID]
    tasks: list[ApprovalSourceTask]
    relevance_hint: Literal["more", "some", "unknown"] | None = None
    same_title_count: int = Field(default=1, ge=1)


class WorkflowApprovalResponse(BaseModel):
    project_id: UUID
    run_id: UUID
    thread_id: UUID
    approval_type: Literal["task_approval", "source_approval"]
    checkpoint_id: UUID
    current_stage: WorkflowStage
    item_ids: list[UUID] = Field(min_length=1, max_length=1000)
    items: list[ApprovalTask] | None = None
    sources: list[ApprovalSource] | None = None


class ApprovalSubmission(BaseModel):
    model_config = ConfigDict(extra="forbid")
    decision: Literal["approve", "reject"]
    approved_ids: list[UUID] | None = Field(default=None, max_length=1000)
    continue_research: bool = False
    continue_writing: bool = False
    segmented_writing: bool = False

    @model_validator(mode="after")
    def validate_decision(self):
        if self.decision == "approve":
            if not self.approved_ids or len(set(self.approved_ids)) != len(self.approved_ids):
                raise ValueError("A nonempty list of unique IDs is required")
        elif self.approved_ids is not None:
            raise ValueError("Reject does not accept approved IDs")
        if self.continue_research and self.decision != "approve":
            raise ValueError("Research requires an approval")
        if self.continue_writing and (self.decision != "approve" or self.continue_research):
            raise ValueError("Writing requires a source approval")
        if self.segmented_writing and not self.continue_writing:
            raise ValueError("Segmented writing requires writing")
        return self


class ApprovalSubmissionResponse(BaseModel):
    project_id: UUID
    previous_run_id: UUID
    run_id: UUID
    thread_id: UUID
    attempt_no: int
    status: Literal["queued"] = "queued"
