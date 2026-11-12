"""Read-only export snapshots and renderers for the current manuscript."""
from dataclasses import dataclass
from io import BytesIO
from threading import Lock
from uuid import UUID
from xml.sax.saxutils import escape

from docx import Document
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Cm, Pt, RGBColor
from reportlab.lib.enums import TA_CENTER
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import cm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.writing import get_draft
from app.api.review import _response
from app.models.review import ReviewReport
from app.models.research_source import ResearchSource
from app.models.writing_segments import WritingManuscriptAttempt, WritingUnit
from app.services.project_scope import ProjectConflict, ProjectScope
from app.services.workflow_run import WorkflowRunService
from app.services.writing_versions import latest_version

_pdf_lock = Lock()


@dataclass(frozen=True)
class ExportSnapshot:
    title: str
    chapters: tuple
    references: tuple
    review_lines: tuple


def snapshot(session: Session, project_id: UUID, run_id: UUID, *, include_review: bool = False) -> ExportSnapshot:
    # The first query starts the snapshot. MySQL's repeatable-read snapshot keeps
    # all chapter/version/source reads consistent while revisions are committed.
    if session.get_bind().dialect.name == 'mysql':
        session.connection(execution_options={'isolation_level': 'REPEATABLE READ'})
    project = ProjectScope(session).require_project(project_id, action='read')
    run = WorkflowRunService(session).get(project_id, run_id)
    draft = get_draft(project_id, run_id, session)
    attempt = session.get(WritingManuscriptAttempt, run_id)
    if run.status != 'completed' or draft.status != 'completed' or not draft.supports_versions or not attempt or not draft.chapters:
        raise ProjectConflict('The current manuscript is not complete')
    if any(ch.status != 'completed' or not ch.paragraphs for ch in draft.chapters):
        raise ProjectConflict('The current manuscript is not complete')
    ids = {citation.source_id for chapter in draft.chapters for paragraph in chapter.paragraphs for citation in paragraph.citations}
    sources = {source.source_id: source for source in session.scalars(select(ResearchSource).where(
        ResearchSource.project_id == project_id, ResearchSource.thread_id == run.thread_id, ResearchSource.source_id.in_(ids)))}
    if set(sources) != ids:
        raise ProjectConflict('Citation sources are unavailable')
    numbers = {}
    chapters = []
    references = []
    for chapter in draft.chapters:
        version = latest_version(session, attempt.manuscript_id, chapter.chapter_id)
        if not version:
            raise ProjectConflict('Chapter version is unavailable')
        paragraphs = []
        for paragraph in chapter.paragraphs:
            cited = []
            for citation in paragraph.citations:
                if citation.source_id not in numbers:
                    source = sources[citation.source_id]
                    numbers[citation.source_id] = len(numbers) + 1
                    authors = ', '.join(source.authors) if source.authors else ''
                    parts = [authors, source.title, str(source.year) if source.year is not None else '', source.journal or '',
                             ('DOI: ' + source.doi) if source.doi else '', source.url or '']
                    references.append(f"[{numbers[citation.source_id]}] " + '. '.join(item for item in parts if item))
                num = numbers[citation.source_id]
                if num not in cited:
                    cited.append(num)
            paragraphs.append(paragraph.text + ''.join(f'[{n}]' for n in cited))
        units = list(session.scalars(select(WritingUnit).where(WritingUnit.manuscript_id == attempt.manuscript_id,
            WritingUnit.chapter_id == chapter.chapter_id).order_by(WritingUnit.position)))
        # Only reuse stored unit headings if their exact paragraphs still make up
        # this version. A revised chapter may have a different structure.
        headings = []
        if units and all(unit.status == 'completed' and unit.paragraphs for unit in units):
            raw = [paragraph for unit in units for paragraph in unit.paragraphs]
            if raw == version.paragraphs:
                offset = 0
                for unit in units:
                    headings.append((offset, f'{chapter.position}.{unit.position} {unit.title}'))
                    offset += len(unit.paragraphs)
        chapters.append((chapter.position, chapter.title, str(version.version_id), version.version_no,
                         tuple(paragraphs), tuple(headings)))
    review_lines = []
    if include_review:
        reports = list(session.scalars(select(ReviewReport).where(ReviewReport.project_id == project_id,
            ReviewReport.run_id == run_id, ReviewReport.status == 'completed', ReviewReport.scope_chapter_id.is_(None))
            .order_by(ReviewReport.created_at.desc())))
        current = {str(c.chapter_id): str(latest_version(session, attempt.manuscript_id, c.chapter_id).version_id) for c in draft.chapters}
        report = next((item for item in reports if item.context_versions == current), None)
        if not report:
            raise ProjectConflict('No completed review report matches the current manuscript')
        detail = _response(session, project_id, run_id, report)
        if detail.context_stale or len(detail.units) != len(chapters) or any(u.status != 'completed' or not u.result for u in detail.units):
            raise ProjectConflict('Review report is incomplete')
        review_lines.append(('title', '论文质量审查报告'))
        review_lines.append(('body', project.title))
        review_lines.append(('body', '模型审查意见仅供人工核验，不能视为事实或学术真实性认证。'))
        for unit in detail.units:
            review_lines.append(('heading', f'{unit.chapter_title}（版本 {unit.version_no}）'))
            review_lines.append(('body', '审查总结：' + unit.result.summary))
            for issue in unit.result.issues:
                review_lines.append(('subheading', f'{issue.severity} · {issue.issue_type} · 第 {issue.paragraph_index} 段'))
                review_lines.extend(('body', label + text) for label, text in [('问题：', issue.description), ('依据：', issue.rationale), ('建议：', issue.suggestion)])
    return ExportSnapshot(project.title, tuple(chapters), tuple(references), tuple(review_lines))


