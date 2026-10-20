"""导出路由：数据表导出 CSV/Excel/Word/PDF，报告导出 Word/PDF/拼图/Excel。"""
from __future__ import annotations

import os
import time
from datetime import datetime

from fastapi import APIRouter, HTTPException

from core.chartset import layout_columns
from core.fsutil import _rm_file
from core.render.combine import combine_images
from core.render.docx_report import render_docx
from core.render.pdf_report import render_pdf
from core.render.table_export import MAX_ROWS as EXPORT_MAX_ROWS
from core.render.table_export import render_table
from core.state import (OUT_DIR, REPORTS, ROOT, _LOCK, _get, _report_key,
                        _safe)

router = APIRouter(prefix="/api", tags=["export"])

@router.post('/export_dataset')
def export_dataset(payload: dict):
    """把会话里（可能刚在界面上修正过）的数据表导出成 CSV / Excel。

    补上「导入 → 改表头行 / 改列类型 / 剔合计行 → **导出**」这条闭环里断掉的最后一环：
    以前这些修正只能用来画图，拿不回去。
    """
    kind = str(payload.get("kind") or "csv").lower()
    if kind not in ("csv", "xlsx", "docx", "pdf"):
        return {"ok": False, "error": "只支持 csv / xlsx / docx / pdf"}
    did = payload.get("id")
    item = _get(did)
    if not item:
        return {"ok": False, "error": "数据表不在当前会话里，请重新导入"}
    ds = item["dataset"]
    if not ds.columns:
        return {"ok": False, "error": "这张表没有列，导不出东西"}

    out_dir = os.path.join(OUT_DIR, "_exports")
    os.makedirs(out_dir, exist_ok=True)
    _sweep_exports(out_dir)

    base = _safe(ds.name) or "dataset"
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    path = os.path.join(out_dir, f"{base}_{stamp}.{kind}")
    headers = [c.name for c in ds.columns]
    try:
        if kind == "csv":
            import csv
            # utf-8-sig（带 BOM）是刻意的：不带 BOM 的 UTF-8 CSV 用 Excel
            # 双击打开会把中文显示成乱码，用户第一反应是「导出坏了」。
            with open(path, "w", encoding="utf-8-sig", newline="") as f:
                w = csv.writer(f)
                w.writerow(headers)
                for r in ds.rows:
                    w.writerow(["" if r.get(h) is None else r.get(h) for h in headers])
        elif kind == "xlsx":
            from openpyxl import Workbook
            wb = Workbook()
            ws = wb.active
            ws.title = (base or "data")[:31]
            ws.append(headers)
            for r in ds.rows:
                ws.append([r.get(h) for h in headers])
            wb.save(path)
        else:
            # docx / pdf：单表文档，不带分析结论。超过上限会截断并在文档里写明。
            render_table(ds, path, kind)
    except Exception as e:
        return {"ok": False, "error": f"导出失败：{type(e).__name__}: {e}"}

    rel = os.path.relpath(path, ROOT).replace("\\", "/")
    return {"ok": True, "url": f"/{rel}", "path": path,
            "rows": len(ds.rows), "cols": len(headers),
            "written": min(len(ds.rows), EXPORT_MAX_ROWS)}


def _sweep_exports(d: str, max_age_days: int = 7, limit: int = 10) -> None:
    """回收过期的数据表导出文件。限量删除，避免撞上批量删除守卫。"""
    cutoff = time.time() - max_age_days * 86400
    n = 0
    try:
        for fn in os.listdir(d):
            if n >= limit:
                break
            if fn.endswith(".deleted"):
                continue          # 上一次删除留下的残片，交给 cleanup_residue 统一回收
            p = os.path.join(d, fn)
            try:
                if os.path.isfile(p) and os.path.getmtime(p) < cutoff:
                    _rm_file(p)
                    n += 1
            except BaseException:
                continue
    except BaseException:
        pass


@router.post('/export')
def export(payload: dict):
    """导出 Word / PDF / 拼图。

    同步 def：python-docx、reportlab 都是纯 CPU + 同步 I/O，
    PDF 还要经过一次浏览器内核渲染，实测 1~5 秒。
    """
    kind = payload.get("kind")
    if kind not in ("docx", "pdf", "png", "xlsx"):
        raise HTTPException(400, "只支持 docx / pdf / png / xlsx")
    out_dir = payload.get("dir")
    if not out_dir or not os.path.isdir(out_dir):
        raise HTTPException(404, "报告目录不存在，请重新生成")
    with _LOCK:
        sub = REPORTS.get(_report_key(out_dir))
    if not sub:
        raise HTTPException(404, "这份报告已过期，请重新生成后再导出")

    chart_images = sub["chart_images"]
    if not chart_images:
        img_dir = os.path.join(out_dir, "charts")
        chart_images = {b["id"]: os.path.join(img_dir, b["id"] + ".png")
                        for b in sub["chart_blocks"]}
        chart_images = {k: v for k, v in chart_images.items() if os.path.exists(v)}

    try:
        if kind == "png":
            # 拼成一张总图。顺序必须跟报告里一致，否则图文对不上。
            paths = [chart_images[b["id"]] for b in sub["chart_blocks"]
                     if b["id"] in chart_images]
            if not paths:
                return {"ok": False,
                        "error": "这份报告没有可拼的图表（可能是纯文本/图片输入，"
                                 "或静态图渲染未成功）"}
            p = combine_images(paths, os.path.join(out_dir, "charts.png"),
                               layout_columns(payload.get("cols")))
            if not p:
                return {"ok": False, "error": "图片拼接失败，可在报告里逐张「保存为 PNG」"}
        elif kind == "docx":
            p = render_docx(sub["datasets"], sub["spec"], sub["chart_blocks"],
                            sub["metric_cards"], sub["summary"], out_dir,
                            chart_images)
        elif kind == "xlsx":
            from core.excel_export import save_report_excel
            p = save_report_excel(sub, out_dir)
        else:
            p = render_pdf(sub["datasets"], sub["spec"], sub["chart_blocks"],
                           sub["metric_cards"], sub["summary"], out_dir,
                           chart_images)
    except RuntimeError as e:
        return {"ok": False, "error": str(e)}

    rel = os.path.relpath(p, ROOT).replace("\\", "/")
    return {"ok": True, "url": f"/{rel}", "path": p}
