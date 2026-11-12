"""API behavior using an isolated in-memory database; no external services."""

from datetime import UTC
from unittest.mock import MagicMock
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.infrastructure import database
from app.main import app
from app.models.base import Base


@pytest.fixture
def client(monkeypatch):
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    monkeypatch.setattr(database, "SessionLocal", factory)
    with TestClient(app) as client:
        yield client
    engine.dispose()


def create(client, **changes):
    data = {"title": "论文项目", "topic": "研究主题", "citation_style": "APA"} | changes
    response = client.post("/api/projects", json=data)
    assert response.status_code == 201, response.text
    return response.json()


def test_create_details_defaults_and_openapi(client):
    project = create(client, requirements={"word_count": 2000})
    assert (project["paper_type"], project["lifecycle_status"], project["progress_status"]) == ("course_paper", "active", "not_started")
    assert project["user_id"] is None and project["requirements"] == {"word_count": 2000}
    assert project["created_at"].endswith("Z")
    response = client.get(f"/api/projects/{project['id']}")
    assert response.status_code == 200 and response.json()["id"] == project["id"]
    paths = client.get("/openapi.json").json()["paths"]
    assert set(paths["/api/projects"]) == {"get", "post"}
    assert set(paths["/api/projects/{project_id}"]) == {"get", "patch", "delete"}
    assert "/api/projects/{project_id}/archive" in paths and "/api/projects/{project_id}/restore" in paths


@pytest.mark.parametrize("body", [
    {}, {"title": " ", "topic": "t", "citation_style": "APA"},
    {"title": "t", "topic": "x", "citation_style": "APA", "user_id": str(uuid4())},
    {"title": "t", "topic": "x", "citation_style": "APA", "paper_type": "bad"},
    {"title": "t", "topic": "x", "citation_style": "APA", "requirements": []},
    {"title": "t", "topic": "x"},
    {"title": "t", "topic": "x", "citation_style": "APA", "lifecycle_status": "deleted"},
])
def test_invalid_create_is_422(client, body):
    assert client.post("/api/projects", json=body).status_code == 422


def test_pagination_and_filters(client):
    projects = [create(client, title=f"论文{i}") for i in range(3)]
    assert client.post(f"/api/projects/{projects[0]['id']}/archive").status_code == 200
    first = client.get("/api/projects", params={"page": 1, "page_size": 2}).json()
    second = client.get("/api/projects", params={"page": 2, "page_size": 2}).json()
    assert first["total"] == 3 and len(first["items"]) == 2 and len(second["items"]) == 1
    assert {p["id"] for p in first["items"]}.isdisjoint({p["id"] for p in second["items"]})
    assert client.get("/api/projects", params={"lifecycle_status": "archived"}).json()["total"] == 1
    assert client.get("/api/projects", params={"lifecycle_status": "deleted"}).status_code == 422
    assert client.get("/api/projects", params={"page": 0}).status_code == 422


def test_update_states_and_soft_delete(client):
    p = create(client, requirements={"k": 1})
    url = f"/api/projects/{p['id']}"
    edited = client.patch(url, json={"title": "新题目", "requirements": None})
    assert edited.status_code == 200 and edited.json()["title"] == "新题目" and edited.json()["requirements"] is None
    for payload in ({}, {"title": None}, {"lifecycle_status": "deleted"}, {"id": str(uuid4())}, {"created_at": "2020-01-01"}):
        assert client.patch(url, json=payload).status_code == 422
    assert client.post(url + "/restore").status_code == 409
    archived = client.post(url + "/archive")
    assert archived.status_code == 200 and archived.json()["archived_at"] is not None
    assert client.post(url + "/archive").status_code == 409
    assert client.patch(url, json={"title": "不可修改"}).status_code == 409
    restored = client.post(url + "/restore")
    assert restored.status_code == 200 and restored.json()["archived_at"] is None
    prior = client.get(url).json()
    assert client.delete(url).status_code == 204
    assert client.get(url).status_code == 404
    assert client.post(url + "/restore").status_code == 404
    assert client.delete(url).status_code == 404
    assert client.get("/api/projects").json()["total"] == 0


def test_missing_invalid_uuid_and_delete_archived(client):
    url = f"/api/projects/{uuid4()}"
    assert client.get(url).status_code == 404
    assert client.patch(url, json={"title": "x"}).status_code == 404
    assert client.post(url + "/archive").status_code == 404
    assert client.delete(url).status_code == 404
    assert client.get("/api/projects/not-a-uuid").status_code == 422
    project = create(client)
    url = f"/api/projects/{project['id']}"
    assert client.post(url + "/archive").status_code == 200
    assert client.delete(url).status_code == 204


