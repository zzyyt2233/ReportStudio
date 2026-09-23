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
        opt["yAxis"] = {"type": "value", "name": _yname(spec.y[0] if spec.y else "", unit),
                        "axisLine": {"lineStyle": {"color": "#B4B2A9"}}}
        opt["series"] = [{"type": "scatter", "name": spec.y[0] if spec.y else "",
                          "data": agg["series"][0]["data"] if agg["series"] else [],
                          "symbolSize": 10}]
        return opt

    if ctype == "pie":
        scale = (lambda v: round(v * 100, 4)) if unit == "%" else (lambda v: v)
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
    y_axes = [{
        "type": "value",
        "name": _yname(spec.y[0] if spec.y else "", unit),
        "axisLabel": {"color": "#5F5E5A",
                      "formatter": "{value}%" if unit == "%" else None},
        "splitLine": {"lineStyle": {"color": "#F1EFE8"}},
    }]
    if spec.dual_axis and len(spec.y) > 1:
        y_axes.append({
            "type": "value",
            "name": _yname("、".join(spec.y[1:]), unit),
            "axisLabel": {"color": "#5F5E5A",
                          "formatter": "{value}%" if unit == "%" else None},
            "splitLine": {"show": False},
        })
    opt["yAxis"] = y_axes

    series = []
    for i, s in enumerate(agg["series"]):
        if ctype == "combo":
            stype = "bar" if i == 0 else "line"
        elif ctype in ("line",):
            stype = "line"
        elif ctype == "stack_bar":
            stype = "bar"
        else:
            stype = "bar"
        # 开了双轴，第二条及以后的数列走右轴
        yidx = 1 if (spec.dual_axis and i > 0) else 0
        # 百分比列展示时乘 100，让轴上看到 12.5 而不是 0.125
        data = [round(v * 100, 4) if unit == "%" else v for v in s["data"]] \
            if unit == "%" else s["data"]
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
            if ctype == "stack_bar":
                one["stack"] = "total"
        if stype == "line":
            one["smooth"] = False
            one["symbolSize"] = 6
        series.append(one)
    opt["series"] = series
    return opt
