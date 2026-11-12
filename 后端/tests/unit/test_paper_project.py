from datetime import UTC, datetime, timedelta, timezone
from uuid import UUID, uuid4

import pytest
from sqlalchemy.dialects import mysql

from app.models import PaperProject
from app.models.base import Base
from app.models.paper_project import UTCDateTime


def test_model_instantiation_and_metadata_registration():
    project_id = uuid4()
    project = PaperProject(id=project_id, title="研究项目", topic="研究主题", citation_style="APA")
    assert project.id == project_id and isinstance(project.id, UUID)
    assert project.title == "研究项目"
    assert Base.metadata.tables["paper_projects"] is PaperProject.__table__
    assert set(PaperProject.__table__.columns.keys()) == {
        "id", "user_id", "title", "paper_type", "topic", "requirements", "citation_style",
        "lifecycle_status", "progress_status", "created_at", "updated_at", "archived_at", "deleted_at",
    }


def test_model_defaults_and_constraints():
    table = PaperProject.__table__
    assert table.c.id.default.is_callable
    assert table.c.paper_type.default.arg == "course_paper"
    assert table.c.lifecycle_status.default.arg == "active"
    assert table.c.progress_status.default.arg == "not_started"
    assert not table.c.title.nullable and not table.c.topic.nullable
    assert table.c.user_id.nullable and table.c.requirements.nullable
    assert table.c.archived_at.nullable and table.c.deleted_at.nullable
    assert table.c.updated_at.onupdate.is_callable
    assert {constraint.name for constraint in table.constraints if constraint.name} == {
        "ck_paper_projects_paper_type", "ck_paper_projects_lifecycle_status", "ck_paper_projects_progress_status"
    }


def test_datetime_converts_to_utc_and_rejects_naive():
    typ = UTCDateTime()
    dialect = mysql.dialect()
    local = datetime(2026, 11, 3, 20, 30, tzinfo=timezone(timedelta(hours=8)))
    stored = typ.process_bind_param(local, dialect)
    assert stored == datetime(2026, 11, 3, 12, 30)
    assert typ.process_result_value(stored, dialect) == datetime(2026, 11, 3, 12, 30, tzinfo=UTC)
    assert typ.process_bind_param(None, dialect) is None
    with pytest.raises(ValueError, match="timezone"):
        typ.process_bind_param(datetime(2026, 11, 3), dialect)
