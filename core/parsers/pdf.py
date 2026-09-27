"""PDF 解析。

分三种情况：
1. 有表格（无论有没有框线）→ 抽成结构化数据表
2. 纯文字 → 退化为「指标：值」键值对 → 再退化为按行罗列 → 同时保留全文文本
3. 几乎没有可提取文字 → 判定为扫描件，交给 image.py 走视觉模型

为什么分两路抽表：pdfplumber 默认的「框线策略」只认有横竖线的表格，
真实的报表经常是**无框线、靠对齐排版**的表（比如用空格对齐的 Excel 导出、Word 转的 PDF），
这种用框线策略一个表都抽不到，整份退化成「一行一条」纯文本，等于没抽。
所以这里先走框线策略，再补一路「按文字对齐」策略抓无框线表，最后去重。
"""

from __future__ import annotations

import os
import uuid

from ..model import Column, Dataset

MIN_TEXT_CHARS = 120          # 整份 PDF 可提取字符少于这个值，视为扫描件

# 两路抽表设置：框线（有横竖线） + 文字对齐（靠词 x/y 坐标推断列边界）
TABLE_SETTINGS_RULED = {"vertical_strategy": "lines", "horizontal_strategy": "lines"}
TABLE_SETTINGS_TEXT = {
    "vertical_strategy": "text",
    "horizontal_strategy": "text",
    "snap_y_tolerance": 4,
    "intersection_y_tolerance": 6,
    "intersection_x_tolerance": 4,
}


def is_scanned(path: str) -> bool:
    """只读前几页粗略判断，避免为了判断把整份文件都翻一遍。"""
    try:
        import pdfplumber
    except ImportError:
        return False
    try:
        with pdfplumber.open(path) as pdf:
            if not pdf.pages:
                return True
            n = min(3, len(pdf.pages))
            chars = sum(len(p.extract_text() or "") for p in pdf.pages[:n])
            return chars < MIN_TEXT_CHARS / 2
    except Exception:
        return False


def _clean(tbl: list[list]) -> list[list] | None:
    """清掉全空行、空串统一成 None；不合格的表返回 None。"""
    rows = []
    for row in tbl:
        r = [(c or "").strip() or None for c in row]
        if any(v is not None for v in r):
            rows.append(r)
    if len(rows) < 2:
        return None
    if max(len(r) for r in rows) < 2:      # 单列当成表没意义，留给正文处理
        return None
    return rows


def _grid_eq(a: list[list], b: list[list]) -> bool:
    if len(a) != len(b):
        return False
    for ra, rb in zip(a, b):
        if len(ra) != len(rb):
            return False
        for x, y in zip(ra, rb):
            if (x or "") != (y or ""):
                return False
    return True


def _is_subset(small: list[list], big: list[list]) -> bool:
    """small 是否 big 的逐单元格子集（去重用：文字策略常抽到框线策略的超集/重复）。"""
    if len(small) > len(big):
        return False
    for ra in small:
        if not any(_grid_eq([ra], [rb]) for rb in big):
            return False
    return True


def parse_pdf(path: str) -> list[Dataset]:
    try:
        import pdfplumber
    except ImportError:
        return [_error_ds(path, "未安装 pdfplumber，无法解析 PDF")]

    name = os.path.basename(path)
    tables: list[list[list]] = []
    body: list[str] = []
    try:
        with pdfplumber.open(path) as pdf:
            for page in pdf.pages:
                # 一路一页两策略：先框线，再文字对齐抓无框线表
                for settings in (TABLE_SETTINGS_RULED, TABLE_SETTINGS_TEXT):
                    try:
                        for tbl in (page.extract_tables(table_settings=settings) or []):
                            grid = _clean(tbl)
                            if not grid:
                                continue
                            # 去重：和已抽到的是同一张（或子集）就跳过
                            if any(_is_subset(grid, t) or _is_subset(t, grid)
                                   for t in tables):
                                continue
                            tables.append(grid)
                    except Exception:
                        pass
                txt = page.extract_text() or ""
                body.extend(ln.strip() for ln in txt.splitlines() if ln.strip())
    except Exception as e:
        return [_error_ds(path, f"PDF 打开失败：{e}")]

    out: list[Dataset] = []
    from .tabular import build_dataset

    for i, grid in enumerate(tables, 1):
        out.append(build_dataset(grid, name=f"{name}｜表{i}", source_type="pdf"))

    from .document import _kv_rows
    kv = _kv_rows(body)
    if kv:
        out.append(Dataset(id=uuid.uuid4().hex[:8], name=f"{name}｜正文指标",
                           source_type="pdf",
                           columns=[Column("指标", "text"), Column("数值", "text")],
                           rows=kv, raw_text="\n".join(body[:200]),
                           total_rows=len(kv)))

    full_text = "\n".join(body)
    # 正文全文：当正文明显多于已抽出的表行时，单列保留原文，方便用户直接看到/检索文本
    table_rows = sum(len(t) for t in tables)
    if full_text and len(body) - table_rows >= 10:
        out.append(Dataset(id=uuid.uuid4().hex[:8], name=f"{name}｜正文全文",
                           source_type="pdf",
                           columns=[Column("行内容", "text")],
                           rows=[{"行内容": ln} for ln in body],
                           raw_text=full_text[:5000], total_rows=len(body)))

    if not out:
        if body:
            out.append(Dataset(id=uuid.uuid4().hex[:8], name=name,
                               source_type="pdf",
                               columns=[Column("行内容", "text")],
                               rows=[{"行内容": ln} for ln in body],
                               raw_text=full_text[:5000], total_rows=len(body)))
        else:
            out.append(_error_ds(path, "PDF 里没找到表格也没有可提取文字，可能是扫描件"))
    out[-1].raw_text = out[-1].raw_text or full_text[:5000]
    return out


def _error_ds(path: str, msg: str) -> Dataset:
    return Dataset(id=uuid.uuid4().hex[:8], name=os.path.basename(path),
                   source_type="pdf", columns=[], rows=[], warnings=[msg],
                   total_rows=0)
