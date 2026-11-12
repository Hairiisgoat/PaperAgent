"""Add writing drafts, chapters, citations and model-call ledger."""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import mysql

revision = "f430d8a09b51"
down_revision = "e93d78a61f04"
branch_labels = None
depends_on = None


def upgrade():
    uuid = sa.Uuid(native_uuid=False)
    stamp = sa.DateTime().with_variant(mysql.DATETIME(fsp=6), "mysql")
    op.create_table("writing_drafts",
        sa.Column("draft_id", uuid, primary_key=True),
        sa.Column("project_id", uuid, sa.ForeignKey("paper_projects.id"), nullable=False),
        sa.Column("thread_id", uuid, nullable=False),
        sa.Column("run_id", uuid, sa.ForeignKey("workflow_runs.run_id"), nullable=False),
        sa.Column("generation_id", uuid, sa.ForeignKey("planner_generations.generation_id"), nullable=False),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("created_at", stamp, nullable=False), sa.Column("updated_at", stamp, nullable=False),
        sa.UniqueConstraint("run_id", name="uq_writing_draft_run"),
        sa.CheckConstraint("status IN ('queued','writing','completed','failed','needs_review')", name="ck_writing_draft_status"))
    op.create_index("ix_writing_project_thread", "writing_drafts", ["project_id", "thread_id"])
    op.create_table("writing_chapters",
        sa.Column("chapter_id", uuid, primary_key=True),
        sa.Column("draft_id", uuid, sa.ForeignKey("writing_drafts.draft_id"), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False), sa.Column("title", sa.String(120), nullable=False),
        sa.Column("objective", sa.String(500), nullable=False), sa.Column("status", sa.String(24), nullable=False),
        sa.Column("paragraphs", sa.JSON(none_as_null=True)),
        sa.Column("created_at", stamp, nullable=False), sa.Column("updated_at", stamp, nullable=False),
        sa.UniqueConstraint("draft_id", "position", name="uq_writing_chapter_position"),
        sa.CheckConstraint("position >= 1", name="ck_writing_chapter_position"),
        sa.CheckConstraint("status IN ('queued','writing','completed','failed','needs_review')", name="ck_writing_chapter_status"))
    op.create_table("writing_citations",
        sa.Column("citation_id", uuid, primary_key=True),
        sa.Column("chapter_id", uuid, sa.ForeignKey("writing_chapters.chapter_id"), nullable=False),
        sa.Column("source_id", uuid, sa.ForeignKey("research_sources.source_id"), nullable=False),
        sa.Column("paragraph_index", sa.Integer(), nullable=False), sa.Column("evidence", sa.Text(), nullable=False),
        sa.CheckConstraint("paragraph_index >= 0", name="ck_writing_citation_index"))
    op.create_table("writing_calls",
        sa.Column("call_id", uuid, primary_key=True),
        sa.Column("draft_id", uuid, sa.ForeignKey("writing_drafts.draft_id"), nullable=False),
        sa.Column("chapter_id", uuid, sa.ForeignKey("writing_chapters.chapter_id")),
        sa.Column("ordinal", sa.Integer(), nullable=False), sa.Column("kind", sa.String(12), nullable=False),
        sa.Column("status", sa.String(24), nullable=False), sa.Column("prompt_tokens", sa.Integer()),
        sa.Column("completion_tokens", sa.Integer()), sa.Column("usage_complete", sa.Boolean(), nullable=False),
        sa.Column("error_code", sa.String(32)), sa.Column("created_at", stamp, nullable=False),
        sa.Column("updated_at", stamp, nullable=False),
        sa.UniqueConstraint("draft_id", "ordinal", name="uq_writing_call_ordinal"),
        sa.CheckConstraint("kind IN ('outline','chapter')", name="ck_writing_call_kind"),
        sa.CheckConstraint("status IN ('prepared','invoking','succeeded','failed','needs_review')", name="ck_writing_call_status"))


def downgrade():
    raise NotImplementedError("Downgrade requires an approved data preservation plan")
