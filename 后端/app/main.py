"""FastAPI application entry point."""

from contextlib import asynccontextmanager
import asyncio
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from app.infrastructure.database import DatabaseAccessError, DatabaseOperationError
from app.api.projects import router as projects_router
from app.api.workflow_runs import router as workflow_runs_router
from app.api.workflow_events import router as workflow_events_router
from app.api.writing import router as writing_router
from app.api.review import router as review_router
from app.services.project_scope import ProjectConflict, ProjectNotFound, ResourceNotFound
from app.services.workflow_resume import WritingStorageMissing
from app.infrastructure.approval_diagnostics import record_missing_writing_storage

@asynccontextmanager
async def lifespan(_app: FastAPI):
    from app.services.workflow_recovery import RecoveryScheduler
    scheduler = RecoveryScheduler()
    await asyncio.to_thread(scheduler.scan)
    try:
        yield
    finally:
        await asyncio.to_thread(scheduler.close)


app = FastAPI(title="PaperAgent", lifespan=lifespan)


@app.exception_handler(WritingStorageMissing)
def writing_storage_missing_handler(request: Request, _exc: WritingStorageMissing) -> JSONResponse:
    record_missing_writing_storage(request.path_params.get("project_id"), request.path_params.get("run_id"))
    return JSONResponse(status_code=503, content={"detail": "Writing storage unavailable", "code": "writing_storage_missing"})


@app.exception_handler(DatabaseAccessError)
def database_error_handler(_request: Request, _exc: DatabaseAccessError) -> JSONResponse:
    """Return a safe error without exposing driver or connection details."""
    return JSONResponse(status_code=503, content={"detail": "Database unavailable"})


@app.get("/health", tags=["health"])
def health_check() -> dict[str, str]:
    """Report whether the HTTP application can respond, not database health."""
    return {"status": "ok"}

app.include_router(projects_router)
app.include_router(workflow_runs_router)
app.include_router(workflow_events_router)
app.include_router(writing_router)
app.include_router(review_router)


@app.exception_handler(ProjectNotFound)
def project_not_found_handler(_request: Request, _exc: ProjectNotFound) -> JSONResponse:
    return JSONResponse(status_code=404, content={"detail": "Project not found"})


@app.exception_handler(ProjectConflict)
def project_conflict_handler(_request: Request, exc: ProjectConflict) -> JSONResponse:
    return JSONResponse(status_code=409, content={"detail": str(exc)})

@app.exception_handler(DatabaseOperationError)
def database_operation_error_handler(_request: Request, _exc: DatabaseOperationError) -> JSONResponse:
    return JSONResponse(status_code=500, content={"detail": "Internal server error"})


@app.exception_handler(ResourceNotFound)
def resource_not_found_handler(_request: Request, _exc: ResourceNotFound) -> JSONResponse:
    return JSONResponse(status_code=404, content={"detail": "Resource not found"})
