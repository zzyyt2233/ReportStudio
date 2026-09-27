"""Spec + 聚合结果 → ECharts option。商务浅色风格。"""

from __future__ import annotations

from ..model import ChartSpec

PALETTE = [
    "#378ADD", "#1D9E75", "#EF9F27", "#7F77DD",
    "#E24B4A", "#5DCAA5", "#888780", "#D4537E",
]

BASE = {
    "textStyle": {"fontFamily": "Microsoft YaHei, PingFang SC, system-ui, sans-serif"},
    "grid": {"left": 60, "right": 40, "top": 60, "bottom": 60, "containLabel": True},
    "tooltip": {"trigger": "axis", "axisPointer": {"type": "shadow"}},
    "legend": {"top": 8, "textStyle": {"color": "#444441"}},
}


def _yname(name: str, unit: str) -> str:
    """y 轴标题：带单位更清楚，百分比列直接标注 (%。"""
    if unit == "%":
        return f"{name} (%)"
    if unit:
        return f"{name} ({unit})"
    return name


def _unit_of(agg: dict, name: str, fallback: str = "") -> str:
    """某个指标自己的单位。查不到才退回整张图的单位（老调用方只给了 unit）。"""
    return (agg.get("units") or {}).get(name, fallback) or ""


def _axis_name(agg: dict, names: list[str]) -> str:
    """共用一根纵轴时，轴上要把落在这根轴的指标都写上，并且各带各的单位。

    以前只写第一个指标的名字 —— 一张图里画「销售额 + 毛利率」时，
    轴上写着「销售额」，读者会以为整根轴都是销售额的刻度。
    """
    parts: list[str] = []
    for n in names:
        t = _yname(n, _unit_of(agg, n))
        if t not in parts:
            parts.append(t)
    return " / ".join(parts)[:70]


def _only_pct(agg: dict, names: list[str]) -> bool:
    """这根轴上的指标是不是全都带 %。混着普通数值时就别加 % 后缀。"""
    return bool(names) and all(_unit_of(agg, n) == "%" for n in names)


def build_option(agg: dict, spec: ChartSpec) -> dict:
    opt: dict = {
        "color": PALETTE,
        "textStyle": BASE["textStyle"],
        "tooltip": BASE["tooltip"],
        "legend": BASE["legend"],
    }
    unit = agg.get("unit", "") or ""
    # 实际用哪种图，以 analyze.aggregate 的决策为准（饼图遇负值会被降级成柱状图）。
    # 渲染层只负责画，不自己判断 —— 两处各自判断必然会有一处漏改。
    ctype = agg.get("effective_type") or spec.type

    if ctype == "scatter":
        opt["grid"] = BASE["grid"]
        opt["tooltip"] = {"trigger": "item"}
        opt["xAxis"] = {"type": "value", "name": spec.x,
                        "axisLine": {"lineStyle": {"color": "#B4B2A9"}}}
        opt["yAxis"] = {"type": "value",
                        "name": _yname(spec.y[0] if spec.y else "",
                                       _unit_of(agg, spec.y[0] if spec.y else "")),
                        "axisLine": {"lineStyle": {"color": "#B4B2A9"}}}
        opt["series"] = [{"type": "scatter", "name": spec.y[0] if spec.y else "",
                          "data": agg["series"][0]["data"] if agg["series"] else [],
                          "symbolSize": 10}]
        return opt

    if ctype == "pie":
        # 百分比列底层存的是小数(0.125)，饼图扇区按 12.5 显示。
        # 单位取这个系列自己的，不是全图第一个指标的。
        su = _unit_of(agg, agg["series"][0]["name"]) if agg["series"] else unit
        scale = (lambda v: round(v * 100, 4)) if su == "%" else (lambda v: v)
        data = [{"name": n, "value": scale(v)}
                for n, v in zip(agg["categories"], agg["series"][0]["data"])] \
            if agg["series"] else []
        opt["tooltip"] = {"trigger": "item", "formatter": "{b}: {c} ({d}%)"}
        opt["legend"] = {"top": 8, "type": "scroll", "textStyle": {"color": "#444441"}}
        opt["series"] = [{
            "type": "pie",
            "radius": ["38%", "65%"],
            "center": ["50%", "58%"],
            "name": spec.y[0] if spec.y else "",
            "data": data,
            "label": {"formatter": "{b}\n{d}%", "color": "#444441"},
            "itemStyle": {"borderColor": "#fff", "borderWidth": 2},
        }]
        return opt

    opt["grid"] = BASE["grid"]
    opt["xAxis"] = {
        "type": "category",
        "data": agg["categories"],
        "axisLabel": {"color": "#5F5E5A", "rotate": 30 if len(agg["categories"]) > 8 else 0},
        "axisLine": {"lineStyle": {"color": "#B4B2A9"}},
    }
    # 百分比列底层存的是小数(0.125)，轴上乘回 100 显示成 12.5%；轴标签用 {value}% 拼接
    # 哪些指标落在左轴、哪些落在右轴，先分好 —— 轴名和「要不要加 % 后缀」都取决于它
    n_all = len(agg["series"])
    left_names = [s["name"] for i, s in enumerate(agg["series"])
                  if not (spec.dual_axis and i > 0)]
    right_names = [s["name"] for i, s in enumerate(agg["series"])
                   if spec.dual_axis and i > 0]
    y_axes = [{
        "type": "value",
        "name": _axis_name(agg, left_names) or _yname(spec.y[0] if spec.y else "", unit),
        "axisLabel": {"color": "#5F5E5A",
                      "formatter": "{value}%" if _only_pct(agg, left_names) else None},
        "splitLine": {"lineStyle": {"color": "#F1EFE8"}},
    }]
    if spec.dual_axis and n_all > 1:
        y_axes.append({
            "type": "value",
            "name": _axis_name(agg, right_names),
            "axisLabel": {"color": "#5F5E5A",
                          "formatter": "{value}%" if _only_pct(agg, right_names) else None},
            "splitLine": {"show": False},
        })
    opt["yAxis"] = y_axes

    series = []
    for i, s in enumerate(agg["series"]):
        # 每个数列各用各的图型 —— 这是「同一张图里销售额柱子、毛利率折线」的基础。
        # 图型在 ChartSpec.series_style 里统一判定，网页版和导出件共用一套规则。
        stype, stacked, area = spec.series_style(s["name"], i)
        # 开了双轴，第二条及以后的数列走右轴
        yidx = 1 if (spec.dual_axis and i > 0) else 0
        # 百分比列展示时乘 100，让轴上看到 12.5 而不是 0.125。
        # 按**这个系列自己的**单位判断：一张图里同时有销售额和毛利率时，
        # 销售额不该被乘 100，毛利率也不该按原样画成 0.15。
        su = _unit_of(agg, s["name"], unit)
        data = [round(v * 100, 4) for v in s["data"]] if su == "%" else s["data"]
        one: dict = {
            "name": s["name"],
            "type": stype,
            "data": data,
            "yAxisIndex": yidx,
            "emphasis": {"focus": "series"},
        }
        if stype == "bar":
            one["barMaxWidth"] = 42
            one["itemStyle"] = {"borderRadius": [3, 3, 0, 0]}
            if stacked:
                one["stack"] = "total"
        if stype == "line":
            one["smooth"] = False
            one["symbolSize"] = 6
            if area:
                one["areaStyle"] = {"opacity": 0.18}
        series.append(one)
    opt["series"] = series
    return opt
