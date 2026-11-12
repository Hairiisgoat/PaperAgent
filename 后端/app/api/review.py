"""Project-scoped review reports and explicit recovery."""
from datetime import datetime
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, BackgroundTasks, Depends
from pydantic import BaseModel, ConfigDict
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.infrastructure.database import get_db
from app.models.review import ReviewCall, ReviewReport, ReviewUnit
from app.models.writing import WritingChapter
from app.models.writing_segments import WritingChapterVersion
from app.schemas.review import ReviewOutput
from app.services.project_scope import ResourceNotFound
from app.services.review import _context, assemble_review, create_review, retry_review, run_review
from app.services.writing_versions import latest_version

router = APIRouter(prefix='/api/projects/{project_id}/workflow-runs/{run_id}/reviews', tags=['reviews'])


class ReviewRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    report_id: UUID
    chapter_id: UUID | None = None


class AssembleRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    report_id: UUID


class ReuseProvenance(BaseModel):
    model_config = ConfigDict(extra='forbid')
    report_id: UUID
    unit_id: UUID
    call_id: UUID


class UnitResponse(BaseModel):
    chapter_id: UUID
    chapter_title: str
    version_id: UUID
    version_no: int
    stale: bool
    status: str
    error_code: str | None
    result: ReviewOutput | None
    reused_from: ReuseProvenance | None = None


class ReportResponse(BaseModel):
    report_id: UUID
    project_id: UUID
    run_id: UUID
    status: str
    created_at: datetime
    context_versions: dict[str, str]
    context_stale: bool
    units: list[UnitResponse]


def _response(session: Session, project_id: UUID, run_id: UUID, report: ReviewReport) -> ReportResponse:
    if report.project_id != project_id or report.run_id != run_id:
        raise ResourceNotFound('Review not found')
    units = []
    for unit in session.scalars(select(ReviewUnit).join(WritingChapter, WritingChapter.chapter_id == ReviewUnit.chapter_id)
                                .where(ReviewUnit.report_id == report.report_id).order_by(WritingChapter.position)):
        chapter, version = session.get(WritingChapter, unit.chapter_id), session.get(WritingChapterVersion, unit.version_id)
        if not chapter or not version or version.chapter_id != chapter.chapter_id or version.manuscript_id != report.manuscript_id:
            raise ResourceNotFound('Review unavailable')
        latest = latest_version(session, report.manuscript_id, chapter.chapter_id)
        last_call = session.scalar(select(ReviewCall).where(ReviewCall.unit_id == unit.unit_id).order_by(ReviewCall.attempt_no.desc()).limit(1))
        raw = dict(unit.result) if unit.result else None
        provenance = raw.pop('_provenance', None) if raw is not None else None
        reused = ReuseProvenance.model_validate(provenance) if provenance else None
        if reused:
            source_report = session.get(ReviewReport, reused.report_id)
            source_unit = session.get(ReviewUnit, reused.unit_id)
            source_call = session.get(ReviewCall, reused.call_id)
            if (not source_report or source_report.project_id != project_id or source_report.run_id != run_id
                or not source_unit or source_unit.report_id != source_report.report_id or source_unit.status != 'completed'
                or source_unit.chapter_id != unit.chapter_id or source_unit.version_id != unit.version_id
                or source_unit.result != raw or not source_call or source_call.unit_id != source_unit.unit_id
                or source_call.status != 'succeeded'):
                raise ResourceNotFound('Review unavailable')
        units.append(UnitResponse(chapter_id=chapter.chapter_id, chapter_title=chapter.title,
            version_id=version.version_id, version_no=version.version_no, stale=not latest or latest.version_id != version.version_id,
            status=unit.status, error_code=last_call.error_code if last_call else None,
            result=ReviewOutput.model_validate(raw) if raw else None, reused_from=reused))
    context_stale = False
    for chapter_id, version_id in report.context_versions.items():
        try:
            latest = latest_version(session, report.manuscript_id, UUID(chapter_id))
            if not latest or str(latest.version_id) != version_id:
                context_stale = True
        except ValueError:
            raise ResourceNotFound('Review unavailable') from None
    return ReportResponse(report_id=report.report_id, project_id=project_id, run_id=run_id,
                          status=report.status, created_at=report.created_at, context_versions=report.context_versions,
                          context_stale=context_stale, units=units)


@router.post('', response_model=ReportResponse, status_code=202)
def start_review(project_id: UUID, run_id: UUID, request: ReviewRequest, background: BackgroundTasks,
                 session: Annotated[Session, Depends(get_db)]):
    report, created = create_review(session, project_id, run_id, request.report_id, request.chapter_id)
    response = _response(session, project_id, run_id, report)
    if created:
        session.commit()
        background.add_task(run_review, project_id, run_id, report.report_id)
    return response


@router.get('', response_model=list[ReportResponse])
def list_reviews(project_id: UUID, run_id: UUID, session: Annotated[Session, Depends(get_db)]):
    _context(session, project_id, run_id)
    reports = session.scalars(select(ReviewReport).where(ReviewReport.project_id == project_id,
        ReviewReport.run_id == run_id).order_by(ReviewReport.created_at.desc()))
    return [_response(session, project_id, run_id, report) for report in reports]


@router.post('/assemble', response_model=ReportResponse, status_code=201)
def assemble(project_id: UUID, run_id: UUID, request: AssembleRequest,
             session: Annotated[Session, Depends(get_db)]):
    report = assemble_review(session, project_id, run_id, request.report_id)
    response = _response(session, project_id, run_id, report)
    session.commit()
    return response


@router.post('/{report_id}/retry', response_model=ReportResponse, status_code=202)
def retry(project_id: UUID, run_id: UUID, report_id: UUID, background: BackgroundTasks,
          session: Annotated[Session, Depends(get_db)]):
    retry_review(session, project_id, run_id, report_id)
    report = session.get(ReviewReport, report_id)
    response = _response(session, project_id, run_id, report)
    session.commit()
    background.add_task(run_review, project_id, run_id, report_id)
    return response
