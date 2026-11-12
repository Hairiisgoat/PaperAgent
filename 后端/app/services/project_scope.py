"""Project lifecycle policy and scoped single-resource retrieval."""

from typing import Literal, TypeVar
from uuid import UUID

from sqlalchemy.orm import Session

from app.models import PaperProject
from app.repositories.project import ProjectRepository
from app.repositories.project_scope import get_scoped_resource

ProjectAction = Literal["read", "edit", "archive", "restore", "delete"]
ResourceAccess = Literal["read", "write"]
T = TypeVar("T")


class ProjectNotFound(Exception):
    pass


class ProjectConflict(Exception):
    pass


class ResourceNotFound(Exception):
    pass


class ProjectScope:
    """Apply project policy within the same Session as the eventual resource access."""

    def __init__(self, session: Session, repository: ProjectRepository | None = None):
        self.session = session
        self.repository = repository if repository is not None else ProjectRepository(session)

    def require_project(self, project_id: UUID, *, action: ProjectAction) -> PaperProject:
        policies = {
            "read": ({"active", "archived"}, "Project state does not permit reading"),
            "edit": ({"active"}, "Archived projects must be restored before editing"),
            "archive": ({"active"}, "Only active projects can be archived"),
            "restore": ({"archived"}, "Only archived projects can be restored"),
            "delete": ({"active", "archived"}, "Project state does not permit deletion"),
        }
        if action not in policies:
            raise ValueError("Invalid project action")
        project = self.repository.get(project_id, exclude_status="deleted", for_update=action != "read")
        if project is None or project.lifecycle_status.casefold() == "deleted":
            raise ProjectNotFound("Project not found")
        states, message = policies[action]
        if project.lifecycle_status.casefold() not in states:
            raise ProjectConflict(message)
        return project

    def require_resource(self, model: type[T], project_id: UUID, resource_id: UUID, *, access: ResourceAccess) -> T:
        """Load a resource only after a parent check and a query scoped by both IDs.

        Writes lock the parent and resource in the same transaction. Repositories for
        future list/create/bulk operations must also include project_id themselves.
        """
        if access not in ("read", "write"):
            raise ValueError("Invalid resource access")
        self.require_project(project_id, action="edit" if access == "write" else "read")
        resource = get_scoped_resource(
            self.session, model, project_id, resource_id, for_update=access == "write"
        )
        if resource is None:
            raise ResourceNotFound("Resource not found")
        return resource
