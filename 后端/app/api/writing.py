"""Read-only draft and source references, scoped to a project and run."""
from typing import Annotated
from uuid import UUID
from fastapi import APIRouter, BackgroundTasks, Depends
from fastapi.responses import Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.infrastructure.database import get_db
from app.models.research_source import ResearchSource
from app.models.workflow_approval_decision import WorkflowApprovalDecision
from app.models.writing import WritingCitation, WritingChapter, WritingDraft
from app.models.writing_segments import WritingChapterVersion, WritingManuscript, WritingManuscriptAttempt, WritingRevision, WritingUnit
from app.schemas.writing_response import ChapterVersionResponse, DraftChapterResponse, DraftCitation, DraftParagraphResponse, DraftResponse, RestoreSubmission, RevisionResponse, RevisionSubmission
from app.services.project_scope import ProjectConflict, ResourceNotFound
from app.services.workflow_run import WorkflowRunService

router = APIRouter(prefix="/api/projects/{project_id}", tags=["writing"])


@router.get('/workflow-runs/{run_id}/export/{kind}')
def export_document(project_id: UUID, run_id: UUID, kind: str, session: Annotated[Session, Depends(get_db)]):
    from app.services.exports import snapshot, render_docx, render_pdf
    if kind not in {'docx', 'pdf', 'review-docx'}:
        raise ResourceNotFound('Export format not found')
    data = snapshot(session, project_id, run_id, include_review=(kind == 'review-docx'))
    if kind == 'pdf':
        try:
            content = render_pdf(data)
        except (OSError, RuntimeError):
            from fastapi import HTTPException
            raise HTTPException(status_code=503, detail='PDF export is unavailable: Chinese font or renderer missing') from None
        return Response(content, media_type='application/pdf', headers={'Content-Disposition': 'attachment; filename="paperagent-paper.pdf"', 'Cache-Control': 'no-store'})
    content = render_docx(data, review=(kind == 'review-docx'))
    name = 'paperagent-review.docx' if kind == 'review-docx' else 'paperagent-paper.docx'
    return Response(content, media_type='application/vnd.openxmlformats-officedocument.wordprocessingml.document',
                    headers={'Content-Disposition': f'attachment; filename="{name}"', 'Cache-Control': 'no-store'})


