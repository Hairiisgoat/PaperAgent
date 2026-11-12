"""Opt-in only for an explicitly isolated MySQL instance; all test rows roll back."""
import json
import os
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event, select, text
from sqlalchemy.orm import sessionmaker

from app.core.config import get_database_settings
from app.graph.checkpointer import sqlite_checkpointer
from app.graph.factory import build_graph
from app.infrastructure import database
from app.infrastructure.glm_client import LLMError, LLMResult
from app.main import app
from app.models import PaperProject, PlannerGeneration
from app.services.planner import planner_node
from app.services.planner_reconciliation import inspect_planner

pytestmark = pytest.mark.skipif(os.getenv("PAPERAGENT_ISOLATED_MYSQL") != "1", reason="Requires explicitly isolated MySQL")


def test_research_flow_isolated_mysql(monkeypatch, tmp_path):
    from app.infrastructure.crossref import ScholarlyRecord
    from app.models.research_source import ResearchSource, ResearchSourceTask
    settings = get_database_settings()
    assert settings.host == "127.0.0.1" and settings.port != 3306
    assert (settings.database, settings.user) == ("paper_agent", "paper_agent_app")
    engine = create_engine(settings.url, hide_parameters=True)
    monkeypatch.setenv("LANGGRAPH_SQLITE_PATH", str(tmp_path / "checkpoint.sqlite"))
    monkeypatch.setenv("LANGSMITH_TRACING", "false")
    monkeypatch.setattr("app.infrastructure.llm_diagnostics.DIAGNOSTIC_PATH", tmp_path / "diagnostics.log")
    monkeypatch.setattr("app.api.workflow_events.load_llm_settings", lambda: SimpleNamespace(model="test"))
    class FakeService:
        model = "test"
        planner_read_timeout_seconds = 120
        def invoke(self, messages, *, max_tokens, call_id, read_timeout_seconds):
            body = {"tasks": [{"title": f"Task {i}", "description": f"Scope {i}", "expected_output": "Sources"} for i in range(3)]}
            return LLMResult(text=json.dumps(body), usage={"prompt_tokens": 7, "completion_tokens": 11}, call_id=call_id)
    monkeypatch.setattr("app.services.planner.LLMService.from_settings", lambda: FakeService())
    record = ScholarlyRecord(doi="10.1234/test", title="Research title", authors=("A Author",), year=2024,
        journal="Journal", url="https://doi.org/10.1234/test", abstract=None)
    record2 = ScholarlyRecord(doi="10.1234/test2", title="Research-title", authors=("A Author",), year=2025,
        journal="Journal", url="https://doi.org/10.1234/test2", abstract=None)
    monkeypatch.setattr("app.services.research.search_crossref", lambda query, *, limit: [record, record2])
    project_id = uuid4()
    with engine.connect() as connection:
        assert connection.execute(text("SELECT DATABASE(), CURRENT_USER(), @@port")).one() == ("paper_agent", "paper_agent_app@127.0.0.1", settings.port)
        connection.rollback()
        outer = connection.begin()
        factory = sessionmaker(bind=connection, autoflush=False, expire_on_commit=False, join_transaction_mode="create_savepoint")
        monkeypatch.setattr(database, "SessionLocal", factory)
        try:
            with factory() as session:
                session.add(PaperProject(id=project_id, title="隔离测试", topic="隔离主题", citation_style="APA"))
                session.commit()
            with TestClient(app) as client:
                first = client.post(f"/api/projects/{project_id}/planner-runs").json()
                url = f"/api/projects/{project_id}/workflow-runs/{first['run_id']}/approval"
                detail = client.get(url).json()
                second = client.post(url, json={"decision": "approve", "approved_ids": detail["item_ids"][:2], "continue_research": True}).json()
                source = client.get(f"/api/projects/{project_id}/workflow-runs/{second['run_id']}/approval")
                assert source.status_code == 200 and len(source.json()["sources"]) == 2
                assert all(item["same_title_count"] == 2 for item in source.json()["sources"])
                with factory() as session:
                    assert len(session.scalars(select(ResearchSource).where(ResearchSource.project_id == project_id)).all()) == 2
                    assert len(session.scalars(select(ResearchSourceTask)).all()) == 4
        finally:
            outer.rollback()
        assert connection.execute(select(PaperProject.id).where(PaperProject.id == project_id)).scalar_one_or_none() is None
    engine.dispose()


