"""Event validation and isolated API tests."""
from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError
from sqlalchemy import create_engine, event, func, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.graph.approval_graph import build_approval_graph
from app.graph.checkpointer import sqlite_checkpointer
from app.infrastructure import database
from app.main import app
from app.api.workflow_events import encode_sse
from app.models import PaperProject, WorkflowEventRecord
from app.models.base import Base
from app.schemas.workflow_event import CompletedPayload, ErrorPayload, HeartbeatPayload, ProgressPayload, TextDeltaPayload, event_adapter
from app.services.project_scope import ResourceNotFound
from app.services.workflow_events import EventBroker, EventService
from app.services.workflow_run import WorkflowRunService
from app.services import mock_start


@pytest.fixture
def env(monkeypatch, tmp_path):
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    @event.listens_for(engine, "connect")
    def fk(conn, _record): conn.execute("PRAGMA foreign_keys=ON")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    monkeypatch.setattr(database, "SessionLocal", factory)
    monkeypatch.setenv("LANGGRAPH_SQLITE_PATH", str(tmp_path / "graph.sqlite"))
    monkeypatch.setenv("LANGSMITH_TRACING", "false")
    monkeypatch.setenv("LANGCHAIN_TRACING_V2", "false")
    yield factory, tmp_path / "graph.sqlite"
    engine.dispose()


def make_project(session):
    p = PaperProject(title="test", topic="test", citation_style="APA")
    session.add(p)
    session.flush()
    return p


def test_six_event_schemas_and_extra_fields():
    now = datetime.now(UTC)
    project, run, target = uuid4(), uuid4(), uuid4()
    base = {"event_id": uuid4(), "run_id": run, "project_id": project, "stage": "planning",
            "agent_name": None, "status": "queued", "timestamp": now}
    payloads = {
        "progress": {"stage": "planning", "agent_name": None, "status": "queued", "message": "Workflow queued"},
        "approval_required": {"checkpoint_id": target, "approval_type": "task_approval", "payload_ref": run},
        "text_delta": {"chapter_id": target, "delta": "text", "delta_sequence": 1},
        "completed": {"result_ref": target, "finished_at": now},
        "error": {"error_code": "timeout", "safe_message": "Workflow execution failed", "retryable": True},
        "heartbeat": {"timestamp": now},
    }
    for kind, payload in payloads.items():
        seq = None if kind in {"heartbeat", "text_delta"} else 1
        status = {"approval_required": "waiting_human", "completed": "completed", "error": "failed"}.get(kind, "queued")
        event_base = {**base, "status": status}
        event = event_adapter.validate_python({**event_base, "sequence": seq, "event_type": kind, "payload": payload})
        assert event.event_type == kind
        with pytest.raises(ValidationError):
            event_adapter.validate_python({**event_base, "sequence": seq, "event_type": kind, "payload": {**payload, "api_key": "secret"}})
        with pytest.raises(ValidationError):
            event_adapter.validate_python({**event_base, "sequence": seq, "event_type": kind, "payload": {}})
    with pytest.raises(ValidationError):
        event_adapter.validate_python({**base, "event_type": "bad", "payload": {}})
    with pytest.raises(ValidationError):
        TextDeltaPayload(chapter_id=target, delta="password=secret", delta_sequence=1)


