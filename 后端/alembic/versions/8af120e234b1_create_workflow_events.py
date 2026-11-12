"""create workflow events; forward only"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import mysql

revision = "8af120e234b1"
down_revision = "19b45fd3ac72"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "workflow_events",
        sa.Column("event_id", sa.Uuid(native_uuid=False), primary_key=True, nullable=False),
        sa.Column("run_id", sa.Uuid(native_uuid=False), sa.ForeignKey("workflow_runs.run_id"), nullable=False),
        sa.Column("project_id", sa.Uuid(native_uuid=False), sa.ForeignKey("paper_projects.id"), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("event_type", sa.String(32), nullable=False),
        sa.Column("stage", sa.String(24), nullable=True),
        sa.Column("agent_name", sa.String(24), nullable=True),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("created_at", mysql.DATETIME(fsp=6), nullable=False),
        sa.UniqueConstraint("run_id", "sequence", name="uq_workflow_events_run_sequence"),
        sa.CheckConstraint("sequence >= 1", name="ck_workflow_events_sequence"),
        sa.CheckConstraint("event_type IN ('progress','approval_required','completed','error')", name="ck_workflow_events_type"),
    )
    op.create_index("ix_workflow_events_project_run_sequence", "workflow_events", ["project_id", "run_id", "sequence"])


def downgrade():
    raise NotImplementedError("Downgrade requires an approved data preservation plan")
