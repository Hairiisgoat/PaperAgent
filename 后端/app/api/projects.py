"""HTTP endpoints for project management."""

from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Response, status
from sqlalchemy.orm import Session

from app.infrastructure.database import get_db
from app.schemas.project import ErrorResponse, ProjectCreate, ProjectPage, ProjectResponse, ProjectUpdate
from app.services.project import ProjectService

router = APIRouter(prefix="/api/projects", tags=["projects"])
Db = Annotated[Session, Depends(get_db)]
error_responses = {404: {"model": ErrorResponse, "description": "Project not found"}, 409: {"model": ErrorResponse, "description": "Invalid project state"}, 503: {"model": ErrorResponse, "description": "Database unavailable"}}


@router.post("", response_model=ProjectResponse, status_code=status.HTTP_201_CREATED, responses={503: error_responses[503]})
def create_project(data: ProjectCreate, db: Db):
    return ProjectService(db).create(data)


@router.get("", response_model=ProjectPage, responses={503: error_responses[503]})
def list_projects(db: Db, page: Annotated[int, Query(ge=1)] = 1, page_size: Annotated[int, Query(ge=1, le=100)] = 20, lifecycle_status: Literal["active", "archived"] | None = None):
    items, total = ProjectService(db).list(lifecycle_status=lifecycle_status, page=page, page_size=page_size)
    return ProjectPage(items=items, total=total, page=page, page_size=page_size)


@router.get("/{project_id}", response_model=ProjectResponse, responses={404: error_responses[404], 503: error_responses[503]})
def get_project(project_id: UUID, db: Db):
    return ProjectService(db).get(project_id)


@router.patch("/{project_id}", response_model=ProjectResponse, responses=error_responses)
def update_project(project_id: UUID, data: ProjectUpdate, db: Db):
    return ProjectService(db).update(project_id, data)


@router.post("/{project_id}/archive", response_model=ProjectResponse, responses=error_responses)
def archive_project(project_id: UUID, db: Db):
    return ProjectService(db).archive(project_id)


@router.post("/{project_id}/restore", response_model=ProjectResponse, responses=error_responses)
def restore_project(project_id: UUID, db: Db):
    return ProjectService(db).restore(project_id)


@router.delete("/{project_id}", status_code=status.HTTP_204_NO_CONTENT, responses=error_responses)
def delete_project(project_id: UUID, db: Db):
    ProjectService(db).delete(project_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