def test_events_scoped_sequence_commit_transient_and_overflow(env):
    factory, _ = env
    local_broker = EventBroker(capacity=1)
    with factory() as s:
        p, other = make_project(s), make_project(s)
        run = WorkflowRunService(s).create(p.id)
        run.current_stage = "planning"
        s.commit()
        service = EventService(s, local_broker)
        sub = local_broker.subscribe(p.id, run.run_id)
        payload = ProgressPayload(stage="planning", agent_name=None, status="queued", message="Workflow queued")
        first = service.prepare(p.id, run.run_id, "progress", stage="planning", agent_name=None, status="queued", payload=payload)
        assert sub.queue.empty()
        service.commit_and_notify(first)
        assert sub.queue.qsize() == 1 and first.sequence == 1
        second = service.prepare(p.id, run.run_id, "progress", stage="planning", agent_name=None, status="queued", payload=payload)
        service.commit_and_notify(second)
        assert second.sequence == 2 and sub.overflow.is_set() and sub.queue.qsize() == 1
        assert [e.sequence for e in service.since(p.id, run.run_id, 1)] == [2]
        with pytest.raises(ResourceNotFound):
            service.prepare(other.id, run.run_id, "progress", stage="planning", agent_name=None, status="queued", payload=payload)
        count = s.scalar(select(func.count()).select_from(WorkflowEventRecord))
        delta = service.publish_transient(p.id, run.run_id, "text_delta", stage="planning", agent_name="Writing",
                                          status="queued", payload=TextDeltaPayload(chapter_id=uuid4(), delta="text", delta_sequence=1))
        now = datetime.now(UTC)
        heartbeat = service.publish_transient(p.id, run.run_id, "heartbeat", stage="planning", agent_name=None,
                                              status="queued", payload=HeartbeatPayload(timestamp=now))
        assert delta.sequence is None and heartbeat.sequence is None
        assert s.scalar(select(func.count()).select_from(WorkflowEventRecord)) == count
        local_broker.unsubscribe(p.id, run.run_id, sub)
        assert not local_broker._subscribers


def test_live_transient_delivery_has_no_replay_cursor(env):
    factory, _ = env
    local_broker = EventBroker()
    with factory() as s:
        p = make_project(s)
        run = WorkflowRunService(s).create(p.id)
        run.current_stage = "writing"
        s.commit()
        sub = local_broker.subscribe(p.id, run.run_id)
        service = EventService(s, local_broker)
        sent = service.publish_transient(p.id, run.run_id, "text_delta", stage="writing", agent_name="Writing",
            status="queued", payload=TextDeltaPayload(chapter_id=uuid4(), delta="small sample", delta_sequence=1))
        assert sub.queue.get_nowait() == sent
        assert not encode_sse(sent).startswith("id:")
        assert service.since(p.id, run.run_id, 0) == []
        local_broker.unsubscribe(p.id, run.run_id, sub)


def test_transient_cannot_publish_uncommitted_run(tmp_path, monkeypatch):
    engine = create_engine(f"sqlite:///{(tmp_path / 'separate.sqlite').as_posix()}")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False, autoflush=False)
    monkeypatch.setattr(database, "SessionLocal", factory)
    try:
        with factory() as session:
            p = make_project(session)
            session.commit()
            run = WorkflowRunService(session).create(p.id)
            run.current_stage = "writing"
            session.flush()
            with pytest.raises(ResourceNotFound):
                EventService(session).publish_transient(p.id, run.run_id, "text_delta", stage="writing",
                    agent_name="Writing", status="queued",
                    payload=TextDeltaPayload(chapter_id=uuid4(), delta="sample", delta_sequence=1))
            session.rollback()
    finally:
        engine.dispose()


def test_commit_failure_sends_no_notification(env, monkeypatch):
    factory, _ = env
    local_broker = EventBroker()
    with factory() as s:
        p = make_project(s)
        run = WorkflowRunService(s).create(p.id)
        run.current_stage = "planning"
        s.commit()
        sub = local_broker.subscribe(p.id, run.run_id)
        service = EventService(s, local_broker)
        payload = ProgressPayload(stage="planning", agent_name=None, status="queued", message="Workflow queued")
        evt = service.prepare(p.id, run.run_id, "progress", stage="planning", agent_name=None, status="queued", payload=payload)
        def fail():
            raise RuntimeError("simulated failure")
        monkeypatch.setattr(s, "commit", fail)
        with pytest.raises(RuntimeError):
            service.commit_and_notify(evt)
        s.rollback()
        assert sub.queue.empty()
        assert s.scalar(select(func.count()).select_from(WorkflowEventRecord)) == 0
        local_broker.unsubscribe(p.id, run.run_id, sub)


