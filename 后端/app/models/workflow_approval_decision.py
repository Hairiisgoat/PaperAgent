"""Durable approval intent for exactly one workflow resume attempt."""
from datetime import datetime
from uuid import UUID
from sqlalchemy import CheckConstraint, ForeignKey, Index, JSON, String, UniqueConstraint, Uuid
from sqlalchemy.orm import Mapped, mapped_column
from app.models.base import Base
from app.models.paper_project import UTCDateTime, utc_now


class WorkflowApprovalDecision(Base):
    __tablename__ = "workflow_approval_decisions"
    __table_args__ = (
        UniqueConstraint("run_id", name="uq_approval_run"),
        CheckConstraint("approval_type IN ('task_approval','source_approval')", name="ck_approval_type"),
        CheckConstraint("decision IN ('approve','reject')", name="ck_approval_decision"),
        CheckConstraint("execution_status IN ('queued','running','finished','needs_review')", name="ck_approval_execution_status"),
        Index("ix_approval_execution_status", "execution_status"),
    )
    previous_run_id: Mapped[UUID] = mapped_column(Uuid(native_uuid=False), ForeignKey("workflow_runs.run_id"), primary_key=True)
    run_id: Mapped[UUID] = mapped_column(Uuid(native_uuid=False), ForeignKey("workflow_runs.run_id"), nullable=False)
    project_id: Mapped[UUID] = mapped_column(Uuid(native_uuid=False), ForeignKey("paper_projects.id"), nullable=False)
    thread_id: Mapped[UUID] = mapped_column(Uuid(native_uuid=False), nullable=False)
    checkpoint_id: Mapped[UUID] = mapped_column(Uuid(native_uuid=False), nullable=False)
    approval_type: Mapped[str] = mapped_column(String(24), nullable=False)
    decision: Mapped[str] = mapped_column(String(12), nullable=False)
    approved_ids: Mapped[list[str]] = mapped_column(JSON, nullable=False)
    execution_status: Mapped[str] = mapped_column(String(24), nullable=False, default="queued")
    submitted_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False, default=utc_now)
