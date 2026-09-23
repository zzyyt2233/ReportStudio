"""PDF 解析。

分三种情况：
1. 有表格 → 直接抽表（pdfplumber 的表格线检测）
2. 纯文字 → 退化为行 → 抽「指标：值」键值对 → 再退化为按行罗列
3. 几乎没有可选文字 → 判定为扫描件，交给 image.py 走视觉模型
"""

from __future__ import annotations

import os
import uuid

from ..model import Column, Dataset

MIN_TEXT_CHARS = 120          # 整份 PDF 可提取字符少于这个值，视为扫描件


def is_scanned(path: str) -> bool:
    """只读第一页粗略判断，避免为了判断把整份文件都翻一遍。"""
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
                try:
                    for tbl in (page.extract_tables() or []):
                        cleaned = [[(c or "").strip() or None for c in row]
                                   for row in tbl]
                        cleaned = [r for r in cleaned
                                   if any(v is not None for v in r)]
                        if len(cleaned) >= 2 and max(len(r) for r in cleaned) >= 2:
                            tables.append(cleaned)
                except Exception:
                    pass
                txt = page.extract_text() or ""
                body.extend(ln.strip() for ln in txt.splitlines() if ln.strip())
    except Exception as e:
        return [_error_ds(path, f"PDF 打开失败：{e}")]

    out: list[Dataset] = []
    from .tabular import build_dataset

    for i, grid in enumerate(tables, 1):
        out.append(build_dataset(grid, name=f"{name}｜表{i}",
                                 source_type="pdf"))

    from .document import _kv_rows

    kv = _kv_rows(body)
    if kv:
        out.append(Dataset(id=uuid.uuid4().hex[:8], name=f"{name}｜正文指标",
                           source_type="pdf",
                           columns=[Column("指标", "text"),
                                    Column("数值", "text")],
                           rows=kv, raw_text="\n".join(body[:200]),
                           total_rows=len(kv)))

    if not out:
        if body:
            out.append(Dataset(id=uuid.uuid4().hex[:8], name=name,
                               source_type="pdf",
                               columns=[Column("行内容", "text")],
                               rows=[{"行内容": ln} for ln in body],
                               raw_text="\n".join(body[:200]),
                               total_rows=len(body)))
        else:
            out.append(_error_ds(path, "PDF 里没找到表格也没有可提取文字，可能是扫描件"))
    out[-1].raw_text = out[-1].raw_text or "\n".join(body[:200])
    return out


def _error_ds(path: str, msg: str) -> Dataset:
    return Dataset(id=uuid.uuid4().hex[:8], name=os.path.basename(path),
                   source_type="pdf", columns=[], rows=[], warnings=[msg],
                   total_rows=0)
