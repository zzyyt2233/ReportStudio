"""粘贴文本解析：把一段文字还原成表格。

分列策略按优先级降级：Tab → 2 个以上连续空格 → 逗号 → 竖线。
只有一列就当纯文本，不硬拆。
"""

from __future__ import annotations

import re
import uuid

from ..model import Dataset
from .tabular import build_dataset

SPLIT_RE = re.compile(r"\s{2,}")


def sniff_delimiter(text: str) -> str:
    lines = [ln for ln in text.strip().splitlines() if ln.strip()]
    if not lines:
        return "none"
    sample = lines[:20]
    candidates = {
        "\t": _score(sample, "\t"),
        "multi_space": _score(sample, None),
        ",": _score(sample, ","),
        "，": _score(sample, "，"),
        "|": _score(sample, "|"),
    }
    best = max(candidates, key=lambda k: candidates[k])
    return best if candidates[best] >= 2 else "none"


def _score(lines: list[str], delim: str | None) -> int:
    """表格越长越规整，得分越高。用「每行列数一致且 >=2」来度量。"""
    counts = []
    for ln in lines:
        if delim is None:
            parts = [p for p in SPLIT_RE.split(ln.strip()) if p.strip()]
        else:
            parts = [p.strip() for p in ln.split(delim)]
        parts = [p for p in parts if p != ""]
        counts.append(len(parts))
    if not counts:
        return 0
    common = max(set(counts), key=counts.count)
    if common < 2:
        return 0
    return sum(1 for c in counts if c == common)


def to_grid(text: str, delim: str) -> list[list[str]]:
    grid = []
    for ln in text.strip().splitlines():
        if not ln.strip():
            continue
        if delim == "none":
            parts = [ln.strip()]
        elif delim == "multi_space":
            parts = [p.strip() for p in SPLIT_RE.split(ln.strip())]
        else:
            parts = [p.strip() for p in ln.split(delim)]
        grid.append([p if p != "" else None for p in parts])
    return [row for row in grid if any(v is not None for v in row)]


def _md_table_grid(text: str) -> list[list[str]] | None:
    """从网页或 Markdown 文档里复制出来的管道表格，优先按它处理。"""
    rows: list[list] = []
    for ln in text.splitlines():
        s = ln.strip()
        if not (s.startswith("|") and s.count("|") >= 2):
            if rows:
                break
            continue
        if re.fullmatch(r"[|\s\-:]+", s):        # 分隔行，跳过
            continue
        rows.append([c.strip() or None for c in s.strip("|").split("|")])
    return rows if len(rows) >= 2 else None


def parse_pasted(text: str, name: str = "粘贴内容") -> Dataset:
    md = _md_table_grid(text or "")
    if md:
        ds = build_dataset(md, name=name, source_type="paste")
        ds.warnings.insert(0, "按 Markdown 表格解析")
        ds.raw_text = (text or "")[:5000]
        return ds, md

    text = (text or "").strip()
    if not text:
        return Dataset(id=uuid.uuid4().hex[:8], name=name, source_type="paste",
                       columns=[], rows=[], warnings=["粘贴内容是空的"],
                       total_rows=0), []
    delim = sniff_delimiter(text)
    grid = to_grid(text, delim)
    warnings = []
    if delim == "none":
        # 全是单列：当成纯文本，逐行作为「行内容」一列
        lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
        grid = [["行内容"]] + [[ln] for ln in lines]
        warnings.append("未检测到表格结构，已按纯文本逐行处理")
    ds = build_dataset(grid, name=name, source_type="paste")
    ds.warnings = warnings + ds.warnings
    ds.raw_text = text[:5000]
    return ds, grid
