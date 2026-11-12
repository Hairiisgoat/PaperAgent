"""Contract and checkpoint serialization tests; no workflow or database."""

from typing import get_type_hints
from uuid import uuid4

import pytest
from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer
from pydantic import TypeAdapter, ValidationError

from app.graph.state import BudgetUsage, PaperAgentState, SafeErrorSummary


def test_state_contract_has_only_required_context_and_lightweight_fields():
    assert PaperAgentState.__required_keys__ == {"project_id", "current_stage"}
    assert PaperAgentState.__optional_keys__ == {
        "tasks", "planner_generation_id", "draft_id", "approved_task_ids", "source_ids", "approved_source_ids", "outline_id",
        "current_chapter_id", "review_id", "user_decision", "retry_count", "budget_usage", "budget_accounting", "last_error",
    }
    assert "thread_id" not in get_type_hints(PaperAgentState)
    assert "run_id" not in get_type_hints(PaperAgentState)
    assert BudgetUsage.__required_keys__ == {"prompt_tokens", "completion_tokens"}
    assert SafeErrorSummary.__required_keys__ == {"code", "retryable"}


def test_state_serializes_through_installed_checkpoint_serializer():
    project, task, source = (str(uuid4()) for _ in range(3))
    state: PaperAgentState = {
        "project_id": project,
        "current_stage": "review",
        "tasks": [task],
        "approved_task_ids": [task],
        "source_ids": [source],
        "approved_source_ids": [source],
        "outline_id": str(uuid4()),
        "current_chapter_id": str(uuid4()),
        "review_id": str(uuid4()),
        "user_decision": None,
        "retry_count": 1,
        "budget_usage": {"prompt_tokens": 120, "completion_tokens": 30},
        "last_error": {"code": "timeout", "retryable": True},
    }
    serializer = JsonPlusSerializer()
    assert serializer.loads_typed(serializer.dumps_typed(state)) == state


def test_optional_keys_can_be_absent_and_nulls_can_be_serialized():
    state: PaperAgentState = {"project_id": str(uuid4()), "current_stage": "planning"}
    serializer = JsonPlusSerializer()
    assert serializer.loads_typed(serializer.dumps_typed(state)) == state
    state.update(outline_id=None, current_chapter_id=None, review_id=None, user_decision=None, last_error=None)
    assert serializer.loads_typed(serializer.dumps_typed(state)) == state


def test_instances_do_not_share_collections():
    first: PaperAgentState = {"project_id": str(uuid4()), "current_stage": "planning", "tasks": []}
    second: PaperAgentState = {"project_id": str(uuid4()), "current_stage": "planning", "tasks": []}
    first["tasks"].append(str(uuid4()))
    assert second["tasks"] == []


@pytest.mark.parametrize("invalid", [
    {"current_stage": "planning"},
    {"project_id": "id", "current_stage": "unexpected"},
    {"project_id": "id", "current_stage": "planning", "tasks": None},
    {"project_id": "id", "current_stage": "planning", "retry_count": "one"},
    {"project_id": "id", "current_stage": "planning", "last_error": {"code": "raw exception", "retryable": True}},
])
def test_existing_type_adapter_rejects_contract_type_errors(invalid):
    # This is a test of the type contract, not production runtime validation.
    with pytest.raises(ValidationError):
        TypeAdapter(PaperAgentState).validate_python(invalid, strict=True)


def test_existing_type_adapter_accepts_complete_and_minimal_contracts():
    adapter = TypeAdapter(PaperAgentState)
    minimal = {"project_id": str(uuid4()), "current_stage": "planning"}
    complete = {**minimal, "tasks": [], "budget_usage": {"prompt_tokens": 0, "completion_tokens": 0}, "last_error": None}
    assert adapter.validate_python(minimal, strict=True) == minimal
    assert adapter.validate_python(complete, strict=True) == complete
