"""Opt-in, read-only integration test against the dedicated PaperAgent database."""

import os
import pytest
from sqlalchemy import text

from app.core.config import get_database_settings
from app.infrastructure.database import get_engine, SessionLocal

pytestmark = pytest.mark.skipif(
    os.getenv("RUN_MYSQL_INTEGRATION") != "1", reason="Set RUN_MYSQL_INTEGRATION=1 explicitly"
)


def test_read_only_mysql_connection_and_session():
    settings = get_database_settings()
    assert (settings.host, settings.port, settings.database, settings.user) == (
        "127.0.0.1", 3306, "paper_agent", "paper_agent_app"
    ), "Integration tests only run against the dedicated local database and account"
    with get_engine().connect() as connection:
        assert connection.execute(text("SELECT 1")).scalar_one() == 1
        assert connection.execute(text("SELECT DATABASE()")).scalar_one() == "paper_agent"
        assert connection.execute(text("SELECT CURRENT_USER()")).scalar_one() == "paper_agent_app@127.0.0.1"
    with SessionLocal() as session:
        assert session.execute(text("SELECT 1")).scalar_one() == 1
