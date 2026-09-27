"""文档解析：docx / txt / md。

一份文档里可能有多张表，所以返回 Dataset 列表：
- docx：抽取正文段落 + 所有内嵌表格
- md / txt：抽 Markdown 表格，再从「指标：值」这类行里抽键值对
- 都没有的话，退回成按行罗列的纯文本表
"""

from __future__ import annotations

import os
import re
import uuid

from ..model import Column, Dataset
from .paste import sniff_delimiter, to_grid

SEP_RE = re.compile(r"[:：=＝]")
KV_RE = re.compile(r"^\s*([^\s:：=＝]{1,30})\s*[:：=＝]\s*(.+?)\s*$")


def _basename(path: str) -> str:
    return os.path.basename(path)


def _from_grid(grid: list[list], name: str, source: str,
               sheet: str | None = None) -> Dataset:
    from .tabular import build_dataset

    ds = build_dataset(grid, name=f"{name}｜{sheet}" if sheet else name,
                       source_type=source)
    return ds


def parse_docx(path: str) -> list[Dataset]:
    from docx import Document

    doc = Document(path)
    name = _basename(path)
    out: list[Dataset] = []

    for i, tbl in enumerate(doc.tables, 1):
        grid = [[c.text.strip() if c.text.strip() else None for c in row.cells]
                for row in tbl.rows]
        if grid:
            out.append(_from_grid(grid, name, "docx", f"表{i}"))

    body = [p.text.strip() for p in doc.paragraphs if p.text.strip()]
    if body:
        kv = _kv_rows(body)
        if kv:
            ds = Dataset(id=uuid.uuid4().hex[:8], name=f"{name}｜正文指标",
                         source_type="docx",
                         columns=[Column("指标", "text"), Column("数值", "text")],
                         rows=kv, raw_text="\n".join(body[:200]),
                         total_rows=len(kv))
            out.append(ds)
        elif not out:
            out.append(_lines_dataset(body, name, "docx"))
    if not out:
        out.append(Dataset(id=uuid.uuid4().hex[:8], name=name,
                           source_type="docx", columns=[], rows=[],
                           warnings=["文档里没找到表格或指标行"], total_rows=0))
    return out


def parse_text(path: str) -> list[Dataset]:
    try:
        with open(path, "r", encoding="utf-8") as f:
            text = f.read()
    except UnicodeDecodeError:
        with open(path, "r", encoding="gbk", errors="ignore") as f:
            text = f.read()
    return parse_text_content(text, _basename(path))


def parse_text_content(text: str, name: str = "文本") -> list[Dataset]:
    out: list[Dataset] = []
    lines = text.splitlines()

    md_tables = _extract_md_tables(lines)
    for i, grid in enumerate(md_tables, 1):
        out.append(_from_grid(grid, name, "text", f"表{i}"))

    kv = _kv_rows([ln.strip() for ln in lines if ln.strip()])
    if kv:
        out.append(Dataset(id=uuid.uuid4().hex[:8], name=f"{name}｜指标行",
                           source_type="text",
                           columns=[Column("指标", "text"),
                                    Column("数值", "text")],
                           rows=kv, raw_text=text[:5000], total_rows=len(kv)))

    if not out:
        body = [ln.strip() for ln in lines if ln.strip()]
        ds = _lines_dataset(body, name, "text") if body else Dataset(
            id=uuid.uuid4().hex[:8], name=name, source_type="text",
            columns=[], rows=[], warnings=["文件是空的"], total_rows=0)
        out.append(ds)

    out[-1].raw_text = out[-1].raw_text or text[:5000]
    return out


def _extract_md_tables(lines: list[str]) -> list[list[list[str]]]:
    """抓 Markdown 管道表格。"""
    tables: list[list[list]] = []
    cur: list[list] = []
    for ln in lines:
        s = ln.strip()
        if s.startswith("|") and s.endswith("|"):
            if re.fullmatch(r"[|\s\-:]+", s):      # 分隔行，跳过
                continue
            cur.append([c.strip() or None for c in s.strip("|").split("|")])
        else:
            if len(cur) >= 2:
                tables.append(cur)
            cur = []
    if len(cur) >= 2:
        tables.append(cur)
    return tables


def _kv_rows(lines: list[str]) -> list[dict]:
    """抽「销售额：1200 万」这种行。数值列会在 tabular 里再判类型。"""
    rows = []
    seen = set()
    for ln in lines:
        m = KV_RE.match(ln)
        if not m:
            continue
        key, val = m.group(1).strip(), m.group(2).strip()
        if len(key) > 30 or len(val) > 80 or key in seen:
            continue
        if SEP_RE.search(val):
            continue
        seen.add(key)
        rows.append({"指标": key, "数值": val})
    return rows


def _lines_dataset(lines: list[str], name: str, source: str) -> Dataset:
    return Dataset(id=uuid.uuid4().hex[:8], name=name, source_type=source,
                   columns=[Column("行内容", "text")],
                   rows=[{"行内容": ln} for ln in lines],
                   raw_text="\n".join(lines[:200]), total_rows=len(lines))


def parse_document(path: str) -> list[Dataset]:
    lower = path.lower()
    if lower.endswith(".docx"):
        return parse_docx(path)
    return parse_text(path)
