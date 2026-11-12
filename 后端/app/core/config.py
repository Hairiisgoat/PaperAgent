"""Database settings loaded from the backend .env and process environment."""

import os
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

from dotenv import dotenv_values
from sqlalchemy.engine import URL

ENV_FILE = Path(__file__).resolve().parents[2] / ".env"
_KEYS = ("MYSQL_HOST", "MYSQL_PORT", "MYSQL_DATABASE", "MYSQL_USER", "MYSQL_PASSWORD")


class ConfigurationError(ValueError):
    """A required setting is missing or invalid; values are never included."""


@dataclass(frozen=True)
class DatabaseSettings:
    host: str
    port: int
    database: str
    user: str
    password: str = field(repr=False)

    @property
    def url(self) -> URL:
        return URL.create(
            "mysql+pymysql", username=self.user, password=self.password,
            host=self.host, port=self.port, database=self.database,
            query={"charset": "utf8mb4"},
        )


def load_database_settings(env_file: Path = ENV_FILE) -> DatabaseSettings:
    # Process environment has precedence. Do not mutate os.environ or interpolate secrets.
    file_values = dotenv_values(env_file, interpolate=False, encoding="utf-8-sig")
    values = {key: os.environ.get(key, file_values.get(key)) for key in _KEYS}
    missing = [key for key, value in values.items() if value is None or not value.strip()]
    if missing:
        raise ConfigurationError("Missing database configuration: " + ", ".join(missing))
    try:
        port = int(values["MYSQL_PORT"])
        if not 1 <= port <= 65535:
            raise ValueError
    except (TypeError, ValueError):
        raise ConfigurationError("MYSQL_PORT must be an integer from 1 to 65535") from None
    return DatabaseSettings(
        host=values["MYSQL_HOST"], port=port, database=values["MYSQL_DATABASE"],
        user=values["MYSQL_USER"], password=values["MYSQL_PASSWORD"],
    )


@lru_cache(maxsize=1)
def get_database_settings() -> DatabaseSettings:
    return load_database_settings()