def test_broadcast_failure_keeps_committed_event(env, caplog):
    factory, _ = env
    class BrokenBroker(EventBroker):
        def notify(self, event):
            raise RuntimeError("internal secret")
    with factory() as s:
        p = make_project(s)
        run = WorkflowRunService(s).create(p.id)
        run.current_stage = "planning"
        s.commit()
        service = EventService(s, BrokenBroker())
        evt = service.prepare(p.id, run.run_id, "progress", stage="planning", agent_name=None, status="queued",
            payload=ProgressPayload(stage="planning", agent_name=None, status="queued", message="Workflow queued"))
        service.commit_and_notify(evt)
        assert [e.sequence for e in service.since(p.id, run.run_id, 0)] == [1]
        assert "notification failed" in caplog.text and "internal secret" not in caplog.text


@pytest.mark.parametrize("kind", ["completed", "error"])
def test_terminal_event_is_durable(env, kind):
    factory, _ = env
    with factory() as s:
        p = make_project(s)
        runs = WorkflowRunService(s)
        run = runs.create(p.id)
        runs.transition(p.id, run.run_id, "running", current_stage="review")
        if kind == "completed":
            runs.transition(p.id, run.run_id, "completed")
            payload = CompletedPayload(result_ref=uuid4(), finished_at=run.finished_at)
            status = "completed"
        else:
            runs.transition(p.id, run.run_id, "failed", error_code="timeout", retryable=True)
            payload = ErrorPayload(error_code="timeout", safe_message="Workflow execution failed", retryable=True)
            status = "failed"
        service = EventService(s, EventBroker())
        evt = service.prepare(p.id, run.run_id, kind, stage="review", agent_name="Review", status=status, payload=payload)
        service.commit_and_notify(evt)
        assert service.since(p.id, run.run_id, 0)[0].event_type == kind


def test_mock_start_persists_real_interrupt_and_independent_runs(env):
    factory, checkpoint = env
    with factory() as s:
        p = make_project(s); project_id = p.id; s.commit()
    with TestClient(app) as client:
        first = client.post(f"/api/projects/{project_id}/runs")
        second = client.post(f"/api/projects/{project_id}/runs")
        assert first.status_code == second.status_code == 202
        a, b = first.json(), second.json()
        assert a["status"] == "queued" and a["thread_id"] != b["thread_id"] and a["run_id"] != b["run_id"]
        status = client.get(f"/api/projects/{project_id}/workflow-runs/{a['run_id']}").json()
        assert status["status"] == "waiting_human"
        assert client.get(f"/api/projects/{uuid4()}/workflow-runs/{a['run_id']}/events").status_code == 404
        assert client.get(f"/api/projects/{project_id}/workflow-runs/{a['run_id']}/events", headers={"Last-Event-ID": "999"}).status_code == 422
    with factory() as s:
        events = EventService(s).since(project_id, UUID(a["run_id"]), 0)
        assert [event.event_type for event in events] == ["progress", "progress", "approval_required"]
        assert [event.sequence for event in events] == [1, 2, 3]
        assert events[-1].payload.approval_type == "task_approval"
    with sqlite_checkpointer(checkpoint) as saver:
        graph = build_approval_graph().compile(checkpointer=saver)
        snap = graph.get_state({"configurable": {"thread_id": a["thread_id"]}})
        assert snap.next == ("task_approval",) and snap.values["tasks"]
        assert "approved_task_ids" not in snap.values


def test_failure_before_claim_keeps_queued_for_reconciliation(env, monkeypatch):
    factory, _ = env
    with factory() as s:
        p = make_project(s); project_id = p.id; s.commit()
    def fail():
        raise RuntimeError("sensitive internal detail")
    monkeypatch.setattr(mock_start, "checkpoint_path", fail)
    with TestClient(app) as client:
        response = client.post(f"/api/projects/{project_id}/runs")
        data = response.json()
        detail = client.get(f"/api/projects/{project_id}/workflow-runs/{data['run_id']}")
        assert detail.json()["status"] == "queued"
        assert detail.json()["error_code"] is None
        assert "sensitive" not in detail.text
    with factory() as s:
        rows = EventService(s).since(project_id, UUID(data["run_id"]), 0)
        assert [e.event_type for e in rows] == ["progress"]


