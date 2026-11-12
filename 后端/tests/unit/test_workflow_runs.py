" ""Isolated workflow attempt and read API tests; no MySQL."""
from datetime import UTC
from pathlib import Path
from unittest.mock import MagicMock
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from langchain_core.callbacks import BaseCallbackHandler
from langgraph.types import Command

from app.infrastructure import database
from app.main import app
from app.models import PaperProject
from app.models.base import Base
from app.services.project_scope import ProjectConflict, ResourceNotFound
from app.services.workflow_run import WorkflowRunService, runnable_config_for_attempt
from app.graph.approval_graph import build_approval_graph
from app.graph.checkpointer import sqlite_checkpointer


@pytest.fixture
def db_env(monkeypatch):
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    @event.listens_for(engine, "connect")
    def foreign_keys(connection, _record):
        connection.execute("PRAGMA foreign_keys=ON")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    monkeypatch.setattr(database, "SessionLocal", factory)
    yield factory
    engine.dispose()


def project(session, *, lifecycle="active"):
    instance = PaperProject(title="test", topic="test", citation_style="APA", lifecycle_status=lifecycle)
    session.add(instance)
    session.flush()
    return instance


def test_attempt_lifecycle_and_run_config(db_env):
    with db_env() as session:
        pid = project(session).id
        service = WorkflowRunService(session)
        first = service.create(pid)
        session.commit()
        assert first.status == "queued" and first.attempt_no == 1
        assert isinstance(first.run_id, UUID) and isinstance(first.thread_id, UUID) and first.run_id != first.thread_id
        config = runnable_config_for_attempt(first)
        assert config["run_id"] == first.run_id and config["configurable"]["thread_id"] == str(first.thread_id)
        service.transition(pid, first.run_id, "running", current_stage="planning")
        service.transition(pid, first.run_id, "waiting_human", current_stage="planning")
        session.commit()
        old_finished = first.finished_at
        service.transition(pid, first.run_id, "waiting_human", current_stage="planning")
        assert first.finished_at == old_finished
        second = service.create(pid, resume_thread_id=first.thread_id)
        session.commit()
        assert second.thread_id == first.thread_id and second.run_id != first.run_id and second.attempt_no == 2
        assert second.current_stage == "planning"
        assert first.status == "waiting_human" and service.latest(pid, first.thread_id).run_id == second.run_id
        assert first.started_at.tzinfo == UTC and first.finished_at.tzinfo == UTC
        with pytest.raises(ProjectConflict):
            service.create(pid, resume_thread_id=first.thread_id)
        with pytest.raises(ProjectConflict):
            service.transition(pid, first.run_id, "completed")
        session.rollback()


def test_run_config_is_used_by_langgraph_root_callback(db_env, tmp_path: Path, monkeypatch):
    monkeypatch.setenv("LANGSMITH_TRACING", "false")
    monkeypatch.setenv("LANGCHAIN_TRACING_V2", "false")
    class TraceRoot(BaseCallbackHandler):
        root_ids = None
        def __init__(self):
            self.root_ids = []
        def on_chain_start(self, serialized, inputs, *, run_id, parent_run_id=None, **kwargs):
            if parent_run_id is None:
                self.root_ids.append(run_id)
    with db_env() as session:
        pid = project(session).id
        run = WorkflowRunService(session).create(pid)
        tracer = TraceRoot()
        config = runnable_config_for_attempt(run)
        config["callbacks"] = [tracer]
        with sqlite_checkpointer(tmp_path / "runtime.sqlite") as saver:
            graph = build_approval_graph().compile(checkpointer=saver)
            graph.invoke({"project_id": str(pid), "current_stage": "planning"}, config)
            assert graph.get_state({"configurable": {"thread_id": str(run.thread_id)}}).next == ("task_approval",)
        assert tracer.root_ids == [run.run_id]