def test_research_mysql_constraints_and_rollback():
    from sqlalchemy import inspect
    from sqlalchemy.exc import IntegrityError
    from app.models import WorkflowRun, PlannerGeneration, WorkflowTask
    from app.models.research_source import ResearchSource, ResearchSourceTask
    settings = get_database_settings()
    assert settings.host == '127.0.0.1' and settings.port != 3306
    assert (settings.database, settings.user) == ('paper_agent', 'paper_agent_app')
    engine = create_engine(settings.url, hide_parameters=True)
    with engine.connect() as connection:
        inspector = inspect(connection)
        for model in (ResearchSource, ResearchSourceTask):
            assert {column['name'] for column in inspector.get_columns(model.__tablename__)} == set(model.__table__.columns.keys())
        assert {fk['referred_table'] for fk in inspector.get_foreign_keys('research_source_tasks')} == {'research_sources', 'workflow_tasks'}
        connection.rollback()
        transaction = connection.begin()
        try:
            with sessionmaker(bind=connection, join_transaction_mode='create_savepoint')() as session:
                project, run, thread, generation, task, source = [uuid4() for _ in range(6)]
                session.add(PaperProject(id=project, title='isolated', topic='isolated', citation_style='APA'))
                session.flush()
                session.add(WorkflowRun(run_id=run, project_id=project, thread_id=thread, attempt_no=1, status='queued', graph_version='planner_research_v2'))
                session.flush()
                session.add(PlannerGeneration(generation_id=generation, project_id=project, thread_id=thread, planner_run_id=run, plan_revision=1,
                    input_snapshot={}, model='mock', max_tokens=2048, call_id=uuid4(), status='tasks_persisted', usage_complete=False))
                session.flush()
                session.add(WorkflowTask(task_id=task, generation_id=generation, position=1, title='task', description='scope', expected_output='sources'))
                session.flush()
                def make_source(source_id, doi):
                    return ResearchSource(source_id=source_id, project_id=project, thread_id=thread, run_id=run, generation_id=generation,
                        doi=doi, title='title', authors=[], url='https://doi.org/' + doi)
                session.add(make_source(source, '10.1234/test'))
                session.flush()
                session.add(ResearchSourceTask(source_id=source, task_id=task, search_query='task'))
                session.flush()
                with pytest.raises(IntegrityError):
                    with session.begin_nested():
                        session.add(make_source(uuid4(), '10.1234/test'))
                        session.flush()
                with pytest.raises(IntegrityError):
                    with session.begin_nested():
                        session.add(ResearchSourceTask(source_id=source, task_id=uuid4(), search_query='bad'))
                        session.flush()
        finally:
            transaction.rollback()
        assert connection.execute(select(PaperProject.id).where(PaperProject.id == project)).scalar_one_or_none() is None
    engine.dispose()


