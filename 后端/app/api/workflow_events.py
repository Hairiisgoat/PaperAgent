"""Mock start and resumable server-sent workflow events."""
import asyncio
import logging
import time
import anyio
from queue import Empty
from typing import Annotated, Literal
from uuid import UUID, uuid4

from fastapi import APIRouter, BackgroundTasks, Depends, Header, HTTPException, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.infrastructure import database
from app.infrastructure.database import get_db
from app.models.paper_project import utc_now
from app.schemas.workflow_event import HeartbeatEvent, HeartbeatPayload, ProgressPayload, WorkflowEvent
from app.services.mock_start import run_mock_attempt
from app.services.workflow_events import EventService, broker
from app.services.workflow_run import WorkflowRunService
from app.core.config import ConfigurationError
from app.core.llm_config import load_llm_settings
from app.models.planner_generation import PlannerGeneration

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/projects/{project_id}", tags=["workflow events"])
Db = Annotated[Session, Depends(get_db)]


class StartRunResponse(BaseModel):
    project_id: UUID
    thread_id: UUID
    run_id: UUID
    status: Literal["queued"]


class ClosingStreamingResponse(StreamingResponse):
    """Close the generator even if ASGI disconnects while sending a yielded chunk."""
    async def __call__(self, scope, receive, send):
        try:
            async with anyio.create_task_group() as group:
                async def stream():
                    try:
                        await self.stream_response(send)
                    finally:
                        group.cancel_scope.cancel()
                async def disconnect():
                    try:
                        await self.listen_for_disconnect(receive)
                    finally:
                        group.cancel_scope.cancel()
                group.start_soon(stream)
                group.start_soon(disconnect)
        finally:
            await self.body_iterator.aclose()


@router.post("/runs", status_code=status.HTTP_202_ACCEPTED, response_model=StartRunResponse)
def start_mock(project_id: UUID, background: BackgroundTasks, db: Db) -> StartRunResponse:
    runs, events = WorkflowRunService(db), EventService(db)
    run = runs.create(project_id)
    run.current_stage = "planning"
    evt = events.prepare(project_id, run.run_id, "progress", stage="planning", agent_name=None, status="queued",
        payload=ProgressPayload(stage="planning", agent_name=None, status="queued", message="Workflow queued"))
    response = StartRunResponse(project_id=project_id, thread_id=run.thread_id, run_id=run.run_id, status="queued")
    events.commit_and_notify(evt)
    try:
        background.add_task(run_mock_attempt, project_id, run.run_id)
    except Exception:
        logger.error("Initial run scheduling failed after commit; startup recovery required: project_id=%s run_id=%s", project_id, run.run_id)
        raise HTTPException(status_code=503, detail="Workflow scheduling unavailable") from None
    return response


@router.post("/planner-runs", status_code=status.HTTP_202_ACCEPTED, response_model=StartRunResponse)
def start_planner(project_id: UUID, background: BackgroundTasks, db: Db) -> StartRunResponse:
    runs, events = WorkflowRunService(db), EventService(db)
    project = runs.scope.require_project(project_id, action="edit")
    if project.paper_type != "course_paper" or project.requirements not in (None, {}):
        raise HTTPException(status_code=422, detail="Planner supports course papers without requirements only")
    if len(project.topic) > 4000 or len(project.topic.encode("utf-8")) > 16384:
        raise HTTPException(status_code=422, detail="Project topic is too long")
    try:
        settings = load_llm_settings()
    except ConfigurationError:
        raise HTTPException(status_code=503, detail="Model configuration unavailable") from None
    run = runs.create(project_id, graph_version="planner_real_v1")
    run.current_stage = "planning"
    db.add(PlannerGeneration(generation_id=uuid4(), project_id=project_id, thread_id=run.thread_id,
        planner_run_id=run.run_id, plan_revision=1, input_snapshot={"title": project.title, "topic": project.topic,
        "paper_type": project.paper_type, "citation_style": project.citation_style, "requirements": project.requirements},
        model=settings.model, max_tokens=2048, call_id=uuid4(), status="prepared", usage_complete=False))
    evt = events.prepare(project_id, run.run_id, "progress", stage="planning", agent_name=None, status="queued",
        payload=ProgressPayload(stage="planning", agent_name=None, status="queued", message="Workflow queued"))
    response = StartRunResponse(project_id=project_id, thread_id=run.thread_id, run_id=run.run_id, status="queued")
    events.commit_and_notify(evt)
    try:
        background.add_task(run_mock_attempt, project_id, run.run_id)
    except Exception:
        logger.error("Initial Planner scheduling failed after commit; recovery required")
        raise HTTPException(status_code=503, detail="Workflow scheduling unavailable") from None
    return response


def encode_sse(event: WorkflowEvent) -> str:
    # Only persisted events advance EventSource's durable Last-Event-ID.
    prefix = f"id: {event.sequence}\n" if event.sequence is not None else ""
    return f"{prefix}event: {event.event_type}\ndata: {event.model_dump_json()}\n\n"


@router.get("/workflow-runs/{run_id}/events")
def stream_events(project_id: UUID, run_id: UUID, db: Db,
                  last_event_id: Annotated[str | None, Header(alias="Last-Event-ID")] = None) -> StreamingResponse:
    service = EventService(db)
    run = service.require_run(project_id, run_id)
    if last_event_id is not None and (not last_event_id.isascii() or not last_event_id.isdecimal() or len(last_event_id) > 10):
        raise HTTPException(status_code=422, detail="Invalid event cursor")
    cursor = int(last_event_id) if last_event_id is not None else 0
    if not service.cursor_exists(project_id, run_id, cursor):
        raise HTTPException(status_code=422, detail="Invalid event cursor")
    initial_stage, initial_status = run.current_stage, run.status
    db.close()  # Streaming must not hold a request-scoped DB connection indefinitely.

    def load_after(sequence: int) -> list[WorkflowEvent]:
        with database.SessionLocal() as session:
            reader = EventService(session)
            reader.require_run(project_id, run_id)
            return reader.since(project_id, run_id, sequence)

    async def generate():
        sub = broker.subscribe(project_id, run_id)
        last = cursor
        last_stage, last_status = initial_stage, initial_status
        heartbeat_at = time.monotonic() + 3
        try:
            # StreamingResponse owns ASGI disconnect handling and cancels this generator.
            while True:
                if sub.overflow.is_set():
                    return  # Client reconnects from last delivered durable cursor.
                # Subscribe before querying, and query durable events before each transient.
                pending = await asyncio.to_thread(load_after, last)
                if pending:
                    for event in pending:
                        last = event.sequence
                        last_stage, last_status = event.stage, event.status
                        yield encode_sse(event)
                    continue
                try:
                    event = sub.queue.get_nowait()
                    if event.sequence is None:
                        yield encode_sse(event)
                    # Durable notifications are hints; ordered replay is read from MySQL.
                    continue
                except Empty:
                    pass
                if time.monotonic() >= heartbeat_at:
                    now = utc_now()
                    heartbeat = HeartbeatEvent(event_id=uuid4(), run_id=run_id, project_id=project_id,
                        stage=last_stage, agent_name=None, status=last_status, timestamp=now,
                        payload=HeartbeatPayload(timestamp=now))
                    yield encode_sse(heartbeat)
                    heartbeat_at = time.monotonic() + 3
                await asyncio.sleep(0.25)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.warning("Workflow event stream failed")
        finally:
            broker.unsubscribe(project_id, run_id, sub)
    return ClosingStreamingResponse(generate(), media_type="text/event-stream",
        headers={"Cache-Control": "no-cache, no-transform", "X-Accel-Buffering": "no"})
