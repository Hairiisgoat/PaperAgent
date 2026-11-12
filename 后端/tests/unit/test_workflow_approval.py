"""Approval read contract; isolated relational and file checkpoint stores."""
from copy import deepcopy
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock
from uuid import UUID, uuid4
import socket
import sqlite3

import pytest
import pymysql
from fastapi.testclient import TestClient
from langgraph.types import Command
from sqlalchemy import create_engine, event
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.graph.approval_graph import build_approval_graph
from app.graph.checkpointer import sqlite_checkpointer
from app.infrastructure import database
from app.main import app
from app.models import PaperProject, WorkflowEventRecord, WorkflowRun
from app.models.base import Base
from app.schemas.workflow_event import ApprovalEvent, ApprovalPayload, ProgressPayload
from app.services.project_scope import ProjectConflict
from app.services.workflow_approval import WorkflowApprovalService
from app.services.workflow_events import EventService
from app.services.workflow_run import WorkflowRunService, runnable_config_for_attempt


@pytest.fixture
def env(monkeypatch, tmp_path):
    def reject(*args, **kwargs):
        raise AssertionError("Network forbidden")
    original_connect = socket.socket.connect
    def local_socketpair_only(sock, address):
        # Windows asyncio implements its internal socketpair using loopback.
        if isinstance(address, tuple) and address[0] == "127.0.0.1":
            return original_connect(sock, address)
        raise AssertionError("External network forbidden")
    monkeypatch.setattr(socket.socket, "connect", local_socketpair_only)
    monkeypatch.setattr(socket, "create_connection", reject)
    monkeypatch.setattr(pymysql, "connect", reject)
    monkeypatch.setenv("LANGSMITH_TRACING", "false")
    monkeypatch.setenv("LANGCHAIN_TRACING_V2", "false")
    path = tmp_path / "checkpoints.sqlite"
    monkeypatch.setenv("LANGGRAPH_SQLITE_PATH", str(path))
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    @event.listens_for(engine, "connect")
    def fk(conn, _record): conn.execute("PRAGMA foreign_keys=ON")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    monkeypatch.setattr(database, "SessionLocal", factory)
    with factory() as session:
        project = PaperProject(id=uuid4(), title="test", topic="test", citation_style="APA")
        other = PaperProject(id=uuid4(), title="other", topic="test", citation_style="APA")
        session.add_all([project, other])
        session.commit()
        ids = (project.id, other.id)
    yield factory, path, ids
    engine.dispose()


def start(client, project_id):
    response = client.post(f"/api/projects/{project_id}/runs")
    assert response.status_code == 202
    return response.json()


def url(project_id, run_id):
    return f"/api/projects/{project_id}/workflow-runs/{run_id}/approval"


def snapshot(path: Path, thread: str):
    with sqlite_checkpointer(path, read_only=True) as saver:
        return build_approval_graph().compile(checkpointer=saver).get_state({"configurable": {"thread_id": thread}})


def test_real_task_interrupt_read_twice_without_resume(env):
    factory, path, (project_id, _) = env
    with TestClient(app) as client:
        run = start(client, project_id)
        before = snapshot(path, run["thread_id"])
        response = client.get(url(project_id, run["run_id"]))
        assert response.status_code == 200
        data = response.json()
        assert data == {
            "project_id": str(project_id), "run_id": run["run_id"], "thread_id": run["thread_id"],
            "approval_type": "task_approval", "checkpoint_id": before.config["configurable"]["checkpoint_id"],
            "current_stage": "planning", "item_ids": before.values["tasks"],
        }
        assert set(data) == {"project_id", "run_id", "thread_id", "approval_type", "checkpoint_id", "current_stage", "item_ids"}
        assert client.get(url(project_id, run["run_id"]), params={"thread_id": str(uuid4())}).json() == data
        with factory() as session:
            events = EventService(session)
            progress = events.prepare(project_id, UUID(run["run_id"]), "progress", stage="planning", agent_name="Planner",
                status="waiting_human", payload=ProgressPayload(stage="planning", agent_name="Planner", status="waiting_human", message="Waiting for approval"))
            events.commit_and_notify(progress)
        assert client.get(url(project_id, run["run_id"])).json() == data
        assert "/api/projects/{project_id}/workflow-runs/{run_id}/approval" in client.get("/openapi.json").json()["paths"]
        after = snapshot(path, run["thread_id"])
        assert before.next == after.next == ("task_approval",)
        assert before.config == after.config and before.values == after.values and before.interrupts == after.interrupts