def test_attempt_records_for_mock_graph_start_and_two_resumes(db_env, tmp_path: Path, monkeypatch):
    monkeypatch.setenv("LANGSMITH_TRACING", "false")
    monkeypatch.setenv("LANGCHAIN_TRACING_V2", "false")
    with db_env() as session, sqlite_checkpointer(tmp_path / "attempts.sqlite") as saver:
        pid = project(session).id
        service = WorkflowRunService(session)
        graph = build_approval_graph().compile(checkpointer=saver)
        first = service.create(pid)
        service.transition(pid, first.run_id, "running", current_stage="planning")
        session.commit()
        graph.invoke({"project_id": str(pid), "current_stage": "planning"}, runnable_config_for_attempt(first))
        state = graph.get_state({"configurable": {"thread_id": str(first.thread_id)}})
        assert state.next == ("task_approval",)
        service.transition(pid, first.run_id, "waiting_human", current_stage="planning")
        second = service.create(pid, resume_thread_id=first.thread_id)
        service.transition(pid, second.run_id, "running")
        session.commit()
        graph.invoke(Command(resume={"decision": "approve", "approved_ids": state.values["tasks"][:1]}), runnable_config_for_attempt(second))
        state = graph.get_state({"configurable": {"thread_id": str(first.thread_id)}})
        assert state.next == ("source_approval",)
        service.transition(pid, second.run_id, "waiting_human", current_stage="research")
        third = service.create(pid, resume_thread_id=first.thread_id)
        service.transition(pid, third.run_id, "running")
        session.commit()
        graph.invoke(Command(resume={"decision": "approve", "approved_ids": state.values["source_ids"]}), runnable_config_for_attempt(third))
        state = graph.get_state({"configurable": {"thread_id": str(first.thread_id)}})
        assert state.next == () and state.values["current_stage"] == "review"
        service.transition(pid, third.run_id, "completed", current_stage="review")
        session.commit()
        assert len({first.run_id, second.run_id, third.run_id}) == 3
        assert first.status == second.status == "waiting_human" and third.status == "completed"
        assert service.latest(pid, first.thread_id).run_id == third.run_id


@pytest.mark.parametrize("status", ["completed", "paused", "cancelled", "failed"])
def test_terminal_transitions_and_safe_failure(db_env, status):
    with db_env() as session:
        pid = project(session).id
        service = WorkflowRunService(session)
        run = service.create(pid)
        service.transition(pid, run.run_id, "running")
        opts = {"error_code": "timeout", "retryable": True} if status == "failed" else {}
        service.transition(pid, run.run_id, status, current_stage="research", **opts)
        session.commit()
        assert run.finished_at and run.error_code == opts.get("error_code")
        if status in {"failed", "paused"}:
            following = service.create(pid, resume_thread_id=run.thread_id)
            assert following.attempt_no == 2
        else:
            with pytest.raises(ProjectConflict):
                service.create(pid, resume_thread_id=run.thread_id)
        session.rollback()


def test_reject_invalid_transitions_and_error_text(db_env):
    with db_env() as session:
        pid = project(session).id
        service = WorkflowRunService(session)
        run = service.create(pid)
        with pytest.raises(ProjectConflict):
            service.transition(pid, run.run_id, "completed")
        service.transition(pid, run.run_id, "running")
        with pytest.raises(ValueError, match="Invalid safe error summary"):
            service.transition(pid, run.run_id, "failed", error_code="password secret", retryable=True)
        with pytest.raises(ValueError):
            service.transition(pid, run.run_id, "running", error_code="timeout", retryable=True)
        assert run.status == "running" and run.error_code is None


def test_nonretryable_failure_and_conflicting_repeats(db_env):
    with db_env() as session:
        pid = project(session).id
        service = WorkflowRunService(session)
        run = service.create(pid)
        service.transition(pid, run.run_id, "running", current_stage="planning")
        service.transition(pid, run.run_id, "failed", current_stage="planning", error_code="other", retryable=False)
        with pytest.raises(ProjectConflict):
            service.transition(pid, run.run_id, "failed", current_stage="planning", error_code="timeout", retryable=True)
        with pytest.raises(ProjectConflict):
            service.create(pid, resume_thread_id=run.thread_id)


def test_archived_project_runs_are_readable_but_deleted_are_hidden(db_env):
    with db_env() as session:
        parent = project(session)
        service = WorkflowRunService(session)
        run = service.create(parent.id)
        project_id, run_id, thread_id = parent.id, run.run_id, run.thread_id
        parent.lifecycle_status = "archived"
        session.commit()
    with TestClient(app) as client:
        assert client.get(f"/api/projects/{project_id}/workflows/{thread_id}").status_code == 200
        assert client.get(f"/api/projects/{project_id}/workflow-runs/{run_id}").status_code == 200
    with db_env() as session:
        parent = session.get(PaperProject, project_id)
        parent.lifecycle_status = "deleted"
        session.commit()
    with TestClient(app) as client:
        assert client.get(f"/api/projects/{project_id}/workflows/{thread_id}").status_code == 404
        assert client.get(f"/api/projects/{project_id}/workflow-runs/{run_id}").status_code == 404


