"""Strict contracts for short writing units; not a substitute for human review."""
import unicodedata
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, ValidationError, model_validator

from app.schemas.writing import ChapterOutput, WritingOutputError, _json


class UnitPlanItem(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    title: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=120)]
    objective: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=500)]


class UnitPlan(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    sections: list[UnitPlanItem] = Field(min_length=2, max_length=4)

    @model_validator(mode="after")
    def unique_titles(self):
        titles = [" ".join(unicodedata.normalize("NFKC", item.title).split()).casefold() for item in self.sections]
        if len(set(titles)) != len(titles):
            raise ValueError("Duplicate unit title")
        return self


def parse_unit_plan(text: str) -> UnitPlan:
    """Validate an entire bounded plan; never retain or expose model output."""
    try:
        return UnitPlan.model_validate(_json(text))
    except (ValueError, TypeError, UnicodeError, RecursionError):
        raise ValueError("Invalid writing unit plan") from None


def parse_writing_unit(text: str, approved_abstracts: dict[str, str]) -> ChapterOutput:
    """Keep every paragraph evidence-bound and bound each unit to 1–3 paragraphs."""
    try:
        value = _json(text)
    except (ValueError, TypeError, UnicodeError, RecursionError):
        raise WritingOutputError('unit_json') from None
    try:
        result = ChapterOutput.model_validate(value)
        if len(result.paragraphs) > 3:
            raise ValueError("Too many paragraphs")
    except ValidationError as error:
        types = {item['type'] for item in error.errors(include_input=False, include_url=False, include_context=False)}
        code = 'unit_missing' if 'missing' in types else 'unit_type' if any(t.endswith('_type') for t in types) else 'unit_schema'
        raise WritingOutputError(code) from None
    except (ValueError, TypeError, UnicodeError, RecursionError):
        raise WritingOutputError('unit_schema') from None
    for paragraph in result.paragraphs:
        for citation in paragraph.citations:
            abstract = approved_abstracts.get(citation.source_id)
            if not isinstance(abstract, str):
                raise WritingOutputError('unit_source')
            if citation.evidence not in abstract:
                raise WritingOutputError('unit_excerpt')
    return result
