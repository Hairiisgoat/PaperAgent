"""Offline validation of draft and approved-source boundaries."""
import json
from uuid import uuid4
import pytest

from app.schemas.writing import WritingOutputError, parse_chapter, parse_outline


@pytest.mark.parametrize("raw,code", [
    ('{"sections":[{"title":"a"},{"title":"b","objective":"x"}]}', 'outline_missing'),
    ('{"sections":[{"title":3,"objective":"x"},{"title":"b","objective":"x"}]}', 'outline_type'),
    ('{"sections":[', 'outline_json'),
    ('{"sections":[{"title":"a","objective":"x"},{"title":"a","objective":"x"}]}', 'outline_schema'),
    ('```json\n{"sections":[]}\n``` trailing', 'outline_json'),
])
def test_outline_safe_error_categories(raw, code):
    with pytest.raises(WritingOutputError) as failure:
        parse_outline(raw)
    assert failure.value.code == code
    assert str(failure.value) == code


def test_outline_json_fence_is_accepted_without_weakening_schema():
    data = {"sections": [{"title":"一", "objective":"目的"}, {"title":"二", "objective":"目标"}]}
    assert len(parse_outline('```json\n' + json.dumps(data, ensure_ascii=False) + '\n```').sections) == 2
    with pytest.raises(WritingOutputError):
        parse_outline('```json\n' + json.dumps({"sections": [], "extra": True}) + '\n```')


def test_outline_requires_bounded_unique_sections():
    output = {"sections": [{"title": "背景", "objective": "介绍背景"}, {"title": "方法", "objective": "比较方法"}]}
    assert len(parse_outline(json.dumps(output)).sections) == 2
    output["sections"][1]["title"] = "背景"
    with pytest.raises(ValueError): parse_outline(json.dumps(output))
    with pytest.raises(ValueError): parse_outline('{"sections":[],"sections":[]}')


def test_chapter_references_only_approved_sources_and_real_excerpt():
    source = str(uuid4())
    abstract = "The software testing approach improved coverage in the evaluated dataset."
    data = {"paragraphs": [{"text": "improved coverage in the evaluated dataset", "citations": [{"source_id": source, "evidence": "improved coverage in the evaluated dataset"}]}]}
    assert parse_chapter(json.dumps(data), {source: abstract}).paragraphs[0].citations[0].source_id == source
    with pytest.raises(ValueError): parse_chapter(json.dumps(data), {})
    with pytest.raises(ValueError): parse_chapter(json.dumps(data), {str(uuid4()): abstract})
    data["paragraphs"][0]["citations"][0]["evidence"] = "a fabricated research conclusion"
    with pytest.raises(ValueError): parse_chapter(json.dumps(data), {source: abstract})


@pytest.mark.parametrize("value", ["10.1234/fake", "https://example.com", "DOI: fake"])
def test_model_cannot_supply_bibliographic_identifiers(value):
    source = str(uuid4())
    text = json.dumps({"paragraphs": [{"text": value, "citations": [{"source_id": source, "evidence": value}]}]})
    with pytest.raises(ValueError): parse_chapter(text, {source: value})


def test_paraphrased_discussion_allowed_with_real_evidence():
    source = str(uuid4())
    data = {"paragraphs": [{"text": "研究讨论了评估结果，仍需人工核对。", "citations": [{"source_id": source, "evidence": "actual verified evidence"}]}]}
    assert parse_chapter(json.dumps(data), {source: "actual verified evidence"}).paragraphs[0].text == data["paragraphs"][0]["text"]


def test_rejects_extra_fields_missing_citations_and_bad_json():
    source = str(uuid4())
    approved = {source: "actual evidence"}
    for value in [
        '{"paragraphs": [], "paragraphs": []}',
        json.dumps({"paragraphs": [{"text": "text", "citations": []}]}),
        json.dumps({"paragraphs": [{"text": "text", "citations": [{"source_id": source, "evidence": "actual evidence", "doi": "fake"}]}]}),
    ]:
        with pytest.raises(ValueError): parse_chapter(value, approved)
