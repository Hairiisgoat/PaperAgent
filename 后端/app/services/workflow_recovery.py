"""Single-process startup recovery for durable approval attempts."""
import logging
from concurrent.futures import ThreadPoolExecutor
from threading import Lock
from uuid import UUID
from sqlalchemy import inspect, select
from app.graph.factory import build_graph
from app.graph.checkpointer import sqlite_checkpointer
from app.infrastructure import database
from app.models.workflow_approval_decision import WorkflowApprovalDecision
from app.models.workflow_run import WorkflowRun
from app.models.planner_generation import PlannerGeneration
from app.models.writing import WritingDraft
from app.models.writing_segments import WritingManuscriptAttempt, WritingRevision
from app.services.mock_start import checkpoint_path, run_mock_attempt
from app.services.workflow_resume import run_resume_attempt

logger = logging.getLogger(__name__)


def inspect_running(project_id: UUID, run_id: UUID, thread_id: UUID, checkpoint_id: UUID, graph_version: str = "mock_v1") -> None:
    """Diagnose only; even an unchanged checkpoint does not prove no work ran."""
    outcome = "checkpoint_unavailable"
    try:
        with sqlite_checkpointer(checkpoint_path(), read_only=True) as saver:
            snap = build_graph(graph_version).compile(checkpointer=saver).get_state({"configurable": {"thread_id": str(thread_id)}})
            config = (snap.config or {}).get("configurable", {})
            if config.get("thread_id") != str(thread_id) or not config.get("checkpoint_id"):
                outcome = "checkpoint_unknown"
            elif config["checkpoint_id"] == str(checkpoint_id):
                outcome = "checkpoint_unchanged_execution_unknown"
            else:
                outcome = "checkpoint_advanced"
    except Exception:
        pass
    logger.warning("Running resume requires reconciliation: project_id=%s run_id=%s reason=%s", project_id, run_id, outcome)


