"""Offline real-Planner graph tests with isolated business and checkpoint SQLite."""
import json
import socket
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest
import pymysql
import httpx
from alembic.migration import MigrationContext
from alembic.operations import Operations
from fastapi.testclient import TestClient
from langchain_core.messages import HumanMessage
from sqlalchemy import create_engine, event, inspect, select, text
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.graph.factory import build_graph
from app.graph.checkpointer import sqlite_checkpointer
from app.infrastructure import database
from app.core.llm_config import LLMSettings
from app.infrastructure.glm_client import GLMClient, LLMError, LLMResult
from app.main import app
from app.models import PaperProject, PlannerGeneration, WorkflowRun
from app.models.base import Base
from app.schemas.planner import parse_planner_output


def payload():
    return {"tasks": [{"title": f"Task {i}", "description": f"Scope {i}", "expected_output": "Sources"} for i in range(3)]}


@pytest.fixture
def isolated(monkeypatch, tmp_path):
    monkeypatch.setattr(pymysql, "connect", lambda *a, **k: (_ for _ in ()).throw(AssertionError("MySQL forbidden")))
    monkeypatch.setattr(socket, "create_connection", lambda *a, **k: (_ for _ in ()).throw(AssertionError("network forbidden")))
    monkeypatch.setenv("LANGGRAPH_SQLITE_PATH", str(tmp_path / "checkpoint.sqlite"))
    monkeypatch.setattr("app.infrastructure.llm_diagnostics.DIAGNOSTIC_PATH", tmp_path / "diagnostics.log")
    monkeypatch.setenv("LANGSMITH_TRACING", "false")
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    @event.listens_for(engine, "connect")
    def fk(connection, _): connection.execute("PRAGMA foreign_keys=ON")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    monkeypatch.setattr(database, "SessionLocal", factory)
    monkeypatch.setattr("app.api.workflow_events.load_llm_settings", lambda: SimpleNamespace(model="test"))
    calls = []
    class Service:
        model = "test"
        planner_read_timeout_seconds = 120
        def invoke(self, messages, *, max_tokens, call_id, read_timeout_seconds):
            calls.append(call_id)
            assert max_tokens == 2048
            assert read_timeout_seconds == 120
            return LLMResult(text=json.dumps(payload()), usage={"prompt_tokens": 10, "completion_tokens": 20}, call_id=call_id)
    monkeypatch.setattr("app.services.planner.LLMService.from_settings", lambda: Service())
    with factory() as session:
        project = PaperProject(id=uuid4(), title="Title", topic="Topic", citation_style="APA")
        session.add(project)
        session.commit()
        project_id = project.id
    yield factory, tmp_path / "checkpoint.sqlite", project_id, calls
    engine.dispose()


def test_approve_real_planner_and_preserve_checkpoint(isolated):
    factory, path, project, calls = isolated
    with TestClient(app) as client:
        start = client.post(f"/api/projects/{project}/planner-runs")
        assert start.status_code == 202
        first = start.json()
        approval_url = f"/api/projects/{project}/workflow-runs/{first['run_id']}/approval"
        detail = client.get(approval_url)
        assert detail.status_code == 200
        data = detail.json()
        assert len(data["items"]) == 3 and len(calls) == 1
        assert data["items"][0]["task_id"] == data["item_ids"][0]
        from app.services.planner_reconciliation import inspect_planner
        inspected = inspect_planner(project, UUID(first["run_id"]))
        assert inspected["decision"] == "waiting_for_approval" and inspected["usage_complete"] is True
        decision = {"decision": "approve", "approved_ids": [data["item_ids"][0]]}
        accepted = client.post(approval_url, json=decision)
        assert accepted.status_code == 202
        second = accepted.json()
        assert second["thread_id"] == first["thread_id"] and second["run_id"] != first["run_id"]
        with sqlite_checkpointer(path, read_only=True) as saver:
            debug_snap = build_graph("planner_real_v1").compile(checkpointer=saver).get_state({"configurable": {"thread_id": first["thread_id"]}})
        assert client.get(f"/api/projects/{project}/workflow-runs/{second['run_id']}").json()["status"] == "paused", (debug_snap.next, debug_snap.interrupts)
        assert client.post(approval_url, json=decision).json() == second
        assert client.post(approval_url, json={"decision": "reject"}).status_code == 409
        with sqlite_checkpointer(path, read_only=True) as saver:
            snap = build_graph("planner_real_v1").compile(checkpointer=saver).get_state({"configurable": {"thread_id": first["thread_id"]}})
            assert snap.next == ("planner_boundary",)
            assert snap.values["approved_task_ids"] == decision["approved_ids"]
            assert snap.values["budget_accounting"]["complete"] is True
        with factory() as session:
            gen = session.get(PlannerGeneration, UUID(snap.values["planner_generation_id"]))
            assert gen.status == "tasks_persisted" and gen.prompt_tokens == 10 and gen.call_id == UUID(calls[0])
            assert session.get(WorkflowRun, UUID(second["run_id"])).graph_version == "planner_real_v1"
        from app.services.planner import planner_node
        replay = planner_node(dict(snap.values), {"metadata": {"paperagent_run_id": first["run_id"]},
            "configurable": {"thread_id": first["thread_id"]}})
        assert replay["tasks"] == data["item_ids"] and replay["budget_accounting"]["known_usage"] == {"prompt_tokens": 10, "completion_tokens": 20}
        assert len(calls) == 1


def test_reject_real_planner(isolated):
    _, _, project, calls = isolated
    with TestClient(app) as client:
        first = client.post(f"/api/projects/{project}/planner-runs").json()
        response = client.post(f"/api/projects/{project}/workflow-runs/{first['run_id']}/approval", json={"decision": "reject"})
        assert response.status_code == 202
        run = response.json()["run_id"]
        assert client.get(f"/api/projects/{project}/workflow-runs/{run}").json()["status"] == "cancelled"
        assert len(calls) == 1


