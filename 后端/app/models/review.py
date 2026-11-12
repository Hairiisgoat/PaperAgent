"""Persistent review snapshots, chapter units, and physical model calls."""
from datetime import datetime
from uuid import UUID

from sqlalchemy import CheckConstraint, ForeignKey, Index, Integer, JSON, String, UniqueConstraint, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base
from app.models.paper_project import UTCDateTime, utc_now


class ReviewReport(Base):
    __tablename__ = 'review_reports'
    __table_args__ = (Index('ix_review_project_run', 'project_id', 'run_id'),
        CheckConstraint("status IN ('queued','running','completed','failed','needs_review')", name='ck_review_report_status'))
    report_id: Mapped[UUID] = mapped_column(Uuid(native_uuid=False), primary_key=True)
    project_id: Mapped[UUID] = mapped_column(Uuid(native_uuid=False), ForeignKey('paper_projects.id'), nullable=False)
    run_id: Mapped[UUID] = mapped_column(Uuid(native_uuid=False), ForeignKey('workflow_runs.run_id'), nullable=False)
    manuscript_id: Mapped[UUID] = mapped_column(Uuid(native_uuid=False), ForeignKey('writing_manuscripts.manuscript_id'), nullable=False)
    scope_chapter_id: Mapped[UUID | None] = mapped_column(Uuid(native_uuid=False), ForeignKey('writing_chapters.chapter_id'))
    context_versions: Mapped[dict[str, str]] = mapped_column(JSON, nullable=False)
    status: Mapped[str] = mapped_column(String(24), nullable=False)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False, default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False, default=utc_now, onupdate=utc_now)


class ReviewUnit(Base):
    __tablename__ = 'review_units'
    __table_args__ = (UniqueConstraint('report_id', 'chapter_id', name='uq_review_report_chapter'),
        CheckConstraint("status IN ('queued','running','completed','failed','needs_review')", name='ck_review_unit_status'))
    unit_id: Mapped[UUID] = mapped_column(Uuid(native_uuid=False), primary_key=True)
    report_id: Mapped[UUID] = mapped_column(Uuid(native_uuid=False), ForeignKey('review_reports.report_id'), nullable=False)
    chapter_id: Mapped[UUID] = mapped_column(Uuid(native_uuid=False), ForeignKey('writing_chapters.chapter_id'), nullable=False)
    version_id: Mapped[UUID] = mapped_column(Uuid(native_uuid=False), ForeignKey('writing_chapter_versions.version_id'), nullable=False)
    status: Mapped[str] = mapped_column(String(24), nullable=False)
    result: Mapped[dict | None] = mapped_column(JSON(none_as_null=True))
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False, default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False, default=utc_now, onupdate=utc_now)


class ReviewCall(Base):
    __tablename__ = 'review_calls'
    __table_args__ = (UniqueConstraint('unit_id', 'attempt_no', name='uq_review_call_attempt'),
        CheckConstraint("status IN ('invoking','succeeded','failed','needs_review')", name='ck_review_call_status'))
    call_id: Mapped[UUID] = mapped_column(Uuid(native_uuid=False), primary_key=True)
    unit_id: Mapped[UUID] = mapped_column(Uuid(native_uuid=False), ForeignKey('review_units.unit_id'), nullable=False)
    attempt_no: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(24), nullable=False)
    error_code: Mapped[str | None] = mapped_column(String(32))
    prompt_tokens: Mapped[int | None] = mapped_column(Integer)
    completion_tokens: Mapped[int | None] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False, default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False, default=utc_now, onupdate=utc_now)