@pytest.mark.parametrize("failure_point", ["event", "status"])
def test_checkpoint_event_failure_does_not_rerun(env, monkeypatch, caplog, failure_point):
    factory, checkpoint = env
    with factory() as s:
        p = make_project(s); project_id = p.id; s.commit()
    original = EventService.prepare
    def fail_approval(self, project_id, run_id, event_type, **kwargs):
        if event_type == "approval_required":
            raise RuntimeError("sensitive internal detail")
        return original(self, project_id, run_id, event_type, **kwargs)
    if failure_point == "event":
        monkeypatch.setattr(EventService, "prepare", fail_approval)
    else:
        transition = WorkflowRunService.transition
        def fail_status(self, project_id, run_id, status, **kwargs):
            if status == "waiting_human":
                raise RuntimeError("sensitive internal detail")
            return transition(self, project_id, run_id, status, **kwargs)
        monkeypatch.setattr(WorkflowRunService, "transition", fail_status)
    with TestClient(app) as client:
        response = client.post(f"/api/projects/{project_id}/runs")
        assert response.status_code == 202
        data = response.json()
        status = client.get(f"/api/projects/{project_id}/workflow-runs/{data['run_id']}").json()
        assert status["status"] == "running"
    with factory() as s:
        events = EventService(s).since(project_id, UUID(data["run_id"]), 0)
        assert [event.event_type for event in events] == ["progress", "progress"]
    assert "reconciliation required" in caplog.text and "sensitive internal detail" not in caplog.text
    with sqlite_checkpointer(checkpoint) as saver:
        graph = build_approval_graph().compile(checkpointer=saver)
        assert graph.get_state({"configurable": {"thread_id": data["thread_id"]}}).next == ("task_approval",)


def create_pending_initial(factory):
    with factory() as session:
        project = make_project(session)
        run = WorkflowRunService(session).create(project.id)
        run.current_stage = "planning"
        event = EventService(session).prepare(project.id, run.run_id, "progress", stage="planning", agent_name=None,
            status="queued", payload=ProgressPayload(stage="planning", agent_name=None, status="queued", message="Workflow queued"))
        EventService(session).commit_and_notify(event)
        return project.id, run.run_id, run.thread_id


def test_initial_queued_recovers_on_startup_and_duplicate_skips(env):
    from app.models.workflow_run import WorkflowRun
    from app.services.mock_start import run_mock_attempt
    factory, path = env
    project_id, run_id, thread_id = create_pending_initial(factory)
    with TestClient(app): pass
    with factory() as session:
        assert session.get(WorkflowRun, run_id).status == "waiting_human"
        assert [e.sequence for e in EventService(session).since(project_id, run_id, 0)] == [1, 2, 3]
    with sqlite_checkpointer(path, read_only=True) as saver:
        snap = build_approval_graph().compile(checkpointer=saver).get_state({"configurable": {"thread_id": str(thread_id)}})
        assert snap.next == ("task_approval",) and snap.metadata["paperagent_run_id"] == str(run_id)
    run_mock_attempt(project_id, run_id)
    with factory() as session:
        assert len(EventService(session).since(project_id, run_id, 0)) == 3


def test_initial_existing_checkpoint_is_not_replayed(env):
    from app.models.workflow_run import WorkflowRun
    from app.services.mock_start import run_mock_attempt
    factory, path = env
    project, run, thread = create_pending_initial(factory)
    with sqlite_checkpointer(path) as saver:
        graph = build_approval_graph().compile(checkpointer=saver)
        graph.invoke({"project_id": str(project), "current_stage": "planning"}, {"configurable": {"thread_id": str(thread)}})
        before = graph.get_state({"configurable": {"thread_id": str(thread)}})
    run_mock_attempt(project, run)
    with sqlite_checkpointer(path, read_only=True) as saver:
        after = build_approval_graph().compile(checkpointer=saver).get_state({"configurable": {"thread_id": str(thread)}})
    assert after.config == before.config
    with factory() as session:
        assert session.get(WorkflowRun, run).status == "queued"
        assert len(EventService(session).since(project, run, 0)) == 1


