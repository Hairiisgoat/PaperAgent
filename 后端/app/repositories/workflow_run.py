"""Scoped persistence operations for workflow attempts."""
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.workflow_run import WorkflowRun


class WorkflowRunRepository:
    def __init__(self, session: Session):
        self.session = session

    def add(self, run: WorkflowRun) -> None:
        self.session.add(run)
        self.session.flush()

    def get(self, project_id: UUID, run_id: UUID, *, for_update: bool = False) -> WorkflowRun | None:
        stmt = select(WorkflowRun).where(WorkflowRun.project_id == project_id, WorkflowRun.run_id == run_id)
        if for_update:
            stmt = stmt.with_for_update()
        return self.session.scalar(stmt)

    def list_project(self, project_id: UUID, *, limit: int = 50) -> list[WorkflowRun]:
        stmt = (select(WorkflowRun).where(WorkflowRun.project_id == project_id)
                .order_by(WorkflowRun.created_at.desc(), WorkflowRun.run_id.desc()).limit(limit))
        return list(self.session.scalars(stmt))

    def latest(self, project_id: UUID, thread_id: UUID, *, for_update: bool = False) -> WorkflowRun | None:
        stmt = (select(WorkflowRun)
                .where(WorkflowRun.project_id == project_id, WorkflowRun.thread_id == thread_id)
                .order_by(WorkflowRun.attempt_no.desc()).limit(1))
        if for_update:
            stmt = stmt.with_for_update()
        return self.session.scalar(stmt)