def test_research_approval_from_existing_planner_graph(isolated, monkeypatch):
    from app.infrastructure.crossref import ScholarlyRecord
    from app.models.research_source import ResearchSource, ResearchSourceTask
    factory, path, project, calls = isolated
    queries = []
    def search(query, *, limit):
        queries.append(query)
        return [ScholarlyRecord(doi="10.1234/article", title="Task 0 Task 1 actual paper", authors=("Author",),
            year=2024, journal="Journal", url="https://doi.org/10.1234/article", abstract=None)]
    monkeypatch.setattr("app.services.research.search_crossref", search)
    with TestClient(app) as client:
        first = client.post(f"/api/projects/{project}/planner-runs").json()
        url = f"/api/projects/{project}/workflow-runs/{first['run_id']}/approval"
        detail = client.get(url).json()
        decision = {"decision": "approve", "approved_ids": detail["item_ids"][:2], "continue_research": True}
        second_response = client.post(url, json=decision)
        assert second_response.status_code == 202
        second = second_response.json()
        assert client.post(url, json=decision).json() == second
        assert client.post(url, json={"decision": "approve", "approved_ids": detail["item_ids"][:2]}).status_code == 409
        assert len(queries) == 2 and any(q.startswith("Task 0") for q in queries) and any(q.startswith("Task 1") for q in queries) and len(calls) == 1
        assert client.get(f"/api/projects/{project}/workflow-runs/{second['run_id']}").json()["status"] == "waiting_human"
        source_url = f"/api/projects/{project}/workflow-runs/{second['run_id']}/approval"
        response = client.get(source_url)
        assert response.status_code == 200, response.text
        source_detail = response.json()
        assert source_detail["approval_type"] == "source_approval"
        assert len(source_detail["sources"]) == 1
        assert len(source_detail["sources"][0]["tasks"]) == 2
        with factory() as session:
            assert len(session.query(ResearchSource).all()) == 1
            assert len(session.query(ResearchSourceTask).all()) == 2
        third = client.post(source_url, json={"decision": "approve", "approved_ids": source_detail["item_ids"]}).json()
        with sqlite_checkpointer(path, read_only=True) as saver:
            snap = build_graph("planner_research_v2").compile(checkpointer=saver).get_state({"configurable": {"thread_id": first["thread_id"]}})
            assert client.get(f"/api/projects/{project}/workflow-runs/{third['run_id']}").json()["status"] == "paused", (snap.next, snap.interrupts)
            assert snap.next == ("research_boundary",)
            assert snap.values["approved_source_ids"] == source_detail["item_ids"]


def test_research_source_reject_and_partial_approval(isolated, monkeypatch):
    from app.infrastructure.crossref import ScholarlyRecord
    _, _, project, calls = isolated
    def search(query, *, limit):
        return [ScholarlyRecord(doi=f"10.1234/{index}", title=f"Paper {index}", authors=(), year=None,
            journal=None, url=f"https://doi.org/10.1234/{index}", abstract=None) for index in range(3)]
    monkeypatch.setattr("app.services.research.search_crossref", search)
    with TestClient(app) as client:
        def start():
            first = client.post(f"/api/projects/{project}/planner-runs").json()
            url = f"/api/projects/{project}/workflow-runs/{first['run_id']}/approval"
            ids = client.get(url).json()["item_ids"]
            second = client.post(url, json={"decision": "approve", "approved_ids": ids[:1], "continue_research": True}).json()
            source_url = f"/api/projects/{project}/workflow-runs/{second['run_id']}/approval"
            return source_url, client.get(source_url).json()["item_ids"]
        url, ids = start()
        assert len(ids) == 3
        response = client.post(url, json={"decision": "approve", "approved_ids": ids[:1]})
        assert response.status_code == 202
        assert client.get(f"/api/projects/{project}/workflow-runs/{response.json()['run_id']}").json()["status"] == "paused"
        url, ids = start()
        response = client.post(url, json={"decision": "reject"})
        assert response.status_code == 202
        assert client.get(f"/api/projects/{project}/workflow-runs/{response.json()['run_id']}").json()["status"] == "cancelled"
        assert len(calls) == 2


def test_research_schema_unavailable_does_not_consume_decision(isolated, monkeypatch):
    from sqlalchemy import select
    from app.infrastructure.database import DatabaseAccessError
    from app.models.workflow_approval_decision import WorkflowApprovalDecision
    factory, _, project, _ = isolated
    def unavailable(session):
        raise DatabaseAccessError("Research storage unavailable")
    monkeypatch.setattr("app.services.workflow_resume._require_research_storage", unavailable)
    with TestClient(app) as client:
        first = client.post(f"/api/projects/{project}/planner-runs").json()
        url = f"/api/projects/{project}/workflow-runs/{first['run_id']}/approval"
        ids = client.get(url).json()["item_ids"]
        result = client.post(url, json={"decision": "approve", "approved_ids": ids[:1], "continue_research": True})
        assert result.status_code == 503
        assert client.get(url).status_code == 200
        with factory() as session:
            assert session.scalar(select(WorkflowApprovalDecision).where(
                WorkflowApprovalDecision.previous_run_id == UUID(first['run_id']))) is None


def test_approval_flags_similar_titles_but_preserves_distinct_dois(isolated, monkeypatch):
    from app.infrastructure.crossref import ScholarlyRecord
    _, _, project, _ = isolated
    items = [ScholarlyRecord(doi=f"10.1234/{i}", title=title, authors=(), year=None, journal=None,
                url=f"https://doi.org/10.1234/{i}", abstract=None)
             for i, title in enumerate(["软件测试：方法研究", "软件测试-方法研究"])]
    monkeypatch.setattr("app.services.research.search_crossref", lambda query, *, limit: items)
    with TestClient(app) as client:
        first = client.post(f"/api/projects/{project}/planner-runs").json()
        url = f"/api/projects/{project}/workflow-runs/{first['run_id']}/approval"
        ids = client.get(url).json()["item_ids"]
        next_run = client.post(url, json={"decision": "approve", "approved_ids": ids[:1], "continue_research": True}).json()
        result = client.get(f"/api/projects/{project}/workflow-runs/{next_run['run_id']}/approval")
        assert result.status_code == 200
        sources = result.json()["sources"]
        assert len(sources) == 2 and len({source["doi"] for source in sources}) == 2
        assert all(source["same_title_count"] == 2 for source in sources)
        assert all(source["relevance_hint"] in {"more", "some", "unknown"} for source in sources)