def test_initial_running_is_not_replayed_on_scan(env, caplog):
    from app.models.workflow_run import WorkflowRun
    factory, path = env
    project, run, thread = create_pending_initial(factory)
    with factory() as session:
        session.get(WorkflowRun, run).status = "running"; session.commit()
    with TestClient(app): pass
    with factory() as session:
        assert session.get(WorkflowRun, run).status == "running"
    assert not path.exists() and "execution position unknown" in caplog.text


def test_initial_deleted_missing_and_nonlatest_are_not_executed(env):
    from app.models.workflow_run import WorkflowRun
    from app.services.mock_start import run_mock_attempt
    factory, path = env
    project, run, thread = create_pending_initial(factory)
    run_mock_attempt(uuid4(), uuid4())
    with factory() as session:
        project_row = session.get(PaperProject, project)
        project_row.lifecycle_status = "deleted"; session.commit()
    run_mock_attempt(project, run)
    with factory() as session:
        project_row = session.get(PaperProject, project)
        project_row.lifecycle_status = "active"
        session.add(WorkflowRun(run_id=uuid4(), project_id=project, thread_id=thread, attempt_no=2, status="queued"))
        session.commit()
    run_mock_attempt(project, run)
    with factory() as session:
        assert session.get(WorkflowRun, run).status == "queued"
    assert not path.exists()


def test_initial_commit_failure_does_not_execute(env, monkeypatch):
    from sqlalchemy.exc import OperationalError
    from app.models.workflow_run import WorkflowRun
    factory, path = env
    with factory() as session:
        p = make_project(session); pid = p.id; session.commit()
    original = EventService.commit_and_notify
    def fail(self, event):
        if event.status == "queued": raise OperationalError("secret", {}, Exception(2003, "secret"))
        return original(self, event)
    monkeypatch.setattr(EventService, "commit_and_notify", fail)
    with TestClient(app) as client:
        response = client.post(f"/api/projects/{pid}/runs")
        assert response.status_code == 503 and "secret" not in response.text
    with factory() as session:
        assert session.query(WorkflowRun).filter_by(project_id=pid).count() == 0
    assert not path.exists()


def test_initial_scheduling_failure_preserves_queued(env, monkeypatch):
    from fastapi import BackgroundTasks
    from app.models.workflow_run import WorkflowRun
    factory, path = env
    with factory() as session:
        p = make_project(session); pid = p.id; session.commit()
    def fail(*args, **kwargs): raise RuntimeError("secret")
    with TestClient(app) as client:
        monkeypatch.setattr(BackgroundTasks, "add_task", fail)
        response = client.post(f"/api/projects/{pid}/runs")
        assert response.status_code == 503 and "secret" not in response.text
        with factory() as session:
            run = session.query(WorkflowRun).filter_by(project_id=pid).one()
            assert run.status == "queued"
    assert not path.exists()


def test_initial_scheduler_deduplicates_pending_task(monkeypatch):
    import threading
    import app.services.workflow_recovery as recovery
    started, release = threading.Event(), threading.Event()
    calls = []
    def blocked(project, run):
        calls.append((project, run)); started.set(); assert release.wait(5)
    monkeypatch.setattr(recovery, "run_mock_attempt", blocked)
    scheduler = recovery.RecoveryScheduler()
    project, run = uuid4(), uuid4()
    try:
        assert scheduler.submit_initial(project, run)
        assert started.wait(5)
        assert not scheduler.submit_initial(project, run)
        assert not scheduler.submit(project, run)
    finally:
        release.set(); scheduler.close()
    assert calls == [(project, run)]
