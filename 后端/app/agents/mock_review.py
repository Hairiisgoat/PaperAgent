"""Offline Review mock: reference a review, never decide for the user."""

from typing import Literal, TypedDict

from app.agents.mock_common import canonical_id, mock_id
from app.graph.state import PaperAgentState


class ReviewUpdate(TypedDict):
    review_id: str
    current_stage: Literal["review"]


def review_mock(state: PaperAgentState) -> ReviewUpdate:
    chapter_id = state.get("current_chapter_id")
    if not canonical_id(chapter_id):
        raise ValueError("A valid chapter ID is required")
    return {"review_id": mock_id(state["project_id"], f"mock-review:{chapter_id}"), "current_stage": "review"}
