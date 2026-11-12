"""create durable workflow approval decisions; forward only"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import mysql

revision = "b614c9e7a208"
down_revision = "8af120e234b1"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table("workflow_approval_decisions",
        sa.Column("previous_run_id", sa.Uuid(native_uuid=False), sa.ForeignKey("workflow_runs.run_id"), primary_key=True, nullable=False),
        sa.Column("run_id", sa.Uuid(native_uuid=False), sa.ForeignKey("workflow_runs.run_id"), nullable=False),
        sa.Column("project_id", sa.Uuid(native_uuid=False), sa.ForeignKey("paper_projects.id"), nullable=False),
        sa.Column("thread_id", sa.Uuid(native_uuid=False), nullable=False),
        sa.Column("checkpoint_id", sa.Uuid(native_uuid=False), nullable=False),
        sa.Column("approval_type", sa.String(24), nullable=False),
        sa.Column("decision", sa.String(12), nullable=False),
        sa.Column("approved_ids", sa.JSON(), nullable=False),
        sa.Column("execution_status", sa.String(24), nullable=False),
        sa.Column("submitted_at", mysql.DATETIME(fsp=6), nullable=False),
        sa.UniqueConstraint("run_id", name="uq_approval_run"),
        sa.CheckConstraint("approval_type IN ('task_approval','source_approval')", name="ck_approval_type"),
        sa.CheckConstraint("decision IN ('approve','reject')", name="ck_approval_decision"),
        sa.CheckConstraint("execution_status IN ('queued','running','finished','needs_review')", name="ck_approval_execution_status"),
    )
    op.create_index("ix_approval_execution_status", "workflow_approval_decisions", ["execution_status"])


def downgrade():
    raise NotImplementedError("Downgrade requires an approved data preservation plan")
