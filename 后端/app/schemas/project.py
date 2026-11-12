"""Request and response contracts for paper project management."""

import json
from datetime import datetime
from typing import Annotated, Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, field_validator, model_validator
from pydantic.json_schema import SkipJsonSchema

Title = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)]
Topic = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]
CitationStyle = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=40)]
PaperType = Literal["course_paper", "thesis", "literature_review", "research_paper"]
LifecycleStatus = Literal["active", "archived", "deleted"]
ProgressStatus = Literal["not_started", "in_progress", "completed"]


class ProjectFields(BaseModel):
    model_config = ConfigDict(extra="forbid")

    @field_validator("topic", check_fields=False)
    @classmethod
    def topic_fits_mysql_text(cls, value: str | None) -> str | None:
        if value is not None and len(value.encode("utf-8")) > 65535:
            raise ValueError("topic exceeds the MySQL TEXT byte limit")
        return value

    @field_validator("requirements", check_fields=False)
    @classmethod
    def requirements_are_json(cls, value: dict[str, Any] | None) -> dict[str, Any] | None:
        try:
            json.dumps(value, allow_nan=False)
        except (ValueError, TypeError, RecursionError):
            raise ValueError("requirements must contain valid JSON values") from None
        return value


class ProjectCreate(ProjectFields):
    title: Title
    topic: Topic
    citation_style: CitationStyle
    paper_type: PaperType = "course_paper"
    requirements: dict[str, Any] | None = None


class ProjectUpdate(ProjectFields):
    title: Title | SkipJsonSchema[None] = Field(default_factory=lambda: None)
    topic: Topic | SkipJsonSchema[None] = Field(default_factory=lambda: None)
    citation_style: CitationStyle | SkipJsonSchema[None] = Field(default_factory=lambda: None)
    paper_type: PaperType | SkipJsonSchema[None] = Field(default_factory=lambda: None)
    requirements: dict[str, Any] | None = None

    @model_validator(mode="after")
    def validate_patch(self):
        if not self.model_fields_set:
            raise ValueError("At least one editable field is required")
        for field in ("title", "topic", "citation_style", "paper_type"):
            if field in self.model_fields_set and getattr(self, field) is None:
                raise ValueError(f"{field} cannot be null")
        return self


class ProjectResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    user_id: UUID | None
    title: str
    paper_type: PaperType
    topic: str
    requirements: dict[str, Any] | None
    citation_style: str
    lifecycle_status: LifecycleStatus
    progress_status: ProgressStatus
    created_at: datetime
    updated_at: datetime
    archived_at: datetime | None
    deleted_at: datetime | None


class ProjectPage(BaseModel):
    items: list[ProjectResponse]
    total: int = Field(ge=0)
    page: int = Field(ge=1)
    page_size: int = Field(ge=1, le=100)


class ErrorResponse(BaseModel):
    detail: str