def test_writing_draft_and_citations_in_isolated_mysql(monkeypatch, tmp_path):
    from app.infrastructure.crossref import ScholarlyRecord
    from app.models.writing import WritingCall, WritingChapter, WritingCitation, WritingDraft
    from sqlalchemy import inspect
    settings = get_database_settings()
    assert settings.host == '127.0.0.1' and settings.port != 3306
    assert (settings.database, settings.user) == ('paper_agent', 'paper_agent_app')
    engine = create_engine(settings.url, hide_parameters=True)
    monkeypatch.setenv('LANGGRAPH_SQLITE_PATH', str(tmp_path / 'checkpoint.sqlite'))
    monkeypatch.setenv('LANGSMITH_TRACING', 'false')
    monkeypatch.setattr('app.infrastructure.llm_diagnostics.DIAGNOSTIC_PATH', tmp_path / 'diagnostics.log')
    monkeypatch.setattr('app.api.workflow_events.load_llm_settings', lambda: SimpleNamespace(model='test'))
    class FakeService:
        model = 'test'
        planner_read_timeout_seconds = 120
        def invoke(self, messages, *, max_tokens, call_id, read_timeout_seconds):
            if messages[0].content.startswith('Create 3 to 8'):
                body = {'tasks': [{'title': f'Task {i}', 'description': f'Scope {i}', 'expected_output': 'Sources'} for i in range(3)]}
            elif messages[0].content.startswith('Create 2 to 4'):
                body = {'sections': [{'title': 'Background', 'objective': 'Context'}, {'title': 'Methods', 'objective': 'Methods'}]}
            else:
                source_id = json.loads(messages[1].content)['sources'][0]['source_id']
                body = {'paragraphs': [{'text': 'The study assessed testing coverage.', 'citations': [{'source_id': source_id, 'evidence': 'software test coverage'}]}]}
            return LLMResult(text=json.dumps(body), usage={'prompt_tokens': 7, 'completion_tokens': 11}, call_id=call_id)
    monkeypatch.setattr('app.services.planner.LLMService.from_settings', lambda: FakeService())
    source = ScholarlyRecord(doi='10.1234/test', title='Software testing', authors=('Author',), year=2024,
        journal='Journal', url='https://doi.org/10.1234/test', abstract='The study evaluated software test coverage in a sample.')
    monkeypatch.setattr('app.services.research.search_crossref', lambda query, *, limit: [source])
    project = uuid4()
    with engine.connect() as connection:
        assert connection.execute(text('SELECT DATABASE(), CURRENT_USER(), @@port')).one() == ('paper_agent', 'paper_agent_app@127.0.0.1', settings.port)
        inspector = inspect(connection)
        for model in (WritingDraft, WritingChapter, WritingCitation, WritingCall):
            assert {column['name'] for column in inspector.get_columns(model.__tablename__)} == set(model.__table__.columns.keys())
        assert {fk['referred_table'] for fk in inspector.get_foreign_keys('writing_citations')} == {'writing_chapters', 'research_sources'}
        connection.rollback()
        outer = connection.begin()
        factory = sessionmaker(bind=connection, autoflush=False, expire_on_commit=False, join_transaction_mode='create_savepoint')
        monkeypatch.setattr(database, 'SessionLocal', factory)
        try:
            with factory() as session:
                session.add(PaperProject(id=project, title='isolated', topic='isolated', citation_style='APA'))
                session.commit()
            with TestClient(app) as client:
                first = client.post(f'/api/projects/{project}/planner-runs').json()
                task_url = f"/api/projects/{project}/workflow-runs/{first['run_id']}/approval"
                task_ids = client.get(task_url).json()['item_ids']
                second = client.post(task_url, json={'decision':'approve', 'approved_ids':task_ids[:1], 'continue_research':True}).json()
                source_url = f"/api/projects/{project}/workflow-runs/{second['run_id']}/approval"
                source_ids = client.get(source_url).json()['item_ids']
                third = client.post(source_url, json={'decision':'approve', 'approved_ids':source_ids, 'continue_writing':True,'segmented_writing':False}).json()
                draft = client.get(f"/api/projects/{project}/workflow-runs/{third['run_id']}/draft")
                assert draft.status_code == 200 and draft.json()['status'] == 'completed'
                with factory() as session:
                    row = session.scalar(select(WritingDraft).where(WritingDraft.run_id == UUID(third['run_id'])))
                    assert len(session.scalars(select(WritingChapter).where(WritingChapter.draft_id == row.draft_id)).all()) == 2
                    assert len(session.scalars(select(WritingCall).where(WritingCall.draft_id == row.draft_id)).all()) == 3
                    assert len(session.scalars(select(WritingCitation)).all()) == 2
        finally:
            outer.rollback()
        assert connection.execute(select(PaperProject.id).where(PaperProject.id == project)).scalar_one_or_none() is None
    engine.dispose()