class RecoveryScheduler:
    """At-most-one local scheduled task per run; DB locks remain authoritative."""
    def __init__(self):
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="paperagent-recovery")
        self._lock = Lock()
        self._pending: set[UUID] = set()

    def submit(self, project_id: UUID, run_id: UUID) -> bool:
        return self._submit(project_id, run_id, run_resume_attempt)

    def submit_initial(self, project_id: UUID, run_id: UUID) -> bool:
        return self._submit(project_id, run_id, run_mock_attempt)

    def submit_writing_retry(self, project_id: UUID, run_id: UUID) -> bool:
        from app.services.writing_retry import run_writing_retry
        return self._submit(project_id, run_id, run_writing_retry)

    def submit_segmented_retry(self, project_id: UUID, run_id: UUID) -> bool:
        from app.services.segmented_writing import run_segmented_retry
        return self._submit(project_id, run_id, run_segmented_retry)

    def submit_revision(self, project_id: UUID, run_id: UUID, chapter_id: UUID, revision_id: UUID) -> bool:
        from app.services.writing_versions import run_revision
        with self._lock:
            if revision_id in self._pending:
                return False
            self._pending.add(revision_id)
        try:
            future = self._executor.submit(run_revision, project_id, run_id, chapter_id, revision_id)
            future.add_done_callback(lambda _future: self._forget(revision_id))
            return True
        except Exception:
            self._forget(revision_id)
            logger.error('Revision scheduling failed')
            return False

    def submit_review(self, project_id: UUID, run_id: UUID, report_id: UUID) -> bool:
        from app.services.review import run_review
        with self._lock:
            if report_id in self._pending:
                return False
            self._pending.add(report_id)
        try:
            future = self._executor.submit(run_review, project_id, run_id, report_id)
            future.add_done_callback(lambda _future: self._forget(report_id))
            return True
        except Exception:
            self._forget(report_id)
            logger.error('Review scheduling failed')
            return False

    def _submit(self, project_id, run_id, worker) -> bool:
        with self._lock:
            if run_id in self._pending:
                return False
            self._pending.add(run_id)
        try:
            future = self._executor.submit(worker, project_id, run_id)
            future.add_done_callback(lambda _future: self._forget(run_id))
            return True
        except Exception:
            self._forget(run_id)
            logger.error("Recovery task scheduling failed: project_id=%s run_id=%s", project_id, run_id)
            return False

    def _forget(self, run_id: UUID) -> None:
        with self._lock:
            self._pending.discard(run_id)

    def scan(self) -> None:
        """Dispatch committed queued intents; never replay running attempts."""
        try:
            with database.SessionLocal() as session:
                rows = session.execute(select(WorkflowApprovalDecision, WorkflowRun).join(
                    WorkflowRun, WorkflowRun.run_id == WorkflowApprovalDecision.run_id).where(
                    WorkflowApprovalDecision.execution_status.in_(("queued", "running"))).order_by(
                    WorkflowApprovalDecision.submitted_at)).all()
                items = [(d.project_id, d.run_id, d.thread_id, d.checkpoint_id, d.execution_status,
                          r.project_id, r.thread_id, r.status, r.graph_version) for d, r in rows]
                initial = session.execute(select(WorkflowRun.project_id, WorkflowRun.run_id,
                    WorkflowRun.thread_id, WorkflowRun.status).where(
                    WorkflowRun.attempt_no == 1, WorkflowRun.status.in_(("queued", "running")),
                    ~select(WorkflowApprovalDecision.run_id).where(
                        WorkflowApprovalDecision.run_id == WorkflowRun.run_id).exists())).all()
                review = session.execute(select(PlannerGeneration.project_id, PlannerGeneration.planner_run_id,
                    PlannerGeneration.generation_id).where(PlannerGeneration.status == "needs_review")).all()
                writing_retries = []
                if inspect(session.connection()).has_table("writing_drafts"):
                    writing_retries = session.execute(select(WorkflowRun.project_id, WorkflowRun.run_id, WorkflowRun.status).join(
                        WritingDraft, WritingDraft.run_id == WorkflowRun.run_id).where(
                        WorkflowRun.graph_version == "planner_research_writing_v3", WorkflowRun.attempt_no > 1,
                        WorkflowRun.status.in_(("queued", "running")),
                        ~select(WorkflowApprovalDecision.run_id).where(WorkflowApprovalDecision.run_id == WorkflowRun.run_id).exists())).all()
                segmented = []
                if inspect(session.connection()).has_table('writing_manuscript_attempts'):
                    segmented = session.execute(select(WorkflowRun.project_id, WorkflowRun.run_id, WorkflowRun.status)
                        .join(WritingManuscriptAttempt, WritingManuscriptAttempt.run_id == WorkflowRun.run_id)
                        .where(WorkflowRun.status.in_(('queued', 'running')),
                            ~select(WorkflowApprovalDecision.run_id).where(WorkflowApprovalDecision.run_id == WorkflowRun.run_id).exists())).all()
                revisions = []
                if inspect(session.connection()).has_table('writing_revisions'):
                    revisions = session.execute(select(WorkflowRun.project_id, WorkflowRun.run_id,
                        WritingRevision.chapter_id, WritingRevision.revision_id, WritingRevision.status)
                        .join(WritingManuscriptAttempt, WritingManuscriptAttempt.run_id == WorkflowRun.run_id)
                        .join(WritingRevision, WritingRevision.manuscript_id == WritingManuscriptAttempt.manuscript_id)
                        .where(WorkflowRun.status == 'completed', WritingRevision.status.in_(('queued', 'running')))).all()
                reviews = []
                if inspect(session.connection()).has_table('review_reports'):
                    from app.models.review import ReviewReport, ReviewUnit, ReviewCall
                    reviews = session.execute(select(ReviewReport.project_id, ReviewReport.run_id, ReviewReport.report_id, ReviewReport.status)
                        .where(ReviewReport.status.in_(('queued', 'running')))).all()
                    for project_id, run_id, report_id, status in reviews:
                        if status != 'running':
                            continue
                        report = session.get(ReviewReport, report_id, with_for_update=True)
                        if report and report.status == 'running':
                            report.status = 'needs_review'
                            for unit in session.scalars(select(ReviewUnit).where(ReviewUnit.report_id == report_id, ReviewUnit.status == 'running').with_for_update()):
                                unit.status = 'needs_review'
                                for call in session.scalars(select(ReviewCall).where(ReviewCall.unit_id == unit.unit_id, ReviewCall.status == 'invoking').with_for_update()):
                                    call.status = 'needs_review'
                    session.commit()
            for project_id, run_id, report_id, status in reviews:
                if status == 'queued':
                    self.submit_review(project_id, run_id, report_id)
                else:
                    logger.warning('Running review requires reconciliation: project_id=%s report_id=%s', project_id, report_id)
            for project_id, run_id, generation_id in review:
                logger.warning("Planner call requires manual reconciliation: project_id=%s run_id=%s generation_id=%s", project_id, run_id, generation_id)
            for project_id, run_id, status in writing_retries:
                if status == "queued":
                    self.submit_writing_retry(project_id, run_id)
                else:
                    logger.warning("Running writing retry requires reconciliation: project_id=%s run_id=%s", project_id, run_id)
            for project_id, run_id, status in segmented:
                if status == 'queued':
                    self.submit_segmented_retry(project_id, run_id)
                else:
                    logger.warning('Running segmented writing requires reconciliation: project_id=%s run_id=%s', project_id, run_id)
            for project_id, run_id, chapter_id, revision_id, status in revisions:
                if status == 'queued':
                    self.submit_revision(project_id, run_id, chapter_id, revision_id)
                else:
                    logger.warning('Running revision requires reconciliation: project_id=%s revision_id=%s', project_id, revision_id)
            for project_id, run_id, thread_id, checkpoint_id, status, run_project, run_thread, run_status, graph_version in items:
                if project_id != run_project or thread_id != run_thread or status != run_status:
                    logger.error("Approval/run state mismatch; reconciliation required: project_id=%s run_id=%s", project_id, run_id)
                elif status == "queued":
                    self.submit(project_id, run_id)
                else:
                    inspect_running(project_id, run_id, thread_id, checkpoint_id, graph_version)
            for project_id, run_id, thread_id, status in initial:
                if status == "queued":
                    self.submit_initial(project_id, run_id)
                else:
                    logger.warning("Running initial workflow requires reconciliation; execution position unknown: project_id=%s run_id=%s", project_id, run_id)
        except Exception:
            logger.error("Startup approval recovery scan failed; reconciliation required")

    def close(self) -> None:
        self._executor.shutdown(wait=True)
