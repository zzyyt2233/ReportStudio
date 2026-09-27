"""PDF 抽取回归：重点验证无框线（靠对齐排版）的表也能被抽成多列数据表。

旧实现只靠 pdfplumber 的「框线策略」抽表，遇到无框线表就退化成单列「行内容」，
等于没抽。本套验证双策略抽表 + 去重 + 正文全文保留。
"""

import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.parsers.pdf import parse_pdf, is_scanned
from core.ingest import parse_file


def _make_borderless(path: str) -> None:
    """造一个真实的无框线表格 PDF（reportlab Platypus Table，不加网格线）。"""
    from reportlab.lib.pagesizes import A4
    from reportlab.platypus import SimpleDocTemplate, Table
    doc = SimpleDocTemplate(path, pagesize=A4)
    data = [["Region", "Q1", "Q2", "Q3", "Q4"],
            ["East", "1200", "1380", "1510", "1620"],
            ["South", "980", "1050", "1120", "1240"],
            ["North", "760", "820", "910", "1030"]]
    doc.build([Table(data, colWidths=[60, 50, 50, 50, 50])])


def _make_ruled(path: str) -> None:
    """造一个有框线的表格 PDF（加网格线样式）。"""
    from reportlab.lib.pagesizes import A4
    from reportlab.platypus import SimpleDocTemplate, Table, TableStyle
    from reportlab.lib import colors
    doc = SimpleDocTemplate(path, pagesize=A4)
    data = [["City", "Sales"], ["Beijing", "820"], ["Shanghai", "760"]]
    t = Table(data, colWidths=[80, 60])
    t.setStyle(TableStyle([("GRID", (0, 0), (-1, -1), 0.5, colors.black)]))
    doc.build([t])


def main() -> int:
    fails = []
    tmp = tempfile.mkdtemp(prefix="pdfextract_")

    bp = os.path.join(tmp, "borderless.pdf")
    _make_borderless(bp)
    rp = os.path.join(tmp, "ruled.pdf")
    _make_ruled(rp)

    # 1) 无框线表应抽成多列（>=2 列、>=2 行）的数据表
    ds_b = parse_pdf(bp)
    multi = [d for d in ds_b if len(d.columns) >= 2 and d.total_rows >= 2]
    if not multi:
        fails.append(f"无框线表未被抽成多列表（实际产出列数: "
                     f"{[len(d.columns) for d in ds_b]}）")
    else:
        # 数值应被正确还原（East 行 Q1=1200）
        east = next((r for d in multi for r in d.rows
                     if str(r.get(d.columns[0].name)) == "East"), None)
        if east is None or float(east.get("Q1", 0)) != 1200.0:
            fails.append("无框线表数值抽取错误")

    # 2) 有框线表（旧路径）仍正常工作
    ds_r = parse_pdf(rp)
    if not any(len(d.columns) >= 2 and d.total_rows >= 1 for d in ds_r):
        fails.append("有框线表抽取失败")

    # 3) 去重：无框线表不应被两路策略重复抽成多张
    if len(multi) > 1:
        fails.append(f"无框线表被重复抽取（{len(multi)} 张）")

    # 4) 经 ingest 路由（模拟上传）也能走到 pdf 解析
    out = parse_file(bp)
    if not any(len(ds.columns) >= 2 for ds, _ in out):
        fails.append("ingest.parse_file 未把 PDF 路由到多列表解析")

    if fails:
        print("PDF 抽取回归失败：")
        for f in fails:
            print("  -", f)
        return 1
    print("PDF 抽取回归全部通过 ✓（无框线表与有框线表均正确抽成多列数据表）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
