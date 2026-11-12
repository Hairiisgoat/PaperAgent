"""Contracts for bounded units; no model or database is contacted."""
import json
from uuid import uuid4

import pytest

from app.schemas.writing import WritingOutputError
from app.schemas.writing_units import parse_unit_plan, parse_writing_unit


def test_unit_plan_accepts_fenced_json_and_rejects_duplicates_and_extra_fields():
    sections = [{"title": "研究背景", "objective": "讨论背景"}, {"title": "研究方法", "objective": "比较方法"}]
    assert len(parse_unit_plan('```json\n' + json.dumps({"sections": sections}) + '\n```').sections) == 2
    duplicate = [{"title": "Ａ", "objective": "one"}, {"title": "a", "objective": "two"}]
    with pytest.raises(ValueError):
        parse_unit_plan(json.dumps({"sections": duplicate}))
    with pytest.raises(ValueError):
        parse_unit_plan(json.dumps({"sections": sections, "unexpected": True}))
    with pytest.raises(ValueError):
        parse_unit_plan('{"sections":[],"sections":[]}')


def test_unit_requires_approved_source_and_real_evidence_for_each_paragraph():
    source = str(uuid4())
    data = {"paragraphs": [{"text": "需要人工核验的论述", "citations": [{"source_id": source, "evidence": "a real evidence excerpt"}]}]}
    result = parse_writing_unit(json.dumps(data), {source: "prefix a real evidence excerpt suffix"})
    assert result.paragraphs[0].citations[0].source_id == source
    with pytest.raises(ValueError):
        parse_writing_unit(json.dumps(data), {str(uuid4()): "a real evidence excerpt"})
    with pytest.raises(ValueError):
        parse_writing_unit(json.dumps(data), {source: "a real  evidence excerpt"})
    data["paragraphs"] *= 4
    with pytest.raises(ValueError):
        parse_writing_unit(json.dumps(data), {source: "a real evidence excerpt"})


@pytest.mark.parametrize('payload,expected', [
    ('{"paragraphs":', 'unit_json'),
    ('{"paragraphs":[{"text":"x"}]}', 'unit_missing'),
    ('{"paragraphs":"bad"}', 'unit_type'),
    ('{"paragraphs":[]}', 'unit_schema'),
])
def test_safe_unit_error_categories(payload, expected):
    with pytest.raises(WritingOutputError) as error:
        parse_writing_unit(payload, {})
    assert error.value.code == expected


def test_fabricated_excerpt_is_distinct_safe_error():
    source = str(uuid4())
    body = {'paragraphs':[{'text':'论述', 'citations':[{'source_id':source, 'evidence':'invented quote'}]}]}
    with pytest.raises(WritingOutputError) as error:
        parse_writing_unit(json.dumps(body), {source:'actual source abstract'})
    assert error.value.code == 'unit_excerpt'


def test_excerpt_and_source_errors_are_distinct():
    source = str(uuid4())
    body = {'paragraphs':[{'text':'论述', 'citations':[{'source_id':source, 'evidence':'valid excerpt'}]}]}
    with pytest.raises(WritingOutputError) as error:
        parse_writing_unit(json.dumps(body), {})
    assert error.value.code == 'unit_source'
