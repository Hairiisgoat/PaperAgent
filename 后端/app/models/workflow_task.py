"""An immutable task belonging to a single Planner generation."""
from datetime import datetime
from uuid import UUID

from sqlalchemy import CheckConstraint, ForeignKey, Integer, String, UniqueConstraint, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base
from app.models.paper_project import UTCDateTime, utc_now


class WorkflowTask(Base):
    __tablename__ = "workflow_tasks"
    __table_args__ = (
        UniqueConstraint("generation_id", "position", name="uq_workflow_task_position"),
        CheckConstraint("position >= 1", name="ck_workflow_task_position"),
    )
    task_id: Mapped[UUID] = mapped_column(Uuid(native_uuid=False), primary_key=True)
    generation_id: Mapped[UUID] = mapped_column(Uuid(native_uuid=False), ForeignKey("planner_generations.generation_id"), nullable=False)
    position: Mapped[int] = mapped_column(Integer, nullable=False)
    title: Mapped[str] = mapped_column(String(80), nullable=False)
    description: Mapped[str] = mapped_column(String(300), nullable=False)
    expected_output: Mapped[str] = mapped_column(String(120), nullable=False)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False, default=utc_now)
