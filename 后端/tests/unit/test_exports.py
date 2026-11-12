from io import BytesIO

from docx import Document
from pypdf import PdfReader

from app.services.exports import ExportSnapshot, render_docx, render_pdf


def test_docx_and_pdf_contain_same_text_citations_and_references():
    snapshot = ExportSnapshot('中文论文', ((1, '研究背景', 'id1', 1, ('正文内容[1][2]', '后续内容[1]'), ((0, '1.1 已保存的小节'),)),
        (2, '研究方法', 'id2', 2, ('第二章内容[2]',))),
        ('[1] 张三. 第一篇. 2024. DOI: 10.1234/one', '[2] 第二篇. 2025'), ())
    word = Document(BytesIO(render_docx(snapshot)))
    pdf = PdfReader(BytesIO(render_pdf(snapshot)))
    word_text = '\n'.join(p.text for p in word.paragraphs)
    pdf_text = '\n'.join(p.extract_text() for p in pdf.pages)
    for text in ('中文论文', '研究背景', '1.1 已保存的小节', '研究方法', '正文内容[1][2]', '后续内容[1]', '第二章内容[2]',
                 '参考文献', '10.1234/one', '[2] 第二篇'):
        assert text in word_text and text in pdf_text
    assert not pdf.is_encrypted
    assert any(p.text == '1.1 已保存的小节' and p.style.name == 'Heading 2' for p in word.paragraphs)


def test_review_is_separate_and_contains_locations_and_recommendations():
    snapshot = ExportSnapshot('论文', (), (), (('title', '论文质量审查报告'), ('heading', '章节（版本 2）'),
        ('subheading', 'high · evidence · 第 1 段'), ('body', '问题：依据不足'), ('body', '建议：补充来源')))
    doc = Document(BytesIO(render_docx(snapshot, review=True)))
    text = '\n'.join(p.text for p in doc.paragraphs)
    assert all(value in text for value in ('论文质量审查报告', '版本 2', '第 1 段', '问题：依据不足', '建议：补充来源'))
