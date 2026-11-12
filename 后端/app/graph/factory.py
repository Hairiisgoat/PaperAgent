"""Versioned graph factory; unknown or unavailable graphs fail closed."""
from langgraph.graph import END, START, StateGraph
from langgraph.types import interrupt

from app.graph.approval_graph import WorkflowInput, _after_task_approval, _after_source_approval, build_approval_graph, source_approval, task_approval
from app.graph.state import PaperAgentState


def planner_boundary(state: PaperAgentState) -> dict:
    interrupt({"type": "planner_paused"})
    return {}


def research_boundary(state: PaperAgentState) -> dict:
    interrupt({"type": "research_paused"})
    return {}


def build_graph(version: str):
    if version == "mock_v1":
        return build_approval_graph()
    if version not in {"planner_real_v1", "planner_research_v2", "planner_research_writing_v3", "planner_research_writing_v4"}:
        raise ValueError("Unknown workflow graph version")
    from app.services.planner import planner_node
    graph = StateGraph(PaperAgentState, input_schema=WorkflowInput)
    graph.add_node("planner", planner_node)
    graph.add_node("task_approval", task_approval)
    graph.add_edge(START, "planner")
    graph.add_edge("planner", "task_approval")
    if version == "planner_real_v1":
        graph.add_node("planner_boundary", planner_boundary)
        graph.add_conditional_edges("task_approval", _after_task_approval, {"research": "planner_boundary", "end": END})
    else:
        from app.services.research import research_node
        graph.add_node("research", research_node)
        graph.add_node("source_approval", source_approval)
        graph.add_conditional_edges("task_approval", _after_task_approval, {"research": "research", "end": END})
        graph.add_edge("research", "source_approval")
        if version == "planner_research_v2":
            graph.add_node("research_boundary", research_boundary)
            graph.add_conditional_edges("source_approval", _after_source_approval, {"writing": "research_boundary", "end": END})
        else:
            if version == "planner_research_writing_v4":
                from app.services.segmented_writing import segmented_node as writing_node
            else:
                from app.services.writing import writing_node
            graph.add_node("writing", writing_node)
            graph.add_conditional_edges("source_approval", _after_source_approval, {"writing": "writing", "end": END})
            graph.add_edge("writing", END)
    return graph