def test_writing_uses_approved_evidence_and_keeps_old_graphs(isolated, monkeypatch):
    from app.infrastructure.crossref import ScholarlyRecord
    from app.models.workflow_event import WorkflowEventRecord
    from app.services.workflow_events import EventService
    factory, path, project, _ = isolated
    calls = []
    transient = []
    original_publish = EventService.publish_transient
    def capture(self, *args, **kwargs):
        transient.append((args[2], kwargs['payload'].chapter_id))
        return original_publish(self, *args, **kwargs)
    monkeypatch.setattr(EventService, 'publish_transient', capture)
    class FakeLLM:
        model = "test"
        planner_read_timeout_seconds = 120
        def invoke(self, messages, *, max_tokens, call_id, read_timeout_seconds):
            system = messages[0].content
            calls.append(call_id)
            if system.startswith("Create 3 to 8"):
                body = payload()
            elif system.startswith("Create 2 to 4"):
                body = {"sections": [{"title": "背景", "objective": "介绍背景"}, {"title": "方法", "objective": "讨论方法"}]}
            else:
                source_id = json.loads(messages[1].content)["sources"][0]["source_id"]
                body = {"paragraphs": [{"text": "这项研究分析软件测试覆盖率。", "citations": [{"source_id": source_id, "evidence": "improved software test coverage"}]}]}
            return LLMResult(text=json.dumps(body), usage={"prompt_tokens": 10, "completion_tokens": 20}, call_id=call_id)
    monkeypatch.setattr("app.services.planner.LLMService.from_settings", lambda: FakeLLM())
    record = ScholarlyRecord(doi="10.1234/article", title="Software testing", authors=("Author",), year=2024,
        journal="Journal", url="https://doi.org/10.1234/article", abstract="The paper improved software test coverage in a controlled study.")
    monkeypatch.setattr("app.services.research.search_crossref", lambda query, *, limit: [record])
    with TestClient(app) as client:
        first = client.post(f"/api/projects/{project}/planner-runs").json()
        task_url = f"/api/projects/{project}/workflow-runs/{first['run_id']}/approval"
        task_ids = client.get(task_url).json()["item_ids"]
        second = client.post(task_url, json={"decision": "approve", "approved_ids": task_ids[:1], "continue_research": True}).json()
        source_url = f"/api/projects/{project}/workflow-runs/{second['run_id']}/approval"
        source_detail = client.get(source_url).json()
        third_response = client.post(source_url, json={"decision": "approve", "approved_ids": source_detail["item_ids"], "continue_writing": True, "segmented_writing": False})
        assert third_response.status_code == 202
        third = third_response.json()
        run = client.get(f"/api/projects/{project}/workflow-runs/{third['run_id']}").json()
        assert run["status"] == "completed", run
        draft = client.get(f"/api/projects/{project}/workflow-runs/{third['run_id']}/draft")
        assert draft.status_code == 200
        chapters = draft.json()["chapters"]
        assert len(chapters) == 2 and all(ch["status"] == "completed" for ch in chapters)
        assert all(ch["paragraphs"][0]["citations"][0]["source_id"] in source_detail["item_ids"] for ch in chapters)
        assert len(calls) == 4  # one Planner, one outline, two chapters
        assert len(transient) == 2 and all(item[0] == 'text_delta' for item in transient)
        with sqlite_checkpointer(path, read_only=True) as saver:
            snap = build_graph("planner_research_writing_v3").compile(checkpointer=saver).get_state({"configurable": {"thread_id": first["thread_id"]}})
            assert not snap.next and snap.values["draft_id"] == draft.json()["draft_id"]
        with factory() as session:
            assert session.get(WorkflowRun, UUID(first["run_id"])).graph_version == "planner_real_v1"
            assert session.get(WorkflowRun, UUID(second["run_id"])).graph_version == "planner_research_v2"
            events = session.scalars(select(WorkflowEventRecord).where(WorkflowEventRecord.run_id == UUID(third['run_id'])).order_by(WorkflowEventRecord.sequence)).all()
            assert events[-1].event_type == 'completed' and any(event.stage == 'writing' and event.event_type == 'progress' for event in events)


def test_writing_timeout_is_not_retried_and_requires_reconciliation(isolated, monkeypatch):
    from app.infrastructure.crossref import ScholarlyRecord
    from app.models.writing import WritingCall, WritingDraft
    factory, _, project, _ = isolated
    calls = []
    class FakeLLM:
        model = "test"
        planner_read_timeout_seconds = 120
        def invoke(self, messages, *, max_tokens, call_id, read_timeout_seconds):
            calls.append(call_id)
            if messages[0].content.startswith("Create 3 to 8"):
                return LLMResult(text=json.dumps(payload()), usage={"prompt_tokens": 1, "completion_tokens": 1}, call_id=call_id)
            raise LLMError("timeout", call_id=call_id, outcome="unknown", request_stage="read")
    monkeypatch.setattr("app.services.planner.LLMService.from_settings", lambda: FakeLLM())
    source = ScholarlyRecord(doi="10.1234/article", title="Software testing", authors=(), year=None,
        journal=None, url="https://doi.org/10.1234/article", abstract="An approved source abstract with sufficient evidence.")
    monkeypatch.setattr("app.services.research.search_crossref", lambda query, *, limit: [source])
    with TestClient(app) as client:
        first = client.post(f"/api/projects/{project}/planner-runs").json()
        tasks = f"/api/projects/{project}/workflow-runs/{first['run_id']}/approval"
        task_ids = client.get(tasks).json()["item_ids"]
        second = client.post(tasks, json={"decision": "approve", "approved_ids": task_ids[:1], "continue_research": True}).json()
        sources = f"/api/projects/{project}/workflow-runs/{second['run_id']}/approval"
        source_ids = client.get(sources).json()["item_ids"]
        decision = {"decision": "approve", "approved_ids": source_ids, "continue_writing": True, "segmented_writing": False}
        third = client.post(sources, json=decision).json()
        assert client.get(f"/api/projects/{project}/workflow-runs/{third['run_id']}").json()["status"] == "failed"
        assert client.post(sources, json=decision).json() == third
        assert client.post(f"/api/projects/{project}/workflow-runs/{third['run_id']}/writing-retry").status_code == 409
        assert len(calls) == 2
        with factory() as session:
            draft = session.scalar(select(WritingDraft).where(WritingDraft.run_id == UUID(third['run_id'])))
            ledger = session.scalar(select(WritingCall).where(WritingCall.draft_id == draft.draft_id))
            assert draft.status == ledger.status == "needs_review"
            assert ledger.usage_complete is False and ledger.prompt_tokens is None


