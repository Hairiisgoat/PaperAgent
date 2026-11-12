"""Add Planner call ledger, immutable task batch, and workflow graph version."""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import mysql

revision = "d52a7c81f103"
down_revision = "b614c9e7a208"
branch_labels = None
depends_on = None


def upgrade():
    uuid = sa.Uuid(native_uuid=False)
    timestamp = sa.DateTime().with_variant(mysql.DATETIME(fsp=6), "mysql")
    op.add_column("workflow_runs", sa.Column("graph_version", sa.String(32), nullable=False, server_default="mock_v1"))
    op.create_table("planner_generations",
        sa.Column("generation_id", uuid, primary_key=True),
        sa.Column("project_id", uuid, sa.ForeignKey("paper_projects.id"), nullable=False),
        sa.Column("thread_id", uuid, nullable=False),
        sa.Column("planner_run_id", uuid, sa.ForeignKey("workflow_runs.run_id"), nullable=False),
        sa.Column("plan_revision", sa.Integer(), nullable=False),
        sa.Column("input_snapshot", sa.JSON(), nullable=False),
        sa.Column("model", sa.String(100), nullable=False),
        sa.Column("max_tokens", sa.Integer(), nullable=False),
        sa.Column("call_id", uuid, nullable=False),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("task_count", sa.Integer()),
        sa.Column("prompt_tokens", sa.Integer()),
        sa.Column("completion_tokens", sa.Integer()),
        sa.Column("usage_complete", sa.Boolean(), nullable=False),
        sa.Column("error_code", sa.String(32)),
        sa.Column("created_at", timestamp, nullable=False),
        sa.Column("updated_at", timestamp, nullable=False),
        sa.UniqueConstraint("project_id", "thread_id", "plan_revision", name="uq_planner_revision"),
        sa.UniqueConstraint("call_id", name="uq_planner_call"),
        sa.UniqueConstraint("planner_run_id", name="uq_planner_run"),
        sa.CheckConstraint("plan_revision >= 1", name="ck_planner_revision"),
        sa.CheckConstraint("status IN ('prepared','invoking','tasks_persisted','failed','needs_review')", name="ck_planner_status"),
    )
    op.create_index("ix_planner_project_thread", "planner_generations", ["project_id", "thread_id"])
    op.create_table("workflow_tasks",
        sa.Column("task_id", uuid, primary_key=True),
        sa.Column("generation_id", uuid, sa.ForeignKey("planner_generations.generation_id"), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("title", sa.String(80), nullable=False),
        sa.Column("description", sa.String(300), nullable=False),
        sa.Column("expected_output", sa.String(120), nullable=False),
        sa.Column("created_at", timestamp, nullable=False),
        sa.UniqueConstraint("generation_id", "position", name="uq_workflow_task_position"),
        sa.CheckConstraint("position >= 1", name="ck_workflow_task_position"),
    )


def downgrade():
    raise NotImplementedError("Downgrade requires an approved data preservation plan")
