"""Strict, bounded review output; model judgments still require human review."""
from typing import Annotated, Literal, Mapping, Sequence
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, ValidationError, model_validator

from app.schemas.writing import _json


Text = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=2000)]
IssueType = Literal['evidence', 'structure', 'logic', 'expression', 'completeness']


class ReviewIssue(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    issue_type: IssueType
    severity: Literal['low', 'medium', 'high']
    chapter_id: str
    version_id: str
    paragraph_index: int = Field(ge=1)
    description: Text
    rationale: Text
    suggestion: Text
    source_ids: list[str] = Field(max_length=8)

    @model_validator(mode='after')
    def canonical_ids(self):
        for value in (self.chapter_id, self.version_id, *self.source_ids):
            if str(UUID(value)) != value:
                raise ValueError('Invalid identifier')
        if len(set(self.source_ids)) != len(self.source_ids):
            raise ValueError('Duplicate sources')
        return self


class ReviewOutput(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    issues: list[ReviewIssue] = Field(max_length=100)
    summary: Text


def parse_review_output(text: str, versions: Mapping[str, tuple[str, int]],
                        approved_sources: set[str], paragraph_sources: Mapping[tuple[str, int], Sequence[str]]) -> ReviewOutput:
    """Accept only locations and source IDs from the supplied immutable snapshot.

    paragraph_index is one-based. A valid output is not proof of correctness or
    semantic entailment; the review is advice for a human to assess.
    """
    try:
        result = ReviewOutput.model_validate(_json(text))
        for issue in result.issues:
            version = versions.get(issue.chapter_id)
            if not version or issue.version_id != version[0] or issue.paragraph_index > version[1]:
                raise ValueError('Invalid review location')
            cited = set(paragraph_sources.get((issue.chapter_id, issue.paragraph_index), ()))
            if not set(issue.source_ids).issubset(approved_sources & cited):
                raise ValueError('Invalid review source')
        return result
    except (ValueError, TypeError, UnicodeError, RecursionError, ValidationError):
        pass
    # Raise outside the handler so validation errors do not retain raw input.
    raise ValueError('Invalid review output')
