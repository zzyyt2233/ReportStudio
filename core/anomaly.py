"""统计异常检测（内化自 data-analyzer 的 anomaly 算法：IQR + z-score + 环比突变）。

为什么内化而不是 import 另一个项目的 venv：
- ReportStudio 有独立 .venv，跨项目 import 既脆又难测；
- 算法本身已在 data-analyzer 跑通验证，这里对齐接口复刻，零新增依赖（纯 Python，不碰 numpy）。

检测粒度是「一条 series」（横轴类别 × 该指标数值），正好对应图上的一根柱子/一条线，
异常点直接映射回图表坐标，落点、结论、视觉三处共用同一份结果。
"""

from __future__ import annotations

import math
import re

# 环比突变只在类别轴像时间序（年/月/季度/日期）时才做，
# 否则对「按值排序后的柱子」算相邻变动毫无意义、纯误报。
_TIME_PAT = re.compile(r"(\d{4}年|\d+月|月\d|Q[1-4]|^\d+-\d+|年\d+|周\d)")


def _quantile(sorted_vals: list[float], q: float) -> float:
    """线性插值分位数，纯 Python 实现（不依赖 numpy）。"""
    if not sorted_vals:
        return 0.0
    n = len(sorted_vals)
    if n == 1:
        return sorted_vals[0]
    pos = (n - 1) * q
    lo = int(math.floor(pos))
    hi = int(math.ceil(pos))
    if lo == hi:
        return sorted_vals[lo]
    frac = pos - lo
    return sorted_vals[lo] * (1 - frac) + sorted_vals[hi] * frac


def _looks_time_ordered(categories: list) -> bool:
    """类别轴像时间序才做环比突变检测。"""
    if len(categories) < 3:
        return False
    hits = sum(1 for c in categories if _TIME_PAT.search(str(c)))
    return hits >= max(2, len(categories) // 2)


def detect_series_anomalies(categories: list, data: list,
                            iqr_k: float = 1.5, z_thresh: float = 3.0,
                            change_thresh: float = 0.5) -> list[dict]:
    """对一条 series（categories 与 data 等长）做异常检测，返回异常明细列表。

    每条明细：{index, category, value, methods:[...], reason}
      - IQR：超出 [q1 - 1.5*iqr, q3 + 1.5*iqr]
      - z-score：|z| > 3
      - 环比突变：仅当类别轴像时间序；相邻环比变动超过自适应阈值
                  max(0.5, 3.5×环比标准差)，防误报

    数值点少于 4 个不检测——样本太小，IQR/z 的结果不可信（比如只有 3 个地区的图）。
    """
    anomalies: list[dict] = []
    num_idx = [i for i, v in enumerate(data)
               if isinstance(v, (int, float)) and not isinstance(v, bool)]
    vals = [data[i] for i in num_idx]
    if len(vals) < 4:
        return anomalies

    s = sorted(vals)
    q1, q3 = _quantile(s, 0.25), _quantile(s, 0.75)
    iqr = q3 - q1
    mean = sum(vals) / len(vals)
    var = sum((v - mean) ** 2 for v in vals) / len(vals)
    std = math.sqrt(var)

    for i in num_idx:
        v = data[i]
        methods: list[str] = []
        reasons: list[str] = []
        if iqr > 0 and (v < q1 - iqr_k * iqr or v > q3 + iqr_k * iqr):
            methods.append("IQR")
            reasons.append(f"超出IQR[{q1:.2f},{q3:.2f}]")
        if std > 0 and abs(v - mean) > z_thresh * std:
            methods.append("z-score")
            reasons.append(f"|z|>{z_thresh}")
        if methods:
            anomalies.append({
                "index": i,
                "category": categories[i] if i < len(categories) else str(i),
                "value": v,
                "methods": methods,
                "reason": "、".join(reasons),
            })

    # 环比突变：类别轴像时间序才做
    if _looks_time_ordered(categories) and len(num_idx) >= 3:
        sv = [data[i] for i in num_idx]
        changes = []
        for a, b in zip(sv, sv[1:]):
            if a not in (0, 0.0):
                changes.append((b - a) / abs(a))
        if changes:
            cmean = sum(changes) / len(changes)
            cvar = sum((c - cmean) ** 2 for c in changes) / len(changes)
            cstd = math.sqrt(cvar)
            # 自适应阈值：固定阈值与该序列自身波动（3.5 倍环比标准差）取高，防误报
            thresh = max(change_thresh, 3.5 * cstd)
            for j, ch in enumerate(changes):
                if abs(ch) > thresh:
                    i = num_idx[j + 1]
                    anomalies.append({
                        "index": i,
                        "category": categories[i] if i < len(categories) else str(i),
                        "value": data[i],
                        "methods": ["环比突变"],
                        "reason": f"环比变动{ch:+.1%}（阈值±{thresh:.0%}）",
                    })
    return anomalies


def detect_row_anomalies(ds, iqr_k: float = 1.5) -> dict:
    """行级离群检测：在原始数据上按列标红，对标 data-analyzer 的「异常红标」。

    与 detect_series_anomalies（标聚合后的类别点）互补：这里标「原始行里的离群单元格」。
    对每列数值列独立做 IQR 检测，返回 {行号: [(列名, 说明), ...]}。
    数值点 <4 或 IQR=0 的列跳过——样本太小/无波动，标了也是噪声。
    """
    result: dict = {}
    if not getattr(ds, "columns", None) or not getattr(ds, "rows", None):
        return result
    numeric_cols = [c.name for c in ds.columns if c.dtype == "number"]
    for col in numeric_cols:
        vals = [r.get(col) for r in ds.rows
                if isinstance(r.get(col), (int, float)) and not isinstance(r.get(col), bool)]
        if len(vals) < 4:
            continue
        s = sorted(vals)
        q1, q3 = _quantile(s, 0.25), _quantile(s, 0.75)
        iqr = q3 - q1
        if iqr <= 0:
            continue
        lo, hi = q1 - iqr_k * iqr, q3 + iqr_k * iqr
        for i, r in enumerate(ds.rows):
            v = r.get(col)
            if isinstance(v, (int, float)) and not isinstance(v, bool) and (v < lo or v > hi):
                result.setdefault(i, []).append((col, f"超出IQR[{q1:.2f},{q3:.2f}]"))
    return result
