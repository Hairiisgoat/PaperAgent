"""Durable segmented manuscript, call attempts, and immutable chapter versions."""
from datetime import datetime
from uuid import UUID

from sqlalchemy import CheckConstraint, ForeignKey, Index, Integer, JSON, String, Text, UniqueConstraint, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base
from app.models.paper_project import UTCDateTime, utc_now


class WritingManuscript(Base):
    __tablename__ = 'writing_manuscripts'
    __table_args__ = (UniqueConstraint('source_draft_id', name='uq_wm_source_draft'),
        Index('ix_wm_project_thread', 'project_id', 'thread_id'),
        CheckConstraint("status IN ('queued','writing','completed','failed','needs_review')", name='ck_wm_status'))
    manuscript_id: Mapped[UUID] = mapped_column(Uuid(native_uuid=False), primary_key=True)
    project_id: Mapped[UUID] = mapped_column(Uuid(native_uuid=False), ForeignKey('paper_projects.id'), nullable=False)
    thread_id: Mapped[UUID] = mapped_column(Uuid(native_uuid=False), nullable=False)
    source_draft_id: Mapped[UUID] = mapped_column(Uuid(native_uuid=False), ForeignKey('writing_drafts.draft_id'), nullable=False)
    generation_id: Mapped[UUID] = mapped_column(Uuid(native_uuid=False), ForeignKey('planner_generations.generation_id'), nullable=False)
    status: Mapped[str] = mapped_column(String(24), nullable=False)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False, default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False, default=utc_now, onupdate=utc_now)


class WritingManuscriptAttempt(Base):
    __tablename__ = 'writing_manuscript_attempts'
    __table_args__ = (UniqueConstraint('previous_run_id', name='uq_wma_previous_run'),
        Index('ix_wma_manuscript', 'manuscript_id'))
    run_id: Mapped[UUID] = mapped_column(Uuid(native_uuid=False), ForeignKey('workflow_runs.run_id'), primary_key=True)
    manuscript_id: Mapped[UUID] = mapped_column(Uuid(native_uuid=False), ForeignKey('writing_manuscripts.manuscript_id'), nullable=False)
    previous_run_id: Mapped[UUID] = mapped_column(Uuid(native_uuid=False), ForeignKey('workflow_runs.run_id'), nullable=False)
    checkpoint_id: Mapped[UUID] = mapped_column(Uuid(native_uuid=False), nullable=False)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False, default=utc_now)


class WritingUnit(Base):
    __tablename__ = 'writing_units'
    __table_args__ = (UniqueConstraint('manuscript_id', 'chapter_id', 'position', name='uq_wu_chapter_position'),
        CheckConstraint('position >= 1', name='ck_wu_position'),
        CheckConstraint("status IN ('queued','writing','completed','failed','needs_review')", name='ck_wu_status'))
    unit_id: Mapped[UUID] = mapped_column(Uuid(native_uuid=False), primary_key=True)
    manuscript_id: Mapped[UUID] = mapped_column(Uuid(native_uuid=False), ForeignKey('writing_manuscripts.manuscript_id'), nullable=False)
    chapter_id: Mapped[UUID] = mapped_column(Uuid(native_uuid=False), ForeignKey('writing_chapters.chapter_id'), nullable=False)
    position: Mapped[int] = mapped_column(Integer, nullable=False)
    title: Mapped[str] = mapped_column(String(120), nullable=False)
    objective: Mapped[str] = mapped_column(String(500), nullable=False)
    status: Mapped[str] = mapped_column(String(24), nullable=False)
    paragraphs: Mapped[list[dict[str, object]] | None] = mapped_column(JSON(none_as_null=True))
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False, default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False, default=utc_now, onupdate=utc_now)