def test_manual_writing_retry_preserves_failed_attempt_and_checkpoint(isolated, monkeypatch):
    from app.infrastructure.crossref import ScholarlyRecord
    from app.models.writing import WritingCall, WritingDraft
    factory, path, project, _ = isolated
    call_ids = []
    class FakeLLM:
        model = 'test'
        planner_read_timeout_seconds = 120
        def invoke(self, messages, *, max_tokens, call_id, read_timeout_seconds):
            call_ids.append(call_id)
            system = messages[0].content
            if system.startswith('Create 3 to 8'):
                body = json.dumps(payload())
            elif system.startswith('Create 2 to 4'):
                outlines = sum(1 for item in call_ids[1:])
                body = '{invalid' if outlines == 1 else '```json\n' + json.dumps({'sections': [
                    {'title': '背景', 'objective': '背景'}, {'title': '方法', 'objective': '方法'}]}) + '\n```'
            else:
                source_id = json.loads(messages[1].content)['sources'][0]['source_id']
                body = json.dumps({'paragraphs': [{'text': '研究结果需要人工核验。', 'citations': [
                    {'source_id': source_id, 'evidence': 'improved software test coverage'}]}]})
            return LLMResult(text=body, usage={'prompt_tokens': 10, 'completion_tokens': 20}, call_id=call_id)
    monkeypatch.setattr('app.services.planner.LLMService.from_settings', lambda: FakeLLM())
    record = ScholarlyRecord(doi='10.1234/article', title='Software testing', authors=('Author',), year=2024,
        journal='Journal', url='https://doi.org/10.1234/article', abstract='The paper improved software test coverage in a controlled study.')
    monkeypatch.setattr('app.services.research.search_crossref', lambda query, *, limit: [record])
    with TestClient(app) as client:
        first = client.post(f'/api/projects/{project}/planner-runs').json()
        tasks = f'/api/projects/{project}/workflow-runs/{first["run_id"]}/approval'
        task_ids = client.get(tasks).json()['item_ids']
        second = client.post(tasks, json={'decision':'approve', 'approved_ids':task_ids[:1], 'continue_research':True}).json()
        sources = f'/api/projects/{project}/workflow-runs/{second["run_id"]}/approval'
        source_ids = client.get(sources).json()['item_ids']
        third = client.post(sources, json={'decision':'approve', 'approved_ids':source_ids, 'continue_writing':True,'segmented_writing':False}).json()
        assert client.get(f'/api/projects/{project}/workflow-runs/{third["run_id"]}').json()['status'] == 'failed'
        with sqlite_checkpointer(path, read_only=True) as saver:
            snap = build_graph('planner_research_writing_v3').compile(checkpointer=saver).get_state({'configurable': {'thread_id': first['thread_id']}})
            assert snap.next == ('writing',)
        url = f'/api/projects/{project}/workflow-runs/{third["run_id"]}/writing-retry'
        retry = client.post(url)
        assert retry.status_code == 202, retry.text
        fourth = retry.json()
        assert fourth['run_id'] != third['run_id'] and fourth['thread_id'] == first['thread_id']
        assert client.get(f'/api/projects/{project}/workflow-runs/{fourth["run_id"]}').json()['status'] == 'completed'
        assert len(client.get(f'/api/projects/{project}/workflow-runs/{fourth["run_id"]}/draft').json()['chapters']) == 2
        assert client.post(url).json()['run_id'] == fourth['run_id']
        assert len(call_ids) == 5 and len(set(call_ids)) == 5
        with factory() as session:
            old = session.get(WorkflowRun, UUID(third['run_id']))
            draft = session.scalar(select(WritingDraft).where(WritingDraft.run_id == old.run_id))
            call = session.scalar(select(WritingCall).where(WritingCall.draft_id == draft.draft_id))
            assert old.status == 'failed' and draft.status == 'failed' and call.error_code == 'outline_json'


