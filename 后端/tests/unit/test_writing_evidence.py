"""Evidence input remains bounded and never silently drops approved abstracts."""
from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.services.writing import MAX_EVIDENCE_CHARACTERS, approved_evidence
from app.services.segmented_writing import evidence_candidates


def test_approved_evidence_keeps_more_than_eight_and_excludes_missing():
    sources = [SimpleNamespace(source_id=uuid4(), abstract=f'Approved abstract {i}') for i in range(12)]
    sources.append(SimpleNamespace(source_id=uuid4(), abstract=None))
    selected = approved_evidence(sources)
    assert len(selected) == 12
    assert {str(source.source_id) for source in sources[:12]} == set(selected)


def test_oversized_context_is_rejected_not_silently_truncated():
    sources = [SimpleNamespace(source_id=uuid4(), abstract='x' * (MAX_EVIDENCE_CHARACTERS + 1))]
    with pytest.raises(RuntimeError, match='context exceeds limit'):
        approved_evidence(sources)


def test_no_abstract_is_not_an_evidence_source():
    with pytest.raises(RuntimeError, match='abstracts unavailable'):
        approved_evidence([SimpleNamespace(source_id=uuid4(), abstract=None)])


def test_candidates_are_contiguous_original_text():
    abstract = '第一句话包含足够长度的实际证据。' + 'A' * 400 + '。第二句也具有足够长度的有效证据。'
    candidates = evidence_candidates(abstract)
    assert 1 <= len(candidates) <= 4
    assert all(len(candidate) >= 8 and candidate in abstract for candidate in candidates)
