"""Project-scoped workflow state and human approval endpoints."""
from typing import Annotated
from uuid import UUID
import logging

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from sqlalchemy.orm import Session

from app.infrastructure.database import get_db
from app.schemas.project import ErrorResponse
from app.schemas.workflow_run import WorkflowRunResponse, WorkflowStatusResponse
from app.schemas.workflow_approval import ApprovalSubmission, ApprovalSubmissionResponse, WorkflowApprovalResponse
from app.services.workflow_approval import ApprovalUnavailable, WorkflowApprovalService
from app.services.workflow_run import WorkflowRunService
from app.services.workflow_resume import queue_approval, run_resume_attempt

router = APIRouter(prefix="/api/projects/{project_id}", tags=["workflows"])
logger = logging.getLogger(__name__)
Db = Annotated[Session, Depends(get_db)]
errors = {404: {"model": ErrorResponse, "description": "Project or workflow not found"},
          503: {"model": ErrorResponse, "description": "Database unavailable"}}


@router.get("/workflows/{thread_id}", response_model=WorkflowStatusResponse, responses=errors)
def get_workflow(project_id: UUID, thread_id: UUID, db: Db) -> WorkflowStatusResponse:
    run = WorkflowRunService(db).latest(project_id, thread_id)
    return WorkflowStatusResponse(project_id=run.project_id, thread_id=run.thread_id, latest_run_id=run.run_id,
                                  status=run.status, current_stage=run.current_stage, updated_at=run.updated_at,
                                  error_code=run.error_code, retryable=run.retryable)


@router.get("/workflow-runs/{run_id}", response_model=WorkflowRunResponse, responses=errors)
def get_workflow_run(project_id: UUID, run_id: UUID, db: Db) -> WorkflowRunResponse:
    return WorkflowRunResponse.model_validate(WorkflowRunService(db).get(project_id, run_id))


@router.get("/workflow-runs", response_model=list[WorkflowRunResponse], responses=errors)
def list_workflow_runs(project_id: UUID, db: Db) -> list[WorkflowRunResponse]:
    return [WorkflowRunResponse.model_validate(run) for run in WorkflowRunService(db).list_project(project_id)]


@router.get("/workflow-runs/{run_id}/approval", response_model=WorkflowApprovalResponse, response_model_exclude_none=True,
            responses={**errors, 409: {"model": ErrorResponse, "description": "Approval unavailable or inconsistent"}})
def get_workflow_approval(project_id: UUID, run_id: UUID, db: Db) -> WorkflowApprovalResponse:
    try:
        return WorkflowApprovalService(db).get(project_id, run_id)
    except ApprovalUnavailable:
        raise HTTPException(status_code=503, detail="Approval storage unavailable") from None


@router.post("/workflow-runs/{run_id}/approval", status_code=202, response_model=ApprovalSubmissionResponse,
             responses={**errors, 409: {"model": ErrorResponse, "description": "Approval unavailable or inconsistent"}})
def submit_workflow_approval(project_id: UUID, run_id: UUID, submission: ApprovalSubmission,
                             background: BackgroundTasks, db: Db) -> ApprovalSubmissionResponse:
    try:
        response, event, task = queue_approval(db, project_id, run_id, submission)
        if event is None:
            return response
        # Register before commit: a registration error rolls back the queued attempt.
        try:
            background.add_task(run_resume_attempt, *task)
        except Exception:
            logger.error("Workflow resume scheduling failed before commit")
            raise HTTPException(status_code=503, detail="Workflow scheduling unavailable") from None
        from app.services.workflow_events import EventService
        EventService(db).commit_and_notify(event)
        return response
    except ApprovalUnavailable:
        raise HTTPException(status_code=503, detail="Approval storage unavailable") from None


@router.post("/workflow-runs/{run_id}/writing-retry", status_code=202, response_model=WorkflowRunResponse,
             responses={**errors, 409: {"model": ErrorResponse, "description": "Writing retry unavailable"}})
def retry_writing(project_id: UUID, run_id: UUID, background: BackgroundTasks, db: Db) -> WorkflowRunResponse:
    from app.services.writing_retry import queue_writing_retry, run_writing_retry
    from app.services.workflow_events import EventService
    run, event = queue_writing_retry(db, project_id, run_id)
    response = WorkflowRunResponse.model_validate(run)
    if event is not None:
        background.add_task(run_writing_retry, project_id, run.run_id)
        EventService(db).commit_and_notify(event)
    return response


@router.post('/workflow-runs/{run_id}/segmented-retry', status_code=202, response_model=WorkflowRunResponse,
             responses={**errors, 409: {'model': ErrorResponse, 'description': 'Writing retry unavailable'}})
def retry_segmented_writing(project_id: UUID, run_id: UUID, background: BackgroundTasks, db: Db) -> WorkflowRunResponse:
    from app.services.segmented_writing import queue_segmented_retry, run_segmented_retry
    from app.services.workflow_events import EventService
    run, event = queue_segmented_retry(db, project_id, run_id)
    response = WorkflowRunResponse.model_validate(run)
    if event is not None:
        background.add_task(run_segmented_retry, project_id, run.run_id)
        EventService(db).commit_and_notify(event)
    return response
