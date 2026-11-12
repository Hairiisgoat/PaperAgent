"""Project-scoped draft read model."""
from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID
from pydantic import BaseModel, ConfigDict, Field, StringConstraints


class DraftCitation(BaseModel):
    source_id: UUID
    title: str
    authors: list[str]
    year: int | None
    doi: str
    url: str
    evidence: str


class DraftParagraphResponse(BaseModel):
    text: str
    citations: list[DraftCitation]


class DraftChapterResponse(BaseModel):
    chapter_id: UUID
    position: int
    title: str
    objective: str
    status: Literal["queued", "writing", "completed", "failed", "needs_review"]
    paragraphs: list[DraftParagraphResponse]


class DraftResponse(BaseModel):
    project_id: UUID
    run_id: UUID
    draft_id: UUID
    status: Literal["queued", "writing", "completed", "failed", "needs_review"]
    chapters: list[DraftChapterResponse]
    supports_versions: bool = False


class ChapterVersionResponse(BaseModel):
    version_id: UUID
    chapter_id: UUID
    version_no: int
    kind: Literal['generated', 'revised', 'restored']
    parent_version_id: UUID | None
    created_at: datetime
    paragraphs: list[DraftParagraphResponse]


class RevisionSubmission(BaseModel):
    model_config = ConfigDict(extra='forbid')
    revision_id: UUID
    base_version_id: UUID
    mode: Literal['expand', 'condense', 'rewrite', 'restructure']
    instruction: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=2000)]


class RevisionResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    revision_id: UUID
    chapter_id: UUID
    base_version_id: UUID
    result_version_id: UUID | None
    mode: Literal['expand', 'condense', 'rewrite', 'restructure']
    status: Literal['queued', 'running', 'completed', 'failed', 'needs_review']
    error_code: str | None
    created_at: datetime


class RestoreSubmission(BaseModel):
    model_config = ConfigDict(extra='forbid')
    request_id: UUID
