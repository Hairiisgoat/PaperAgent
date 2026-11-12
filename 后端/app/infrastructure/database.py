"""Lazy database engine and request-scoped session management."""

import logging
from collections.abc import Iterator
from functools import lru_cache

from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.exc import DBAPIError, DisconnectionError, SQLAlchemyError, TimeoutError
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import get_database_settings

logger = logging.getLogger(__name__)
_SessionFactory = sessionmaker(autoflush=False, expire_on_commit=False)


class DatabaseAccessError(RuntimeError):
    """Sanitized database connectivity failure."""


class DatabaseOperationError(RuntimeError):
    """Sanitized unexpected database operation failure."""


# MySQL connection and transport error codes. Unknown errors default to 500.
_CONNECTION_ERROR_CODES = frozenset({1040, 1042, 1043, 1045, 1158, 1159, 1160, 1161, 1203, 2002, 2003, 2005, 2006, 2012, 2013, 2026, 2055, 4031})


def _is_connection_failure(error: SQLAlchemyError) -> bool:
    if isinstance(error, (TimeoutError, DisconnectionError)):
        return True
    if isinstance(error, DBAPIError):
        args = getattr(error.orig, "args", ())
        return bool(error.connection_invalidated or (args and args[0] in _CONNECTION_ERROR_CODES))
    return False


@lru_cache(maxsize=1)
def get_engine() -> Engine:
    """Create one engine per process, without opening a connection."""
    return create_engine(
        get_database_settings().url,
        pool_size=5, max_overflow=5, pool_timeout=30, pool_recycle=1800,
        pool_pre_ping=True, connect_args={"connect_timeout": 5},
        echo=False, hide_parameters=True,
    )


def SessionLocal() -> Session:
    """Create a session bound to the process-wide engine."""
    return _SessionFactory(bind=get_engine())


def _rollback(session: Session) -> None:
    try:
        session.rollback()
    except SQLAlchemyError:
        # Do not mask the original exception or log database error details.
        logger.warning("Database session rollback failed")


def get_db() -> Iterator[Session]:
    """Yield a session; callers explicitly commit. Roll back on failure and always close."""
    session = None
    try:
        session = SessionLocal()
        yield session
    except SQLAlchemyError as error:
        if session is not None:
            _rollback(session)
        if _is_connection_failure(error):
            raise DatabaseAccessError("Database unavailable") from None
        raise DatabaseOperationError("Database operation failed") from None
    except BaseException:
        if session is not None:
            _rollback(session)
        raise
    finally:
        if session is not None:
            try:
                session.close()
            except SQLAlchemyError:
                logger.warning("Database session close failed")