@pytest.mark.parametrize('uncertain', [False, True])
def test_segmented_retry_preserves_units_and_old_run(isolated, monkeypatch, uncertain):
    from app.infrastructure.crossref import ScholarlyRecord
    from app.models.writing_segments import WritingManuscript, WritingUnit, WritingUnitCall
    factory, path, project, _ = isolated
    calls = []
    revision_failure = []
    class FakeLLM:
        model = 'test'
        planner_read_timeout_seconds = 120
        def invoke(self, messages, *, max_tokens, call_id, read_timeout_seconds, reasoning_effort=None):
            system = messages[0].content
            calls.append((system[:15], call_id))
            if system.startswith('Create 3 to 8'):
                body = json.dumps(payload())
            elif system.startswith('Create 2 to 4'):
                body = json.dumps({'sections':[{'title':'背景','objective':'背景介绍；证据分析'}, {'title':'方法','objective':'方法介绍；证据分析'}]})
            elif system.startswith('Write a Chinese'):
                raise LLMError('output_truncated', outcome='received', usage={'prompt_tokens':1,'completion_tokens':10}, call_id=call_id)
            else:
                assert reasoning_effort == 'high'
                source_id = json.loads(messages[1].content)['sources'][0]['source_id']
                if system.startswith('只修改') and revision_failure:
                    raise LLMError('timeout', outcome='unknown', call_id=call_id)
                unit_calls = [item for item in calls if item[0].startswith('请撰写论文')]
                if len(unit_calls) == 2:
                    if uncertain:
                        raise LLMError('timeout', outcome='unknown', call_id=call_id)
                    raise LLMError('output_truncated', outcome='received', usage={'prompt_tokens':2,'completion_tokens':10}, call_id=call_id)
                body = json.dumps({'paragraphs':[{'text':'修改后的论述。' if system.startswith('只修改') else '研究结果需要人工核验。','citations':[{'source_id':source_id,'evidence':'software test coverage'}]}]})
            return LLMResult(text=body, usage={'prompt_tokens':1,'completion_tokens':2}, call_id=call_id)
    monkeypatch.setattr('app.services.planner.LLMService.from_settings', lambda: FakeLLM())
    source = ScholarlyRecord(doi='10.1234/test', title='Software testing', authors=('Author',), year=2024,
        journal='Journal', url='https://doi.org/10.1234/test', abstract='The study evaluated software test coverage in a sample.')
    monkeypatch.setattr('app.services.research.search_crossref', lambda query, *, limit: [source])
    with TestClient(app) as client:
        first = client.post(f'/api/projects/{project}/planner-runs').json()
        url = f'/api/projects/{project}/workflow-runs/{first["run_id"]}/approval'
        tasks = client.get(url).json()['item_ids']
        second = client.post(url, json={'decision':'approve','approved_ids':tasks[:1],'continue_research':True}).json()
        url = f'/api/projects/{project}/workflow-runs/{second["run_id"]}/approval'
        sources = client.get(url).json()['item_ids']
        legacy = client.post(url, json={'decision':'approve','approved_ids':sources,'continue_writing':True,'segmented_writing':False}).json()
        assert client.get(f'/api/projects/{project}/workflow-runs/{legacy["run_id"]}').json()['status'] == 'failed'
        url = f'/api/projects/{project}/workflow-runs/{legacy["run_id"]}/segmented-retry'
        response = client.post(url)
        assert response.status_code == 202, response.text
        fourth = response.json()
        assert client.get(f'/api/projects/{project}/workflow-runs/{fourth["run_id"]}').json()['status'] == 'failed'
        with factory() as session:
            manuscript = session.scalar(select(WritingManuscript).where(WritingManuscript.project_id == project))
            units = list(session.scalars(select(WritingUnit).where(WritingUnit.manuscript_id == manuscript.manuscript_id)))
            assert sum(u.status == 'completed' for u in units) == 1
            first_unit = next(u.unit_id for u in units if u.status == 'completed')
        assert client.post(url).json()['run_id'] == fourth['run_id']
        retry_url = f'/api/projects/{project}/workflow-runs/{fourth["run_id"]}/segmented-retry'
        if uncertain:
            assert client.post(retry_url).status_code == 409
            with factory() as session:
                assert len(list(session.scalars(select(WritingUnitCall).where(WritingUnitCall.unit_id == first_unit)))) == 1
            return
        retry = client.post(retry_url)
        assert retry.status_code == 202, retry.text
        fifth = retry.json()
        assert client.get(f'/api/projects/{project}/workflow-runs/{fifth["run_id"]}').json()['status'] == 'completed'
        draft = client.get(f'/api/projects/{project}/workflow-runs/{fifth["run_id"]}/draft')
        assert draft.status_code == 200 and len(draft.json()['chapters']) == 2
        assert all(ch['status'] == 'completed' and ch['paragraphs'] for ch in draft.json()['chapters'])
        chapters = draft.json()['chapters']
        base_url = f'/api/projects/{project}/workflow-runs/{fifth["run_id"]}/draft/chapters/{chapters[0]["chapter_id"]}'
        versions = client.get(base_url + '/versions').json()
        assert len(versions) == 1 and versions[0]['kind'] == 'generated'
        request = {'revision_id':str(uuid4()), 'base_version_id':versions[0]['version_id'], 'mode':'expand', 'instruction':'扩充讨论'}
        revised = client.post(base_url + '/revisions', json=request)
        assert revised.status_code == 202, revised.text
        revision_url = base_url + '/revisions/' + request['revision_id']
        assert client.get(revision_url).json()['status'] == 'completed'
        assert client.post(base_url + '/revisions', json=request).json()['revision_id'] == request['revision_id']
        versions = client.get(base_url + '/versions').json()
        assert [v['kind'] for v in versions] == ['revised', 'generated']
        assert versions[0]['paragraphs'][0]['text'] == '修改后的论述。'
        changed = client.get(f'/api/projects/{project}/workflow-runs/{fifth["run_id"]}/draft').json()['chapters']
        assert changed[1] == chapters[1]
        restore_id = str(uuid4())
        restore_url = base_url + '/versions/' + versions[1]['version_id'] + '/restore'
        restored = client.post(restore_url, json={'request_id':restore_id})
        assert restored.status_code == 200 and restored.json()['kind'] == 'restored'
        assert client.post(restore_url, json={'request_id':restore_id}).json()['version_id'] == restore_id
        assert client.get(base_url + '/versions').json()[0]['paragraphs'] == versions[1]['paragraphs']
        revision_failure.append(True)
        bad_request = {'revision_id':str(uuid4()), 'base_version_id':restore_id, 'mode':'rewrite', 'instruction':'重写'}
        assert client.post(base_url + '/revisions', json=bad_request).status_code == 202
        assert client.get(base_url + '/revisions/' + bad_request['revision_id']).json()['status'] == 'needs_review'
        assert len(client.get(base_url + '/versions').json()) == 3
        with factory() as session:
            assert len(list(session.scalars(select(WritingUnitCall).where(WritingUnitCall.unit_id == first_unit)))) == 1
            assert session.get(WorkflowRun, UUID(legacy['run_id'])).status == 'failed'
        with sqlite_checkpointer(path, read_only=True) as saver:
            snapshot = build_graph('planner_research_writing_v4').compile(checkpointer=saver).get_state({'configurable': {'thread_id': first['thread_id']}})
            assert not snapshot.next


