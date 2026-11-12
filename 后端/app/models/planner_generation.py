"""Durable, project-scoped Planner intent and per-call usage ledger."""
from datetime import datetime
from uuid import UUID

from sqlalchemy import Boolean, CheckConstraint, ForeignKey, Index, Integer, JSON, String, UniqueConstraint, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base
from app.models.paper_project import UTCDateTime, utc_now


class PlannerGeneration(Base):
    __tablename__ = "planner_generations"
    __table_args__ = (
        UniqueConstraint("project_id", "thread_id", "plan_revision", name="uq_planner_revision"),
        UniqueConstraint("call_id", name="uq_planner_call"),
        UniqueConstraint("planner_run_id", name="uq_planner_run"),
        CheckConstraint("plan_revision >= 1", name="ck_planner_revision"),
        CheckConstraint("status IN ('prepared','invoking','tasks_persisted','failed','needs_review')", name="ck_planner_status"),
        Index("ix_planner_project_thread", "project_id", "thread_id"),
    )
    generation_id: Mapped[UUID] = mapped_column(Uuid(native_uuid=False), primary_key=True)
    project_id: Mapped[UUID] = mapped_column(Uuid(native_uuid=False), ForeignKey("paper_projects.id"), nullable=False)
    thread_id: Mapped[UUID] = mapped_column(Uuid(native_uuid=False), nullable=False)
    planner_run_id: Mapped[UUID] = mapped_column(Uuid(native_uuid=False), ForeignKey("workflow_runs.run_id"), nullable=False)
    plan_revision: Mapped[int] = mapped_column(Integer, nullable=False)
    input_snapshot: Mapped[dict] = mapped_column(JSON, nullable=False)
    model: Mapped[str] = mapped_column(String(100), nullable=False)
    max_tokens: Mapped[int] = mapped_column(Integer, nullable=False)
    call_id: Mapped[UUID] = mapped_column(Uuid(native_uuid=False), nullable=False)
    status: Mapped[str] = mapped_column(String(24), nullable=False)
    task_count: Mapped[int | None] = mapped_column(Integer)
    prompt_tokens: Mapped[int | None] = mapped_column(Integer)
    completion_tokens: Mapped[int | None] = mapped_column(Integer)
    usage_complete: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    error_code: Mapped[str | None] = mapped_column(String(32))
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False, default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False, default=utc_now, onupdate=utc_now)
