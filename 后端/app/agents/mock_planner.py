"""Offline Planner mock: create stable task references only."""

from typing import Literal, TypedDict

from app.agents.mock_common import mock_id
from app.graph.state import PaperAgentState


class PlannerUpdate(TypedDict):
    tasks: list[str]
    current_stage: Literal["planning"]


def planner_mock(state: PaperAgentState) -> PlannerUpdate:
    project_id = state["project_id"]
    return {
        "tasks": [mock_id(project_id, "mock-task:1"), mock_id(project_id, "mock-task:2")],
        "current_stage": "planning",
    }
