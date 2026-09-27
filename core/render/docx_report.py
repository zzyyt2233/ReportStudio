"""Word 导出。图表以 matplotlib 静态 PNG 插入，数据控制在一页内可读。"""

from __future__ import annotations

import os

from ..analyze import detail_rows, detail_sort_label
from ..model import Dataset, ReportSpec

try:
    from docx import Document
    from docx.enum.text import WD_ALIGN_PARAGRAPH
    from docx.shared import Inches, Pt, RGBColor
except ImportError:
    Document = None


def render_docx(datasets: list[Dataset], spec: ReportSpec,
                chart_blocks: list[dict], metric_cards: list[dict],
                summary: str, out_dir: str,
                chart_images: dict[str, str]) -> str:
    if Document is None:
        raise RuntimeError("未安装 python-docx，无法导出 Word")

    os.makedirs(out_dir, exist_ok=True)
    doc = Document()
    style = doc.styles["Normal"]
    style.font.name = "Microsoft YaHei"
    style.font.size = Pt(10.5)

    doc.add_heading(spec.title, level=0)
    src = "、".join(d.name for d in datasets)
    p = doc.add_paragraph(f"数据来源：{src}　共 {len(datasets)} 个数据表")
    p.runs[0].font.color.rgb = RGBColor(0x5F, 0x5E, 0x5A)
    p.runs[0].font.size = Pt(9)

    if summary:
        doc.add_heading("摘要结论", level=1)
        doc.add_paragraph(summary)

    if metric_cards:
        doc.add_heading("关键指标", level=1)
        t = doc.add_table(rows=1, cols=3)
        t.style = "Light Grid Accent 1"
        hdr = t.rows[0].cells
        hdr[0].text, hdr[1].text, hdr[2].text = "指标", "数值", "说明"
        for c in metric_cards:
            row = t.add_row().cells
            row[0].text = c["title"]
            row[1].text = f"{c['value']} {c.get('unit', '')}".strip()
            row[2].text = c.get("detail", "")

    if chart_blocks:
        doc.add_heading("图表", level=1)
        for b in chart_blocks:
            img = chart_images.get(b.get("id", ""))
            if not img or not os.path.exists(img):
                continue
            doc.add_paragraph(b.get("title") or "")
            doc.add_picture(img, width=Inches(6.2))
            doc.paragraphs[-1].alignment = WD_ALIGN_PARAGRAPH.CENTER

    if spec.template == "full":
        note = detail_sort_label(spec, {c.name for d in datasets for c in d.columns})
        for ds in datasets:
            cols = ds.columns[:12]
            if not cols:
                continue
            # 排序说明挂在标题里：Word 里没有「点表头排序」，不写清楚的话
            # 读者会以为这张表是随便排的
            head = (ds.name if len(datasets) > 1 else "明细数据") + (
                f"（{note}）" if note else "")
            doc.add_heading(head, level=1)
            t = doc.add_table(rows=1, cols=len(cols))
            t.style = "Light Grid Accent 1"
            sby = (getattr(spec, "detail_sort_by", "") or "").strip()
            for i, c in enumerate(cols):
                mark = ""
                if sby and c.name == sby:
                    mark = " ↓" if str(getattr(spec, "detail_sort_order", "asc")
                                       or "asc") == "desc" else " ↑"
                t.rows[0].cells[i].text = c.name + mark
            for r in detail_rows(ds, spec)[:200]:
                cells = t.add_row().cells
                for i, c in enumerate(cols):
                    v = r.get(c.name)
                    cells[i].text = "" if v is None else (
                        f"{v:,.2f}" if isinstance(v, float) else str(v))
            if ds.total_rows > 200:
                doc.add_paragraph(f"共 {ds.total_rows} 行，此处展示前 200 行")

    # 提示不随模板消失：数据来源、截断、缺失值处理这些是判断数字可不可信的依据
    warns = [w for d in datasets for w in d.warnings]
    if warns:
        doc.add_heading("解析提示", level=1)
        for w in warns:
            doc.add_paragraph(w, style="List Bullet")

    path = os.path.join(out_dir, "report.docx")
    doc.save(path)
    return path