def test_project_scope_and_read_api(db_env):
    with db_env() as session:
        p1, p2, deleted, archived = (project(session), project(session), project(session, lifecycle="deleted"), project(session, lifecycle="archived"))
        p1id, p2id, did, aid = p1.id, p2.id, deleted.id, archived.id
        service = WorkflowRunService(session)
        run = service.create(p1id)
        service.transition(p1id, run.run_id, "running", current_stage="planning")
        service.transition(p1id, run.run_id, "waiting_human")
        thread, first_id = run.thread_id, run.run_id
        later = service.create(p1id, resume_thread_id=thread)
        latest_id = later.run_id
        session.commit()
        with pytest.raises(ResourceNotFound):
            service.create(p2id, resume_thread_id=thread)
    with TestClient(app) as client:
        base = f"/api/projects/{p1id}"
        response = client.get(f"{base}/workflows/{thread}")
        assert response.status_code == 200
        assert response.json()["latest_run_id"] == str(latest_id) and response.json()["status"] == "queued"
        assert response.json()["updated_at"].endswith("Z")
        detail = client.get(f"{base}/workflow-runs/{first_id}")
        assert detail.status_code == 200 and detail.json()["status"] == "waiting_human"
        for url in (f"/api/projects/{p2id}/workflows/{thread}", f"/api/projects/{p2id}/workflow-runs/{first_id}",
                    f"/api/projects/{did}/workflows/{thread}", f"/api/projects/{aid}/workflows/{thread}",
                    f"{base}/workflows/{uuid4()}", f"{base}/workflow-runs/{uuid4()}", f"/api/projects/{uuid4()}/workflows/{thread}"):
            assert client.get(url).status_code == 404
        assert client.get(f"{base}/workflows/not-a-uuid").status_code == 422
        assert client.get(f"/api/projects/not-a-uuid/workflow-runs/{first_id}").status_code == 422
        assert client.get(f"{base}/workflow-runs/not-a-uuid").status_code == 422
        paths = client.get("/openapi.json").json()["paths"]
        assert set(paths["/api/projects/{project_id}/workflows/{thread_id}"]) == {"get"}
        assert set(paths["/api/projects/{project_id}/workflow-runs/{run_id}"]) == {"get"}


def test_project_run_list_is_scoped_and_hides_deleted_projects(db_env):
    with db_env() as session:
        p1, p2 = project(session), project(session)
        pid, other = p1.id, p2.id
        one = WorkflowRunService(session).create(pid)
        two = WorkflowRunService(session).create(other)
        one_id, two_id = str(one.run_id), str(two.run_id)
        session.commit()
    with TestClient(app) as client:
        response = client.get(f"/api/projects/{pid}/workflow-runs")
        assert response.status_code == 200
        assert [run['run_id'] for run in response.json()] == [one_id]
        assert two_id not in response.text
        assert client.get(f"/api/projects/{uuid4()}/workflow-runs").status_code == 404
    with db_env() as session:
        session.get(PaperProject, pid).lifecycle_status = 'deleted'
        session.commit()
    with TestClient(app) as client:
        assert client.get(f"/api/projects/{pid}/workflow-runs").status_code == 404


@pytest.mark.parametrize(("code", "status", "message"), [(2003, 503, "Database unavailable"), (3819, 500, "Internal server error")])
def test_api_database_failure_is_sanitized_and_rolled_back(monkeypatch, code, status, message):
    session = MagicMock()
    session.scalar.side_effect = OperationalError("secret SQL", {}, Exception(code, "password secret"))
    monkeypatch.setattr(database, "SessionLocal", lambda: session)
    with TestClient(app) as client:
        response = client.get(f"/api/projects/{uuid4()}/workflows/{uuid4()}")
    assert response.status_code == status and response.json() == {"detail": message}
    assert "secret" not in response.text
    session.rollback.assert_called()