def test_real_source_interrupt_and_historical_run_rejected(env):
    factory, path, (project_id, _) = env
    with TestClient(app) as client:
        first = start(client, project_id)
        with factory() as session:
            service = WorkflowRunService(session)
            second = service.create(project_id, resume_thread_id=UUID(first["thread_id"]))
            service.transition(project_id, second.run_id, "running", current_stage="planning")
            session.commit()
            with sqlite_checkpointer(path) as saver:
                graph = build_approval_graph().compile(checkpointer=saver)
                config = {"configurable": {"thread_id": first["thread_id"]}}
                tasks = graph.get_state(config).values["tasks"]
                graph.invoke(Command(resume={"decision": "approve", "approved_ids": tasks[:1]}), runnable_config_for_attempt(second))
                snap = graph.get_state(config)
                assert snap.next == ("source_approval",)
                checkpoint = UUID(snap.config["configurable"]["checkpoint_id"])
                sources = snap.values["source_ids"]
            service.transition(project_id, second.run_id, "waiting_human", current_stage="research")
            events = EventService(session)
            entry = events.prepare(project_id, second.run_id, "approval_required", stage="research", agent_name="Research",
                status="waiting_human", payload=ApprovalPayload(checkpoint_id=checkpoint, approval_type="source_approval", payload_ref=second.run_id))
            events.commit_and_notify(entry)
            second_id = second.run_id
        assert client.get(url(project_id, first["run_id"])).status_code == 409
        result = client.get(url(project_id, second_id))
        assert result.status_code == 200
        assert result.json()["approval_type"] == "source_approval" and result.json()["item_ids"] == sources
        assert snapshot(path, first["thread_id"]).next == ("source_approval",)


def test_scope_status_and_missing_checkpoint(env):
    factory, path, (project_id, other_id) = env
    with TestClient(app) as client:
        run = start(client, project_id)
        assert client.get(url(other_id, run["run_id"])).status_code == 404
        assert client.get(url(project_id, uuid4())).status_code == 404
        assert client.get(url(uuid4(), run["run_id"])).status_code == 404
        assert client.get(url(project_id, "not-uuid")).status_code == 422
        with factory() as s:
            entry = s.get(WorkflowRun, UUID(run["run_id"]))
            entry.status = "running"
            s.commit()
        assert client.get(url(project_id, run["run_id"])).status_code == 409
        with factory() as s:
            entry = s.get(WorkflowRun, UUID(run["run_id"]))
            entry.status = "waiting_human"
            s.get(PaperProject, project_id).lifecycle_status = "deleted"
            s.commit()
        assert client.get(url(project_id, run["run_id"])).status_code == 404


def test_missing_checkpoint_does_not_create_file(env, monkeypatch):
    _factory, path, (project_id, _) = env
    with TestClient(app) as client:
        run = start(client, project_id)
        missing = path.parent / "missing.sqlite"
        monkeypatch.setenv("LANGGRAPH_SQLITE_PATH", str(missing))
        response = client.get(url(project_id, run["run_id"]))
        assert response.status_code == 409 and not missing.exists()
        assert str(missing) not in response.text


def test_archived_project_allows_read_only_approval(env):
    factory, _path, (project_id, _) = env
    with TestClient(app) as client:
        run = start(client, project_id)
        with factory() as session:
            session.get(PaperProject, project_id).lifecycle_status = "archived"
            session.commit()
        assert client.get(url(project_id, run["run_id"])).status_code == 200


def test_checkpoint_unavailable_is_sanitized(env, monkeypatch, caplog):
    _factory, _path, (project_id, _) = env
    with TestClient(app) as client:
        run = start(client, project_id)
        @contextmanager
        def broken(*args, **kwargs):
            raise sqlite3.OperationalError("password=secret /private/path")
            yield
        monkeypatch.setattr("app.services.workflow_approval.sqlite_checkpointer", broken)
        response = client.get(url(project_id, run["run_id"]))
        assert response.status_code == 503 and response.json() == {"detail": "Approval storage unavailable"}
        assert "secret" not in response.text and "/private/path" not in caplog.text