@router.get("/workflow-runs/{run_id}/draft", response_model=DraftResponse)
def get_draft(project_id: UUID, run_id: UUID, session: Annotated[Session, Depends(get_db)]) -> DraftResponse:
    run = WorkflowRunService(session).get(project_id, run_id)
    draft = session.scalar(select(WritingDraft).where(WritingDraft.project_id == project_id, WritingDraft.run_id == run_id))
    attempt = session.get(WritingManuscriptAttempt, run_id) if run.graph_version == 'planner_research_writing_v4' else None
    manuscript = session.get(WritingManuscript, attempt.manuscript_id) if attempt else None
    if manuscript:
        if manuscript.project_id != project_id or manuscript.thread_id != run.thread_id:
            raise ResourceNotFound('Resource not found')
        draft = session.get(WritingDraft, manuscript.source_draft_id)
    if draft is None:
        raise ResourceNotFound("Resource not found")
    from app.services.writing_retry import source_decision
    decision = source_decision(session, project_id, run)
    if not decision or decision.thread_id != run.thread_id or draft.thread_id != run.thread_id or decision.approval_type != "source_approval" or decision.decision != "approve":
        raise ProjectConflict("Draft unavailable or inconsistent")
    approved = set(decision.approved_ids)
    sources = {str(source.source_id): source for source in session.scalars(select(ResearchSource).where(
        ResearchSource.project_id == project_id, ResearchSource.thread_id == run.thread_id,
        ResearchSource.run_id == decision.previous_run_id, ResearchSource.generation_id == draft.generation_id))}
    chapters = list(session.scalars(select(WritingChapter).where(WritingChapter.draft_id == draft.draft_id).order_by(WritingChapter.position)))
    output = []
    for chapter in chapters:
        if manuscript:
            units = list(session.scalars(select(WritingUnit).where(WritingUnit.manuscript_id == manuscript.manuscript_id,
                WritingUnit.chapter_id == chapter.chapter_id).order_by(WritingUnit.position)))
            version = session.scalar(select(WritingChapterVersion).where(WritingChapterVersion.manuscript_id == manuscript.manuscript_id,
                WritingChapterVersion.chapter_id == chapter.chapter_id).order_by(WritingChapterVersion.version_no.desc()).limit(1))
            raw = version.paragraphs if version else [paragraph for unit in units if unit.status == 'completed' for paragraph in unit.paragraphs or []]
            paragraphs = []
            for paragraph in raw:
                if not isinstance(paragraph, dict) or not isinstance(paragraph.get('text'), str) or not isinstance(paragraph.get('citations'), list) or not paragraph['citations']:
                    raise ProjectConflict('Draft unavailable or inconsistent')
                item = DraftParagraphResponse(text=paragraph['text'], citations=[])
                for citation in paragraph['citations']:
                    if not isinstance(citation, dict):
                        raise ProjectConflict('Draft unavailable or inconsistent')
                    source_id, evidence = citation.get('source_id'), citation.get('evidence')
                    source = sources.get(source_id) if isinstance(source_id, str) else None
                    if (not source or source_id not in approved or not isinstance(evidence, str) or len(evidence.strip()) < 8 or not source.abstract
                            or evidence not in source.abstract):
                        raise ProjectConflict('Draft unavailable or inconsistent')
                    item.citations.append(DraftCitation(source_id=source.source_id, title=source.title, authors=source.authors,
                        year=source.year, doi=source.doi, url=source.url, evidence=evidence))
                paragraphs.append(item)
            statuses = {unit.status for unit in units}
            status = 'completed' if version else ('needs_review' if 'needs_review' in statuses else 'failed' if 'failed' in statuses else 'writing' if 'writing' in statuses else 'queued')
            output.append(DraftChapterResponse(chapter_id=chapter.chapter_id, position=chapter.position,
                title=chapter.title, objective=chapter.objective, status=status, paragraphs=paragraphs))
            continue
        paragraphs = [DraftParagraphResponse(text=text, citations=[]) for text in chapter.paragraphs or []]
        citations = session.scalars(select(WritingCitation).where(WritingCitation.chapter_id == chapter.chapter_id).order_by(WritingCitation.paragraph_index, WritingCitation.citation_id))
        for citation in citations:
            source = sources.get(str(citation.source_id))
            if source is None or str(citation.source_id) not in approved or not 0 <= citation.paragraph_index < len(paragraphs):
                raise ProjectConflict("Draft unavailable or inconsistent")
            paragraphs[citation.paragraph_index].citations.append(DraftCitation(source_id=source.source_id,
                title=source.title, authors=source.authors, year=source.year, doi=source.doi, url=source.url, evidence=citation.evidence))
        output.append(DraftChapterResponse(chapter_id=chapter.chapter_id, position=chapter.position,
            title=chapter.title, objective=chapter.objective, status=chapter.status, paragraphs=paragraphs))
    return DraftResponse(project_id=project_id, run_id=run_id, draft_id=draft.draft_id,
        status=manuscript.status if manuscript else draft.status, chapters=output, supports_versions=manuscript is not None)


