"""One immutable-identity record per workflow execution attempt."""
from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import Boolean, CheckConstraint, ForeignKey, Index, Integer, String, UniqueConstraint, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base
from app.models.paper_project import UTCDateTime, utc_now


class WorkflowRun(Base):
    __tablename__ = "workflow_runs"
    __table_args__ = (
        UniqueConstraint("thread_id", "attempt_no", name="uq_workflow_runs_thread_attempt"),
        CheckConstraint("attempt_no >= 1", name="ck_workflow_runs_attempt_no"),
        CheckConstraint("status IN ('queued','running','waiting_human','paused','failed','completed','cancelled')", name="ck_workflow_runs_status"),
        CheckConstraint("current_stage IS NULL OR current_stage IN ('planning','research','outline','writing','review','export')", name="ck_workflow_runs_stage"),
        CheckConstraint("error_code IS NULL OR error_code IN ('timeout','provider_unavailable','invalid_output','database_unavailable','budget_exceeded','internal_error','other')", name="ck_workflow_runs_error"),
        Index("ix_workflow_runs_project_thread_attempt", "project_id", "thread_id", "attempt_no"),
    )

    run_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True, native_uuid=False), primary_key=True, default=uuid4)
    thread_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True, native_uuid=False), nullable=False)
    graph_version: Mapped[str] = mapped_column(String(32), nullable=False, default="mock_v1", server_default="mock_v1")
    project_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True, native_uuid=False), ForeignKey("paper_projects.id"), nullable=False)
    attempt_no: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(24), nullable=False, default="queued")
    current_stage: Mapped[str | None] = mapped_column(String(24))
    started_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    finished_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    error_code: Mapped[str | None] = mapped_column(String(32))
    retryable: Mapped[bool | None] = mapped_column(Boolean)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False, default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False, default=utc_now, onupdate=utc_now)
