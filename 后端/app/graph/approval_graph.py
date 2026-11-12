"""Offline workflow with real LangGraph human approval interrupts."""

from typing import Literal, TypedDict

from langgraph.graph import END, START, StateGraph
from langgraph.types import interrupt

from app.agents.mock_common import approved_subset
from app.agents.mock_planner import planner_mock
from app.agents.mock_research import research_mock
from app.agents.mock_review import review_mock
from app.agents.mock_writing import writing_mock
from app.graph.state import PaperAgentState, WorkflowStage


class ApprovalError(ValueError):
    """An invalid resume payload; the message contains no user input."""


class WorkflowInput(TypedDict):
    """Only fresh project context can enter through the graph input."""

    project_id: str
    current_stage: WorkflowStage


class TaskApprovalUpdate(TypedDict):
    approved_task_ids: list[str]


class SourceApprovalUpdate(TypedDict):
    approved_source_ids: list[str]


def _approved_ids(response: object, available: object) -> list[str]:
    """Validate untrusted resume input without including it in exceptions."""
    if not isinstance(response, dict) or set(response) - {"decision", "approved_ids"}:
        raise ApprovalError("Invalid approval response")
    decision = response.get("decision")
    ids = response.get("approved_ids")
    if decision == "reject":
        if ids is not None and ids != []:
            raise ApprovalError("Invalid approval response")
        return []
    if decision != "approve" or not approved_subset(ids, available):
        raise ApprovalError("Invalid approval response")
    # approved_subset has established a nonempty list containing only strings.
    assert isinstance(ids, list)
    if len(ids) != len(set(ids)):
        raise ApprovalError("Invalid approval response")
    return list(ids)


def _wait_for_approval(kind: str, id_field: str, available: list[str]) -> list[str]:
    # The whole node restarts on resume; no side effects occur before interrupt.
    error = False
    while True:
        payload: dict[str, object] = {"type": kind, id_field: list(available)}
        if error:
            payload["error"] = "invalid_approval"
        response = interrupt(payload)
        try:
            return _approved_ids(response, available)
        except ApprovalError:
            # Reject the value and interrupt again; never route with it.
            error = True


def task_approval(state: PaperAgentState) -> TaskApprovalUpdate:
    return {"approved_task_ids": _wait_for_approval("task_approval", "task_ids", state.get("tasks", []))}


def source_approval(state: PaperAgentState) -> SourceApprovalUpdate:
    return {"approved_source_ids": _wait_for_approval("source_approval", "source_ids", state.get("source_ids", []))}


def _after_task_approval(state: PaperAgentState) -> Literal["research", "end"]:
    return "research" if approved_subset(state.get("approved_task_ids"), state.get("tasks")) else "end"


def _after_source_approval(state: PaperAgentState) -> Literal["writing", "end"]:
    return "writing" if approved_subset(state.get("approved_source_ids"), state.get("source_ids")) else "end"


def build_approval_graph() -> StateGraph[PaperAgentState, None, WorkflowInput, PaperAgentState]:
    """Build the graph; callers must compile with a managed checkpointer."""
    graph = StateGraph(PaperAgentState, input_schema=WorkflowInput)
    graph.add_node("planner", planner_mock)
    graph.add_node("task_approval", task_approval)
    graph.add_node("research", research_mock)
    graph.add_node("source_approval", source_approval)
    graph.add_node("writing", writing_mock)
    graph.add_node("review", review_mock)
    graph.add_edge(START, "planner")
    graph.add_edge("planner", "task_approval")
    graph.add_conditional_edges("task_approval", _after_task_approval, {"research": "research", "end": END})
    graph.add_edge("research", "source_approval")
    graph.add_conditional_edges("source_approval", _after_source_approval, {"writing": "writing", "end": END})
    graph.add_edge("writing", "review")
    graph.add_edge("review", END)
    return graph