@pytest.mark.parametrize("alter", ["event_missing", "event_checkpoint", "event_project", "thread"])
def test_mismatched_records_are_rejected(env, alter):
    factory, _path, (project_id, other_id) = env
    with TestClient(app) as client:
        run = start(client, project_id)
        with factory() as s:
            entry = s.get(WorkflowRun, UUID(run["run_id"]))
            records = list(s.query(WorkflowEventRecord).filter_by(project_id=project_id, run_id=entry.run_id).order_by(WorkflowEventRecord.sequence.desc()))
            approval = records[0]
            if alter == "event_missing":
                approval.event_type = "progress"
            elif alter == "event_checkpoint":
                approval.payload = {**approval.payload, "checkpoint_id": str(uuid4())}
            elif alter == "event_project":
                approval.project_id = other_id
            else:
                entry.thread_id = uuid4()
            s.commit()
        response = client.get(url(project_id, run["run_id"]))
        assert response.status_code == 409 and response.json() == {"detail": "Approval is unavailable or inconsistent"}


def make_snapshot():
    project_id, run_id, thread_id, checkpoint, item = (uuid4() for _ in range(5))
    payload = ApprovalPayload(checkpoint_id=checkpoint, approval_type="task_approval", payload_ref=run_id)
    event = ApprovalEvent(event_id=uuid4(), sequence=1, run_id=run_id, project_id=project_id,
        stage="planning", agent_name="Planner", status="waiting_human",
        timestamp=datetime.now(UTC), payload=payload)
    snap = SimpleNamespace(config={"configurable": {"thread_id": str(thread_id), "checkpoint_id": str(checkpoint)}},
        metadata={"paperagent_run_id": str(run_id), "paperagent_project_id": str(project_id)},
        values={"project_id": str(project_id), "current_stage": "planning", "tasks": [str(item)]},
        next=("task_approval",), interrupts=(SimpleNamespace(value={"type": "task_approval", "task_ids": [str(item)]}),))
    return project_id, run_id, thread_id, event, snap


@pytest.mark.parametrize("change", ["none", "bad_type", "bad_interrupt", "bad_values", "empty", "bad_id", "duplicate", "wrong_project", "wrong_thread", "wrong_run", "wrong_node", "mismatch_items", "extra"])
def test_invalid_snapshot_is_rejected(change, caplog):
    project_id, run_id, thread_id, event, snap = make_snapshot()
    snap = deepcopy(snap)
    if change == "none": snap.interrupts = ()
    elif change == "bad_type": snap.interrupts[0].value["type"] = "private-unexpected"
    elif change == "bad_interrupt": snap.interrupts[0].value = "private-unexpected"
    elif change == "bad_values": snap.values = "private-unexpected"
    elif change == "empty": snap.interrupts[0].value["task_ids"] = []
    elif change == "bad_id": snap.interrupts[0].value["task_ids"] = ["invalid"]
    elif change == "duplicate": snap.interrupts[0].value["task_ids"] *= 2
    elif change == "wrong_project": snap.values["project_id"] = str(uuid4())
    elif change == "wrong_thread": snap.config["configurable"]["thread_id"] = str(uuid4())
    elif change == "wrong_run": snap.metadata["paperagent_run_id"] = str(uuid4())
    elif change == "wrong_node": snap.next = ("writing",)
    elif change == "mismatch_items": snap.values["tasks"] = [str(uuid4())]
    else: snap.interrupts[0].value["secret"] = "private"
    with pytest.raises(ProjectConflict, match="^Approval is unavailable or inconsistent$"):
        WorkflowApprovalService(MagicMock())._extract(snap, project_id, run_id, thread_id, "planning", event)
    assert "private-unexpected" not in caplog.text and "private" not in caplog.text


