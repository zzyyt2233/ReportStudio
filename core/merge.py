"""多文件整合：判断哪些表结构相同可以合并，哪些该各自成章。

判定口径：列名集合的重合度（取较小的那份做分母）+ 同名列的类型是否一致。
界面上会给出建议，用户可以手动改。
"""

from __future__ import annotations

import uuid

from .model import Column, Dataset
from .parsers.tabular import _to_number

DEFAULT_THRESHOLD = 0.7

# 类型强弱：合并同名列时，谁更"精确"听谁的（数字 > 日期 > 文本）
_DTYPE_RANK = {"text": 0, "date": 1, "number": 2}


def _reconcile_dtype(types: list[str]) -> str:
    if not types:
        return "text"
    return max(set(types), key=lambda t: _DTYPE_RANK.get(t, 0))


def _coerce(val: object, dtype: str) -> object:
    """把值规整到目标列类型。

    合并时一张表的同名列是 number、另一张是 text（常见：一张表混了脏单元格被误判文本），
    若不规整，number 表里干干净净的数字会被当成文本、出不了图、静默丢失。
    - number：尽力转数字（"100"→100、200→200、不可解析→None）
    - text：统一转字符串
    - date：原样保留
    """
    if val is None:
        return None
    if dtype == "number":
        return _to_number(val)
    if dtype == "text":
        return str(val)
    return val


def _canon(name: str) -> str:
    """列名归一化：去空格、全角转半角、统一小写，避免「销售额 」和「销售额」对不上。"""
    s = str(name).strip().replace(" ", "").replace("\u3000", "")
    return s.lower()


def similarity(a: Dataset, b: Dataset) -> float:
    """两张表的结构相似度，0~1。"""
    ca = {_canon(c.name): c.dtype for c in a.columns}
    cb = {_canon(c.name): c.dtype for c in b.columns}
    if not ca or not cb:
        return 0.0
    common = set(ca) & set(cb)
    if not common:
        return 0.0
    overlap = len(common) / min(len(ca), len(cb))
    type_ok = sum(1 for k in common if ca[k] == cb[k]) / len(common)
    return overlap * 0.7 + type_ok * 0.3


def group_datasets(datasets: list[Dataset],
                   threshold: float = DEFAULT_THRESHOLD
                   ) -> list[list[Dataset]]:
    """贪心聚类：结构相近的放一组。"""
    if len(datasets) <= 1:
        return [datasets] if datasets else []
    groups: list[list[Dataset]] = []
    for ds in datasets:
        placed = False
        for g in groups:
            score = max(similarity(ds, x) for x in g)
            if score >= threshold:
                g.append(ds)
                placed = True
                break
        if not placed:
            groups.append([ds])
    return groups


def merge_group(group: list[Dataset]) -> Dataset:
    """把一组同构表合并成一张大表，自动补一列「来源」。

    同名列若在各来源里类型不一致（一张 text、一张 number），合并列取较强类型，
    并把值按目标类型规整，避免数字被当成文本而静默丢失。
    """
    if len(group) == 1:
        return group[0]

    # 收齐所有列的归一名、原始名、出现过的类型与单位
    info: dict[str, dict] = {}
    order: list[str] = []
    for ds in group:
        for c in ds.columns:
            k = _canon(c.name)
            if k not in info:
                info[k] = {"name": c.name, "dtypes": set(), "units": set()}
                order.append(k)
            info[k]["dtypes"].add(c.dtype)
            if c.unit:
                info[k]["units"].add(c.unit)

    src_col = "来源"
    while _canon(src_col) in info:
        src_col += "_"

    columns: list[Column] = []
    for k in order:
        dtype = _reconcile_dtype(list(info[k]["dtypes"]))
        unit = next(iter(info[k]["units"]), None)
        columns.append(Column(name=info[k]["name"], dtype=dtype, unit=unit))

    def _target_name(ds: Dataset, actual: str) -> str | None:
        k = _canon(actual)
        for c in columns:
            if _canon(c.name) == k:
                return c.name
        return None

    def _dtype_of(name: str) -> str:
        for c in columns:
            if c.name == name:
                return c.dtype
        return "text"

    rows: list[dict] = []
    for ds in group:
        for r in ds.rows:
            rec = {c.name: None for c in columns}
            for actual, val in r.items():
                tgt = _target_name(ds, actual)
                if tgt:
                    rec[tgt] = _coerce(val, _dtype_of(tgt))
            rec[src_col] = ds.name
            rows.append(rec)

    columns.append(Column(name=src_col, dtype="text"))

    # 哪些列在来源间类型不一致，提示用户
    conflict_cols = [info[k]["name"] for k in order if len(info[k]["dtypes"]) > 1]
    warnings = [f"由 {len(group)} 张同构表合并而成，已自动增加「{src_col}」列"]
    if conflict_cols:
        warnings.append(
            "以下列在各来源中类型不一致（如有的表是文本、有的表是数字），"
            "已统一按更精确的类型合并并规整数值：" + "、".join(conflict_cols)
        )
    warnings += [w for ds in group for w in ds.warnings]

    return Dataset(
        id=uuid.uuid4().hex[:8],
        name=f"合并表（{len(group)} 份来源）",
        source_type="merged",
        columns=columns,
        rows=rows,
        raw_text="\n".join(ds.raw_text[:1000] for ds in group if ds.raw_text),
        warnings=warnings,
        total_rows=len(rows),
    )


def suggest_plan(datasets: list[Dataset],
                 threshold: float = DEFAULT_THRESHOLD) -> dict:
    """给界面的建议：哪些合并、哪些单独成章。"""
    groups = group_datasets(datasets, threshold)
    return {
        "groups": [[d.id for d in g] for g in groups],
        "group_names": [[d.name for d in g] for g in groups],
        "suggestion": "merge" if len(groups) == 1 and len(datasets) > 1
                      else "separate",
        "similarity": [[round(similarity(a, b), 2)
                        for b in datasets] for a in datasets],
    }
