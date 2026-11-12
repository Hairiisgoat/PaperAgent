"""Durable workflow event records; transient events are never inserted."""
from datetime import datetime
from uuid import UUID
from sqlalchemy import CheckConstraint, ForeignKey, Index, Integer, JSON, String, UniqueConstraint, Uuid
from sqlalchemy.orm import Mapped, mapped_column
from app.models.base import Base
from app.models.paper_project import UTCDateTime


class WorkflowEventRecord(Base):
    __tablename__ = "workflow_events"
    __table_args__ = (
        UniqueConstraint("run_id", "sequence", name="uq_workflow_events_run_sequence"),
        CheckConstraint("sequence >= 1", name="ck_workflow_events_sequence"),
        CheckConstraint("event_type IN ('progress','approval_required','completed','error')", name="ck_workflow_events_type"),
        Index("ix_workflow_events_project_run_sequence", "project_id", "run_id", "sequence"),
    )
    event_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True, native_uuid=False), primary_key=True)
    run_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True, native_uuid=False), ForeignKey("workflow_runs.run_id"), nullable=False)
    project_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True, native_uuid=False), ForeignKey("paper_projects.id"), nullable=False)
    sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    event_type: Mapped[str] = mapped_column(String(32), nullable=False)
    stage: Mapped[str | None] = mapped_column(String(24))
    agent_name: Mapped[str | None] = mapped_column(String(24))
    status: Mapped[str] = mapped_column(String(24), nullable=False)
    payload: Mapped[dict] = mapped_column(JSON, nullable=False)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
