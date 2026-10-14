"""趋势叙事分析：仅当横轴像时间序时，提炼「涨最快/跌最快区间」与「拐点」。

复用 anomaly 模块的时间序判定与环比口径，保证结论和图上红点来自同一套数字。
纯 Python，零依赖（不碰 sklearn / numpy）。
"""

from __future__ import annotations

from .anomaly import _looks_time_ordered


def _pct_changes(categories: list, data: list) -> list[tuple]:
    """返回 [(from_cat, to_cat, rate), ...]，仅相邻且分母非零的数值对。"""
    out: list[tuple] = []
    num_idx = [i for i, v in enumerate(data)
               if isinstance(v, (int, float)) and not isinstance(v, bool)]
    for a, b in zip(num_idx, num_idx[1:]):
        x0, x1 = data[a], data[b]
        if (isinstance(x0, (int, float)) and isinstance(x1, (int, float))
                and x0 not in (0, 0.0)):
            out.append((categories[a], categories[b], (x1 - x0) / abs(x0)))
    return out


def analyze_trend(categories: list, data: list) -> dict | None:
    """横向趋势摘要；非时间序（如按值排序的柱子）直接返回 None，避免误报。

    返回结构：
        {
          "fastest_rise": {"from","to","rate"} | None,   # 环比涨幅最大的一段
          "fastest_drop": {"from","to","rate"} | None,   # 环比跌幅最大的一段
          "turning":      [cat, ...]                      # 相邻环比符号翻转处
        }
    """
    if not _looks_time_ordered(categories):
        return None
    changes = _pct_changes(categories, data)
    if not changes:
        return None
    rises = [c for c in changes if c[2] > 0]
    drops = [c for c in changes if c[2] < 0]
    fastest_rise = max(rises, key=lambda x: x[2]) if rises else None
    fastest_drop = min(drops, key=lambda x: x[2]) if drops else None
    # 拐点：相邻环比符号翻转（涨→跌 或 跌→涨）
    turning = [changes[i][1] for i in range(1, len(changes))
               if changes[i - 1][2] * changes[i][2] < 0]
    return {
        "fastest_rise": ({"from": fastest_rise[0], "to": fastest_rise[1],
                          "rate": fastest_rise[2]} if fastest_rise else None),
        "fastest_drop": ({"from": fastest_drop[0], "to": fastest_drop[1],
                          "rate": fastest_drop[2]} if fastest_drop else None),
        "turning": turning,
    }
