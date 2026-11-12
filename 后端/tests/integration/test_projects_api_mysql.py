"""Opt-in API test with savepoints and an outer rollback; never deletes data."""

import os
from uuid import UUID

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import event, select, text
from sqlalchemy.orm import sessionmaker

from app.core.config import get_database_settings
from app.infrastructure import database
from app.main import app
from app.models import PaperProject

pytestmark = pytest.mark.skipif(os.getenv("RUN_MYSQL_INTEGRATION") != "1", reason="Requires explicit opt-in")


def test_mysql_api_lifecycle_in_rolled_back_transaction(monkeypatch):
    settings = get_database_settings()
    assert (settings.host, settings.port, settings.database, settings.user) == ("127.0.0.1", 3306, "paper_agent", "paper_agent_app")
    engine = database.get_engine()
    statements = []
    ids = []

    def capture(_conn, _cursor, statement, _parameters, _context, _executemany):
        statements.append(statement.lstrip().upper())
        if statement.lstrip().upper().startswith(("DELETE ", "DROP ", "TRUNCATE ", "ALTER ", "CREATE ")):
            raise AssertionError("Unexpected destructive or DDL statement")

    with engine.connect() as connection:
        assert connection.execute(text("SELECT DATABASE()")).scalar_one() == "paper_agent"
        assert connection.execute(text("SELECT CURRENT_USER()")).scalar_one() == "paper_agent_app@127.0.0.1"
        connection.rollback()
        outer = connection.begin()
        factory = sessionmaker(bind=connection, autoflush=False, expire_on_commit=False, join_transaction_mode="create_savepoint")
        monkeypatch.setattr(database, "SessionLocal", factory)
        event.listen(engine, "before_cursor_execute", capture)
        try:
            with TestClient(app) as client:
                from app.repositories.project import ProjectRepository
                original_add = ProjectRepository.add
                def add_invalid(self, project):
                    project.lifecycle_status = "invalid"
                    original_add(self, project)
                monkeypatch.setattr(ProjectRepository, "add", add_invalid)
                failure = client.post("/api/projects", json={"title": "invalid", "topic": "test", "citation_style": "APA"})
                assert failure.status_code == 500 and failure.json() == {"detail": "Internal server error"}
                assert outer.is_active
                monkeypatch.setattr(ProjectRepository, "add", original_add)
                response = client.post("/api/projects", json={"title": "隔离测试", "topic": "研究主题", "citation_style": "APA", "requirements": {"words": 1800}})
                assert response.status_code == 201, response.text
                project_id = UUID(response.json()["id"])
                ids.append(project_id)
                url = f"/api/projects/{project_id}"
                assert client.get(url).json()["requirements"] == {"words": 1800}
                listing = client.get("/api/projects", params={"lifecycle_status": "active", "page_size": 100})
                assert listing.status_code == 200 and any(item["id"] == str(project_id) for item in listing.json()["items"])
                assert client.patch(url, json={"title": "修改后"}).json()["title"] == "修改后"
                assert client.post(url + "/archive").json()["lifecycle_status"] == "archived"
                assert client.patch(url, json={"title": "不可修改"}).status_code == 409
                assert client.post(url + "/restore").json()["lifecycle_status"] == "active"
                before = connection.execute(select(PaperProject.__table__).where(PaperProject.id == project_id)).mappings().one()
                snapshot = dict(before)
                assert client.delete(url).status_code == 204
                assert client.get(url).status_code == 404
                assert client.post(url + "/restore").status_code == 404
                after = dict(connection.execute(select(PaperProject.__table__).where(PaperProject.id == project_id)).mappings().one())
                assert after["lifecycle_status"] == "deleted" and after["deleted_at"] is not None
                for key in snapshot.keys() - {"lifecycle_status", "deleted_at"}:
                    assert snapshot[key] == after[key], f"soft delete unexpectedly changed {key}"
            assert outer.is_active, "Test transaction unexpectedly ended"
        finally:
            if outer.is_active:
                outer.rollback()
            event.remove(engine, "before_cursor_execute", capture)
    with engine.connect() as verify:
        for project_id in ids:
            assert verify.execute(select(PaperProject.id).where(PaperProject.id == project_id)).scalar_one_or_none() is None
    assert not any(statement.startswith(("DELETE ", "DROP ", "TRUNCATE ", "ALTER ", "CREATE ")) for statement in statements)