def test_approved_sources_start_segmented_writing_without_extra_review(isolated, monkeypatch):
    from app.infrastructure.crossref import ScholarlyRecord
    factory, path, project, _ = isolated
    calls = []
    class FakeLLM:
        model = 'test'
        planner_read_timeout_seconds = 120
        def invoke(self, messages, *, max_tokens, call_id, read_timeout_seconds, reasoning_effort=None):
            system = messages[0].content
            calls.append((system[:15], call_id))
            if system.startswith('Create 3 to 8'):
                body = json.dumps(payload())
            elif system.startswith('Create 2 to 4'):
                body = json.dumps({'sections':[{'title':'背景','objective':'背景介绍；证据分析'}, {'title':'方法','objective':'方法介绍；证据分析'}]})
            elif system.startswith('你是严格的学术论文审查员'):
                body = json.dumps({'summary': '人工核验', 'issues': []})
            else:
                assert reasoning_effort == 'high'
                source_id = json.loads(messages[1].content)['sources'][0]['source_id']
                body = json.dumps({'paragraphs':[{'text':'研究结果需要人工核验。','citations':[{'source_id':source_id,'evidence':'software test coverage'}]}]})
            return LLMResult(text=body, usage={'prompt_tokens':1,'completion_tokens':2}, call_id=call_id)
    monkeypatch.setattr('app.services.planner.LLMService.from_settings', lambda: FakeLLM())
    source = ScholarlyRecord(doi='10.1234/test', title='Software testing', authors=('Author',), year=2024,
        journal='Journal', url='https://doi.org/10.1234/test', abstract='The study evaluated software test coverage in a sample.')
    monkeypatch.setattr('app.services.research.search_crossref', lambda query, *, limit: [source])
    with TestClient(app) as client:
        first = client.post(f'/api/projects/{project}/planner-runs').json()
        tasks_url = f'/api/projects/{project}/workflow-runs/{first["run_id"]}/approval'
        tasks = client.get(tasks_url).json()['item_ids']
        second = client.post(tasks_url, json={'decision':'approve','approved_ids':tasks[:1],'continue_research':True}).json()
        source_url = f'/api/projects/{project}/workflow-runs/{second["run_id"]}/approval'
        ids = client.get(source_url).json()['item_ids']
        request = {'decision':'approve','approved_ids':ids,'continue_writing':True}
        response = client.post(source_url, json=request)
        assert response.status_code == 202, response.text
        third = response.json()
        assert client.get(f'/api/projects/{project}/workflow-runs/{third["run_id"]}').json()['status'] == 'completed'
        draft = client.get(f'/api/projects/{project}/workflow-runs/{third["run_id"]}/draft').json()
        assert draft['supports_versions'] and len(draft['chapters']) == 2
        assert all(c['status'] == 'completed' and len(c['paragraphs']) == 2 for c in draft['chapters'])
        assert client.post(source_url, json=request).json() == third
        assert len(calls) == len(set(call_id for _,call_id in calls)) == 8
        reports = client.get(f'/api/projects/{project}/workflow-runs/{third["run_id"]}/reviews')
        assert reports.status_code == 200 and reports.json()[0]['status'] == 'completed'
        assert len(reports.json()[0]['units']) == 2
        assert reports.json()[0]['context_stale'] is False
        chapter = draft['chapters'][0]['chapter_id']
        chapter_path = f'/api/projects/{project}/workflow-runs/{third["run_id"]}/draft/chapters/{chapter}'
        original = client.get(chapter_path + '/versions').json()[0]
        restored = client.post(chapter_path + f'/versions/{original["version_id"]}/restore',
                               json={'request_id': str(uuid4())})
        assert restored.status_code == 200
        report_url = f'/api/projects/{project}/workflow-runs/{third["run_id"]}/reviews'
        assert client.get(report_url).json()[0]['context_stale'] is True
        new_id = str(uuid4())
        new_review = client.post(report_url, json={'report_id': new_id, 'chapter_id': chapter})
        assert new_review.status_code == 202
        current = next(item for item in client.get(report_url).json() if item['report_id'] == new_id)
        assert current['status'] == 'completed' and len(current['units']) == 1
        assert current['units'][0]['version_id'] == restored.json()['version_id'] and not current['context_stale']
        before = len(calls)
        assembled_id = str(uuid4())
        assembled = client.post(report_url + '/assemble', json={'report_id': assembled_id})
        assert assembled.status_code == 201, assembled.text
        assert assembled.json()['status'] == 'completed' and len(assembled.json()['units']) == 2
        assert all(item['reused_from'] for item in assembled.json()['units'])
        assert assembled.json()['context_stale'] is False and len(calls) == before
        assert client.post(report_url + '/assemble', json={'report_id': assembled_id}).json() == assembled.json()
        from io import BytesIO
        from docx import Document
        from pypdf import PdfReader
        export_base = f'/api/projects/{project}/workflow-runs/{third["run_id"]}/export/'
        word = client.get(export_base + 'docx')
        pdf = client.get(export_base + 'pdf')
        report_file = client.get(export_base + 'review-docx')
        assert word.status_code == pdf.status_code == report_file.status_code == 200
        word_text = '\n'.join(p.text for p in Document(BytesIO(word.content)).paragraphs)
        pdf_text = '\n'.join(page.extract_text() for page in PdfReader(BytesIO(pdf.content)).pages)
        report_text = '\n'.join(p.text for p in Document(BytesIO(report_file.content)).paragraphs)
        for text in ('背景', '方法', '参考文献', '[1]', '10.1234/test'):
            assert text in word_text and text in pdf_text
        assert '论文质量审查报告' in report_text
        assert '论文质量审查报告' not in word_text
        assert client.get(f'/api/projects/{uuid4()}/workflow-runs/{third["run_id"]}/reviews').status_code == 404
        with sqlite_checkpointer(path, read_only=True) as saver:
            snap = build_graph('planner_research_writing_v4').compile(checkpointer=saver).get_state({'configurable':{'thread_id':first['thread_id']}})
            assert not snap.next and snap.values['draft_id'] == draft['draft_id']


def test_writing_refuses_sources_without_abstract_before_accepting(isolated, monkeypatch):
    from app.infrastructure.crossref import ScholarlyRecord
    from app.models.workflow_approval_decision import WorkflowApprovalDecision
    factory, _, project, _ = isolated
    source = ScholarlyRecord(doi="10.1234/article", title="Software testing", authors=(), year=None,
        journal=None, url="https://doi.org/10.1234/article", abstract=None)
    monkeypatch.setattr("app.services.research.search_crossref", lambda query, *, limit: [source])
    with TestClient(app) as client:
        first = client.post(f"/api/projects/{project}/planner-runs").json()
        tasks = f"/api/projects/{project}/workflow-runs/{first['run_id']}/approval"
        task_ids = client.get(tasks).json()["item_ids"]
        second = client.post(tasks, json={"decision": "approve", "approved_ids": task_ids[:1], "continue_research": True}).json()
        sources = f"/api/projects/{project}/workflow-runs/{second['run_id']}/approval"
        source_ids = client.get(sources).json()["item_ids"]
        result = client.post(sources, json={"decision": "approve", "approved_ids": source_ids, "continue_writing": True, "segmented_writing": False})
        assert result.status_code == 409
        with factory() as session:
            assert session.get(WorkflowApprovalDecision, UUID(second["run_id"])) is None


def test_missing_writing_table_has_specific_error_without_accepting_approval(isolated, monkeypatch):
    from app.infrastructure.crossref import ScholarlyRecord
    from app.models.workflow_approval_decision import WorkflowApprovalDecision
    from sqlalchemy.exc import DBAPIError
    factory, path, project, _ = isolated
    monkeypatch.setattr('app.infrastructure.approval_diagnostics.DIAGNOSTIC_PATH', path.parent / 'approval-diagnostics.log')
    source = ScholarlyRecord(doi='10.1234/article', title='Software testing', authors=(), year=None,
        journal=None, url='https://doi.org/10.1234/article', abstract='Approved evidence long enough to cite.')
    monkeypatch.setattr('app.services.research.search_crossref', lambda query, *, limit: [source])
    with TestClient(app) as client:
        first = client.post(f'/api/projects/{project}/planner-runs').json()
        task_url = f"/api/projects/{project}/workflow-runs/{first['run_id']}/approval"
        task_ids = client.get(task_url).json()['item_ids']
        second = client.post(task_url, json={'decision': 'approve', 'approved_ids': task_ids[:1], 'continue_research': True}).json()
        source_url = f"/api/projects/{project}/workflow-runs/{second['run_id']}/approval"
        source_ids = client.get(source_url).json()['item_ids']
        engine = factory.kw['bind']
        def missing_table(_conn, _cursor, statement, parameters, _context, _executemany):
            if 'FROM writing_drafts' in statement and 'LIMIT' in statement:
                raise DBAPIError(statement, parameters, Exception(1146, 'simulated missing table'))
        event.listen(engine, 'before_cursor_execute', missing_table)
        try:
            response = client.post(source_url, json={'decision':'approve', 'approved_ids':source_ids, 'continue_writing':True,'segmented_writing':False})
            assert response.status_code == 503
            assert response.json()['code'] == 'writing_storage_missing'
            line = json.loads((path.parent / 'approval-diagnostics.log').read_text().strip())
            assert line['code'] == 'writing_storage_missing' and line['stage'] == 'precommit_schema_check'
            assert set(line) == {'timestamp', 'project_id', 'run_id', 'code', 'stage', 'http_status'}
        finally:
            event.remove(engine, 'before_cursor_execute', missing_table)
        with factory() as session:
            assert session.get(WorkflowApprovalDecision, UUID(second['run_id'])) is None


