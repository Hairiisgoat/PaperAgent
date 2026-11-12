"""Drafts, chapter references and per-invocation writing ledger."""
from datetime import datetime
from uuid import UUID

from sqlalchemy import Boolean, CheckConstraint, ForeignKey, Index, Integer, JSON, String, Text, UniqueConstraint, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base
from app.models.paper_project import UTCDateTime, utc_now


class WritingDraft(Base):
    __tablename__ = "writing_drafts"
    __table_args__ = (UniqueConstraint("run_id", name="uq_writing_draft_run"),
        Index("ix_writing_project_thread", "project_id", "thread_id"),
        CheckConstraint("status IN ('queued','writing','completed','failed','needs_review')", name="ck_writing_draft_status"))
    draft_id: Mapped[UUID] = mapped_column(Uuid(native_uuid=False), primary_key=True)
    project_id: Mapped[UUID] = mapped_column(Uuid(native_uuid=False), ForeignKey("paper_projects.id"), nullable=False)
    thread_id: Mapped[UUID] = mapped_column(Uuid(native_uuid=False), nullable=False)
    run_id: Mapped[UUID] = mapped_column(Uuid(native_uuid=False), ForeignKey("workflow_runs.run_id"), nullable=False)
    generation_id: Mapped[UUID] = mapped_column(Uuid(native_uuid=False), ForeignKey("planner_generations.generation_id"), nullable=False)
    status: Mapped[str] = mapped_column(String(24), nullable=False)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False, default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False, default=utc_now, onupdate=utc_now)


class WritingChapter(Base):
    __tablename__ = "writing_chapters"
    __table_args__ = (UniqueConstraint("draft_id", "position", name="uq_writing_chapter_position"),
        CheckConstraint("position >= 1", name="ck_writing_chapter_position"),
        CheckConstraint("status IN ('queued','writing','completed','failed','needs_review')", name="ck_writing_chapter_status"))
    chapter_id: Mapped[UUID] = mapped_column(Uuid(native_uuid=False), primary_key=True)
    draft_id: Mapped[UUID] = mapped_column(Uuid(native_uuid=False), ForeignKey("writing_drafts.draft_id"), nullable=False)
    position: Mapped[int] = mapped_column(Integer, nullable=False)
    title: Mapped[str] = mapped_column(String(120), nullable=False)
    objective: Mapped[str] = mapped_column(String(500), nullable=False)
    status: Mapped[str] = mapped_column(String(24), nullable=False)
    paragraphs: Mapped[list[str] | None] = mapped_column(JSON(none_as_null=True))
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False, default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False, default=utc_now, onupdate=utc_now)


class WritingCitation(Base):
    __tablename__ = "writing_citations"
    __table_args__ = (CheckConstraint("paragraph_index >= 0", name="ck_writing_citation_index"),)
    citation_id: Mapped[UUID] = mapped_column(Uuid(native_uuid=False), primary_key=True)
    chapter_id: Mapped[UUID] = mapped_column(Uuid(native_uuid=False), ForeignKey("writing_chapters.chapter_id"), nullable=False)
    source_id: Mapped[UUID] = mapped_column(Uuid(native_uuid=False), ForeignKey("research_sources.source_id"), nullable=False)
    paragraph_index: Mapped[int] = mapped_column(Integer, nullable=False)
    evidence: Mapped[str] = mapped_column(Text, nullable=False)


class WritingCall(Base):
    __tablename__ = "writing_calls"
    __table_args__ = (UniqueConstraint("draft_id", "ordinal", name="uq_writing_call_ordinal"),
        CheckConstraint("kind IN ('outline','chapter')", name="ck_writing_call_kind"),
        CheckConstraint("status IN ('prepared','invoking','succeeded','failed','needs_review')", name="ck_writing_call_status"))
    call_id: Mapped[UUID] = mapped_column(Uuid(native_uuid=False), primary_key=True)
    draft_id: Mapped[UUID] = mapped_column(Uuid(native_uuid=False), ForeignKey("writing_drafts.draft_id"), nullable=False)
    chapter_id: Mapped[UUID | None] = mapped_column(Uuid(native_uuid=False), ForeignKey("writing_chapters.chapter_id"))
    ordinal: Mapped[int] = mapped_column(Integer, nullable=False)
    kind: Mapped[str] = mapped_column(String(12), nullable=False)
    status: Mapped[str] = mapped_column(String(24), nullable=False)
    prompt_tokens: Mapped[int | None] = mapped_column(Integer)
    completion_tokens: Mapped[int | None] = mapped_column(Integer)
    usage_complete: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    error_code: Mapped[str | None] = mapped_column(String(32))
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False, default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False, default=utc_now, onupdate=utc_now)
