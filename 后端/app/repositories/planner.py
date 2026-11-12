"""Project- and thread-scoped access to Planner generations and tasks."""
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.planner_generation import PlannerGeneration
from app.models.workflow_task import WorkflowTask


class PlannerRepository:
    def __init__(self, session: Session):
        self.session = session

    def generation(self, project_id: UUID, thread_id: UUID, generation_id: UUID, *, lock: bool = False):
        stmt = select(PlannerGeneration).where(PlannerGeneration.project_id == project_id,
            PlannerGeneration.thread_id == thread_id, PlannerGeneration.generation_id == generation_id)
        return self.session.scalar(stmt.with_for_update() if lock else stmt)

    def for_run(self, project_id: UUID, run_id: UUID, *, lock: bool = False):
        stmt = select(PlannerGeneration).where(PlannerGeneration.project_id == project_id,
            PlannerGeneration.planner_run_id == run_id)
        return self.session.scalar(stmt.with_for_update() if lock else stmt)

    def tasks(self, project_id: UUID, thread_id: UUID, generation_id: UUID):
        stmt = (select(WorkflowTask).join(PlannerGeneration).where(
            PlannerGeneration.project_id == project_id, PlannerGeneration.thread_id == thread_id,
            PlannerGeneration.generation_id == generation_id).order_by(WorkflowTask.position))
        return list(self.session.scalars(stmt))
