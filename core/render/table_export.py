"""把「一张数据表」导出成 Word / PDF。

报告导出（core/render/docx_report.py、pdf_report.py）是为完整研报设计的，
需要 ReportSpec、图表、分析结论这些结构，拿来做单表导出太重。
这里只做一件事：标题 + 一张规整的表，让「导入 → 修正 → 导出」的闭环能出可交付文档。
"""
from __future__ import annotations

import os

from ..model import Dataset

# 单表导出一次写几千行已经够用了；再多的表拿来当文档也没人翻，
# 超了就截断并在文件里写明，不假装完整。
MAX_ROWS = 2000


def _cell(v) -> str:
    """单元格文本：None 给空串，其余转成不带换行的短文本。"""
    if v is None:
        return ""
    s = str(v)
    return s.replace("\r", " ").replace("\n", " ")


def _clipped(ds: Dataset) -> tuple[list[list], bool]:
    """取要写进文档的行，超过 MAX_ROWS 就截断并返回 True。"""
    headers = [c.name for c in ds.columns]
    data = [[_cell(r.get(h)) for h in headers] for r in ds.rows[:MAX_ROWS]]
    return data, len(ds.rows) > MAX_ROWS


def _note(ds: Dataset, clipped: bool) -> str:
    """文件末尾的说明：真实总行数、是否截断、来源类型。"""
    total = ds.total_rows or len(ds.rows)
    base = f"共 {total} 行 × {len(ds.columns)} 列"
    if clipped:
        base += f"，文档里只放前 {MAX_ROWS} 行（完整数据请导出 CSV / Excel）"
    return base


def render_table_docx(ds: Dataset, path: str) -> None:
    """数据表 → .docx。表格用内置 Table Grid 样式，带框线。"""
    from docx import Document
    from docx.oxml.ns import qn
    from docx.shared import Pt

    data, clipped = _clipped(ds)
    headers = [c.name for c in ds.columns]

    doc = Document()
    # 中文字体：不改 East Asia 字体的话，Word 里中文会掉成宋体方框或默认衬线。
    style = doc.styles["Normal"]
    style.font.name = "Microsoft YaHei"
    style.font.size = Pt(9)
    try:
        style.element.rPr.rFonts.set(qn("w:eastAsia"), "Microsoft YaHei")
    except Exception:
        pass

    doc.add_heading(ds.name or "数据表", level=0)
    if ds.warnings:
        for w in ds.warnings[:5]:
            doc.add_paragraph(str(w))

    tbl = doc.add_table(rows=1, cols=max(1, len(headers)))
    try:
        tbl.style = "Table Grid"
    except Exception:
        pass
    for i, h in enumerate(headers):
        cell = tbl.rows[0].cells[i]
        cell.text = str(h)
        for p in cell.paragraphs:
            for r in p.runs:
                r.font.bold = True
    for row in data:
        cells = tbl.add_row().cells
        for i, v in enumerate(row):
            cells[i].text = v

    doc.add_paragraph(_note(ds, clipped))
    doc.save(path)


def render_table_pdf(ds: Dataset, path: str) -> None:
    """数据表 → .pdf。用 reportlab 的表格，表头每页重复。"""
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4, landscape
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.lib.units import mm
    from reportlab.platypus import Paragraph, SimpleDocTemplate, Table, TableStyle

    from . import pdf_report

    pdf_report._register_font()
    font = getattr(pdf_report, "CN_FONT", "Helvetica")

    data, clipped = _clipped(ds)
    headers = [c.name for c in ds.columns]

    hs = ParagraphStyle("th", fontName=font, fontSize=7.5, leading=10,
                        textColor=colors.white)
    bs = ParagraphStyle("td", fontName=font, fontSize=7.5, leading=10)
    ns = ParagraphStyle("note", fontName=font, fontSize=9, leading=13)

    body = [[Paragraph(str(h), hs) for h in headers]]
    body += [[Paragraph(v, bs) for v in row] for row in data]

    pagesize = landscape(A4)
    doc = SimpleDocTemplate(path, pagesize=pagesize,
                            leftMargin=10 * mm, rightMargin=10 * mm,
                            topMargin=10 * mm, bottomMargin=10 * mm)
    avail = pagesize[0] - 20 * mm
    colw = avail / max(1, len(headers))

    tbl = Table(body, colWidths=[colw] * max(1, len(headers)), repeatRows=1)
    tbl.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#3c5a78")),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#b8bfc9")),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1),
         [colors.white, colors.HexColor("#f2f5f9")]),
        ("LEFTPADDING", (0, 0), (-1, -1), 3),
        ("RIGHTPADDING", (0, 0), (-1, -1), 3),
        ("TOPPADDING", (0, 0), (-1, -1), 2),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
    ]))

    title = Paragraph(f"<b>{_esc(ds.name or '数据表')}</b>",
                      ParagraphStyle("t", fontName=font, fontSize=13, leading=18))
    doc.build([title, tbl, Paragraph(_esc(_note(ds, clipped)), ns)])


def _esc(s) -> str:
    from xml.sax.saxutils import escape
    return escape(str(s))


def render_table(ds: Dataset, path: str, kind: str) -> None:
    """按 kind 分派：docx / pdf。"""
    if kind == "docx":
        render_table_docx(ds, path)
    elif kind == "pdf":
        render_table_pdf(ds, path)
    else:
        raise ValueError(f"不支持的导出格式：{kind}")
