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
from .model import CARTESIAN_TYPES, SOLO_TYPES, ChartSpec, Dataset
from .render.echarts import build_option


def split_groups(c: ChartSpec) -> list[list[str]]:
    """一张卡片里的指标，按「能不能在同一张图里共存」分成几组，每组出一张图。

    三种情况：
      1. 用户勾了「拆成多张单指标图」→ 每个指标一组（量级差太多时用）
      2. 指标被人指定了 pie / scatter → 这类图一张图只装得下一个指标，各自一组
      3. 其余 → 全部合到一组，画成一张「每个系列各用各的形状」的混搭图
         （销售额柱状 + 毛利率折线，就是 Tableau 里给每个度量单独设标记类型的效果）

    第 3 组刻意放在最前面：它是用户最先勾的那批指标，理当是主图，
    pie/scatter 那些被挤出来的是附属图。
    """
    if not c.y:
        return []
    if getattr(c, "split_series", False) or len(c.y) == 1:
        return [[y] for y in c.y]

    groups: list[list[str]] = []
    shared: list[str] = []
    for y in c.y:
        if c.type_of(y) in SOLO_TYPES:
            groups.append([y])
        else:
            shared.append(y)
    if shared:
        groups.insert(0, shared)
    return groups


def expand_split(charts: list[ChartSpec]) -> list[tuple[ChartSpec, ChartSpec]]:
    """把「多指标」的配置按需拆成多张图的配置。

    返回 `(实际要画的 spec, 它的来源配置)` 配对。没拆开时两个是同一个对象。
    带上来源是为了让调用方还能按**用户填的那条配置**去找附带的显示属性
    （比如看板里那张图的宽度 span）—— 拆开后的 y 只剩一个，按 y 是匹配不上的。
    """
    pairs: list[tuple[ChartSpec, ChartSpec]] = []
    for c in charts:
        groups = split_groups(c)
        # 配置不需要改写时就原样返回，不复制一份。
        # 看板靠「来源配置」去取图宽/图高，对象身份一致能少一处对不上的可能；
        # 也只有默认图型和第一个指标一致时才成立 —— 单独设了图型的要走下面重建。
        if len(groups) == 1 and c.type_of(groups[0][0]) == c.type:
            pairs.append((c, c))
            continue
        for g in groups:
            d = c.to_dict()
            d["y"] = list(g)
            # 这张图的图型取这组第一个指标的。
            # 必须做这一步：卡片默认图型可能和指标自己选的不一样
            # （只勾了「毛利率」一个指标、把它设成饼图，卡片默认还是柱状图），
            # 而 aggregate 里散点取点、饼图遇负值降级这两段都看 spec.type，
            # 不归一化的话这里会照着柱状图去聚合，饼图永远出不来。
            d["type"] = c.type_of(g[0])
            # 一个系列就无所谓「第二个系列走右轴」了
            if len(g) == 1:
                d["dual_axis"] = False
            # 拆成多张时，用户自己写了标题的话每张都得带上是哪些指标，否则几张同名
            if len(groups) > 1 and d.get("title"):
                d["title"] = f"{d['title']} · {'、'.join(g)}"
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
                # 横轴列名带上：看板里「查看这张图的数据」要用它当表格第一列的表头，
                # 写着「省份」比写着「类目」清楚得多。
                "x": cs2.x,
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