def test_database_error_returns_503_and_rolls_back(monkeypatch):
    session = MagicMock()
    session.flush.side_effect = OperationalError("secret", {}, Exception(2003, "secret"))
    monkeypatch.setattr(database, "SessionLocal", lambda: session)
    with TestClient(app) as client:
        response = client.post("/api/projects", json={"title": "x", "topic": "y", "citation_style": "APA"})
    assert response.status_code == 503 and "secret" not in response.text
    session.rollback.assert_called()
    session.close.assert_called_once()
    session.commit.assert_not_called()


def test_commit_failure_rolls_back_and_returns_503(monkeypatch):
    session = MagicMock()
    session.commit.side_effect = OperationalError("statement", {}, Exception(2003, "secret"))
    monkeypatch.setattr(database, "SessionLocal", lambda: session)
    with TestClient(app) as client:
        response = client.post("/api/projects", json={"title": "x", "topic": "y", "citation_style": "APA"})
    assert response.status_code == 503 and "secret" not in response.text
    assert session.rollback.call_count >= 1
    session.close.assert_called_once()


def test_openapi_matches_patch_and_error_contract(client):
    doc = client.get("/openapi.json").json()
    patch = doc["components"]["schemas"]["ProjectUpdate"]
    for name in ("title", "topic", "paper_type", "citation_style"):
        assert "null" not in str(patch["properties"][name])
    assert "null" in str(patch["properties"]["requirements"])
    response = doc["paths"]["/api/projects/{project_id}"]["patch"]["responses"]
    assert "application/json" in response["409"]["content"]
    assert "ErrorResponse" in str(response["409"])


def test_case_insensitive_deleted_status_cannot_be_retrieved():
    from app.services.project import ProjectNotFound, ProjectService
    service = ProjectService(MagicMock())
    service.repository.get = MagicMock(return_value=MagicMock(lifecycle_status="DELETED"))
    with pytest.raises(ProjectNotFound):
        service.get(uuid4())


def test_pagination_tie_breaks_by_id(client):
    from datetime import datetime
    from uuid import UUID
    from app.models import PaperProject
    stamp = datetime(2026, 1, 1, tzinfo=UTC)
    with database.SessionLocal() as session:
        for value in (1, 3, 2):
            session.add(PaperProject(id=UUID(int=value), title="tie", topic="t", citation_style="APA", created_at=stamp, updated_at=stamp))
        session.commit()
    ids = []
    for page in (1, 2, 3):
        body = client.get("/api/projects", params={"page": page, "page_size": 1}).json()
        assert body["total"] == 3
        ids.append(UUID(body["items"][0]["id"]).int)
    assert ids == [3, 2, 1]


@pytest.mark.parametrize("error", [
    OperationalError("private SQL", {}, Exception(3819, "private constraint")),
    OperationalError("private SQL", {}, Exception(9999, "private driver message")),
])
def test_non_connection_database_error_is_safe_500(monkeypatch, error):
    session = MagicMock()
    session.flush.side_effect = error
    monkeypatch.setattr(database, "SessionLocal", lambda: session)
    with TestClient(app) as client:
        response = client.post("/api/projects", json={"title": "x", "topic": "y", "citation_style": "APA"})
    assert response.status_code == 500
    assert response.json() == {"detail": "Internal server error"}
    assert "private" not in response.text
    session.rollback.assert_called()
    session.close.assert_called_once()


def test_unexpected_non_database_error_is_safe_500(monkeypatch):
    from app.services.project import ProjectService
    session = MagicMock()
    monkeypatch.setattr(database, "SessionLocal", lambda: session)
    def fail(*args, **kwargs):
        raise RuntimeError("sensitive internal message")
    monkeypatch.setattr(ProjectService, "get", fail)
    with TestClient(app, raise_server_exceptions=False) as client:
        response = client.get(f"/api/projects/{uuid4()}")
    assert response.status_code == 500
    assert "sensitive" not in response.text
    session.rollback.assert_called()
    session.close.assert_called_once()


def test_archived_project_is_readable_but_not_editable(client):
    project = create(client)
    url = f"/api/projects/{project['id']}"
    assert client.post(url + "/archive").status_code == 200
    assert client.get(url).status_code == 200
    assert client.patch(url, json={"title": "no"}).status_code == 409
