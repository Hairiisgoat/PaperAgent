"""Real MySQL events under an outer transaction, rolled back without DELETE."""
import os
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import event, inspect, select, text
from sqlalchemy.orm import sessionmaker

from app.core.config import get_database_settings
from app.infrastructure import database
from app.main import app
from app.models import PaperProject, WorkflowEventRecord, WorkflowRun
from app.models.workflow_approval_decision import WorkflowApprovalDecision
from sqlalchemy.exc import IntegrityError
from app.graph.approval_graph import build_approval_graph
from app.graph.checkpointer import sqlite_checkpointer
from app.schemas.workflow_event import ProgressPayload
from app.services.project_scope import ResourceNotFound
from app.services.workflow_events import EventBroker, EventService
from app.services.workflow_run import WorkflowRunService
from app.services.workflow_resume import queue_approval, run_resume_attempt
from app.services.mock_start import run_mock_attempt
from app.services.workflow_recovery import RecoveryScheduler
from app.schemas.workflow_approval import ApprovalSubmission

pytestmark = pytest.mark.skipif(os.getenv("RUN_MYSQL_INTEGRATION") != "1", reason="Explicit opt-in required")


def test_mysql_events_and_mock_start_outer_transaction(monkeypatch, tmp_path):
    s = get_database_settings()
    assert (s.host, s.port, s.database, s.user) == ("127.0.0.1", 3306, "paper_agent", "paper_agent_app")
    monkeypatch.setenv("LANGGRAPH_SQLITE_PATH", str(tmp_path / "checkpoint.sqlite"))
    monkeypatch.setenv("LANGSMITH_TRACING", "false")
    monkeypatch.setenv("LANGCHAIN_TRACING_V2", "false")
    engine = database.get_engine()
    ids, event_ids, run_ids = [], [], []
    statements = []
    def guard(_conn, _cursor, statement, _params, _ctx, _many):
        sql = statement.lstrip().upper()
        statements.append(sql)
        if sql.startswith(("DELETE ", "DROP ", "TRUNCATE ", "ALTER ", "CREATE ")):
            raise AssertionError("Forbidden DDL or physical delete")
    with engine.connect() as conn:
        assert conn.execute(text("SELECT DATABASE(), CURRENT_USER()")).one() == ("paper_agent", "paper_agent_app@127.0.0.1")
        inspector = inspect(conn)
        assert set(c["name"] for c in inspector.get_columns("workflow_events")) == set(WorkflowEventRecord.__table__.columns.keys())
        assert any(i["unique"] and i["column_names"] == ["run_id", "sequence"] for i in inspector.get_indexes("workflow_events"))
        assert len(inspector.get_foreign_keys("workflow_events")) == 2
        conn.rollback()
        outer = conn.begin()
        factory = sessionmaker(bind=conn, autoflush=False, expire_on_commit=False, join_transaction_mode="create_savepoint")
        monkeypatch.setattr(database, "SessionLocal", factory)
        event.listen(engine, "before_cursor_execute", guard)
        try:
            with factory() as session:
                p = PaperProject(id=uuid4(), title="隔离事件测试", topic="test", citation_style="APA")
                other = PaperProject(id=uuid4(), title="隔离范围测试", topic="test", citation_style="APA")
                session.add_all([p, other]); session.flush()
                ids.extend([p.id, other.id])
                run = WorkflowRunService(session).create(p.id)
                run_ids.append(run.run_id)
                run.current_stage = "planning"
                broker = EventBroker()
                events = EventService(session, broker)
                payload = ProgressPayload(stage="planning", agent_name=None, status="queued", message="Workflow queued")
                one = events.prepare(p.id, run.run_id, "progress", stage="planning", agent_name=None, status="queued", payload=payload)
                events.commit_and_notify(one)
                two = events.prepare(p.id, run.run_id, "progress", stage="planning", agent_name=None, status="queued", payload=payload)
                events.commit_and_notify(two)
                event_ids.extend([one.event_id, two.event_id])
                assert [one.sequence, two.sequence] == [1, 2]
                assert [e.sequence for e in events.since(p.id, run.run_id, 1)] == [2]
                with pytest.raises(ResourceNotFound):
                    events.prepare(other.id, run.run_id, "progress", stage="planning", agent_name=None, status="queued", payload=payload)
                session.commit()
            assert outer.is_active
            with TestClient(app) as client:
                response = client.post(f"/api/projects/{p.id}/runs")
                assert response.status_code == 202
                data = response.json()
                detail = client.get(f"/api/projects/{p.id}/workflow-runs/{data['run_id']}")
                assert detail.status_code == 200 and detail.json()["status"] == "waiting_human"
                run_ids.append(UUID(data["run_id"]))
                with sqlite_checkpointer(tmp_path / "checkpoint.sqlite", read_only=True) as saver:
                    graph = build_approval_graph().compile(checkpointer=saver)
                    before = graph.get_state({"configurable": {"thread_id": data["thread_id"]}})
                approval = client.get(f"/api/projects/{p.id}/workflow-runs/{data['run_id']}/approval")
                assert approval.status_code == 200 and approval.json()["item_ids"] == before.values["tasks"]
                assert approval.json()["checkpoint_id"] == before.config["configurable"]["checkpoint_id"]
                with sqlite_checkpointer(tmp_path / "checkpoint.sqlite", read_only=True) as saver:
                    after = build_approval_graph().compile(checkpointer=saver).get_state({"configurable": {"thread_id": data["thread_id"]}})
                assert before.next == after.next == ("task_approval",) and before.config == after.config
                assert client.get(f"/api/projects/{other.id}/workflow-runs/{data['run_id']}/approval").status_code == 404
                assert client.get(f"/api/projects/{other.id}/workflow-runs/{data['run_id']}/events").status_code == 404
                resumed = client.post(f"/api/projects/{p.id}/workflow-runs/{data['run_id']}/approval", json={
                    "decision": "approve", "approved_ids": approval.json()["item_ids"][:1]})
                assert resumed.status_code == 202, resumed.text
                resumed_data = resumed.json()
                run_ids.append(UUID(resumed_data["run_id"]))
                assert resumed_data["thread_id"] == data["thread_id"] and resumed_data["attempt_no"] == 2
                assert client.get(f"/api/projects/{p.id}/workflow-runs/{resumed_data['run_id']}").json()["status"] == "waiting_human"
                assert client.get(f"/api/projects/{p.id}/workflow-runs/{resumed_data['run_id']}/approval").json()["approval_type"] == "source_approval"
                assert client.post(f"/api/projects/{p.id}/workflow-runs/{data['run_id']}/approval", json={"decision": "reject"}).status_code == 409
                again = client.post(f"/api/projects/{p.id}/workflow-runs/{data['run_id']}/approval", json={
                    "decision": "approve", "approved_ids": approval.json()["item_ids"][:1]})
                assert again.status_code == 202 and again.json() == resumed_data
                with factory() as check:
                    intent = check.get(WorkflowApprovalDecision, UUID(data["run_id"]))
                    assert intent.run_id == UUID(resumed_data["run_id"])
                    assert intent.thread_id == UUID(data["thread_id"]) and intent.execution_status == "finished"
                    assert intent.approved_ids == approval.json()["item_ids"][:1]
                    with pytest.raises(IntegrityError):
                        with check.begin_nested():
                            check.execute(WorkflowApprovalDecision.__table__.insert().values(
                                previous_run_id=intent.previous_run_id, run_id=run.run_id,
                                project_id=p.id, thread_id=intent.thread_id, checkpoint_id=intent.checkpoint_id,
                                approval_type="task_approval", decision="reject", approved_ids=[],
                                execution_status="queued", submitted_at=intent.submitted_at))
                    resumed_events = EventService(check).since(p.id, UUID(resumed_data["run_id"]), 0)
                    assert [e.sequence for e in resumed_events] == [1, 2, 3]
                    assert [e.event_type for e in resumed_events] == ["progress", "progress", "approval_required"]
                    assert [e.sequence for e in EventService(check).since(p.id, UUID(resumed_data["run_id"]), 1)] == [2, 3]
                    event_ids.extend(e.event_id for e in resumed_events)
                pending_start = client.post(f"/api/projects/{p.id}/runs").json()
                pending_old = UUID(pending_start["run_id"])
                run_ids.append(pending_old)
                with factory() as isolated:
                    pending, pending_event, _ = queue_approval(isolated, p.id, pending_old,
                        ApprovalSubmission(decision="reject"))
                    EventService(isolated).commit_and_notify(pending_event)
                    run_ids.append(pending.run_id)
                    assert isolated.get(WorkflowRun, pending.run_id).status == "queued"
                run_resume_attempt(p.id, pending.run_id)
                with factory() as check:
                    assert check.get(WorkflowRun, pending.run_id).status == "cancelled"
                    assert check.get(WorkflowApprovalDecision, pending_old).execution_status == "finished"
                    event_ids.extend(e.event_id for e in EventService(check).since(p.id, pending.run_id, 0))
                    event_ids.extend(e.event_id for e in EventService(check).since(p.id, pending_old, 0))
                with factory() as check:
                    initial = WorkflowRunService(check).create(p.id)
                    initial.current_stage = "planning"
                    queued = EventService(check).prepare(p.id, initial.run_id, "progress", stage="planning",
                        agent_name=None, status="queued", payload=ProgressPayload(stage="planning", agent_name=None,
                        status="queued", message="Workflow queued"))
                    EventService(check).commit_and_notify(queued)
                    initial_id, initial_thread = initial.run_id, initial.thread_id
                    run_ids.append(initial_id)
                scheduler = RecoveryScheduler()
                scheduled = []
                monkeypatch.setattr(scheduler, "submit_initial", lambda project, run_id: scheduled.append((project, run_id)))
                scheduler.scan()
                scheduler.scan()
                scheduler.close()
                assert scheduled.count((p.id, initial_id)) == 2
                run_mock_attempt(p.id, initial_id)
                run_mock_attempt(p.id, initial_id)
                with factory() as check:
                    assert check.get(WorkflowRun, initial_id).status == "waiting_human"
                    initial_events = EventService(check).since(p.id, initial_id, 0)
                    assert [e.sequence for e in initial_events] == [1, 2, 3]
                    event_ids.extend(e.event_id for e in initial_events)
                with sqlite_checkpointer(tmp_path / "checkpoint.sqlite", read_only=True) as saver:
                    snap = build_approval_graph().compile(checkpointer=saver).get_state({"configurable": {"thread_id": str(initial_thread)}})
                    assert snap.next == ("task_approval",) and snap.metadata["paperagent_run_id"] == str(initial_id)
            with factory() as session:
                rows = EventService(session).since(p.id, UUID(data["run_id"]), 0)
                assert [e.event_type for e in rows] == ["progress", "progress", "approval_required"]
                assert [e.sequence for e in rows] == [1, 2, 3]
                event_ids.extend(e.event_id for e in rows)
            assert outer.is_active
        finally:
            if outer.is_active:
                outer.rollback()
            event.remove(engine, "before_cursor_execute", guard)
    with engine.connect() as conn:
        assert not conn.execute(select(PaperProject.id).where(PaperProject.id.in_(ids))).all()
        assert not conn.execute(select(WorkflowEventRecord.event_id).where(WorkflowEventRecord.event_id.in_(event_ids))).all()
        assert not conn.execute(select(WorkflowRun.run_id).where(WorkflowRun.run_id.in_(run_ids))).all()
        assert not conn.execute(select(WorkflowApprovalDecision.previous_run_id).where(WorkflowApprovalDecision.project_id.in_(ids))).all()
    assert not any(s.startswith(("DELETE ", "DROP ", "TRUNCATE ", "ALTER ", "CREATE ")) for s in statements)
