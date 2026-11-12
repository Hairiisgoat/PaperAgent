"""Opt-in Planner constraints on the dedicated MySQL database; rollback all rows."""
import os
from uuid import uuid4

import pytest
from sqlalchemy import inspect, select, text
from sqlalchemy.exc import IntegrityError, OperationalError
from sqlalchemy.orm import Session

from app.core.config import get_database_settings
from app.infrastructure.database import get_engine
from app.models import PaperProject, PlannerGeneration, WorkflowRun, WorkflowTask

pytestmark = pytest.mark.skipif(os.getenv("RUN_MYSQL_INTEGRATION") != "1", reason="Explicit opt-in required")


def test_planner_mysql_schema_and_rollback():
    settings = get_database_settings()
    assert (settings.host, settings.port, settings.database, settings.user) == ("127.0.0.1", 3306, "paper_agent", "paper_agent_app")
    engine = get_engine()
    with engine.connect() as connection:
        assert connection.execute(text("SELECT DATABASE(), CURRENT_USER()")).one() == ("paper_agent", "paper_agent_app@127.0.0.1")
        inspector = inspect(connection)
        for model in (PlannerGeneration, WorkflowTask, WorkflowRun):
            assert {c["name"] for c in inspector.get_columns(model.__tablename__)} == set(model.__table__.columns.keys())
        assert {fk["referred_table"] for fk in inspector.get_foreign_keys("planner_generations")} == {"paper_projects", "workflow_runs"}
        assert {fk["referred_table"] for fk in inspector.get_foreign_keys("workflow_tasks")} == {"planner_generations"}
        assert {i["name"] for i in inspector.get_indexes("planner_generations") if i["unique"]} >= {"uq_planner_revision", "uq_planner_call", "uq_planner_run"}
        assert any(i["name"] == "uq_workflow_task_position" and i["unique"] for i in inspector.get_indexes("workflow_tasks"))
        connection.rollback()
        outer = connection.begin()
        project_id, run_id, thread_id, generation_id = uuid4(), uuid4(), uuid4(), uuid4()
        try:
            with Session(bind=connection, join_transaction_mode="create_savepoint") as session:
                session.add(PaperProject(id=project_id, title="隔离测试", topic="隔离测试", citation_style="APA"))
                session.flush()
                session.add(WorkflowRun(run_id=run_id, project_id=project_id, thread_id=thread_id, attempt_no=1,
                                        status="queued", graph_version="planner_real_v1"))
                session.flush()
                session.add(PlannerGeneration(generation_id=generation_id, project_id=project_id, thread_id=thread_id,
                    planner_run_id=run_id, plan_revision=1, input_snapshot={"topic": "隔离测试"}, model="mock",
                    max_tokens=2048, call_id=uuid4(), status="prepared", usage_complete=False))
                session.flush()
                session.add(WorkflowTask(task_id=uuid4(), generation_id=generation_id, position=1,
                                         title="测试", description="测试", expected_output="测试"))
                session.flush()
                with pytest.raises(IntegrityError):
                    with session.begin_nested():
                        session.add(WorkflowTask(task_id=uuid4(), generation_id=generation_id, position=1,
                                                 title="重复", description="重复", expected_output="重复"))
                        session.flush()
                with pytest.raises(IntegrityError):
                    with session.begin_nested():
                        session.add(WorkflowTask(task_id=uuid4(), generation_id=uuid4(), position=1,
                                                 title="越界", description="越界", expected_output="越界"))
                        session.flush()
                with pytest.raises(OperationalError):
                    with session.begin_nested():
                        session.execute(text("UPDATE planner_generations SET status='invalid' WHERE generation_id=:id"),
                                        {"id": generation_id.hex})
                session.commit()
                assert outer.is_active
        finally:
            outer.rollback()
        assert connection.execute(select(PlannerGeneration.generation_id).where(PlannerGeneration.generation_id == generation_id)).scalar_one_or_none() is None
