"""Strict, bounded model output; no provider-generated business IDs."""
import json
import unicodedata
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator


class PlannerTaskOutput(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    title: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=80)]
    description: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=300)]
    expected_output: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=120)]


class PlannerOutput(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    tasks: list[PlannerTaskOutput] = Field(min_length=3, max_length=8)

    @model_validator(mode="after")
    def unique_tasks(self):
        def normalize(value: str) -> str:
            return " ".join(unicodedata.normalize("NFKC", value).split()).casefold()
        pairs = [(normalize(item.title), normalize(item.description)) for item in self.tasks]
        if len(set(pairs)) != len(pairs):
            raise ValueError("Duplicate tasks")
        return self


def parse_planner_output(text: str) -> PlannerOutput:
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError("Duplicate key")
            result[key] = value
        return result

    def invalid_constant(_value):
        raise ValueError("Invalid JSON constant")

    try:
        if not isinstance(text, str) or len(text.encode("utf-8")) > 32768:
            raise ValueError("Invalid output")
        value = json.loads(text, object_pairs_hook=pairs, parse_constant=invalid_constant)
        return PlannerOutput.model_validate(value)
    except (ValueError, TypeError, UnicodeError, RecursionError):
        raise ValueError("Invalid planner output") from None
