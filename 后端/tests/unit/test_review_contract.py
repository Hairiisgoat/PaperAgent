import json
from uuid import uuid4

import pytest

from app.schemas.review import parse_review_output


CHAPTER, VERSION, SOURCE, OTHER = (str(uuid4()) for _ in range(4))
VERSIONS = {CHAPTER: (VERSION, 2)}
SOURCES = {(CHAPTER, 1): [SOURCE], (CHAPTER, 2): []}


def payload(**changes):
    issue = {'issue_type': 'evidence', 'severity': 'high', 'chapter_id': CHAPTER,
             'version_id': VERSION, 'paragraph_index': 1, 'description': '证据不足',
             'rationale': '摘要没有提供该结论', 'suggestion': '缩小论断范围', 'source_ids': [SOURCE]}
    issue.update(changes)
    return json.dumps({'issues': [issue], 'summary': '请人工核验'}, ensure_ascii=False)


def parse(text, approved=None):
    return parse_review_output(text, VERSIONS, {SOURCE} if approved is None else approved, SOURCES)


def test_valid_review_is_version_and_paragraph_bound():
    result = parse(payload())
    assert result.issues[0].version_id == VERSION
    assert result.issues[0].paragraph_index == 1
    assert result.issues[0].source_ids == [SOURCE]


@pytest.mark.parametrize('changes', [
    {'chapter_id': OTHER}, {'version_id': OTHER}, {'paragraph_index': 0},
    {'paragraph_index': 3}, {'paragraph_index': 2}, {'source_ids': [OTHER]},
    {'source_ids': [SOURCE, SOURCE]}, {'severity': 'critical'}, {'extra': 'no'},
    {'paragraph_index': '1'},
])
def test_invalid_location_and_unverified_sources_are_rejected(changes):
    with pytest.raises(ValueError, match='^Invalid review output$'):
        parse(payload(**changes))


def test_non_approved_source_rejected():
    with pytest.raises(ValueError, match='^Invalid review output$'):
        parse(payload(), approved=set())


@pytest.mark.parametrize('text', ['{"issues":[],"issues":[],"summary":"x"}', '{broken',
                                  '{"issues":[],"summary":"x","unknown":1}'])
def test_invalid_json_and_extra_fields_rejected(text):
    with pytest.raises(ValueError, match='^Invalid review output$'):
        parse(text)


def test_empty_findings_are_allowed_without_claiming_correctness():
    result = parse('{"issues":[],"summary":"未发现问题，但不能保证没有遗漏"}')
    assert not result.issues


def test_invalid_output_does_not_retain_provider_response_in_exception_chain():
    with pytest.raises(ValueError) as caught:
        parse('{"issues":[],"summary":7,"private":"sensitive-example"}')
    assert caught.value.__context__ is None
    assert caught.value.__cause__ is None
    assert 'sensitive-example' not in str(caught.value)