def test_database_unavailable_is_sanitized(monkeypatch):
    session = MagicMock()
    session.scalar.side_effect = OperationalError("secret sql", {}, Exception(2003, "password secret"))
    monkeypatch.setattr(database, "SessionLocal", lambda: session)
    with TestClient(app) as client:
        response = client.get(url(uuid4(), uuid4()))
    assert response.status_code == 503 and response.json() == {"detail": "Database unavailable"}
    assert "secret" not in response.text


def test_resume_partial_then_complete_and_replay(env):
    factory, path, (project_id, _) = env
    with TestClient(app) as client:
        first = start(client, project_id)
        before = snapshot(path, first["thread_id"])
        task = before.values["tasks"][0]
        response = client.post(url(project_id, first["run_id"]), json={"decision": "approve", "approved_ids": [task]})
        assert response.status_code == 202, response.text
        second = response.json()
        assert second["attempt_no"] == 2 and second["thread_id"] == first["thread_id"] and second["run_id"] != first["run_id"]
        assert client.post(url(project_id, first["run_id"]), json={"decision": "approve", "approved_ids": [task]}).json() == second
        assert client.get(url(project_id, first["run_id"])).status_code == 409
        detail = client.get(url(project_id, second["run_id"]))
        assert detail.status_code == 200, detail.text
        assert detail.json()["approval_type"] == "source_approval"
        middle = snapshot(path, first["thread_id"])
        assert middle.values["tasks"] == before.values["tasks"] and middle.values["approved_task_ids"] == [task]
        assert middle.next == ("source_approval",)
        sources = detail.json()["item_ids"]
        third_response = client.post(url(project_id, second["run_id"]), json={"decision": "approve", "approved_ids": sources[:1]})
        assert third_response.status_code == 202, third_response.text
        third = third_response.json()
        assert third["attempt_no"] == 3 and third["thread_id"] == first["thread_id"]
        final = snapshot(path, first["thread_id"])
        assert final.next == () and final.values["approved_source_ids"] == sources[:1]
        assert final.values["current_stage"] == "review" and final.values.get("user_decision") != "accepted"
        with factory() as session:
            assert session.get(WorkflowRun, UUID(first["run_id"])).status == "waiting_human"
            assert session.get(WorkflowRun, UUID(second["run_id"])).status == "waiting_human"
            assert session.get(WorkflowRun, UUID(third["run_id"])).status == "completed"
            entries = EventService(session).since(project_id, UUID(third["run_id"]), 0)
            assert [e.sequence for e in entries] == [1, 2, 3]
            assert [e.event_type for e in entries] == ["progress", "progress", "completed"]
            assert [e.sequence for e in EventService(session).since(project_id, UUID(third["run_id"]), 1)] == [2, 3]


@pytest.mark.parametrize("at_source", [False, True])
def test_resume_rejection_is_cancelled(env, at_source):
    factory, path, (project_id, _) = env
    with TestClient(app) as client:
        first = start(client, project_id)
        if at_source:
            tasks = client.get(url(project_id, first["run_id"])).json()["item_ids"]
            first = client.post(url(project_id, first["run_id"]), json={"decision": "approve", "approved_ids": tasks}).json()
        result = client.post(url(project_id, first["run_id"]), json={"decision": "reject"})
        assert result.status_code == 202, result.text
        with factory() as session:
            run = session.get(WorkflowRun, UUID(result.json()["run_id"]))
            assert run.status == "cancelled"
            assert EventService(session).since(project_id, run.run_id, 0)[-1].status == "cancelled"
        assert snapshot(path, first["thread_id"]).next == ()


@pytest.mark.parametrize("body", [
    {"decision": "approve"}, {"decision": "approve", "approved_ids": []},
    {"decision": "approve", "approved_ids": ["invalid"]},
    {"decision": "reject", "approved_ids": []}, {"decision": "maybe"},
    {"decision": "reject", "extra": "secret"},
])
def test_resume_bad_request(env, body):
    _factory, _path, (project_id, _) = env
    with TestClient(app) as client:
        first = start(client, project_id)
        assert client.post(url(project_id, first["run_id"]), json=body).status_code == 422


