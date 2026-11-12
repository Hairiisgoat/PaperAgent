"""Build the temporary offline graph; stopping is not an interrupt."""

from typing import Literal

from langgraph.graph import END, START, StateGraph

from app.agents.mock_common import approved_subset
from app.agents.mock_planner import planner_mock
from app.agents.mock_research import research_mock
from app.agents.mock_review import review_mock
from app.agents.mock_writing import writing_mock
from app.graph.state import PaperAgentState


def route_after_planner(state: PaperAgentState) -> Literal["research", "end"]:
    return "research" if approved_subset(state.get("approved_task_ids"), state.get("tasks")) else "end"


def route_after_research(state: PaperAgentState) -> Literal["writing", "end"]:
    return "writing" if approved_subset(state.get("approved_source_ids"), state.get("source_ids")) else "end"


def build_mock_graph() -> StateGraph[PaperAgentState, None, PaperAgentState, PaperAgentState]:
    """Return an uncompiled graph; caller decides when to compile it."""
    graph = StateGraph(PaperAgentState)
    graph.add_node("planner", planner_mock)
    graph.add_node("research", research_mock)
    graph.add_node("writing", writing_mock)
    graph.add_node("review", review_mock)
    graph.add_edge(START, "planner")
    graph.add_conditional_edges("planner", route_after_planner, {"research": "research", "end": END})
    graph.add_conditional_edges("research", route_after_research, {"writing": "writing", "end": END})
    graph.add_edge("writing", "review")
    graph.add_edge("review", END)
    return graph
