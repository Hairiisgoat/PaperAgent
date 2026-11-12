"""Alembic environment using the application settings and metadata."""

from alembic import context
from sqlalchemy.exc import SQLAlchemyError

from app.core.config import get_database_settings
from app.infrastructure.database import get_engine
from app.models.base import Base
import app.models  # Register future ORM models here before autogenerate.

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    context.configure(
        url=get_database_settings().url.render_as_string(hide_password=False),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    try:
        with get_engine().connect() as connection:
            context.configure(connection=connection, target_metadata=target_metadata, compare_type=True)
            with context.begin_transaction():
                context.run_migrations()
    except SQLAlchemyError:
        raise RuntimeError("Database connection or migration failed") from None


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