@pytest.mark.parametrize('retry_output', ['success', 'truncated', 'timeout'])
def test_writing_manual_retry_isolated_mysql(monkeypatch, tmp_path, retry_output):
    from app.infrastructure.crossref import ScholarlyRecord
    from app.models import WorkflowRun
    from app.models.writing import WritingCall, WritingDraft
    settings = get_database_settings()
    assert os.getenv('PAPERAGENT_DISPOSABLE_MYSQL') == '1'
    assert settings.host == '127.0.0.1' and settings.port != 3306
    assert (settings.database, settings.user) == ('paper_agent', 'paper_agent_app')
    engine = create_engine(settings.url, hide_parameters=True)
    monkeypatch.setenv('LANGGRAPH_SQLITE_PATH', str(tmp_path / 'checkpoint.sqlite'))
    monkeypatch.setenv('LANGSMITH_TRACING', 'false')
    monkeypatch.setattr('app.api.workflow_events.load_llm_settings', lambda: SimpleNamespace(model='test'))
    call_ids = []
    class FakeService:
        model = 'test'
        planner_read_timeout_seconds = 120
        def invoke(self, messages, *, max_tokens, call_id, read_timeout_seconds):
            call_ids.append(call_id)
            if messages[0].content.startswith('Create 3 to 8'):
                body = json.dumps({'tasks': [{'title':f'Task {i}', 'description':'Scope', 'expected_output':'Sources'} for i in range(3)]})
            elif messages[0].content.startswith('Create 2 to 4'):
                assert max_tokens == 16384 and read_timeout_seconds == 480
                if len(call_ids) == 3 and retry_output == 'truncated':
                    raise LLMError('output_truncated', outcome='received', usage={'prompt_tokens':7, 'completion_tokens':4096}, call_id=call_id)
                if len(call_ids) == 3 and retry_output == 'timeout':
                    raise LLMError('timeout', outcome='unknown', call_id=call_id)
                body = 'bad json' if len(call_ids) == 2 else '```json\n' + json.dumps({'sections':[
                    {'title':'Background','objective':'Context'}, {'title':'Methods','objective':'Methods'}]}) + '\n```'
            else:
                source_id = json.loads(messages[1].content)['sources'][0]['source_id']
                body = json.dumps({'paragraphs':[{'text':'Study result.', 'citations':[{'source_id':source_id, 'evidence':'software test coverage'}]}]})
            return LLMResult(text=body, usage={'prompt_tokens':7, 'completion_tokens':11}, call_id=call_id)
    monkeypatch.setattr('app.services.planner.LLMService.from_settings', lambda: FakeService())
    source = ScholarlyRecord(doi='10.1234/test', title='Software testing', authors=('Author',), year=2024,
        journal='Journal', url='https://doi.org/10.1234/test', abstract='The study evaluated software test coverage in a sample.')
    monkeypatch.setattr('app.services.research.search_crossref', lambda query, *, limit: [source])
    project = uuid4()
    with engine.connect() as connection:
        assert connection.execute(text('SELECT DATABASE(), CURRENT_USER(), @@port')).one() == ('paper_agent', 'paper_agent_app@127.0.0.1', settings.port)
    factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    monkeypatch.setattr(database, 'SessionLocal', factory)
    with factory() as session:
        session.add(PaperProject(id=project, title='isolated', topic='isolated', citation_style='APA')); session.commit()
    with TestClient(app) as client:
        first = client.post(f'/api/projects/{project}/planner-runs').json()
        url = f'/api/projects/{project}/workflow-runs/{first["run_id"]}/approval'
        tasks = client.get(url).json()['item_ids']
        second = client.post(url, json={'decision':'approve','approved_ids':tasks[:1],'continue_research':True}).json()
        url = f'/api/projects/{project}/workflow-runs/{second["run_id"]}/approval'
        sources = client.get(url).json()['item_ids']
        failed = client.post(url, json={'decision':'approve','approved_ids':sources,'continue_writing':True,'segmented_writing':False}).json()
        failed_status = client.get(f'/api/projects/{project}/workflow-runs/{failed["run_id"]}').json()
        assert failed_status['status'] == 'failed'
        with factory() as session:
            diagnostic_draft = session.scalar(select(WritingDraft).where(WritingDraft.run_id == UUID(failed['run_id'])))
            diagnostic_call = session.scalar(select(WritingCall).where(WritingCall.draft_id == diagnostic_draft.draft_id))
            assert diagnostic_draft.status == 'failed', (diagnostic_draft.status, diagnostic_call.status, diagnostic_call.error_code)
        url = f'/api/projects/{project}/workflow-runs/{failed["run_id"]}/writing-retry'
        response = client.post(url)
        assert response.status_code == 202, (response.text, failed_status)
        retry = response.json()
        status = client.get(f'/api/projects/{project}/workflow-runs/{retry["run_id"]}').json()['status']
        assert status == ('completed' if retry_output == 'success' else 'failed')
        if retry_output == 'success':
            assert len(client.get(f'/api/projects/{project}/workflow-runs/{retry["run_id"]}/draft').json()['chapters']) == 2
        else:
            with factory() as session:
                retry_draft = session.scalar(select(WritingDraft).where(WritingDraft.run_id == UUID(retry['run_id'])))
                retry_call = session.scalar(select(WritingCall).where(WritingCall.draft_id == retry_draft.draft_id))
                assert retry_draft.status == retry_call.status == ('failed' if retry_output == 'truncated' else 'needs_review')
                assert retry_call.error_code == ('output_truncated' if retry_output == 'truncated' else 'timeout')
            if retry_output == 'timeout':
                assert client.post(f'/api/projects/{project}/workflow-runs/{retry["run_id"]}/writing-retry').status_code == 409
        assert client.post(url).json()['run_id'] == retry['run_id']
        with factory() as session:
            old = session.get(WorkflowRun, UUID(failed['run_id']))
            draft = session.scalar(select(WritingDraft).where(WritingDraft.run_id == old.run_id))
            assert old.status == 'failed' and session.scalar(select(WritingCall).where(WritingCall.draft_id == draft.draft_id)).error_code == 'outline_json'
        assert len(call_ids) == len(set(call_ids)) == (5 if retry_output == 'success' else 3)
    engine.dispose()