def test_strict_model_output_and_duplicate_rules():
    value = payload()
    value["tasks"][1]["description"] = value["tasks"][0]["description"]
    assert len(parse_planner_output(json.dumps(value)).tasks) == 3
    value["tasks"][1]["title"] = value["tasks"][0]["title"]
    with pytest.raises(ValueError): parse_planner_output(json.dumps(value))
    with pytest.raises(ValueError): parse_planner_output('{"tasks": [], "tasks": []}')
    with pytest.raises(ValueError): parse_planner_output(json.dumps({"tasks": [dict(t, task_id="fake") for t in payload()["tasks"]]}))


def test_preallocated_call_id_and_no_retry():
    calls = []
    def respond(request):
        calls.append(request.url.path)
        return httpx.Response(200, json={"choices": [{"message": {"role": "assistant", "content": "ok"}, "finish_reason": "stop"}], "usage": {"prompt_tokens": 1, "completion_tokens": 2}})
    client = GLMClient(LLMSettings(api_key="test", model="test"), transport=httpx.MockTransport(respond))
    call_id = str(uuid4())
    assert client.invoke([HumanMessage(content="test")], call_id=call_id).call_id == call_id
    with pytest.raises(LLMError) as error:
        client.invoke([], call_id=call_id)
    assert error.value.call_id == call_id and len(calls) == 1


def test_planner_migration_isolated_sqlite(tmp_path):
    from importlib.util import module_from_spec, spec_from_file_location
    from pathlib import Path
    path = Path(__file__).resolve().parents[2] / "alembic" / "versions" / "d52a7c81f103_create_planner_tables.py"
    spec = spec_from_file_location("planner_migration", path)
    assert spec and spec.loader
    migration = module_from_spec(spec)
    spec.loader.exec_module(migration)
    engine = create_engine(f"sqlite:///{(tmp_path / 'migration.sqlite').as_posix()}")
    with engine.begin() as connection:
        connection.execute(text("CREATE TABLE paper_projects (id CHAR(32) PRIMARY KEY)"))
        connection.execute(text("CREATE TABLE workflow_runs (run_id CHAR(32) PRIMARY KEY)"))
        with Operations.context(MigrationContext.configure(connection)):
            migration.upgrade()
        tables = set(inspect(connection).get_table_names())
        assert {"planner_generations", "workflow_tasks"}.issubset(tables)
        assert "graph_version" in {c["name"] for c in inspect(connection).get_columns("workflow_runs")}
    engine.dispose()


def test_planner_migration_renders_mysql_without_connecting():
    from importlib.util import module_from_spec, spec_from_file_location
    from io import StringIO
    from pathlib import Path
    path = Path(__file__).resolve().parents[2] / "alembic" / "versions" / "d52a7c81f103_create_planner_tables.py"
    spec = spec_from_file_location("planner_migration_mysql", path)
    assert spec and spec.loader
    migration = module_from_spec(spec)
    spec.loader.exec_module(migration)
    output = StringIO()
    context = MigrationContext.configure(dialect_name="mysql", opts={"as_sql": True, "output_buffer": output})
    with Operations.context(context): migration.upgrade()
    sql = output.getvalue()
    assert "CREATE TABLE planner_generations" in sql and "CREATE TABLE workflow_tasks" in sql
    assert "ALTER TABLE workflow_runs ADD COLUMN graph_version" in sql


def test_queued_restart_uses_original_snapshot(isolated, monkeypatch):
    factory, _, project, calls = isolated
    monkeypatch.setattr("app.api.workflow_events.run_mock_attempt", lambda *_: None)
    with TestClient(app) as client:
        first = client.post(f"/api/projects/{project}/planner-runs").json()
        with factory() as session:
            obj = session.get(PaperProject, project)
            obj.topic = "Edited after enqueue"
            session.commit()
        from app.services.workflow_recovery import RecoveryScheduler
        scheduler = RecoveryScheduler()
        scheduler.scan()
        scheduler.close()
        response = client.get(f"/api/projects/{project}/workflow-runs/{first['run_id']}/approval")
        assert response.status_code == 200 and len(calls) == 1
        with factory() as session:
            from sqlalchemy import select
            gen = session.scalar(select(PlannerGeneration).where(PlannerGeneration.planner_run_id == UUID(first["run_id"])))
            assert gen.input_snapshot["topic"] == "Topic" and gen.status == "tasks_persisted"


def test_uncertain_invocation_is_not_replayed(isolated, monkeypatch):
    factory, _, project, calls = isolated
    monkeypatch.setattr("app.api.workflow_events.run_mock_attempt", lambda *_: None)
    with TestClient(app) as client:
        first = client.post(f"/api/projects/{project}/planner-runs").json()
        with factory() as session:
            from sqlalchemy import select
            gen = session.scalar(select(PlannerGeneration).where(PlannerGeneration.planner_run_id == UUID(first["run_id"])))
            gen.status = "invoking"
            session.commit()
        from app.services.mock_start import run_mock_attempt
        run_mock_attempt(project, UUID(first["run_id"]))
        assert calls == []
        with factory() as session:
            assert session.get(WorkflowRun, UUID(first["run_id"])).status == "running"


def test_provider_timeout_records_safe_failure_and_never_retries(isolated, monkeypatch):
    factory, _, project, _ = isolated
    called = []
    class TimeoutService:
        model = "test"
        planner_read_timeout_seconds = 120
        def invoke(self, messages, *, max_tokens, call_id, read_timeout_seconds):
            called.append(call_id)
            raise LLMError("timeout", call_id=call_id, outcome="unknown")
    monkeypatch.setattr("app.services.planner.LLMService.from_settings", lambda: TimeoutService())
    with TestClient(app) as client:
        first = client.post(f"/api/projects/{project}/planner-runs").json()
        result = client.get(f"/api/projects/{project}/workflow-runs/{first['run_id']}").json()
        assert result["status"] == "failed" and result["retryable"] is False
        from app.services.planner_reconciliation import inspect_planner
        inspected = inspect_planner(project, UUID(first["run_id"]))
        assert inspected["decision"] == "manual_review" and inspected["prompt_tokens"] is None and inspected["completion_tokens"] is None
        with factory() as session:
            from sqlalchemy import select
            gen = session.scalar(select(PlannerGeneration).where(PlannerGeneration.planner_run_id == UUID(first["run_id"])))
            assert gen.status == "needs_review" and gen.usage_complete is False
        from app.services.mock_start import run_mock_attempt
        run_mock_attempt(project, UUID(first["run_id"]))
        assert len(called) == 1
        from app.services.workflow_recovery import RecoveryScheduler
        scheduler = RecoveryScheduler()
        dispatched = []
        monkeypatch.setattr(scheduler, "submit_initial", lambda *args: dispatched.append(args))
        scheduler.scan()
        scheduler.close()
        assert dispatched == []


