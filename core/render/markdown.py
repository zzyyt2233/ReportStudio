"""Markdown 源文件：方便二次编辑。

兼容性原则：所有来自用户文件的文本，拼进 Markdown 前都要过 _cell，
把管道符（撑破表格）、换行（撑破行）、以及引号和尖括号（被渲染成标记）处理掉。
没有图片、没有某种输入、某项识别失败，都不应让整份报告崩掉。
"""

from __future__ import annotations

import os
from datetime import datetime

from ..analyze import detail_rows, detail_sort_label
from ..model import Dataset, ReportSpec


def _cell(v) -> str:
    """Markdown 表格单元格：管道符会撑破表格、换行会撑破行、尖括号会被当作标签。

    转义顺序必须先把 & 转掉，否则 &lt; 里的 & 会被二次转义成 &amp;lt;。
    """
    s = "" if v is None else str(v)
    return (s.replace("\\", "\\\\").replace("&", "&amp;")
            .replace("|", "\\|").replace("\n", " ").replace("\r", " ")
            .replace("<", "&lt;").replace(">", "&gt;").strip())


def render_markdown(datasets: list[Dataset], spec: ReportSpec,
                    chart_blocks: list[dict], metric_cards: list[dict],
                    summary: str, out_dir: str) -> str:
    os.makedirs(out_dir, exist_ok=True)
    lines = [f"# {_cell(spec.title)}", "",
             f"> 生成时间 {datetime.now().strftime('%Y-%m-%d %H:%M')} ｜ "
             f"来源：{'、'.join(_cell(d.name) for d in datasets)}", ""]

    if summary:
        lines += ["## 摘要结论", "", summary, ""]

    if metric_cards:
        lines += ["## 关键指标", "", "| 指标 | 数值 | 说明 |", "| --- | --- | --- |"]
        for c in metric_cards:
            lines.append(
                f"| {_cell(c['title'])} | {_cell(c['value'])} {_cell(c.get('unit', ''))} "
                f"| {_cell(c['detail'])} |")
        lines.append("")

    if chart_blocks:
        lines += ["## 图表数据", ""]
        for b in chart_blocks:
            opt = b["option"]
            lines.append(f"### {_cell(b.get('title') or '图表')}")
            xname = opt.get("xAxis", {}).get("name") or "维度"
            series = opt.get("series", [])
            if opt.get("xAxis", {}).get("data"):
                cats = opt["xAxis"]["data"]
                header = "| " + _cell(xname) + " | " + \
                    " | ".join(_cell(s["name"]) for s in series) + " |"
                lines += [header,
                          "| " + " | ".join(["---"] * (len(series) + 1)) + " |"]
                for i, cat in enumerate(cats):
                    vals = [s["data"][i] if i < len(s["data"]) else "" for s in series]
                    lines.append("| " + _cell(cat) + " | " +
                                 " | ".join(_cell(v) for v in vals) + " |")
            lines.append("")

    if spec.template == "full" and datasets:
        note = detail_sort_label(spec, {c.name for d in datasets for c in d.columns})
        lines += ["## 明细数据" + (f"（{note}）" if note else ""), ""]
        for ds in datasets:
            cols = [c.name for c in ds.columns]
            if not cols:
                continue
            if len(datasets) > 1:
                lines += [f"### {_cell(ds.name)}（{ds.total_rows} 行）", ""]
            lines += ["| " + " | ".join(_cell(c) for c in cols) + " |",
                      "| " + " | ".join(["---"] * len(cols)) + " |"]
            for r in detail_rows(ds, spec)[:300]:
                lines.append("| " + " | ".join(
                    "" if r.get(c) is None else _cell(r.get(c)) for c in cols) + " |")
            lines.append("")

    for d in datasets:
        if d.warnings:
            lines += [f"## 解析提示 · {_cell(d.name)}", ""]
            lines += [f"- {_cell(w)}" for w in d.warnings]
            lines.append("")

    path = os.path.join(out_dir, "report.md")
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    return path