def _set_font(style, name='宋体', size=12):
    style.font.name = name
    style.font.size = Pt(size)
    fonts = style._element.get_or_add_rPr().rFonts
    fonts.set(qn('w:eastAsia'), name)
    for attr in ('asciiTheme', 'hAnsiTheme', 'eastAsiaTheme', 'cstheme'):
        fonts.attrib.pop(qn('w:' + attr), None)


def render_docx(data: ExportSnapshot, *, review=False) -> bytes:
    doc = Document()
    section = doc.sections[0]
    section.page_width, section.page_height = Cm(21), Cm(29.7)
    section.top_margin = section.bottom_margin = Cm(2.54)
    section.left_margin = section.right_margin = Cm(3.0)
    _set_font(doc.styles['Normal'])
    for style, size in [('Title', 18), ('Heading 1', 16), ('Heading 2', 14)]:
        _set_font(doc.styles[style], '黑体', size)
        doc.styles[style].font.color.rgb = RGBColor(0, 0, 0)
    title_properties = doc.styles['Title']._element.get_or_add_pPr()
    border = title_properties.find(qn('w:pBdr'))
    if border is not None:
        title_properties.remove(border)
    normal = doc.styles['Normal'].paragraph_format
    normal.line_spacing = 1.5
    normal.space_after = Pt(6)
    footer = section.footer.paragraphs[0]
    footer.alignment = WD_ALIGN_PARAGRAPH.CENTER
    field = OxmlElement('w:fldSimple'); field.set(qn('w:instr'), 'PAGE'); footer._p.append(field)
    if review:
        for kind, text in data.review_lines:
            doc.add_paragraph(text, style={'title':'Title','heading':'Heading 1','subheading':'Heading 2','body':'Normal'}[kind])
    else:
        doc.add_paragraph(data.title, style='Title')
        for chapter in data.chapters:
            position, title, _, _, paragraphs = chapter[:5]
            headings = dict(chapter[5]) if len(chapter) > 5 else {}
            doc.add_heading(f'{position}. {title}', level=1)
            for index, text in enumerate(paragraphs):
                if index in headings:
                    doc.add_heading(headings[index], level=2)
                paragraph = doc.add_paragraph(text)
                paragraph.paragraph_format.first_line_indent = Pt(24)
        doc.add_heading('参考文献', level=1)
        for reference in data.references:
            doc.add_paragraph(reference)
    output = BytesIO(); doc.save(output); return output.getvalue()


def render_pdf(data: ExportSnapshot) -> bytes:
    # ReportLab's registered TrueType font state is process-global.
    with _pdf_lock:
        return _render_pdf(data)


def _render_pdf(data: ExportSnapshot) -> bytes:
    # Use an embedded TrueType collection font; fail explicitly if unavailable.
    import os
    font = os.path.join(os.environ.get('WINDIR', r'C:\Windows'), 'Fonts', 'simsun.ttc')
    if not os.path.isfile(font):
        raise RuntimeError('Chinese PDF font unavailable')
    if 'PaperAgentCJK' not in pdfmetrics.getRegisteredFontNames():
        pdfmetrics.registerFont(TTFont('PaperAgentCJK', font, subfontIndex=0))
    out = BytesIO()
    doc = SimpleDocTemplate(out, pagesize=A4, leftMargin=3*cm, rightMargin=3*cm, topMargin=2.54*cm, bottomMargin=2.54*cm)
    normal = ParagraphStyle('body', fontName='PaperAgentCJK', fontSize=12, leading=18, spaceAfter=8, wordWrap='CJK', firstLineIndent=24)
    heading = ParagraphStyle('heading', parent=normal, fontSize=16, leading=24, firstLineIndent=0, spaceBefore=12, keepWithNext=True)
    title_style = ParagraphStyle('title', parent=heading, fontSize=18, leading=27, alignment=TA_CENTER)
    ref_style = ParagraphStyle('references', parent=normal, firstLineIndent=0)
    def paragraph(text, style):
        return Paragraph(escape(text).replace('\n', '<br/>'), style)
    story = [paragraph(data.title, title_style), Spacer(1, 12)]
    subheading = ParagraphStyle('subheading', parent=heading, fontSize=14, leading=21)
    for chapter in data.chapters:
        position, title, _, _, paragraphs = chapter[:5]
        headings = dict(chapter[5]) if len(chapter) > 5 else {}
        story.append(paragraph(f'{position}. {title}', heading))
        for index, text in enumerate(paragraphs):
            if index in headings:
                story.append(paragraph(headings[index], subheading))
            story.append(paragraph(text, normal))
    story.append(paragraph('参考文献', heading))
    story.extend(paragraph(text, ref_style) for text in data.references)
    def page(canvas, document):
        canvas.setFont('PaperAgentCJK', 10)
        canvas.drawCentredString(A4[0]/2, 1.4*cm, str(document.page))
    doc.build(story, onFirstPage=page, onLaterPages=page)
    return out.getvalue()