def test_resume_duplicate_and_foreign_ids(env):
    factory, _path, (project_id, other_id) = env
    with TestClient(app) as client:
        first = start(client, project_id)
        item = client.get(url(project_id, first["run_id"])).json()["item_ids"][0]
        assert client.post(url(project_id, first["run_id"]), json={"decision": "approve", "approved_ids": [item, item]}).status_code == 422
        assert client.post(url(project_id, first["run_id"]), json={"decision": "approve", "approved_ids": [str(uuid4())]}).status_code == 409
        assert client.post(url(other_id, first["run_id"]), json={"decision": "reject"}).status_code == 404
        with factory() as session:
            assert len(session.query(WorkflowRun).filter_by(project_id=project_id).all()) == 1
            from app.models.workflow_approval_decision import WorkflowApprovalDecision
            assert session.query(WorkflowApprovalDecision).count() == 0


def test_resume_missing_checkpoint_and_execution_failure(env, monkeypatch):
    factory, path, (project_id, _) = env
    with TestClient(app) as client:
        first = start(client, project_id)
        monkeypatch.setenv("LANGGRAPH_SQLITE_PATH", str(path.parent / "missing.sqlite"))
        assert client.post(url(project_id, first["run_id"]), json={"decision": "reject"}).status_code == 409
        monkeypatch.setenv("LANGGRAPH_SQLITE_PATH", str(path))
        import app.services.workflow_resume as resume
        monkeypatch.setattr(resume, "build_graph", lambda _version: (_ for _ in ()).throw(RuntimeError("private secret")))
        result = client.post(url(project_id, first["run_id"]), json={"decision": "reject"})
        assert result.status_code == 202
        with factory() as session:
            run = session.get(WorkflowRun, UUID(result.json()["run_id"]))
            assert run.status == "failed" and run.error_code == "internal_error" and not run.retryable
            assert EventService(session).since(project_id, run.run_id, 0)[-1].event_type == "error"


def test_resume_commit_failure_rolls_back_and_does_not_dispatch(env, monkeypatch):
    factory, path, (project_id, _) = env
    with TestClient(app) as client:
        first = start(client, project_id)
        from sqlalchemy.exc import OperationalError
        original = EventService.commit_and_notify
        def fail(self, event):
            if event.status == "queued":
                raise OperationalError("private sql", {}, Exception(2003, "password secret"))
            return original(self, event)
        monkeypatch.setattr(EventService, "commit_and_notify", fail)
        result = client.post(url(project_id, first["run_id"]), json={"decision": "reject"})
        assert result.status_code == 503 and "secret" not in result.text
        assert snapshot(path, first["thread_id"]).next == ("task_approval",)
        with factory() as session:
            assert len(session.query(WorkflowRun).filter_by(project_id=project_id).all()) == 1
            from app.models.workflow_approval_decision import WorkflowApprovalDecision
            assert session.query(WorkflowApprovalDecision).count() == 0


def test_resume_duplicate_background_execution_is_ignored(env):
    factory, path, (project_id, _) = env
    from app.services.workflow_resume import run_resume_attempt
    with TestClient(app) as client:
        first = start(client, project_id)
        detail = client.get(url(project_id, first["run_id"])).json()
        result = client.post(url(project_id, first["run_id"]), json={"decision": "reject"}).json()
        before = snapshot(path, first["thread_id"])
        run_resume_attempt(project_id, UUID(result["run_id"]))
        after = snapshot(path, first["thread_id"])
        assert before.config == after.config and before.values == after.values
        with factory() as session:
            assert len(EventService(session).since(project_id, UUID(result["run_id"]), 0)) == 3


def test_full_task_approval_partial_sources_without_replaying_nodes(env, monkeypatch):
    _factory, path, (project_id, _) = env
    import app.graph.approval_graph as approval_graph
    with TestClient(app) as client:
        first = start(client, project_id)
        original = snapshot(path, first["thread_id"])
        def no_replay(_state):
            raise AssertionError("Completed node replayed")
        monkeypatch.setattr(approval_graph, "planner_mock", no_replay)
        second = client.post(url(project_id, first["run_id"]), json={"decision": "approve", "approved_ids": original.values["tasks"]})
        assert second.status_code == 202
        detail = client.get(url(project_id, second.json()["run_id"]))
        assert detail.status_code == 200 and len(detail.json()["item_ids"]) == 2
        monkeypatch.setattr(approval_graph, "research_mock", no_replay)
        third = client.post(url(project_id, second.json()["run_id"]), json={"decision": "approve", "approved_ids": detail.json()["item_ids"][:1]})
        assert third.status_code == 202
        final = snapshot(path, first["thread_id"])
        assert final.next == () and final.values["approved_source_ids"] == detail.json()["item_ids"][:1]
        assert client.get(f"/api/projects/{project_id}/workflow-runs/{third.json()['run_id']}").json()["status"] == "completed"


