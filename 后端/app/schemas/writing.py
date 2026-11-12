"""Bounded, citation-aware model outputs for the proposed Writing Agent.

These contracts do not establish that a cited source supports a claim. Human
review remains necessary; evidence is checked against the approved abstract.
"""
import json
import re
import unicodedata
from typing import Annotated
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, ValidationError, model_validator


ShortTitle = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=120)]
Description = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=500)]
ParagraphText = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=4000)]
EvidenceText = Annotated[str, StringConstraints(strip_whitespace=True, min_length=8, max_length=600)]


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


def _normalized(value: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", value).split()).casefold()


class OutlineSection(_Strict):
    title: ShortTitle
    objective: Description


class OutlineOutput(_Strict):
    sections: list[OutlineSection] = Field(min_length=2, max_length=8)

    @model_validator(mode="after")
    def unique_titles(self):
        names = [_normalized(section.title) for section in self.sections]
        if len(names) != len(set(names)):
            raise ValueError("Duplicate outline title")
        return self


class CitationOutput(_Strict):
    source_id: str
    evidence: EvidenceText

    @model_validator(mode="after")
    def canonical_uuid(self):
        try:
            valid = str(UUID(self.source_id)) == self.source_id
        except (ValueError, TypeError, AttributeError):
            valid = False
        if not valid:
            raise ValueError("Invalid source identity")
        return self


class DraftParagraph(_Strict):
    text: ParagraphText
    citations: list[CitationOutput] = Field(min_length=1, max_length=8)

    @model_validator(mode="after")
    def no_unverified_identifiers(self):
        # Bibliographic information is supplied by the server, never the LLM.
        if re.search(r"(?i)\b10\.\d{4,9}/\S+|https?://|\bdoi\s*:", self.text):
            raise ValueError("Unverified bibliographic identifier")
        keys = [(c.source_id, _normalized(c.evidence)) for c in self.citations]
        if len(keys) != len(set(keys)):
            raise ValueError("Duplicate citation")
        return self


class ChapterOutput(_Strict):
    paragraphs: list[DraftParagraph] = Field(min_length=1, max_length=12)


def _json(text: str) -> object:
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError("Duplicate key")
            result[key] = value
        return result
    def invalid_constant(_value):
        raise ValueError("Invalid constant")
    if not isinstance(text, str) or len(text.encode("utf-8")) > 131072:
        raise ValueError("Invalid output")
    text = text.strip()
    if text.startswith("```"):
        fenced = re.fullmatch(r"```(?:json)?[ \t]*\r?\n([\s\S]*?)\r?\n```[ \t]*", text, re.IGNORECASE)
        if fenced is None:
            raise ValueError("Invalid JSON wrapper")
        text = fenced.group(1)
    return json.loads(text, object_pairs_hook=pairs, parse_constant=invalid_constant)


class WritingOutputError(ValueError):
    """A safe output-validation category; never contains model output."""
    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


def parse_outline(text: str) -> OutlineOutput:
    if not isinstance(text, str):
        raise WritingOutputError("outline_type")
    text = text.strip()
    if text.startswith("```"):
        fenced = re.fullmatch(r"```(?:json)?[ \t]*\r?\n([\s\S]*?)\r?\n```[ \t]*", text, re.IGNORECASE)
        if fenced is None:
            raise WritingOutputError("outline_json")
        text = fenced.group(1)
    try:
        value = _json(text)
    except (ValueError, TypeError, UnicodeError, RecursionError):
        raise WritingOutputError("outline_json") from None
    try:
        return OutlineOutput.model_validate(value)
    except ValidationError as error:
        types = {item["type"] for item in error.errors(include_input=False, include_url=False, include_context=False)}
        code = "outline_missing" if "missing" in types else "outline_type" if any(t.endswith("_type") for t in types) else "outline_schema"
        raise WritingOutputError(code) from None


def parse_chapter(text: str, approved_abstracts: dict[str, str]) -> ChapterOutput:
    """Validate source membership and direct excerpts; never store raw output."""
    try:
        if not approved_abstracts:
            raise ValueError("No approved evidence")
        chapter = ChapterOutput.model_validate(_json(text))
        for paragraph in chapter.paragraphs:
            for citation in paragraph.citations:
                abstract = approved_abstracts.get(citation.source_id)
                if not isinstance(abstract, str) or _normalized(citation.evidence) not in _normalized(abstract):
                    raise ValueError("Evidence not present in approved source")
        return chapter
    except (ValueError, TypeError, UnicodeError, RecursionError):
        raise ValueError("Invalid chapter output") from None
