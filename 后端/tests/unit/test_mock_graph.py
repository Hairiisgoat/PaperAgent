"""Offline tests for mock node boundaries and the real LangGraph routes."""

from copy import deepcopy
import socket
from typing import cast
from uuid import UUID

import pytest

from app.agents.mock_common import mock_id
from app.agents.mock_planner import planner_mock
from app.agents.mock_research import research_mock
from app.agents.mock_review import review_mock
from app.agents.mock_writing import writing_mock
from app.graph.mock_graph import build_mock_graph
from app.graph.state import PaperAgentState


PROJECT = "19f8b1b2-7dcc-44bf-92bb-dbc394c2173d"
OTHER_PROJECT = "865cb988-66e9-45a4-a080-5ef3ae20e088"
TASK = mock_id(PROJECT, "mock-task:1")
SOURCE = mock_id(PROJECT, f"mock-source:{TASK}")
UNRELATED = "11111111-1111-4111-8111-111111111111"


def initial(**updates: object) -> PaperAgentState:
    # Test inputs simulate externally supplied state, including intentionally invalid values.
    return cast(PaperAgentState, {"project_id": PROJECT, "current_stage": "planning", **updates})


@pytest.fixture(autouse=True)
def forbid_network(monkeypatch: pytest.MonkeyPatch) -> None:
    def reject(*args: object, **kwargs: object) -> None:
        raise AssertionError("Network access is forbidden in mock graph tests")

    monkeypatch.setenv("LANGSMITH_TRACING", "false")
    monkeypatch.setenv("LANGCHAIN_TRACING_V2", "false")
    monkeypatch.setattr(socket.socket, "connect", reject)
    monkeypatch.setattr(socket.socket, "connect_ex", reject)
    monkeypatch.setattr(socket, "create_connection", reject)


def execute_steps(state: PaperAgentState) -> tuple[list[str], dict[str, object]]:
    graph = build_mock_graph().compile()
    events = list(graph.stream(state, stream_mode="updates"))
    names = [name for event in events for name in event]
    result = dict(state)
    for event in events:
        for update in event.values():
            result.update(update)
    return names, result


def test_build_and_compile_without_checkpointer():
    graph = build_mock_graph().compile()
    assert graph.checkpointer is None


def test_individual_nodes_return_only_their_updates_without_mutating_input():
    state = initial(tasks=[TASK], approved_task_ids=[TASK], source_ids=[SOURCE], approved_source_ids=[SOURCE])
    state["current_chapter_id"] = mock_id(PROJECT, "mock-chapter:1")
    before = deepcopy(state)
    planner = planner_mock(state)
    research = research_mock(state)
    writing = writing_mock(state)
    review = review_mock(state)
    assert state == before
    assert set(planner) == {"tasks", "current_stage"}
    assert set(research) == {"source_ids", "current_stage"}
    assert set(writing) == {"current_chapter_id", "current_stage"}
    assert set(review) == {"review_id", "current_stage"}
    assert [planner["current_stage"], research["current_stage"], writing["current_stage"], review["current_stage"]] == ["planning", "research", "writing", "review"]
    assert planner["tasks"][0] == TASK and research["source_ids"] == [SOURCE]
    for value in [*planner["tasks"], *research["source_ids"], writing["current_chapter_id"], review["review_id"]]:
        assert str(UUID(value)) == value
    assert planner_mock(initial(project_id=OTHER_PROJECT))["tasks"][0] != TASK


def test_complete_graph_uses_only_explicit_approvals():
    state = initial(approved_task_ids=[TASK], approved_source_ids=[SOURCE])
    original = deepcopy(state)
    names, result = execute_steps(state)
    assert names == ["planner", "research", "writing", "review"]
    assert state == original
    assert result["tasks"][0] == TASK and result["source_ids"] == [SOURCE]
    assert result["approved_task_ids"] == [TASK] and result["approved_source_ids"] == [SOURCE]
    assert result["current_stage"] == "review" and result["review_id"]
    assert "user_decision" not in result
    invoked = build_mock_graph().compile().invoke(state)
    assert invoked["current_stage"] == "review"
    assert invoked["review_id"] == result["review_id"]
    assert state == original


@pytest.mark.parametrize("approval", [None, [], [UNRELATED], [TASK, UNRELATED], "not-a-list"])
def test_unapproved_or_invalid_tasks_stop_after_planner(approval):
    names, result = execute_steps(initial(approved_task_ids=approval))
    assert names == ["planner"]
    assert result["current_stage"] == "planning"
    assert "source_ids" not in result


@pytest.mark.parametrize("approval", [None, [], [UNRELATED], [SOURCE, UNRELATED], "not-a-list"])
def test_unapproved_or_invalid_sources_stop_after_research(approval):
    names, result = execute_steps(initial(approved_task_ids=[TASK], approved_source_ids=approval))
    assert names == ["planner", "research"]
    assert result["current_stage"] == "research"
    assert "current_chapter_id" not in result


def test_nodes_reject_missing_approval_even_when_called_directly():
    with pytest.raises(ValueError, match="^Approved tasks are required$"):
        research_mock(initial(tasks=[TASK], approved_task_ids=[UNRELATED]))
    with pytest.raises(ValueError, match="^Approved sources are required$"):
        writing_mock(initial(source_ids=[SOURCE], approved_source_ids=[UNRELATED]))
    with pytest.raises(ValueError, match="^A valid chapter ID is required$"):
        review_mock(initial(current_chapter_id="invalid"))


def test_preexisting_user_decision_is_not_modified():
    state = initial(approved_task_ids=[TASK], approved_source_ids=[SOURCE], user_decision="needs_revision")
    names, result = execute_steps(state)
    assert names[-1] == "review" and result["user_decision"] == "needs_revision"


def test_invalid_project_id_error_is_sanitized():
    with pytest.raises(ValueError, match="^Invalid project ID$"):
        planner_mock(initial(project_id="sensitive invalid input"))
