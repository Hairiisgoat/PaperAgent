import json
from types import SimpleNamespace
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.infrastructure import database
from app.infrastructure.glm_client import LLMError, LLMResult
from app.models.base import Base
from app.models.review import ReviewCall, ReviewReport, ReviewUnit
from app.models.writing import WritingChapter
from app.models.writing_segments import WritingChapterVersion
from app.services import review
from app.services.project_scope import ProjectConflict


@pytest.fixture
def context(monkeypatch):
    engine = create_engine('sqlite://', connect_args={'check_same_thread': False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False, autoflush=False)
    monkeypatch.setattr(database, 'SessionLocal', factory)
    project, run_id, manuscript_id, draft, chapter_id, version_id, source_id, report_id = (uuid4() for _ in range(8))
    manuscript = SimpleNamespace(manuscript_id=manuscript_id, source_draft_id=draft)
    run = SimpleNamespace()
    monkeypatch.setattr(review, '_context', lambda *args, **kwargs: (run, manuscript))
    abstract = 'A long enough exact source excerpt for testing.'
    source = SimpleNamespace(source_id=source_id, title='Source')
    monkeypatch.setattr(review, '_sources', lambda *args: ([source], {str(source_id): abstract}))
    paragraph = {'text': 'Claim.', 'citations': [{'source_id': str(source_id), 'evidence': abstract}]}
    with factory() as session:
        session.add(WritingChapter(chapter_id=chapter_id, draft_id=draft, position=1, title='Chapter', objective='Objective', status='completed'))
        session.add(WritingChapterVersion(version_id=version_id, manuscript_id=manuscript_id, chapter_id=chapter_id,
            version_no=1, kind='generated', paragraphs=[paragraph]))
        session.commit()
    yield factory, project, run_id, manuscript, chapter_id, version_id, source_id, report_id
    engine.dispose()


@pytest.mark.parametrize('mode', ['success', 'invalid', 'timeout'])
def test_review_snapshot_call_ledger_and_recovery(context, monkeypatch, mode):
    factory, project, run_id, manuscript, chapter_id, version_id, source_id, report_id = context
    calls = []
    class Service:
        def invoke(self, messages, *, max_tokens, call_id, read_timeout_seconds, reasoning_effort):
            calls.append(call_id)
            if mode == 'timeout':
                raise LLMError('timeout', outcome='unknown', call_id=call_id)
            data = json.loads(messages[1].content)
            assert data['version_id'] == str(version_id)
            issue = {'issue_type':'evidence', 'severity':'high', 'chapter_id':str(chapter_id), 'version_id':str(version_id),
                     'paragraph_index':1, 'description':'不足', 'rationale':'摘要未支持', 'suggestion':'缩小论断',
                     'source_ids':[str(source_id)]}
            return LLMResult(text='bad' if mode == 'invalid' else json.dumps({'summary':'核验', 'issues':[issue]}),
                             usage={'prompt_tokens': 5, 'completion_tokens': 7}, call_id=call_id)
    monkeypatch.setattr(review.LLMService, 'from_settings', lambda: Service())
    with factory() as session:
        report, created = review.create_review(session, project, run_id, report_id)
        session.commit()
        assert created and report.status == 'queued'
        assert not review.create_review(session, project, run_id, report_id)[1]
    review.run_review(project, run_id, report_id)
    with factory() as session:
        report = session.get(ReviewReport, report_id)
        unit = session.scalar(select(ReviewUnit).where(ReviewUnit.report_id == report_id))
        call = session.scalar(select(ReviewCall).where(ReviewCall.unit_id == unit.unit_id))
        assert unit.version_id == version_id
        assert report.status == {'success':'completed', 'invalid':'failed', 'timeout':'needs_review'}[mode]
        assert call.status == {'success':'succeeded', 'invalid':'failed', 'timeout':'needs_review'}[mode]
        if mode == 'success':
            assert unit.result['issues'][0]['source_ids'] == [str(source_id)]
        elif mode == 'invalid':
            review.retry_review(session, project, run_id, report_id)
            session.commit()
            assert report.status == unit.status == 'queued'
        else:
            with pytest.raises(ProjectConflict):
                review.retry_review(session, project, run_id, report_id)
    assert len(calls) == 1


def test_known_failure_retry_uses_new_call_and_preserves_old_call(context, monkeypatch):
    factory, project, run_id, _, chapter_id, version_id, _, report_id = context
    calls = []
    class Service:
        def invoke(self, messages, *, call_id, **_kwargs):
            calls.append(call_id)
            body = 'bad json' if len(calls) == 1 else json.dumps({'summary': '复查结果', 'issues': []})
            return LLMResult(text=body, usage={'prompt_tokens': 5, 'completion_tokens': 7}, call_id=call_id)
    monkeypatch.setattr(review.LLMService, 'from_settings', lambda: Service())
    with factory() as session:
        review.create_review(session, project, run_id, report_id)
        session.commit()
    review.run_review(project, run_id, report_id)
    with factory() as session:
        assert session.get(ReviewReport, report_id).status == 'failed'
        review.retry_review(session, project, run_id, report_id)
        session.commit()
    review.run_review(project, run_id, report_id)
    with factory() as session:
        unit = session.scalar(select(ReviewUnit).where(ReviewUnit.report_id == report_id))
        ledger = list(session.scalars(select(ReviewCall).where(ReviewCall.unit_id == unit.unit_id).order_by(ReviewCall.attempt_no)))
        assert unit.version_id == version_id and unit.status == 'completed'
        assert [call.status for call in ledger] == ['failed', 'succeeded']
        assert ledger[0].call_id != ledger[1].call_id
    assert len(calls) == 2 and calls[0] != calls[1]


def test_restart_never_replays_inflight_review(context, monkeypatch):
    from app.services.workflow_recovery import RecoveryScheduler
    factory, project, run_id, _, _, _, _, report_id = context
    with factory() as session:
        review.create_review(session, project, run_id, report_id)
        session.commit()
        unit = session.scalar(select(ReviewUnit).where(ReviewUnit.report_id == report_id))
        session.add(ReviewCall(call_id=uuid4(), unit_id=unit.unit_id, attempt_no=1, status='invoking'))
        unit.status = 'running'
        session.get(ReviewReport, report_id).status = 'running'
        session.commit()
    scheduler = RecoveryScheduler()
    submitted = []
    monkeypatch.setattr(scheduler, 'submit_review', lambda *args: submitted.append(args))
    try:
        scheduler.scan()
        with factory() as session:
            unit = session.scalar(select(ReviewUnit).where(ReviewUnit.report_id == report_id))
            assert session.get(ReviewReport, report_id).status == unit.status == 'needs_review'
            assert session.scalar(select(ReviewCall).where(ReviewCall.unit_id == unit.unit_id)).status == 'needs_review'
        assert not submitted
    finally:
        scheduler.close()


def test_failed_new_review_does_not_change_completed_report_or_version(context, monkeypatch):
    factory, project, run_id, _, _, version_id, _, report_id = context
    second_id = uuid4()
    class Service:
        def invoke(self, messages, *, call_id, **_kwargs):
            return LLMResult(text=json.dumps({'summary': '原审查', 'issues': []}),
                             usage={'prompt_tokens': 1, 'completion_tokens': 1}, call_id=call_id)
    monkeypatch.setattr(review.LLMService, 'from_settings', lambda: Service())
    with factory() as session:
        review.create_review(session, project, run_id, report_id)
        session.commit()
    review.run_review(project, run_id, report_id)
    class Failed:
        def invoke(self, messages, *, call_id, **_kwargs):
            raise LLMError('timeout', outcome='unknown', call_id=call_id)
    monkeypatch.setattr(review.LLMService, 'from_settings', lambda: Failed())
    with factory() as session:
        before = session.get(WritingChapterVersion, version_id).paragraphs
        review.create_review(session, project, run_id, second_id)
        session.commit()
    review.run_review(project, run_id, second_id)
    with factory() as session:
        assert session.get(ReviewReport, report_id).status == 'completed'
        assert session.get(ReviewReport, second_id).status == 'needs_review'
        assert session.get(WritingChapterVersion, version_id).paragraphs == before
        assert session.scalar(select(ReviewUnit).where(ReviewUnit.report_id == report_id)).result['summary'] == '原审查'


def test_assemble_reuses_exact_chapter_versions_with_identical_context(context, monkeypatch):
    factory, project, run_id, manuscript, chapter_id, version_id, _, first_id = context
    chapter2, version2, version2b, second_id, combined_id = (uuid4() for _ in range(5))
    calls = []
    class Service:
        def invoke(self, messages, *, call_id, **_kwargs):
            calls.append(call_id)
            return LLMResult(text=json.dumps({'summary': '复核', 'issues': []}),
                             usage={'prompt_tokens': 2, 'completion_tokens': 3}, call_id=call_id)
    monkeypatch.setattr(review.LLMService, 'from_settings', lambda: Service())
    with factory() as session:
        paragraphs = session.get(WritingChapterVersion, version_id).paragraphs
        session.add(WritingChapter(chapter_id=chapter2, draft_id=manuscript.source_draft_id,
            position=2, title='Second', objective='Scope', status='completed'))
        session.add(WritingChapterVersion(version_id=version2, manuscript_id=manuscript.manuscript_id,
            chapter_id=chapter2, version_no=1, kind='generated', paragraphs=paragraphs))
        session.commit()
        review.create_review(session, project, run_id, first_id)
        session.commit()
    review.run_review(project, run_id, first_id)
    assert len(calls) == 2
    with factory() as session:
        session.add(WritingChapterVersion(version_id=version2b, manuscript_id=manuscript.manuscript_id,
            chapter_id=chapter2, version_no=2, kind='restored', paragraphs=paragraphs))
        session.commit()
        review.create_review(session, project, run_id, second_id, chapter2)
        session.commit()
    review.run_review(project, run_id, second_id)
    with factory() as session:
        result = review.assemble_review(session, project, run_id, combined_id)
        session.commit()
        assert result.status == 'completed'
        units = list(session.scalars(select(ReviewUnit).where(ReviewUnit.report_id == combined_id)))
        assert {u.version_id for u in units} == {version_id, version2b}
        assert all(u.status == 'completed' and '_provenance' in u.result for u in units)
        assert {u.result['_provenance']['report_id'] for u in units} == {str(first_id), str(second_id)}
        assert not list(session.scalars(select(ReviewCall).where(ReviewCall.unit_id.in_([u.unit_id for u in units]))))
    assert len(calls) == 3


def test_assemble_rejects_missing_matching_result_and_rolls_back(context, monkeypatch):
    factory, project, run_id, _, _, _, _, report_id = context
    with factory() as session:
        with pytest.raises(ProjectConflict, match='current chapter'):
            review.assemble_review(session, project, run_id, report_id)
        session.rollback()
        assert session.get(ReviewReport, report_id) is None


def test_assemble_rejects_changed_cross_chapter_context(context, monkeypatch):
    factory, project, run_id, manuscript, _, version_id, _, first_id = context
    chapter2, old_version, changed_version, second_id, combined_id = (uuid4() for _ in range(5))
    class Service:
        def invoke(self, messages, *, call_id, **_kwargs):
            return LLMResult(text=json.dumps({'summary': 'review', 'issues': []}), usage=None, call_id=call_id)
    monkeypatch.setattr(review.LLMService, 'from_settings', lambda: Service())
    with factory() as session:
        paragraphs = session.get(WritingChapterVersion, version_id).paragraphs
        session.add(WritingChapter(chapter_id=chapter2, draft_id=manuscript.source_draft_id,
            position=2, title='Second', objective='Scope', status='completed'))
        session.add(WritingChapterVersion(version_id=old_version, manuscript_id=manuscript.manuscript_id,
            chapter_id=chapter2, version_no=1, kind='generated', paragraphs=paragraphs))
        session.commit()
        review.create_review(session, project, run_id, first_id)
        session.commit()
    review.run_review(project, run_id, first_id)
    changed = [{**paragraphs[0], 'text': 'A different claim.'}]
    with factory() as session:
        session.add(WritingChapterVersion(version_id=changed_version, manuscript_id=manuscript.manuscript_id,
            chapter_id=chapter2, version_no=2, kind='revised', paragraphs=changed))
        session.commit()
        review.create_review(session, project, run_id, second_id, chapter2)
        session.commit()
    review.run_review(project, run_id, second_id)
    with factory() as session:
        with pytest.raises(ProjectConflict, match='current chapter'):
            review.assemble_review(session, project, run_id, combined_id)
        session.rollback()
        assert session.get(ReviewReport, combined_id) is None
