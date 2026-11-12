"""create workflow runs

Revision ID: 19b45fd3ac72
Revises: 52ec5364111e
"""
from typing import Sequence
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import mysql

revision: str = "19b45fd3ac72"
down_revision: str | None = "52ec5364111e"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "workflow_runs",
        sa.Column("run_id", sa.Uuid(native_uuid=False), primary_key=True, nullable=False),
        sa.Column("thread_id", sa.Uuid(native_uuid=False), nullable=False),
        sa.Column("project_id", sa.Uuid(native_uuid=False), sa.ForeignKey("paper_projects.id"), nullable=False),
        sa.Column("attempt_no", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("current_stage", sa.String(24), nullable=True),
        sa.Column("started_at", mysql.DATETIME(fsp=6), nullable=True),
        sa.Column("finished_at", mysql.DATETIME(fsp=6), nullable=True),
        sa.Column("error_code", sa.String(32), nullable=True),
        sa.Column("retryable", sa.Boolean(), nullable=True),
        sa.Column("created_at", mysql.DATETIME(fsp=6), nullable=False),
        sa.Column("updated_at", mysql.DATETIME(fsp=6), nullable=False),
        sa.CheckConstraint("attempt_no >= 1", name="ck_workflow_runs_attempt_no"),
        sa.CheckConstraint("status IN ('queued','running','waiting_human','paused','failed','completed','cancelled')", name="ck_workflow_runs_status"),
        sa.CheckConstraint("current_stage IS NULL OR current_stage IN ('planning','research','outline','writing','review','export')", name="ck_workflow_runs_stage"),
        sa.CheckConstraint("error_code IS NULL OR error_code IN ('timeout','provider_unavailable','invalid_output','database_unavailable','budget_exceeded','internal_error','other')", name="ck_workflow_runs_error"),
        sa.UniqueConstraint("thread_id", "attempt_no", name="uq_workflow_runs_thread_attempt"),
    )
    op.create_index("ix_workflow_runs_project_thread_attempt", "workflow_runs", ["project_id", "thread_id", "attempt_no"])


def downgrade() -> None:
    raise NotImplementedError("Downgrade requires an approved data preservation plan")
