"""Offline Research mock: reference sources for approved mock tasks."""

from typing import Literal, TypedDict

from app.agents.mock_common import approved_subset, mock_id
from app.graph.state import PaperAgentState


class ResearchUpdate(TypedDict):
    source_ids: list[str]
    current_stage: Literal["research"]


def research_mock(state: PaperAgentState) -> ResearchUpdate:
    approved = state.get("approved_task_ids", [])
    if not approved_subset(approved, state.get("tasks")):
        raise ValueError("Approved tasks are required")
    project_id = state["project_id"]
    return {
        "source_ids": [mock_id(project_id, f"mock-source:{task_id}") for task_id in approved],
        "current_stage": "research",
    }
