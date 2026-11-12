from contextlib import nullcontext
from pathlib import Path
import runpy
from unittest.mock import MagicMock

import pytest
from alembic import context
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.exc import OperationalError

from app.core.config import ConfigurationError, DatabaseSettings, load_database_settings
from app.infrastructure import database
from app.main import database_error_handler
from app.models.base import Base

KEYS = ("MYSQL_HOST", "MYSQL_PORT", "MYSQL_DATABASE", "MYSQL_USER", "MYSQL_PASSWORD")


@pytest.fixture
def clean_env(monkeypatch):
    for key in KEYS:
        monkeypatch.delenv(key, raising=False)


def test_load_settings_safe_url_and_environment_override(tmp_path, monkeypatch, clean_env):
    secret = "abc@:/%# $literal"
    path = tmp_path / ".env"
    path.write_text('MYSQL_HOST=local\nMYSQL_PORT=3306\nMYSQL_DATABASE=paper_agent\n'
                    'MYSQL_USER=app\nMYSQL_PASSWORD="' + secret + '"\n', encoding="utf-8")
    monkeypatch.setenv("MYSQL_HOST", "override")
    settings = load_database_settings(path)
    assert settings.host == "override"
    assert settings.password == secret
    assert settings.url.password == secret
    assert secret not in repr(settings)
    assert secret not in str(settings.url)
    assert "***" in str(settings.url)


def test_invalid_config_does_not_echo_values(tmp_path, clean_env):
    path = tmp_path / ".env"
    path.write_text("MYSQL_HOST=localhost\nMYSQL_PORT=secretbadport\nMYSQL_DATABASE=db\n"
                    "MYSQL_USER=app\nMYSQL_PASSWORD=secretvalue\n", encoding="utf-8")
    with pytest.raises(ConfigurationError) as exc:
        load_database_settings(path)
    assert "MYSQL_PORT" in str(exc.value)
    assert "secret" not in str(exc.value)


def test_missing_config_lists_keys_not_values(tmp_path, clean_env):
    with pytest.raises(ConfigurationError) as exc:
        load_database_settings(tmp_path / ".env")
    assert "MYSQL_PASSWORD" in str(exc.value)


def test_engine_is_cached_and_pool_configured(monkeypatch):
    settings = DatabaseSettings("127.0.0.1", 3306, "paper_agent", "app", "secret")
    monkeypatch.setattr(database, "get_database_settings", lambda: settings)
    database.get_engine.cache_clear()
    try:
        engine = database.get_engine()
        assert engine is database.get_engine()
        assert engine.url.drivername == "mysql+pymysql"
        assert engine.pool._pre_ping is True
        assert engine.pool.size() == 5
        assert engine.hide_parameters is True
        assert "secret" not in str(engine.url)
    finally:
        engine.dispose()
        database.get_engine.cache_clear()


def test_session_success_closes_without_commit(monkeypatch):
    session = MagicMock()
    monkeypatch.setattr(database, "SessionLocal", lambda: session)
    dependency = database.get_db()
    assert next(dependency) is session
    with pytest.raises(StopIteration):
        next(dependency)
    session.commit.assert_not_called()
    session.close.assert_called_once()


def test_session_error_rolls_back_and_closes(monkeypatch):
    session = MagicMock()
    monkeypatch.setattr(database, "SessionLocal", lambda: session)
    dependency = database.get_db()
    next(dependency)
    with pytest.raises(ValueError, match="business error"):
        dependency.throw(ValueError("business error"))
    session.rollback.assert_called_once()
    session.close.assert_called_once()
    session.commit.assert_not_called()


def test_database_failure_is_sanitized_and_returns_503(monkeypatch):
    session = MagicMock()
    monkeypatch.setattr(database, "SessionLocal", lambda: session)
    app = FastAPI()
    app.add_exception_handler(database.DatabaseAccessError, database_error_handler)

    @app.get("/test")
    def failing_route(_db=Depends(database.get_db)):
        raise OperationalError("secret SQL", {}, Exception(2003, "password secret"))

    with TestClient(app) as client:
        response = client.get("/test")
    assert response.status_code == 503
    assert response.json() == {"detail": "Database unavailable"}
    assert "secret" not in response.text
    session.rollback.assert_called_once()
    session.close.assert_called_once()


def test_session_factory_failure_is_sanitized(monkeypatch):
    def fail():
        raise OperationalError("url with secret", {}, Exception(2003, "secret"))
    monkeypatch.setattr(database, "SessionLocal", fail)
    with pytest.raises(database.DatabaseAccessError) as exc:
        next(database.get_db())
    assert "secret" not in str(exc.value)


def test_alembic_offline_uses_shared_metadata_and_url(monkeypatch):
    settings = DatabaseSettings("localhost", 3306, "paper_agent", "app", "a@b%")
    configure = MagicMock()
    run = MagicMock()
    monkeypatch.setattr("app.core.config.get_database_settings", lambda: settings)
    monkeypatch.setattr(context, "is_offline_mode", lambda: True, raising=False)
    monkeypatch.setattr(context, "configure", configure, raising=False)
    monkeypatch.setattr(context, "begin_transaction", lambda: nullcontext(), raising=False)
    monkeypatch.setattr(context, "run_migrations", run, raising=False)
    runpy.run_path(str(Path(__file__).resolve().parents[2] / "alembic" / "env.py"))
    kwargs = configure.call_args.kwargs
    assert kwargs["target_metadata"] is Base.metadata
    assert kwargs["url"] == settings.url.render_as_string(hide_password=False)
    run.assert_called_once()


def test_alembic_online_uses_same_engine_and_metadata(monkeypatch):
    from app.infrastructure import database as db_module
    engine = MagicMock()
    connection = MagicMock()
    engine.connect.return_value = nullcontext(connection)
    configure = MagicMock()
    run = MagicMock()
    monkeypatch.setattr(db_module, "get_engine", lambda: engine)
    monkeypatch.setattr(context, "is_offline_mode", lambda: False, raising=False)
    monkeypatch.setattr(context, "configure", configure, raising=False)
    monkeypatch.setattr(context, "begin_transaction", lambda: nullcontext(), raising=False)
    monkeypatch.setattr(context, "run_migrations", run, raising=False)
    runpy.run_path(str(Path(__file__).resolve().parents[2] / "alembic" / "env.py"))
    assert configure.call_args.kwargs["connection"] is connection
    assert configure.call_args.kwargs["target_metadata"] is Base.metadata
    run.assert_called_once()


def test_real_session_rolls_back_read_transaction_on_error(monkeypatch):
    from sqlalchemy import create_engine, text
    from sqlalchemy.orm import Session
    engine = create_engine("sqlite:///:memory:")
    session = Session(engine)
    monkeypatch.setattr(database, "SessionLocal", lambda: session)
    try:
        dependency = database.get_db()
        next(dependency)
        assert session.execute(text("SELECT 1")).scalar_one() == 1
        assert session.in_transaction()
        with pytest.raises(ValueError):
            dependency.throw(ValueError("abort"))
        assert not session.in_transaction()
    finally:
        engine.dispose()
