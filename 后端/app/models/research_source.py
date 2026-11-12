"""Research metadata and its explicit links to approved Planner tasks."""
from datetime import datetime
from uuid import UUID

from sqlalchemy import ForeignKey, Index, Integer, JSON, String, Text, UniqueConstraint, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base
from app.models.paper_project import UTCDateTime, utc_now


class ResearchSource(Base):
    __tablename__ = "research_sources"
    __table_args__ = (
        UniqueConstraint("run_id", "doi", name="uq_research_run_doi"),
        Index("ix_research_project_thread_run", "project_id", "thread_id", "run_id"),
    )
    source_id: Mapped[UUID] = mapped_column(Uuid(native_uuid=False), primary_key=True)
    project_id: Mapped[UUID] = mapped_column(Uuid(native_uuid=False), ForeignKey("paper_projects.id"), nullable=False)
    thread_id: Mapped[UUID] = mapped_column(Uuid(native_uuid=False), nullable=False)
    run_id: Mapped[UUID] = mapped_column(Uuid(native_uuid=False), ForeignKey("workflow_runs.run_id"), nullable=False)
    generation_id: Mapped[UUID] = mapped_column(Uuid(native_uuid=False), ForeignKey("planner_generations.generation_id"), nullable=False)
    doi: Mapped[str] = mapped_column(String(255), nullable=False)
    title: Mapped[str] = mapped_column(String(1000), nullable=False)
    authors: Mapped[list[str]] = mapped_column(JSON, nullable=False)
    year: Mapped[int | None] = mapped_column(Integer)
    journal: Mapped[str | None] = mapped_column(String(500))
    url: Mapped[str] = mapped_column(String(1024), nullable=False)
    abstract: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False, default=utc_now)


class ResearchSourceTask(Base):
    __tablename__ = "research_source_tasks"
    source_id: Mapped[UUID] = mapped_column(Uuid(native_uuid=False), ForeignKey("research_sources.source_id"), primary_key=True)
    task_id: Mapped[UUID] = mapped_column(Uuid(native_uuid=False), ForeignKey("workflow_tasks.task_id"), primary_key=True)
    search_query: Mapped[str] = mapped_column(String(300), nullable=False)