def test_task_write_failure_does_not_reissue_call(isolated):
    factory, _, project, calls = isolated
    engine = factory.kw["bind"]
    def fail_tasks(_connection, _cursor, statement, _parameters, _context, _executemany):
        if statement.lstrip().upper().startswith("INSERT INTO WORKFLOW_TASKS"):
            raise RuntimeError("simulated storage failure")
    event.listen(engine, "before_cursor_execute", fail_tasks)
    try:
        with TestClient(app) as client:
            first = client.post(f"/api/projects/{project}/planner-runs").json()
            with factory() as session:
                from sqlalchemy import select
                gen = session.scalar(select(PlannerGeneration).where(PlannerGeneration.planner_run_id == UUID(first["run_id"])))
                assert gen.status == "invoking" and gen.usage_complete is False
            from app.services.mock_start import run_mock_attempt
            run_mock_attempt(project, UUID(first["run_id"]))
            assert len(calls) == 1
    finally:
        event.remove(engine, "before_cursor_execute", fail_tasks)


def test_saved_batch_reused_after_checkpoint_window(isolated, monkeypatch):
    factory, _, project, calls = isolated
    import app.services.planner as planner
    original = planner.planner_node
    def fail_after_commit(state, config):
        original(state, config)
        raise RuntimeError("simulated checkpoint failure")
    monkeypatch.setattr(planner, "planner_node", fail_after_commit)
    with TestClient(app) as client:
        first = client.post(f"/api/projects/{project}/planner-runs").json()
        with factory() as session:
            from sqlalchemy import select
            gen = session.scalar(select(PlannerGeneration).where(PlannerGeneration.planner_run_id == UUID(first["run_id"])))
            assert gen.status == "tasks_persisted" and gen.task_count == 3
        state = {"project_id": str(project), "current_stage": "planning"}
        config = {"metadata": {"paperagent_run_id": first["run_id"]}, "configurable": {"thread_id": first["thread_id"]}}
        restored = original(state, config)
        assert len(restored["tasks"]) == 3 and len(calls) == 1
        with pytest.raises(RuntimeError, match="checkpoint inconsistent"):
            original(dict(state, tasks=[str(uuid4())]), config)


def test_real_planner_queued_cross_process_restart(tmp_path):
    import os
    import subprocess
    import sys
    from sqlalchemy import select
    from app.schemas.workflow_event import ProgressPayload
    from app.services.workflow_events import EventService

    db_path = tmp_path / "business.sqlite"
    checkpoint = tmp_path / "checkpoints.sqlite"
    engine = create_engine(f"sqlite:///{db_path.as_posix()}")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False, autoflush=False)
    project_id, run_id, thread_id, generation_id = uuid4(), uuid4(), uuid4(), uuid4()
    with factory() as session:
        session.add(PaperProject(id=project_id, title="Title", topic="Topic", citation_style="APA"))
        session.flush()
        session.add(WorkflowRun(run_id=run_id, project_id=project_id, thread_id=thread_id,
            attempt_no=1, status="queued", current_stage="planning", graph_version="planner_real_v1"))
        session.flush()
        session.add(PlannerGeneration(generation_id=generation_id, project_id=project_id, thread_id=thread_id,
            planner_run_id=run_id, plan_revision=1, input_snapshot={"title": "Title", "topic": "Topic",
            "paper_type": "course_paper", "citation_style": "APA", "requirements": None},
            model="test", max_tokens=2048, call_id=uuid4(), status="prepared", usage_complete=False))
        events = EventService(session)
        item = events.prepare(project_id, run_id, "progress", stage="planning", agent_name=None,
            status="queued", payload=ProgressPayload(stage="planning", agent_name=None, status="queued", message="Workflow queued"))
        events.commit_and_notify(item)
    engine.dispose()
    child = r'''
import json, sys
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from app.infrastructure import database
from app.infrastructure.glm_client import LLMResult
from app.services.planner import LLMService
from app.services.workflow_recovery import RecoveryScheduler
import app.infrastructure.llm_diagnostics as diagnostics
from pathlib import Path
diagnostics.DIAGNOSTIC_PATH = Path(sys.argv[1]).parent / 'child-diagnostics.log'
engine = create_engine("sqlite:///" + sys.argv[1], connect_args={"check_same_thread": False})
database.SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
class Fake:
    model = "test"
    planner_read_timeout_seconds = 120
    def invoke(self, messages, *, max_tokens, call_id, read_timeout_seconds):
        data = {"tasks": [{"title": f"Task {i}", "description": f"Scope {i}", "expected_output": "Sources"} for i in range(3)]}
        return LLMResult(text=json.dumps(data), usage={"prompt_tokens": 10, "completion_tokens": 20}, call_id=call_id)
LLMService.from_settings = lambda: Fake()
scheduler = RecoveryScheduler()
scheduler.scan()
scheduler.close()
engine.dispose()
'''
    env = dict(os.environ, LANGGRAPH_SQLITE_PATH=str(checkpoint), LANGSMITH_TRACING="false",
               PAPERAGENT_LLM_LIVE_TEST="0", RUN_MYSQL_INTEGRATION="0")
    result = subprocess.run([sys.executable, "-c", child, db_path.as_posix()], env=env,
                            capture_output=True, text=True, timeout=30)
    assert result.returncode == 0
    engine = create_engine(f"sqlite:///{db_path.as_posix()}")
    with sessionmaker(bind=engine)() as session:
        run = session.get(WorkflowRun, run_id)
        gen = session.scalar(select(PlannerGeneration).where(PlannerGeneration.planner_run_id == run_id))
        assert run.status == "waiting_human" and run.thread_id == thread_id
        assert gen.generation_id == generation_id and gen.status == "tasks_persisted"
    with sqlite_checkpointer(checkpoint, read_only=True) as saver:
        snap = build_graph("planner_real_v1").compile(checkpointer=saver).get_state({"configurable": {"thread_id": str(thread_id)}})
        assert snap.next == ("task_approval",) and snap.values["planner_generation_id"] == str(generation_id)
    engine.dispose()
