"""SQLAlchemy operations for paper projects; business decisions belong to services."""

from datetime import datetime
from uuid import UUID

from sqlalchemy import bindparam, func, select, text
from sqlalchemy.orm import Session

from app.models import PaperProject


class ProjectRepository:
    def __init__(self, session: Session):
        self.session = session

    def add(self, project: PaperProject) -> None:
        self.session.add(project)
        self.session.flush()

    def get(self, project_id: UUID, *, exclude_status: str | None = None, for_update: bool = False) -> PaperProject | None:
        statement = select(PaperProject).where(PaperProject.id == project_id)
        if exclude_status is not None:
            statement = statement.where(PaperProject.lifecycle_status != exclude_status)
        if for_update:
            statement = statement.with_for_update()
        return self.session.scalar(statement)

    def list(self, *, lifecycle_status: str | None, offset: int, limit: int) -> tuple[list[PaperProject], int]:
        condition = PaperProject.lifecycle_status.in_(("active", "archived"))
        if lifecycle_status is not None:
            condition = PaperProject.lifecycle_status == lifecycle_status
        total = self.session.scalar(select(func.count()).select_from(PaperProject).where(condition)) or 0
        stmt = select(PaperProject).where(condition).order_by(PaperProject.created_at.desc(), PaperProject.id.desc()).offset(offset).limit(limit)
        return list(self.session.scalars(stmt)), total

    def mark_deleted(self, project_id: UUID, deleted_at: datetime) -> None:
        # Explicit SQL prevents ORM's updated_at onupdate from changing any other column.
        stmt = text("UPDATE paper_projects SET lifecycle_status = :status, deleted_at = :deleted_at WHERE id = :id").bindparams(
            bindparam("id", type_=PaperProject.__table__.c.id.type),
            bindparam("deleted_at", type_=PaperProject.__table__.c.deleted_at.type),
        )
        self.session.execute(stmt, {"status": "deleted", "deleted_at": deleted_at, "id": project_id})
