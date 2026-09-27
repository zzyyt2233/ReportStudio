"""PDF 导出。reportlab 直接排版，图表用 matplotlib 产出的 PNG。

中文字体从 Windows 系统目录注册，找不到就退回 STSong-Light（reportlab 自带）。
"""

from __future__ import annotations

import os
from xml.sax.saxutils import escape as _xml_escape

from ..analyze import detail_rows, detail_sort_label
from ..model import Dataset, ReportSpec


def _esc(s) -> str:
    """PDF 的 Paragraph 会把文本当 XML 解析，含 < & 等内容会崩，必须先转义。"""
    return _xml_escape("" if s is None else str(s))

try:
    from reportlab.lib import colors
    from reportlab.lib.enums import TA_CENTER
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
    from reportlab.lib.units import cm
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.cidfonts import UnicodeCIDFont
    from reportlab.pdfbase.ttfonts import TTFont
    from reportlab.platypus import (Image, Paragraph, SimpleDocTemplate,
                                    Spacer, Table, TableStyle)
except ImportError:
    SimpleDocTemplate = None

CN_FONT = "CN"


def _register_font() -> bool:
    if "CN" in pdfmetrics.getRegisteredFontNames():
        return True
    windir = os.environ.get("WINDIR", r"C:\Windows")
    candidates = [
        (os.path.join(windir, "Fonts", "msyh.ttc"), 0),
        (os.path.join(windir, "Fonts", "msyhbd.ttc"), 0),
        (os.path.join(windir, "Fonts", "simhei.ttf"), 0),
        (os.path.join(windir, "Fonts", "simsun.ttc"), 0),
    ]
    for path, idx in candidates:
        if os.path.exists(path):
            try:
                pdfmetrics.registerFont(TTFont(CN_FONT, path, subfontIndex=idx))
                return True
            except Exception:
                continue
    try:
        pdfmetrics.registerFont(UnicodeCIDFont("STSong-Light"))
        globals()["CN_FONT"] = "STSong-Light"
        return True
    except Exception:
        return False


def render_pdf(datasets: list[Dataset], spec: ReportSpec,
               chart_blocks: list[dict], metric_cards: list[dict],
               summary: str, out_dir: str,
               chart_images: dict[str, str]) -> str:
    if SimpleDocTemplate is None:
        raise RuntimeError("未安装 reportlab，无法导出 PDF")

    os.makedirs(out_dir, exist_ok=True)
    _register_font()
    font = CN_FONT if CN_FONT in pdfmetrics.getRegisteredFontNames() else "Helvetica"

    ss = getSampleStyleSheet()
    h1 = ParagraphStyle("h1", parent=ss["Title"], fontName=font, fontSize=20,
                        leading=28, alignment=TA_CENTER, spaceAfter=6)
    meta = ParagraphStyle("meta", parent=ss["Normal"], fontName=font,
                          fontSize=9, textColor=colors.HexColor("#5F5E5A"),
                          alignment=TA_CENTER, spaceAfter=16)
    sec = ParagraphStyle("sec", parent=ss["Heading2"], fontName=font,
                         fontSize=13, textColor=colors.HexColor("#185FA5"),
                         spaceBefore=14, spaceAfter=6)
    body = ParagraphStyle("body", parent=ss["Normal"], fontName=font,
                          fontSize=10, leading=16, spaceAfter=4)

    story = [Paragraph(_esc(spec.title), h1)]
    src = "、".join(d.name for d in datasets)
    story.append(Paragraph(f"数据来源：{_esc(src)}　共 {len(datasets)} 个数据表", meta))

    if summary:
        story.append(Paragraph("摘要结论", sec))
        story.append(Paragraph(_esc(summary), body))

    if metric_cards:
        story.append(Paragraph("关键指标", sec))
        data = [["指标", "数值", "说明"]]
        for c in metric_cards:
            data.append([c["title"], f"{c['value']} {c.get('unit', '')}".strip(),
                         c.get("detail", "")])
        t = Table(data, colWidths=[3.2 * cm, 3.4 * cm, 10 * cm])
        t.setStyle(TableStyle([
            ("FONTNAME", (0, 0), (-1, -1), font),
            ("FONTSIZE", (0, 0), (-1, -1), 9),
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#E6F1FB")),
            ("TEXTCOLOR", (0, 0), (-1, 0), colors.HexColor("#0C447C")),
            ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#D3D1C7")),
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ("ROWBACKGROUNDS", (0, 1), (-1, -1),
             [colors.white, colors.HexColor("#F7F7F5")]),
            ("TOPPADDING", (0, 0), (-1, -1), 4),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
        ]))
        story.append(t)

    if chart_blocks:
        story.append(Paragraph("图表", sec))
        for b in chart_blocks:
            img = chart_images.get(b.get("id", ""))
            if not img or not os.path.exists(img):
                continue
            story.append(Spacer(1, 6))
            story.append(Paragraph(_esc(b.get("title") or ""), body))
            try:
                story.append(Image(img, width=16 * cm, height=8 * cm))
            except Exception:
                pass

    if spec.template == "full":
        note = detail_sort_label(spec, {c.name for d in datasets for c in d.columns})
        sby = (getattr(spec, "detail_sort_by", "") or "").strip()
        sdesc = str(getattr(spec, "detail_sort_order", "asc") or "asc") == "desc"
        for ds in datasets:
            cols = ds.columns[:10]
            if not cols:
                continue
            head = _esc(ds.name) if len(datasets) > 1 else "明细数据"
            if note:
                head += f"（{_esc(note)}）"
            story.append(Paragraph(head, sec))
            mark = (" ↓" if sdesc else " ↑") if sby else ""
            data = [[c.name + (mark if sby and c.name == sby else "")
                     for c in cols]]
            for r in detail_rows(ds, spec)[:120]:
                row = []
                for c in cols:
                    v = r.get(c.name)
                    row.append("" if v is None else (
                        f"{v:,.2f}" if isinstance(v, float) else str(v)[:24]))
                data.append(row)
            t = Table(data, repeatRows=1, hAlign="LEFT")
            t.setStyle(TableStyle([
                ("FONTNAME", (0, 0), (-1, -1), font),
                ("FONTSIZE", (0, 0), (-1, -1), 7.5),
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#F1EFE8")),
                ("GRID", (0, 0), (-1, -1), 0.3, colors.HexColor("#D3D1C7")),
                ("TOPPADDING", (0, 0), (-1, -1), 2),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
            ]))
            story.append(t)
            if ds.total_rows > 120:
                story.append(Paragraph(f"共 {ds.total_rows} 行，此处展示前 120 行", meta))

    # 提示不随模板消失：数据来源、截断、缺失值处理这些是判断数字可不可信的依据
    warns = [w for d in datasets for w in d.warnings]
    if warns:
        story.append(Paragraph("解析提示", sec))
        for w in warns:
            story.append(Paragraph("· " + _esc(w), body))

    path = os.path.join(out_dir, "report.pdf")
    SimpleDocTemplate(path, pagesize=A4, leftMargin=2 * cm, rightMargin=2 * cm,
                      topMargin=1.8 * cm, bottomMargin=1.6 * cm,
                      title=spec.title).build(story)
    return path
