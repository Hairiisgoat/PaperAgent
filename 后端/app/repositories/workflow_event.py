"""Project- and run-scoped event lookup."""
from uuid import UUID
from sqlalchemy import select
from sqlalchemy.orm import Session
from app.models.workflow_event import WorkflowEventRecord


class WorkflowEventRepository:
    def __init__(self, session: Session):
        self.session = session

    def latest_approval(self, project_id: UUID, run_id: UUID, *, for_update: bool = False) -> WorkflowEventRecord | None:
        stmt = (select(WorkflowEventRecord)
                .where(WorkflowEventRecord.project_id == project_id, WorkflowEventRecord.run_id == run_id,
                       WorkflowEventRecord.event_type == "approval_required")
                .order_by(WorkflowEventRecord.sequence.desc()).limit(1))
        if for_update:
            stmt = stmt.with_for_update()
        return self.session.scalar(stmt)
