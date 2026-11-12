"""Shared query for resources with both resource and project identifiers."""

from typing import TypeVar
from uuid import UUID

from sqlalchemy import inspect, select
from sqlalchemy.orm import Session

T = TypeVar("T")


def get_scoped_resource(session: Session, model: type[T], project_id: UUID, resource_id: UUID, *, for_update: bool) -> T | None:
    mapper = inspect(model)
    if "id" not in mapper.columns or "project_id" not in mapper.columns:
        raise ValueError("Project resources must map id and project_id columns")
    stmt = select(model).where(model.id == resource_id, model.project_id == project_id)
    if for_update:
        stmt = stmt.with_for_update()
    return session.scalar(stmt)
