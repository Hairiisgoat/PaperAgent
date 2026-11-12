"""Add scholarly metadata and its Planner task associations."""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import mysql

revision = "e93d78a61f04"
down_revision = "d52a7c81f103"
branch_labels = None
depends_on = None


def upgrade():
    uuid = sa.Uuid(native_uuid=False)
    timestamp = sa.DateTime().with_variant(mysql.DATETIME(fsp=6), "mysql")
    op.create_table("research_sources",
        sa.Column("source_id", uuid, primary_key=True),
        sa.Column("project_id", uuid, sa.ForeignKey("paper_projects.id"), nullable=False),
        sa.Column("thread_id", uuid, nullable=False),
        sa.Column("run_id", uuid, sa.ForeignKey("workflow_runs.run_id"), nullable=False),
        sa.Column("generation_id", uuid, sa.ForeignKey("planner_generations.generation_id"), nullable=False),
        sa.Column("doi", sa.String(255), nullable=False),
        sa.Column("title", sa.String(1000), nullable=False),
        sa.Column("authors", sa.JSON(), nullable=False),
        sa.Column("year", sa.Integer()),
        sa.Column("journal", sa.String(500)),
        sa.Column("url", sa.String(1024), nullable=False),
        sa.Column("abstract", sa.Text()),
        sa.Column("created_at", timestamp, nullable=False),
        sa.UniqueConstraint("run_id", "doi", name="uq_research_run_doi"))
    op.create_index("ix_research_project_thread_run", "research_sources", ["project_id", "thread_id", "run_id"])
    op.create_table("research_source_tasks",
        sa.Column("source_id", uuid, sa.ForeignKey("research_sources.source_id"), primary_key=True),
        sa.Column("task_id", uuid, sa.ForeignKey("workflow_tasks.task_id"), primary_key=True),
        sa.Column("search_query", sa.String(300), nullable=False))


def downgrade():
    raise NotImplementedError("Downgrade requires an approved data preservation plan")
