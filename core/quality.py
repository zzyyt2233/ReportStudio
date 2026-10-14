"""数据质量闸门：ingest 之后、出图之前，对每列做轻量体检。

不碰 DataHub.DataGate 那种深度校验/对账——那是另一个套件的事。
这里只做本地前置过滤，拦住「垃圾进垃圾图」最常见的几类：
  - 完全重复的行（会虚增合计、扭曲占比）
  - 空值率过高的列（>50% 偏高，>80% 几乎不可用）
  - 取值恒定的数值列（无波动，分析价值为零）
每条问题写进 Dataset.warnings，报告/看板渲染层已全盘消费 warnings，
所以无需改渲染代码就能出现在所有产物里。
"""

from __future__ import annotations

from .model import Dataset


def _is_blank(v) -> bool:
    return v is None or (isinstance(v, str) and not v.strip())


def profile_dataset(ds: Dataset) -> dict:
    """返回每列体检 + 汇总问题 + 健康度(0-100)。纯计算，不写 warnings。"""
    n = len(ds.rows)
    per_col: dict[str, dict] = {}
    dup_rows = 0

    if n:
        seen = set()
        for r in ds.rows:
            key = tuple((c.name, r.get(c.name)) for c in ds.columns)
            if key in seen:
                dup_rows += 1
            else:
                seen.add(key)

    for c in ds.columns:
        vals = [r.get(c.name) for r in ds.rows]
        present = [v for v in vals if not _is_blank(v)]
        null_rate = (n - len(present)) / n if n else 0.0
        if c.dtype == "number":
            nums = [v for v in present
                    if isinstance(v, (int, float)) and not isinstance(v, bool)]
            rng = (min(nums), max(nums)) if nums else None
            distinct = len(set(nums))
        else:
            distinct = len(set(str(v) for v in present))
            rng = None
        per_col[c.name] = {
            "dtype": c.dtype,
            "null_rate": round(null_rate, 3),
            "distinct": distinct,
            "range": rng,
            "constant": distinct <= 1 and n > 0,
        }

    issues: list[str] = []
    if dup_rows > 0:
        issues.append(f"发现 {dup_rows} 行完全重复的数据，合计/占比会被虚增，建议先去重")
    for name, p in per_col.items():
        if p["null_rate"] >= 0.8:
            issues.append(f"「{name}」空值率 {p['null_rate'] * 100:.0f}%（过高，几乎不可用）")
        elif p["null_rate"] >= 0.5:
            issues.append(f"「{name}」空值率 {p['null_rate'] * 100:.0f}%（偏高）")
        if p["constant"] and p["dtype"] == "number":
            issues.append(f"「{name}」所有取值相同（无波动，分析价值低）")

    # 健康度：满分 100，每个问题扣 12，空值率再按严重程度追加扣分
    penalty = 12 * len(issues)
    for p in per_col.values():
        if p["null_rate"] >= 0.8:
            penalty += 15
        elif p["null_rate"] >= 0.5:
            penalty += 5
    health = max(0, 100 - penalty)

    return {"n": n, "columns": per_col, "issues": issues,
            "dup_rows": dup_rows, "health": health}


def assess_quality(ds: Dataset) -> dict:
    """体检并把问题写进 ds.warnings。用 _qc 标记防重复执行（build_blocks 可能被多次调用）。"""
    if getattr(ds, "_qc", False):
        # 已检过：直接返回上次的 profile（存在 _qc_prof）
        return getattr(ds, "_qc_prof", {})
    ds._qc = True
    prof = profile_dataset(ds)
    ds._qc_prof = prof
    for issue in prof["issues"]:
        ds.warnings.append("数据质量：" + issue)
    return prof
