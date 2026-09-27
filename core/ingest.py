"""摄入路由：按扩展名把文件分发到对应解析器。

统一返回 [(Dataset, 原始网格), ...]，因为一份文件里可能含多张表
（多 sheet Excel、多表文档）。

原始网格必须留住：界面上「第几行是表头」指的是原始文件里的行号，
只留解析结果的话，用户改表头行时对不上位置。
"""

from __future__ import annotations

import os
import uuid

from .model import Dataset

TABLE_EXT = {".xlsx", ".xlsm", ".xls", ".csv", ".tsv"}
DOC_EXT = {".docx", ".txt", ".md", ".markdown", ".log"}
PDF_EXT = {".pdf"}


def ext_of(path: str) -> str:
    return os.path.splitext(path)[1].lower()


def is_supported(path: str) -> bool:
    return ext_of(path) in TABLE_EXT | DOC_EXT | PDF_EXT | _image_exts()


def _image_exts() -> set[str]:
    from .parsers.image import SUPPORTED
    return set(SUPPORTED)


def parse_file(path: str) -> list[tuple[Dataset, list[list] | None]]:
    """[(Dataset, 原始网格 or None), ...]

    表格类来源能拿到原始网格；文档/PDF/图片没法回溯原始排版，给 None，
    由调用方用 columns+rows 还原出一个等价网格。
    """
    ext = ext_of(path)

    if ext in TABLE_EXT:
        from .parsers.tabular import parse_table_all_sheets
        return parse_table_all_sheets(path)

    if ext in DOC_EXT:
        from .parsers.document import parse_document
        return [(ds, None) for ds in parse_document(path)]

    if ext in PDF_EXT:
        from .parsers.pdf import is_scanned, parse_pdf
        if is_scanned(path):
            from .parsers.image import parse_image
            return [(parse_image(path), None)]
        return [(ds, None) for ds in parse_pdf(path)]

    if ext in _image_exts():
        from .parsers.image import parse_image
        return [(parse_image(path), None)]

    return [(Dataset(id=uuid.uuid4().hex[:8], name=os.path.basename(path),
                     source_type="unknown", columns=[], rows=[],
                     warnings=[f"暂不支持的文件类型：{ext or '（无扩展名）'}"],
                     total_rows=0), None)]


def parse_files(paths: list[str]) -> list[tuple[Dataset, list[list] | None]]:
    out: list[tuple[Dataset, list[list] | None]] = []
    for p in paths:
        try:
            out.extend(parse_file(p))
        except Exception as e:
            out.append((Dataset(id=uuid.uuid4().hex[:8],
                                name=os.path.basename(p), source_type="unknown",
                                columns=[], rows=[],
                                warnings=[f"解析出错：{e}"], total_rows=0), None))
    return out


def dataset_grids(datasets: list[Dataset]) -> dict[str, list[list]]:
    """给界面预览用：Dataset 转回二维网格（含表头行）。"""
    grids = {}
    for ds in datasets:
        grid = [[c.name for c in ds.columns]]
        for r in ds.rows:
            grid.append([r.get(c.name) for c in ds.columns])
        grids[ds.id] = grid
    return grids
