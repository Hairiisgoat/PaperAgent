"""Paper project persistence model and UTC datetime conversion."""

from datetime import UTC, datetime
from uuid import UUID, uuid4

from sqlalchemy import CheckConstraint, DateTime, Index, JSON, String, Text, Uuid, text
from sqlalchemy.dialects.mysql import DATETIME
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.types import TypeDecorator

from app.models.base import Base


class UTCDateTime(TypeDecorator[datetime]):
    """Store UTC in timezone-less DATETIME; expose aware UTC datetimes in Python."""

    impl = DateTime
    cache_ok = True

    def load_dialect_impl(self, dialect):
        if dialect.name == "mysql":
            return dialect.type_descriptor(DATETIME(fsp=6))
        return dialect.type_descriptor(DateTime())

    def process_bind_param(self, value, dialect):
        if value is None:
            return None
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("Datetime must include a timezone")
        return value.astimezone(UTC).replace(tzinfo=None)

    def process_result_value(self, value, dialect):
        if value is None:
            return None
        return value.replace(tzinfo=UTC)


def utc_now() -> datetime:
    return datetime.now(UTC)


class PaperProject(Base):
    """One independently managed paper project."""

    __tablename__ = "paper_projects"
    __table_args__ = (
        CheckConstraint("paper_type IN ('course_paper', 'thesis', 'literature_review', 'research_paper')", name="ck_paper_projects_paper_type"),
        CheckConstraint("lifecycle_status IN ('active', 'archived', 'deleted')", name="ck_paper_projects_lifecycle_status"),
        CheckConstraint("progress_status IN ('not_started', 'in_progress', 'completed')", name="ck_paper_projects_progress_status"),
        Index("ix_paper_projects_user_lifecycle_updated", "user_id", "lifecycle_status", "updated_at"),
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True, native_uuid=False), primary_key=True, default=uuid4)
    user_id: Mapped[UUID | None] = mapped_column(Uuid(as_uuid=True, native_uuid=False), nullable=True)
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    paper_type: Mapped[str] = mapped_column(String(40), nullable=False, default="course_paper", server_default=text("'course_paper'"))
    topic: Mapped[str] = mapped_column(Text, nullable=False)
    requirements: Mapped[dict | None] = mapped_column(JSON(none_as_null=True), nullable=True)
    citation_style: Mapped[str] = mapped_column(String(40), nullable=False)
    lifecycle_status: Mapped[str] = mapped_column(String(20), nullable=False, default="active", server_default=text("'active'"))
    progress_status: Mapped[str] = mapped_column(String(20), nullable=False, default="not_started", server_default=text("'not_started'"))
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False, default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False, default=utc_now, onupdate=utc_now)
    archived_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    deleted_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
