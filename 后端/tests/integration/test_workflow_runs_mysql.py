"""Explicit opt-in MySQL tests: savepoints under an outer rollback; no DDL or delete."""
import os
from datetime import UTC, datetime, timedelta, timezone
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import event, inspect, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker

from app.core.config import get_database_settings
from app.infrastructure import database
from app.main import app
from app.models import PaperProject, WorkflowRun
from app.services.workflow_run import WorkflowRunService

pytestmark = pytest.mark.skipif(os.getenv("RUN_MYSQL_INTEGRATION") != "1", reason="Explicit opt-in required")


def test_mysql_schema_and_rollback_isolated_attempts(monkeypatch):
    s = get_database_settings()
    assert (s.host, s.port, s.database, s.user) == ("127.0.0.1", 3306, "paper_agent", "paper_agent_app")
    engine = database.get_engine()
    project_ids, run_ids = [], []
    statements = []
    def guard(_conn, _cursor, statement, _parameters, _context, _executemany):
        statement = statement.lstrip().upper()
        statements.append(statement)
        if statement.startswith(("DELETE ", "DROP ", "TRUNCATE ", "ALTER ", "CREATE ")):
            raise AssertionError("Forbidden DDL or physical delete")

    with engine.connect() as connection:
        assert connection.execute(text("SELECT DATABASE(), CURRENT_USER()")).one() == ("paper_agent", "paper_agent_app@127.0.0.1")
        inspector = inspect(connection)
        assert {c["name"] for c in inspector.get_columns("workflow_runs")} == set(WorkflowRun.__table__.columns.keys())
        columns = {c["name"]: c for c in inspector.get_columns("workflow_runs")}
        assert all(str(columns[name]["type"]) == "CHAR(32)" for name in ("run_id", "thread_id", "project_id"))
        assert all(columns[name]["type"].fsp == 6 for name in ("created_at", "updated_at", "started_at", "finished_at"))
        assert inspector.get_pk_constraint("workflow_runs")["constrained_columns"] == ["run_id"]
        assert any(fk["constrained_columns"] == ["project_id"] and fk["referred_table"] == "paper_projects" for fk in inspector.get_foreign_keys("workflow_runs"))
        assert any(i["unique"] and i["column_names"] == ["thread_id", "attempt_no"] for i in inspector.get_indexes("workflow_runs"))
        assert any(i["column_names"] == ["project_id", "thread_id", "attempt_no"] for i in inspector.get_indexes("workflow_runs"))
        connection.rollback()
        outer = connection.begin()
        factory = sessionmaker(bind=connection, autoflush=False, expire_on_commit=False, join_transaction_mode="create_savepoint")
        monkeypatch.setattr(database, "SessionLocal", factory)
        event.listen(engine, "before_cursor_execute", guard)
        try:
            with factory() as session:
                p1 = PaperProject(id=uuid4(), title="隔离测试", topic="test", citation_style="APA")
                p2 = PaperProject(id=uuid4(), title="隔离测试2", topic="test", citation_style="APA")
                session.add_all([p1, p2])
                session.flush()
                project_ids.extend([p1.id, p2.id])
                service = WorkflowRunService(session)
                first = service.create(p1.id)
                run_ids.append(first.run_id)
                service.transition(p1.id, first.run_id, "running", current_stage="planning")
                service.transition(p1.id, first.run_id, "waiting_human")
                second = service.create(p1.id, resume_thread_id=first.thread_id)
                run_ids.append(second.run_id)
                service.transition(p1.id, second.run_id, "running", current_stage="research")
                local = datetime(2026, 11, 4, 12, 30, tzinfo=timezone(timedelta(hours=8)))
                second.started_at = local
                session.flush()
                session.refresh(second)
                assert second.started_at == local.astimezone(UTC)
                assert first.thread_id == second.thread_id and first.run_id != second.run_id
                assert (first.attempt_no, second.attempt_no) == (1, 2)
                with pytest.raises(IntegrityError):
                    with session.begin_nested():
                        session.execute(WorkflowRun.__table__.insert().values(run_id=first.run_id, thread_id=uuid4(), project_id=p1.id, attempt_no=1, status="queued"))
                with pytest.raises(IntegrityError):
                    with session.begin_nested():
                        session.add(WorkflowRun(run_id=uuid4(), thread_id=uuid4(), project_id=uuid4(), attempt_no=1, status="queued"))
                        session.flush()
                with pytest.raises(IntegrityError):
                    with session.begin_nested():
                        session.add(WorkflowRun(run_id=uuid4(), thread_id=first.thread_id, project_id=p2.id, attempt_no=1, status="queued"))
                        session.flush()
                session.commit()  # Releases a savepoint only; outer transaction remains open.
                assert outer.is_active
            with TestClient(app) as client:
                base = f"/api/projects/{p1.id}"
                response = client.get(f"{base}/workflows/{first.thread_id}")
                assert response.status_code == 200 and response.json()["latest_run_id"] == str(second.run_id)
                assert response.json()["status"] == "running"
                detail = client.get(f"{base}/workflow-runs/{first.run_id}")
                assert detail.status_code == 200 and detail.json()["status"] == "waiting_human"
                assert client.get(f"/api/projects/{p2.id}/workflows/{first.thread_id}").status_code == 404
                assert client.get(f"/api/projects/{p2.id}/workflow-runs/{first.run_id}").status_code == 404
                assert client.get(f"{base}/workflow-runs/not-a-uuid").status_code == 422
            assert outer.is_active
        finally:
            if outer.is_active:
                outer.rollback()
            event.remove(engine, "before_cursor_execute", guard)
    with engine.connect() as check:
        assert not check.execute(select(PaperProject.id).where(PaperProject.id.in_(project_ids))).all()
        assert not check.execute(select(WorkflowRun.run_id).where(WorkflowRun.run_id.in_(run_ids))).all()
    assert not any(s.startswith(("DELETE ", "DROP ", "TRUNCATE ", "ALTER ", "CREATE ")) for s in statements)