def _version_response(session: Session, project_id: UUID, run, manuscript, version: WritingChapterVersion) -> ChapterVersionResponse:
    from app.services.writing_retry import source_decision
    decision = source_decision(session, project_id, run)
    sources = {str(item.source_id): item for item in session.scalars(select(ResearchSource).where(
        ResearchSource.project_id == project_id, ResearchSource.thread_id == run.thread_id,
        ResearchSource.run_id == decision.previous_run_id, ResearchSource.generation_id == manuscript.generation_id))}
    paragraphs = []
    for paragraph in version.paragraphs:
        if not isinstance(paragraph, dict) or not isinstance(paragraph.get('text'), str) or not isinstance(paragraph.get('citations'), list) or not paragraph['citations']:
            raise ProjectConflict('Version unavailable or inconsistent')
        item = DraftParagraphResponse(text=paragraph['text'], citations=[])
        for citation in paragraph['citations']:
            if not isinstance(citation, dict):
                raise ProjectConflict('Version unavailable or inconsistent')
            source_id, evidence = citation.get('source_id'), citation.get('evidence')
            source = sources.get(source_id) if isinstance(source_id, str) else None
            if (not source or source_id not in decision.approved_ids or not isinstance(evidence, str)
                    or len(evidence.strip()) < 8 or not source.abstract or evidence not in source.abstract):
                raise ProjectConflict('Version unavailable or inconsistent')
            item.citations.append(DraftCitation(source_id=source.source_id, title=source.title, authors=source.authors,
                year=source.year, doi=source.doi, url=source.url, evidence=evidence))
        paragraphs.append(item)
    return ChapterVersionResponse(version_id=version.version_id, chapter_id=version.chapter_id, version_no=version.version_no,
        kind=version.kind, parent_version_id=version.parent_version_id, created_at=version.created_at, paragraphs=paragraphs)


@router.get('/workflow-runs/{run_id}/draft/chapters/{chapter_id}/versions', response_model=list[ChapterVersionResponse])
def list_chapter_versions(project_id: UUID, run_id: UUID, chapter_id: UUID, session: Annotated[Session, Depends(get_db)]):
    from app.services.writing_versions import scoped_chapter
    run, manuscript, _ = scoped_chapter(session, project_id, run_id, chapter_id)
    versions = session.scalars(select(WritingChapterVersion).where(WritingChapterVersion.manuscript_id == manuscript.manuscript_id,
        WritingChapterVersion.chapter_id == chapter_id).order_by(WritingChapterVersion.version_no.desc()))
    return [_version_response(session, project_id, run, manuscript, version) for version in versions]


@router.post('/workflow-runs/{run_id}/draft/chapters/{chapter_id}/revisions', status_code=202, response_model=RevisionResponse)
def revise_chapter(project_id: UUID, run_id: UUID, chapter_id: UUID, request: RevisionSubmission,
                   background: BackgroundTasks, session: Annotated[Session, Depends(get_db)]):
    from app.services.writing_versions import queue_revision, run_revision
    job, created = queue_revision(session, project_id, run_id, chapter_id, request.revision_id,
        request.base_version_id, request.mode, request.instruction)
    response = RevisionResponse.model_validate(job)
    if created:
        background.add_task(run_revision, project_id, run_id, chapter_id, job.revision_id)
        session.commit()
    return response


@router.get('/workflow-runs/{run_id}/draft/chapters/{chapter_id}/revisions/{revision_id}', response_model=RevisionResponse)
def get_revision(project_id: UUID, run_id: UUID, chapter_id: UUID, revision_id: UUID, session: Annotated[Session, Depends(get_db)]):
    from app.services.writing_versions import scoped_chapter
    _, manuscript, _ = scoped_chapter(session, project_id, run_id, chapter_id)
    job = session.get(WritingRevision, revision_id)
    if not job or job.manuscript_id != manuscript.manuscript_id or job.chapter_id != chapter_id:
        raise ResourceNotFound('Revision not found')
    return RevisionResponse.model_validate(job)


@router.post('/workflow-runs/{run_id}/draft/chapters/{chapter_id}/versions/{version_id}/restore', response_model=ChapterVersionResponse)
def restore_chapter_version(project_id: UUID, run_id: UUID, chapter_id: UUID, version_id: UUID,
                            request: RestoreSubmission, session: Annotated[Session, Depends(get_db)]):
    from app.services.writing_versions import restore_version, scoped_chapter
    version = restore_version(session, project_id, run_id, chapter_id, version_id, request.request_id)
    run, manuscript, _ = scoped_chapter(session, project_id, run_id, chapter_id)
    response = _version_response(session, project_id, run, manuscript, version)
    session.commit()
    return response