def test_resume_scheduling_failure_rolls_back(env, monkeypatch, caplog):
    factory, path, (project_id, _) = env
    from fastapi import BackgroundTasks
    with TestClient(app) as client:
        first = start(client, project_id)
        def broken(*args, **kwargs):
            raise RuntimeError("private secret")
        monkeypatch.setattr(BackgroundTasks, "add_task", broken)
        result = client.post(url(project_id, first["run_id"]), json={"decision": "reject"})
        assert result.status_code == 503 and "secret" not in result.text and "secret" not in caplog.text
        assert snapshot(path, first["thread_id"]).next == ("task_approval",)
        with factory() as session:
            assert len(session.query(WorkflowRun).filter_by(project_id=project_id).all()) == 1


def test_resume_checkpoint_advanced_then_event_failure_never_replays(env, monkeypatch, caplog):
    factory, path, (project_id, _) = env
    with TestClient(app) as client:
        first = start(client, project_id)
        tasks = client.get(url(project_id, first["run_id"])).json()["item_ids"]
        original = EventService.commit_and_notify
        def fail_approval(self, event):
            if event.event_type == "approval_required":
                raise RuntimeError("private secret")
            return original(self, event)
        monkeypatch.setattr(EventService, "commit_and_notify", fail_approval)
        response = client.post(url(project_id, first["run_id"]), json={"decision": "approve", "approved_ids": tasks})
        assert response.status_code == 202
        after = snapshot(path, first["thread_id"])
        assert after.next == ("source_approval",)
        assert "reconciliation required" in caplog.text and "private secret" not in caplog.text
        with factory() as session:
            entry = session.get(WorkflowRun, UUID(response.json()["run_id"]))
            assert entry.status == "failed" and entry.retryable is False
            assert [e.event_type for e in EventService(session).since(project_id, entry.run_id, 0)] == ["progress", "progress", "error"]
        assert client.post(url(project_id, first["run_id"]), json={"decision": "approve", "approved_ids": tasks}).json() == response.json()


def queue_without_dispatch(factory, project_id, old_run_id, submission):
    from app.schemas.workflow_approval import ApprovalSubmission
    from app.services.workflow_resume import queue_approval
    with factory() as session:
        response, event, _task = queue_approval(session, project_id, UUID(old_run_id), ApprovalSubmission.model_validate(submission))
        EventService(session).commit_and_notify(event)
        return response


@pytest.mark.parametrize("decision", ["approve", "reject"])
def test_queued_intent_recovers_at_lifespan_start(env, decision):
    factory, path, (project_id, _) = env
    from app.models.workflow_approval_decision import WorkflowApprovalDecision
    with TestClient(app) as client:
        first = start(client, project_id)
        items = client.get(url(project_id, first["run_id"])).json()["item_ids"]
        body = {"decision": decision}
        if decision == "approve": body["approved_ids"] = items[:1]
        queued = queue_without_dispatch(factory, project_id, first["run_id"], body)
        with factory() as session:
            intent = session.get(WorkflowApprovalDecision, UUID(first["run_id"]))
            assert intent.execution_status == "queued" and intent.run_id == queued.run_id
            assert intent.decision == decision and intent.approved_ids == (items[:1] if decision == "approve" else [])
            assert session.get(WorkflowRun, queued.run_id).status == "queued"
    # Closing and recreating the app lifecycle scans committed work.
    with TestClient(app):
        pass
    with factory() as session:
        run = session.get(WorkflowRun, queued.run_id)
        intent = session.get(WorkflowApprovalDecision, UUID(first["run_id"]))
        assert run.status == ("waiting_human" if decision == "approve" else "cancelled")
        assert intent.execution_status == "finished"
        assert [event.sequence for event in EventService(session).since(project_id, queued.run_id, 0)] == [1, 2, 3]
    assert snapshot(path, first["thread_id"]).next == (("source_approval",) if decision == "approve" else ())


