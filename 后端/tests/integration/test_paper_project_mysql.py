"""Opt-in tests use only the dedicated database; test rows are rolled back."""

import os
from datetime import UTC, datetime, timedelta, timezone
from uuid import UUID

import pytest
from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.migration import MigrationContext
from sqlalchemy import event, inspect, select, text
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from app.core.config import get_database_settings
from app.infrastructure.database import get_engine
from app.models import PaperProject
from app.models.base import Base

pytestmark = pytest.mark.skipif(os.getenv("RUN_MYSQL_INTEGRATION") != "1", reason="Requires explicit opt-in")


@pytest.fixture
def connection():
    s = get_database_settings()
    assert (s.host, s.port, s.database, s.user) == ("127.0.0.1", 3306, "paper_agent", "paper_agent_app")
    with get_engine().connect() as connection:
        assert connection.execute(text("SELECT DATABASE()")).scalar_one() == "paper_agent"
        assert connection.execute(text("SELECT CURRENT_USER()")).scalar_one() == "paper_agent_app@127.0.0.1"
        connection.rollback()
        yield connection


def test_mysql_schema_matches_metadata(connection):
    inspector = inspect(connection)
    assert set(inspector.get_table_names()) == {"alembic_version", "paper_projects", "workflow_runs", "workflow_events", "workflow_approval_decisions", "planner_generations", "workflow_tasks"}
    assert connection.execute(text("SELECT version_num FROM alembic_version")).scalar_one() == "d52a7c81f103"
    assert inspector.get_pk_constraint("paper_projects")["constrained_columns"] == ["id"]
    columns = {col["name"]: col for col in inspector.get_columns("paper_projects")}
    assert set(columns) == set(PaperProject.__table__.columns.keys())
    assert str(columns["id"]["type"]) == "CHAR(32)"
    assert str(columns["requirements"]["type"]) == "JSON"
    assert columns["created_at"]["type"].fsp == 6
    assert {item["name"] for item in inspector.get_check_constraints("paper_projects")} == {
        "ck_paper_projects_paper_type", "ck_paper_projects_lifecycle_status", "ck_paper_projects_progress_status"
    }
    indexes = inspector.get_indexes("paper_projects")
    assert any(index["column_names"] == ["user_id", "lifecycle_status", "updated_at"] for index in indexes)
    differences = compare_metadata(MigrationContext.configure(connection, opts={"compare_type": True, "compare_server_default": True}), Base.metadata)
    assert differences == []


def test_model_defaults_utc_and_rollback(connection):
    with Session(bind=connection) as session:
        try:
            start = datetime.now(UTC) - timedelta(days=1)
            local = datetime(2026, 11, 3, 20, 30, tzinfo=timezone(timedelta(hours=8)))
            project = PaperProject(title="事务测试", topic="测试主题", citation_style="APA", requirements={"words": 2000}, created_at=start, updated_at=start, archived_at=local)
            session.add(project)
            session.flush()
            project_id = project.id
            assert isinstance(project_id, UUID)
            assert (project.paper_type, project.lifecycle_status, project.progress_status) == ("course_paper", "active", "not_started")
            session.refresh(project)
            assert project.archived_at == local.astimezone(UTC)
            assert project.requirements == {"words": 2000}
            assert project.created_at.tzinfo == UTC
            project.title = "更新测试"
            session.flush()
            session.refresh(project)
            assert project.updated_at > start
        finally:
            session.rollback()
    assert connection.execute(select(PaperProject.id).where(PaperProject.id == project_id)).scalar_one_or_none() is None


def test_invalid_status_is_rejected(connection):
    with Session(bind=connection) as session:
        try:
            session.add(PaperProject(title="test", topic="test", citation_style="APA", lifecycle_status="invalid"))
            with pytest.raises(OperationalError) as exc:
                session.flush()
            assert exc.value.orig.args[0] == 3819
        finally:
            session.rollback()


def test_upgrade_head_again_does_not_execute_ddl(connection):
    assert connection.execute(text("SELECT version_num FROM alembic_version")).scalar_one() == "d52a7c81f103"
    connection.rollback()
    statements = []
    engine = get_engine()
    def capture(_conn, _cursor, statement, _parameters, _context, _executemany):
        statements.append(statement.lstrip().upper())
    event.listen(engine, "before_cursor_execute", capture)
    try:
        command.upgrade(Config("alembic.ini"), "head")
    finally:
        event.remove(engine, "before_cursor_execute", capture)
    assert not any(statement.startswith(("CREATE ", "ALTER ", "DROP ", "TRUNCATE ")) for statement in statements)
    assert connection.execute(text("SELECT version_num FROM alembic_version")).scalar_one() == "d52a7c81f103"
