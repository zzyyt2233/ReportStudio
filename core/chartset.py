"""图表集合的规范化：拆图与图块构造。

**为什么单独一层**：同一份图表配置要在两个地方变成图块 ——
报告的 `/api/generate` 和看板的 `core/dashboard.render`。这两处以前各写了一遍
「过滤列、聚合、拼标题、跳过空数据」的逻辑，改一处漏一处就会出现
「同一张图在报告和看板里名字不一样」这种怪事。所以口径收在这里。

拆图（split_series）为什么存在：
一个图表卡里勾了「销售额、利润、毛利率」，默认合并成一张三系列图（看趋势对比方便）；
但这三个指标量级差很大时（销售额上万、毛利率是百分数），合并到一张图上
小数值会压成一条贴地直线，等于看不见。这时就该拆成三张单指标图各看各的。
两种都不算错，取决于要回答什么问题，所以交给用户选。
"""

from __future__ import annotations

from .analyze import aggregate
from .model import ChartSpec, Dataset
from .render.echarts import build_option


def expand_split(charts: list[ChartSpec]) -> list[tuple[ChartSpec, ChartSpec]]:
    """把「多指标合并」的配置按需拆成多个单指标配置。

    返回 `(实际要画的 spec, 它的来源配置)` 配对。没拆开时两个是同一个对象。
    带上来源是为了让调用方还能按**用户填的那条配置**去找附带的显示属性
    （比如看板里那张图的宽度 span）—— 拆开后的 y 只剩一个，按 y 是匹配不上的。
    """
    pairs: list[tuple[ChartSpec, ChartSpec]] = []
    for c in charts:
        if not getattr(c, "split_series", False) or len(c.y) <= 1:
            pairs.append((c, c))
            continue
        for y in c.y:
            d = c.to_dict()
            d["y"] = [y]
            # 拆开后一张图只有一个系列，「第二个系列走右轴」就无从谈起了
            d["dual_axis"] = False
            # 用户自己写了标题的话，拆开后每张都得带上是哪个指标，否则三张同名
            if d.get("title"):
                d["title"] = f"{d['title']} · {y}"
            pairs.append((ChartSpec(**d), c))
    return pairs


def build_blocks(datasets: list[Dataset], charts: list[ChartSpec],
                 decorate=None) -> tuple[list[dict], list[dict]]:
    """(数据表 × 图表配置) → 图块列表 + 待出图清单。

    - 图块列表：给 HTML 报告和看板用（含 ECharts option）。
    - 待出图清单：给 matplotlib 用（Word/PDF/拼长图），带 agg 和 spec。

    decorate(block, spec, origin) 可选，让调用方补自己的字段（看板的 span/height）。
    """
    blocks: list[dict] = []
    pending: list[dict] = []
    for ds in datasets:
        cols = {c.name for c in ds.columns}
        for cs, origin in expand_split(charts):
            if cs.x not in cols or not (set(cs.y) & cols):
                continue
            cs2 = ChartSpec(**{**cs.to_dict(),
                               "y": [y for y in cs.y if y in cols]})
            agg = aggregate(ds, cs2)
            if agg.get("empty") or (not agg.get("categories")
                                    and not agg.get("scatter")):
                continue
            cid = f"chart{len(blocks)}"
            title = cs2.title or f"{'、'.join(cs2.y)} 按 {cs2.x}"
            if len(datasets) > 1:
                title += f"（{ds.name}）"
            # 标记列的「求和」实际是数条数，标题上要写清楚，免得读者误读
            flagged = [s["name"] for s in agg.get("series", []) if s.get("flag")]
            if flagged and cs2.agg == "sum":
                title += f" · {('、').join(flagged)}按「是」的条数计"
            block = {
                "id": cid,
                "title": title,
                "option": build_option(agg, cs2),
                "dataset": ds.id,
            }
            if decorate is not None:
                decorate(block, cs2, origin)
            blocks.append(block)
            pending.append({"id": cid, "agg": agg, "spec": cs2})
    return blocks, pending


def layout_columns(raw) -> int:
    """报告的图表版面：一行放几张。非法值一律回落到 1（每张一行）。"""
    try:
        n = int(raw)
    except (TypeError, ValueError):
        return 1
    return n if n in (1, 2, 3) else 1
