"""Lightweight, checkpointable workflow state contract.

All IDs are canonical UUID strings. Business records remain authoritative in
the database. TypedDict describes the contract; it does not validate input.
"""

from typing import Literal, NotRequired, TypedDict


WorkflowStage = Literal["planning", "research", "outline", "writing", "review", "export"]
ErrorCode = Literal[
    "timeout",
    "provider_unavailable",
    "invalid_output",
    "database_unavailable",
    "budget_exceeded",
    "internal_error",
    "other",
]


class BudgetUsage(TypedDict):
    """Known aggregate token usage for this workflow snapshot, not a bill."""

    prompt_tokens: int
    completion_tokens: int


class BudgetAccounting(TypedDict):
    """Checkpointable totals and call identities; complete may never revert to true."""
    known_usage: BudgetUsage
    complete: bool
    call_ids: list[str]


class SafeErrorSummary(TypedDict):
    """Allowlisted error category; never include exception messages or inputs."""

    code: ErrorCode
    retryable: bool


class PaperAgentState(TypedDict):
    """State snapshot: required context plus optional workflow references.

    Missing optional keys mean not yet available, not an empty result. Optional
    reference, decision, and error values may be explicitly cleared with None.
    Collection and counter values, when present, are not nullable.
    """

    project_id: str
    current_stage: WorkflowStage
    tasks: NotRequired[list[str]]
    planner_generation_id: NotRequired[str]
    draft_id: NotRequired[str]
    approved_task_ids: NotRequired[list[str]]
    source_ids: NotRequired[list[str]]
    approved_source_ids: NotRequired[list[str]]
    outline_id: NotRequired[str | None]
    current_chapter_id: NotRequired[str | None]
    review_id: NotRequired[str | None]
    user_decision: NotRequired[str | None]
    retry_count: NotRequired[int]
    budget_usage: NotRequired[BudgetUsage]
    budget_accounting: NotRequired[BudgetAccounting]
    last_error: NotRequired[SafeErrorSummary | None]