def test_idempotent_decisions_and_conflicting_retry(env):
    factory, _path, (project_id, _) = env
    from app.models.workflow_approval_decision import WorkflowApprovalDecision
    with TestClient(app) as client:
        first = start(client, project_id)
        ids = client.get(url(project_id, first["run_id"])).json()["item_ids"]
        body = {"decision": "approve", "approved_ids": ids}
        initial = client.post(url(project_id, first["run_id"]), json=body)
        assert initial.status_code == 202
        retry = client.post(url(project_id, first["run_id"]), json={"decision": "approve", "approved_ids": list(reversed(ids))})
        assert retry.status_code == 202 and retry.json() == initial.json()
        assert client.post(url(project_id, first["run_id"]), json={"decision": "reject"}).status_code == 409
        with factory() as session:
            rows = session.query(WorkflowApprovalDecision).all()
            assert len(rows) == 1 and rows[0].approved_ids == sorted(ids)
            assert len(session.query(WorkflowRun).filter_by(project_id=project_id).all()) == 2


def test_running_resume_never_replayed_at_startup(env, caplog):
    factory, path, (project_id, _) = env
    from app.models.workflow_approval_decision import WorkflowApprovalDecision
    with TestClient(app) as client:
        first = start(client, project_id)
        queued = queue_without_dispatch(factory, project_id, first["run_id"], {"decision": "reject"})
        with factory() as session:
            session.get(WorkflowRun, queued.run_id).status = "running"
            session.get(WorkflowApprovalDecision, UUID(first["run_id"])).execution_status = "running"
            session.commit()
        before = snapshot(path, first["thread_id"])
    with TestClient(app): pass
    after = snapshot(path, first["thread_id"])
    assert before.config == after.config and after.next == ("task_approval",)
    assert "checkpoint_unchanged_execution_unknown" in caplog.text
    with factory() as session:
        assert session.get(WorkflowRun, queued.run_id).status == "running"


def test_queued_missing_checkpoint_records_safe_failure(env, monkeypatch):
    factory, path, (project_id, _) = env
    from app.models.workflow_approval_decision import WorkflowApprovalDecision
    with TestClient(app) as client:
        first = start(client, project_id)
        queued = queue_without_dispatch(factory, project_id, first["run_id"], {"decision": "reject"})
    monkeypatch.setenv("LANGGRAPH_SQLITE_PATH", str(path.parent / "absent.sqlite"))
    with TestClient(app): pass
    with factory() as session:
        assert session.get(WorkflowRun, queued.run_id).status == "failed"
        assert session.get(WorkflowApprovalDecision, UUID(first["run_id"])).execution_status == "needs_review"
    assert not (path.parent / "absent.sqlite").exists()


