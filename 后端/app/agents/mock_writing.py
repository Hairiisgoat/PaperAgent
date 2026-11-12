"""Offline Writing mock: reference a chapter, never generate text."""

from typing import Literal, TypedDict

from app.agents.mock_common import approved_subset, mock_id
from app.graph.state import PaperAgentState


class WritingUpdate(TypedDict):
    current_chapter_id: str
    current_stage: Literal["writing"]


def writing_mock(state: PaperAgentState) -> WritingUpdate:
    if not approved_subset(state.get("approved_source_ids"), state.get("source_ids")):
        raise ValueError("Approved sources are required")
    return {"current_chapter_id": mock_id(state["project_id"], "mock-chapter:1"), "current_stage": "writing"}