def test_segmented_retry_and_versions_disposable_mysql(monkeypatch, tmp_path):
    import importlib.util
    from pathlib import Path
    settings = get_database_settings()
    assert os.getenv('PAPERAGENT_DISPOSABLE_MYSQL') == '1'
    assert (settings.host, settings.database, settings.user) == ('127.0.0.1', 'paper_agent', 'paper_agent_app') and settings.port != 3306
    engine = create_engine(settings.url, hide_parameters=True)
    with engine.connect() as connection:
        assert connection.execute(text('SELECT DATABASE(), CURRENT_USER(), @@port')).one() == ('paper_agent', 'paper_agent_app@127.0.0.1', settings.port)
    monkeypatch.setenv('LANGGRAPH_SQLITE_PATH', str(tmp_path / 'checkpoint.sqlite'))
    monkeypatch.setenv('LANGSMITH_TRACING', 'false')
    monkeypatch.setattr('app.api.workflow_events.load_llm_settings', lambda: SimpleNamespace(model='test'))
    factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    monkeypatch.setattr(database, 'SessionLocal', factory)
    project = uuid4()
    with factory() as session:
        session.add(PaperProject(id=project, title='isolated', topic='isolated', citation_style='APA'))
        session.commit()
    path = Path(__file__).resolve().parents[1] / 'unit' / 'test_real_planner.py'
    spec = importlib.util.spec_from_file_location('segmented_unit_tests', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.test_segmented_retry_preserves_units_and_old_run((factory, tmp_path / 'checkpoint.sqlite', project, []), monkeypatch, False)
    project2 = uuid4()
    with factory() as session:
        session.add(PaperProject(id=project2, title='isolated 2', topic='isolated', citation_style='APA'))
        session.commit()
    module.test_approved_sources_start_segmented_writing_without_extra_review((factory, tmp_path / 'checkpoint.sqlite', project2, []), monkeypatch)
    engine.dispose()


@pytest.mark.parametrize("scenario", ["success", "task_write_failure", "timeout"])
def test_planner_ledger_and_checkpoint_recovery_on_isolated_mysql(monkeypatch, tmp_path, scenario):
    settings = get_database_settings()
    assert settings.host == "127.0.0.1" and settings.port != 3306
    assert (settings.database, settings.user) == ("paper_agent", "paper_agent_app")
    engine = create_engine(settings.url, hide_parameters=True)
    monkeypatch.setenv("LANGGRAPH_SQLITE_PATH", str(tmp_path / "checkpoint.sqlite"))
    monkeypatch.setenv("LANGSMITH_TRACING", "false")
    monkeypatch.setattr("app.infrastructure.llm_diagnostics.DIAGNOSTIC_PATH", tmp_path / "diagnostics.log")
    monkeypatch.setattr("app.api.workflow_events.load_llm_settings", lambda: SimpleNamespace(model="test"))
    calls = []
    class FakeService:
        model = "test"
        planner_read_timeout_seconds = 120
        def invoke(self, messages, *, max_tokens, call_id, read_timeout_seconds):
            calls.append(call_id)
            if scenario == "timeout":
                raise LLMError("timeout", call_id=call_id, request_stage="read", outcome="unknown")
            body = {"tasks": [{"title": f"Task {i}", "description": f"Scope {i}", "expected_output": "Sources"} for i in range(3)]}
            return LLMResult(text=json.dumps(body), usage={"prompt_tokens": 7, "completion_tokens": 11}, call_id=call_id)
    monkeypatch.setattr("app.services.planner.LLMService.from_settings", lambda: FakeService())
    project_id = uuid4()
    with engine.connect() as connection:
        assert connection.execute(text("SELECT DATABASE(), CURRENT_USER(), @@port")).one() == ("paper_agent", "paper_agent_app@127.0.0.1", settings.port)
        connection.rollback()
        outer = connection.begin()
        factory = sessionmaker(bind=connection, autoflush=False, expire_on_commit=False, join_transaction_mode="create_savepoint")
        monkeypatch.setattr(database, "SessionLocal", factory)
        def fail_task_write(_conn, _cursor, statement, _parameters, _context, _executemany):
            if scenario == "task_write_failure" and statement.lstrip().upper().startswith("INSERT INTO WORKFLOW_TASKS"):
                raise RuntimeError("simulated task storage failure")
        event.listen(engine, "before_cursor_execute", fail_task_write)
        try:
            with factory() as session:
                session.add(PaperProject(id=project_id, title="隔离测试", topic="隔离主题", citation_style="APA"))
                session.commit()
            with TestClient(app) as client:
                response = client.post(f"/api/projects/{project_id}/planner-runs")
                assert response.status_code == 202
                run = response.json()
                if scenario != "success":
                    with factory() as session:
                        gen = session.scalar(select(PlannerGeneration).where(PlannerGeneration.planner_run_id == UUID(run["run_id"])))
                        assert gen.status == ("needs_review" if scenario == "timeout" else "invoking")
                        assert gen.usage_complete is False and gen.prompt_tokens is None and gen.completion_tokens is None
                    inspected = inspect_planner(project_id, UUID(run["run_id"]))
                    assert inspected["decision"] == "manual_review" and inspected["usage_complete"] is False
                    from app.services.mock_start import run_mock_attempt
                    run_mock_attempt(project_id, UUID(run["run_id"]))
                    assert len(calls) == 1
                else:
                    detail = client.get(f"/api/projects/{project_id}/workflow-runs/{run['run_id']}/approval")
                    assert detail.status_code == 200 and len(detail.json()["items"]) == 3
                    inspected = inspect_planner(project_id, UUID(run["run_id"]))
                    assert inspected["decision"] == "waiting_for_approval" and inspected["usage_complete"] is True
                    with sqlite_checkpointer(tmp_path / "checkpoint.sqlite", read_only=True) as saver:
                        snapshot = build_graph("planner_real_v1").compile(checkpointer=saver).get_state({"configurable": {"thread_id": run["thread_id"]}})
                    assert snapshot.next == ("task_approval",)
                    with factory() as session:
                        gen = session.scalar(select(PlannerGeneration).where(PlannerGeneration.planner_run_id == UUID(run["run_id"])))
                        assert gen.status == "tasks_persisted" and str(gen.call_id) == calls[0]
                        assert (gen.prompt_tokens, gen.completion_tokens, gen.usage_complete) == (7, 11, True)
                    config = {"metadata": {"paperagent_run_id": run["run_id"]}, "configurable": {"thread_id": run["thread_id"]}}
                    replay = planner_node(dict(snapshot.values), config)
                    assert replay["tasks"] == detail.json()["item_ids"] and len(calls) == 1
                    assert replay["budget_accounting"]["known_usage"] == {"prompt_tokens": 7, "completion_tokens": 11}
                    with pytest.raises(RuntimeError, match="checkpoint inconsistent"):
                        planner_node(dict(snapshot.values, planner_generation_id=str(uuid4())), config)
            assert outer.is_active
        finally:
            outer.rollback()
            event.remove(engine, "before_cursor_execute", fail_task_write)
        assert connection.execute(select(PaperProject.id).where(PaperProject.id == project_id)).scalar_one_or_none() is None
    engine.dispose()
