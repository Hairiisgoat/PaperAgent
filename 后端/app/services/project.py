"""Project use cases and lifecycle transitions."""

from uuid import UUID

from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.models import PaperProject
from app.models.paper_project import utc_now
from app.repositories.project import ProjectRepository
from app.schemas.project import ProjectCreate, ProjectUpdate
from app.services.project_scope import ProjectConflict, ProjectNotFound, ProjectScope


class ProjectService:
    def __init__(self, session: Session):
        self.session = session
        self.repository = ProjectRepository(session)
        self.scope = ProjectScope(session, self.repository)

    def _commit(self) -> None:
        try:
            self.session.commit()
        except SQLAlchemyError:
            self.session.rollback()
            raise

    def create(self, data: ProjectCreate) -> PaperProject:
        project = PaperProject(**data.model_dump())
        self.repository.add(project)
        self._commit()
        return project

    def list(self, *, lifecycle_status: str | None, page: int, page_size: int) -> tuple[list[PaperProject], int]:
        return self.repository.list(lifecycle_status=lifecycle_status, offset=(page - 1) * page_size, limit=page_size)

    def get(self, project_id: UUID) -> PaperProject:
        return self.scope.require_project(project_id, action="read")

    def update(self, project_id: UUID, data: ProjectUpdate) -> PaperProject:
        project = self.scope.require_project(project_id, action="edit")
        for key, value in data.model_dump(exclude_unset=True).items():
            setattr(project, key, value)
        self._commit()
        return project

    def archive(self, project_id: UUID) -> PaperProject:
        project = self.scope.require_project(project_id, action="archive")
        project.lifecycle_status = "archived"
        project.archived_at = utc_now()
        self._commit()
        return project

    def restore(self, project_id: UUID) -> PaperProject:
        project = self.scope.require_project(project_id, action="restore")
        project.lifecycle_status = "active"
        project.archived_at = None
        self._commit()
        return project

    def delete(self, project_id: UUID) -> None:
        project = self.scope.require_project(project_id, action="delete")
        self.repository.mark_deleted(project.id, utc_now())
        self._commit()
