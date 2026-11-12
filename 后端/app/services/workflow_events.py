"""Durable event transactions and bounded single-process notifications."""
import logging
from queue import Full, Queue
from threading import Event, Lock
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session
from app.infrastructure import database
from app.models.paper_project import utc_now
from app.models.workflow_event import WorkflowEventRecord
from app.repositories.workflow_run import WorkflowRunRepository
from app.schemas.workflow_event import WorkflowEvent, event_adapter
from app.services.project_scope import ProjectScope, ResourceNotFound

logger = logging.getLogger(__name__)
_DURABLE = frozenset({"progress", "approval_required", "completed", "error"})


class Subscription:
    def __init__(self, capacity: int):
        self.queue = Queue(maxsize=capacity)
        self.overflow = Event()


class EventBroker:
    def __init__(self, capacity: int = 64):
        self.capacity = capacity
        self._lock = Lock()
        self._subscribers: dict[tuple[UUID, UUID], set[Subscription]] = {}

    def subscribe(self, project_id: UUID, run_id: UUID) -> Subscription:
        sub = Subscription(self.capacity)
        with self._lock:
            self._subscribers.setdefault((project_id, run_id), set()).add(sub)
        return sub

    def unsubscribe(self, project_id: UUID, run_id: UUID, sub: Subscription) -> None:
        with self._lock:
            key = (project_id, run_id)
            subscribers = self._subscribers.get(key)
            if subscribers is not None:
                subscribers.discard(sub)
                if not subscribers:
                    self._subscribers.pop(key, None)

    def notify(self, event: WorkflowEvent) -> None:
        with self._lock:
            for sub in tuple(self._subscribers.get((event.project_id, event.run_id), ())):
                try:
                    sub.queue.put_nowait(event)
                except Full:
                    sub.overflow.set()


broker = EventBroker()


def from_record(record: WorkflowEventRecord) -> WorkflowEvent:
    return event_adapter.validate_python({
        "event_id": record.event_id, "sequence": record.sequence, "run_id": record.run_id,
        "project_id": record.project_id, "event_type": record.event_type,
        "stage": record.stage, "agent_name": record.agent_name, "status": record.status,
        "timestamp": record.created_at, "payload": record.payload,
    })


class EventService:
    def __init__(self, session: Session, event_broker: EventBroker = broker):
        self.session, self.broker = session, event_broker
        self.scope, self.runs = ProjectScope(session), WorkflowRunRepository(session)

    def require_run(self, project_id: UUID, run_id: UUID, *, lock: bool = False):
        self.scope.require_project(project_id, action="read")
        run = self.runs.get(project_id, run_id, for_update=lock)
        if run is None:
            raise ResourceNotFound("Resource not found")
        return run

    def prepare(self, project_id: UUID, run_id: UUID, event_type: str, *, stage: str | None,
                agent_name: str | None, status: str, payload: object) -> WorkflowEvent:
        """Add durable event to caller transaction; run row lock serializes sequence."""
        run = self.require_run(project_id, run_id, lock=True)
        if event_type not in _DURABLE:
            raise ValueError("Expected durable event")
        if run.status != status or run.current_stage != stage:
            raise ValueError("Event status and stage must match run")
        last = self.session.scalar(select(WorkflowEventRecord.sequence).where(
            WorkflowEventRecord.project_id == project_id, WorkflowEventRecord.run_id == run_id
        ).order_by(WorkflowEventRecord.sequence.desc()).limit(1).with_for_update())
        sequence = (last or 0) + 1
        event = event_adapter.validate_python({"event_id": uuid4(), "sequence": sequence, "run_id": run_id,
            "project_id": project_id, "event_type": event_type, "stage": stage, "agent_name": agent_name,
            "status": status, "timestamp": utc_now(), "payload": payload})
        self.session.add(WorkflowEventRecord(event_id=event.event_id, run_id=run_id, project_id=project_id,
            sequence=sequence, event_type=event.event_type, stage=event.stage, agent_name=event.agent_name,
            status=event.status, payload=event.payload.model_dump(mode="json"), created_at=event.timestamp))
        self.session.flush()
        return event

    def commit_and_notify(self, event: WorkflowEvent) -> None:
        """Commit caller's transaction before notifying. Never rerun a graph on broadcast errors."""
        self.session.commit()
        try:
            self.broker.notify(event)
        except Exception:
            logger.warning("Workflow event notification failed")

    def publish_transient(self, project_id: UUID, run_id: UUID, event_type: str, *, stage: str | None,
                          agent_name: str | None, status: str, payload: object) -> WorkflowEvent:
        # Use an independent session: never announce a run state that only exists
        # in the producer's uncommitted transaction.
        with database.SessionLocal() as read_session:
            run = EventService(read_session, self.broker).require_run(project_id, run_id)
            if event_type not in {"text_delta", "heartbeat"} or run.status != status or run.current_stage != stage:
                raise ValueError("Invalid transient event")
        event = event_adapter.validate_python({"event_id": uuid4(), "sequence": None, "run_id": run_id,
            "project_id": project_id, "event_type": event_type, "stage": stage, "agent_name": agent_name,
            "status": status, "timestamp": utc_now(), "payload": payload})
        try:
            self.broker.notify(event)
        except Exception:
            logger.warning("Workflow event notification failed")
        return event

    def since(self, project_id: UUID, run_id: UUID, sequence: int, limit: int = 100) -> list[WorkflowEvent]:
        rows = self.session.scalars(select(WorkflowEventRecord).where(
            WorkflowEventRecord.project_id == project_id, WorkflowEventRecord.run_id == run_id,
            WorkflowEventRecord.sequence > sequence).order_by(WorkflowEventRecord.sequence).limit(limit))
        return [from_record(row) for row in rows]

    def cursor_exists(self, project_id: UUID, run_id: UUID, sequence: int) -> bool:
        if sequence == 0:
            return True
        return self.session.scalar(select(WorkflowEventRecord.event_id).where(
            WorkflowEventRecord.project_id == project_id, WorkflowEventRecord.run_id == run_id,
            WorkflowEventRecord.sequence == sequence)) is not None