@pytest.mark.parametrize("decision", ["approve", "reject"])
def test_process_restart_recovers_durable_decision(tmp_path, monkeypatch, decision):
    import os
    import subprocess
    import sys
    db_path = tmp_path / "business.sqlite"
    checkpoint = tmp_path / "checkpoint.sqlite"
    monkeypatch.setenv("LANGGRAPH_SQLITE_PATH", str(checkpoint))
    monkeypatch.setenv("LANGSMITH_TRACING", "false")
    engine = create_engine(f"sqlite:///{db_path.as_posix()}", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    monkeypatch.setattr(database, "SessionLocal", factory)
    with factory() as session:
        project = PaperProject(id=uuid4(), title="process test", topic="test", citation_style="APA")
        session.add(project); session.commit(); project_id = project.id
    with TestClient(app) as client:
        first = start(client, project_id)
        items = client.get(url(project_id, first["run_id"])).json()["item_ids"]
        body = {"decision": decision}
        if decision == "approve": body["approved_ids"] = items[:1]
        queued = queue_without_dispatch(factory, project_id, first["run_id"], body)
    engine.dispose()
    code = '''
import sys
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from fastapi.testclient import TestClient
from app.infrastructure import database
from app.main import app
engine = create_engine("sqlite:///" + sys.argv[1], connect_args={"check_same_thread": False})
database.SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
with TestClient(app):
    pass
engine.dispose()
'''
    env_vars = dict(os.environ, LANGGRAPH_SQLITE_PATH=str(checkpoint), LANGSMITH_TRACING="false", LANGCHAIN_TRACING_V2="false")
    result = subprocess.run([sys.executable, "-c", code, db_path.as_posix()], env=env_vars, capture_output=True, timeout=30)
    assert result.returncode == 0, result.stderr.decode(errors="replace")
    with factory() as session:
        run = session.get(WorkflowRun, queued.run_id)
        assert run.status == ("waiting_human" if decision == "approve" else "cancelled")
        assert [e.sequence for e in EventService(session).since(project_id, queued.run_id, 0)] == [1, 2, 3]
    after = snapshot(checkpoint, first["thread_id"])
    assert after.next == (("source_approval",) if decision == "approve" else ())
    engine.dispose()


def test_recovery_scheduler_deduplicates_pending_task(monkeypatch):
    import threading
    import app.services.workflow_recovery as recovery
    started, release = threading.Event(), threading.Event()
    calls = []
    def blocked(project_id, run_id):
        calls.append((project_id, run_id))
        started.set()
        assert release.wait(5)
    monkeypatch.setattr(recovery, "run_resume_attempt", blocked)
    scheduler = recovery.RecoveryScheduler()
    project, run = uuid4(), uuid4()
    try:
        assert scheduler.submit(project, run)
        assert started.wait(5)
        assert scheduler.submit(project, run) is False
    finally:
        release.set(); scheduler.close()
    assert calls == [(project, run)]


def test_queued_checkpoint_mismatch_is_not_resumed(env):
    factory, path, (project_id, _) = env
    from app.models.workflow_approval_decision import WorkflowApprovalDecision
    with TestClient(app) as client:
        first = start(client, project_id)
        queued = queue_without_dispatch(factory, project_id, first["run_id"], {"decision": "reject"})
        with factory() as session:
            session.get(WorkflowApprovalDecision, UUID(first["run_id"])).checkpoint_id = uuid4()
            session.commit()
        before = snapshot(path, first["thread_id"])
    with TestClient(app): pass
    after = snapshot(path, first["thread_id"])
    assert after.config == before.config and after.next == ("task_approval",)
    with factory() as session:
        assert session.get(WorkflowRun, queued.run_id).status == "failed"
        assert session.get(WorkflowApprovalDecision, UUID(first["run_id"])).execution_status == "needs_review"


def test_running_advanced_checkpoint_only_reports(env, caplog):
    factory, path, (project_id, _) = env
    from app.services.workflow_recovery import inspect_running
    with TestClient(app) as client:
        first = start(client, project_id)
        current = snapshot(path, first["thread_id"])
        inspect_running(project_id, UUID(first["run_id"]), UUID(first["thread_id"]), uuid4())
        assert "checkpoint_advanced" in caplog.text
        assert snapshot(path, first["thread_id"]).config == current.config


def test_approval_commit_failure_does_not_broadcast_queued_event(env, monkeypatch):
    factory, _path, (project_id, _) = env
    from app.schemas.workflow_approval import ApprovalSubmission
    from app.services.workflow_events import EventBroker
    from app.services.workflow_resume import queue_approval
    from app.models.workflow_approval_decision import WorkflowApprovalDecision
    with TestClient(app) as client:
        first = start(client, project_id)
        with factory() as session:
            response, event, _ = queue_approval(session, project_id, UUID(first["run_id"]), ApprovalSubmission(decision="reject"))
            broker = EventBroker()
            subscriber = broker.subscribe(project_id, response.run_id)
            def fail_commit(): raise RuntimeError("private secret")
            monkeypatch.setattr(session, "commit", fail_commit)
            with pytest.raises(RuntimeError):
                EventService(session, broker).commit_and_notify(event)
            session.rollback()
            assert subscriber.queue.empty()
        with factory() as session:
            assert session.get(WorkflowApprovalDecision, UUID(first["run_id"])) is None
            assert session.get(WorkflowRun, response.run_id) is None