class WritingUnitCall(Base):
    __tablename__ = 'writing_unit_calls'
    __table_args__ = (UniqueConstraint('unit_id', 'attempt_no', name='uq_wuc_unit_attempt'),
        Index('ix_wuc_run', 'run_id'), CheckConstraint('attempt_no >= 1', name='ck_wuc_attempt'),
        CheckConstraint("status IN ('prepared','invoking','succeeded','failed','needs_review')", name='ck_wuc_status'))
    call_id: Mapped[UUID] = mapped_column(Uuid(native_uuid=False), primary_key=True)
    unit_id: Mapped[UUID] = mapped_column(Uuid(native_uuid=False), ForeignKey('writing_units.unit_id'), nullable=False)
    run_id: Mapped[UUID] = mapped_column(Uuid(native_uuid=False), ForeignKey('workflow_runs.run_id'), nullable=False)
    attempt_no: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(24), nullable=False)
    prompt_tokens: Mapped[int | None] = mapped_column(Integer)
    completion_tokens: Mapped[int | None] = mapped_column(Integer)
    error_code: Mapped[str | None] = mapped_column(String(32))
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False, default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False, default=utc_now, onupdate=utc_now)


class WritingChapterVersion(Base):
    __tablename__ = 'writing_chapter_versions'
    __table_args__ = (UniqueConstraint('manuscript_id', 'chapter_id', 'version_no', name='uq_wcv_chapter_version'),
        CheckConstraint('version_no >= 1', name='ck_wcv_version'),
        CheckConstraint("kind IN ('generated','revised','restored')", name='ck_wcv_kind'))
    version_id: Mapped[UUID] = mapped_column(Uuid(native_uuid=False), primary_key=True)
    manuscript_id: Mapped[UUID] = mapped_column(Uuid(native_uuid=False), ForeignKey('writing_manuscripts.manuscript_id'), nullable=False)
    chapter_id: Mapped[UUID] = mapped_column(Uuid(native_uuid=False), ForeignKey('writing_chapters.chapter_id'), nullable=False)
    version_no: Mapped[int] = mapped_column(Integer, nullable=False)
    kind: Mapped[str] = mapped_column(String(16), nullable=False)
    parent_version_id: Mapped[UUID | None] = mapped_column(Uuid(native_uuid=False), ForeignKey('writing_chapter_versions.version_id'))
    paragraphs: Mapped[list[dict[str, object]]] = mapped_column(JSON, nullable=False)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False, default=utc_now)


class WritingRevision(Base):
    __tablename__ = 'writing_revisions'
    __table_args__ = (Index('ix_wr_chapter', 'manuscript_id', 'chapter_id'),
        CheckConstraint("status IN ('queued','running','completed','failed','needs_review')", name='ck_wr_status'),
        CheckConstraint("mode IN ('expand','condense','rewrite','restructure')", name='ck_wr_mode'))
    revision_id: Mapped[UUID] = mapped_column(Uuid(native_uuid=False), primary_key=True)
    manuscript_id: Mapped[UUID] = mapped_column(Uuid(native_uuid=False), ForeignKey('writing_manuscripts.manuscript_id'), nullable=False)
    chapter_id: Mapped[UUID] = mapped_column(Uuid(native_uuid=False), ForeignKey('writing_chapters.chapter_id'), nullable=False)
    base_version_id: Mapped[UUID] = mapped_column(Uuid(native_uuid=False), ForeignKey('writing_chapter_versions.version_id'), nullable=False)
    result_version_id: Mapped[UUID | None] = mapped_column(Uuid(native_uuid=False), ForeignKey('writing_chapter_versions.version_id'))
    call_id: Mapped[UUID] = mapped_column(Uuid(native_uuid=False), nullable=False, unique=True)
    mode: Mapped[str] = mapped_column(String(16), nullable=False)
    instruction: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String(24), nullable=False)
    error_code: Mapped[str | None] = mapped_column(String(32))
    prompt_tokens: Mapped[int | None] = mapped_column(Integer)
    completion_tokens: Mapped[int | None] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False, default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False, default=utc_now, onupdate=utc_now)
