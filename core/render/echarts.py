"""Spec + 聚合结果 → ECharts option。配色统一从 core.render.theme 取。"""

from __future__ import annotations

from ..model import ChartSpec
from . import theme as T

# 保留导出名：tests 与旧调用方按 PALETTE 引用浅色色板。
PALETTE = T.PALETTE["light"]

TEXT_STYLE = {"fontFamily": "Microsoft YaHei, PingFang SC, system-ui, sans-serif"}
GRID = {"left": 60, "right": 40, "top": 60, "bottom": 60, "containLabel": True}


def _theme_parts(theme: str) -> dict:
    """把一次渲染要用的主题色收拢成局部字典，避免满篇 T.echart_colors(...) 查表。"""
    t = T.norm(theme)
    c = T.echart_colors(t)
    return {"name": t, "palette": T.palette(t), "anom": T.tokens(t)["anom"], **c}


def _toolbox(th: dict, ctype: str) -> dict:
    """右上角工具：保存图片、框选缩放、还原。

    饼图没有轴，dataZoom 框选对它无意义，就不放（放上去点了没反应更迷惑）。
    saveAsImage 的底色跟主题卡片色一致 —— 深色看板截出来不该带一块白边。
    """
    feature: dict = {
        "saveAsImage": {"title": "保存图片", "pixelRatio": 2,
                        "backgroundColor": T.card_bg(th["name"])},
    }
    if ctype not in ("pie",):
        feature["dataZoom"] = {"title": {"zoom": "框选缩放", "back": "还原缩放"}}
        feature["restore"] = {"title": "还原"}
    return {
        "right": 12, "top": 4, "itemSize": 14, "itemGap": 8,
        "iconStyle": {"borderColor": th["icon_border"], "color": th["icon"]},
        "emphasis": {"iconStyle": {"borderColor": th["text"], "color": th["text"]}},
        "feature": feature,
    }


def _tooltip(th: dict, base: dict | None = None) -> dict:
    tip = dict(base or {"trigger": "axis", "axisPointer": {"type": "shadow"}})
    tip["backgroundColor"] = th["tooltip_bg"]
    tip["borderColor"] = th["tooltip_border"]
    tip["textStyle"] = {"color": th["tooltip_text"]}
    return tip


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


def build_option(agg: dict, spec: ChartSpec, theme: str = "light") -> dict:
    th = _theme_parts(theme)
    opt: dict = {
        "color": th["palette"],
        "textStyle": TEXT_STYLE,
        "tooltip": _tooltip(th),
        "legend": {"top": 8, "textStyle": {"color": th["text"]}},
    }
    unit = agg.get("unit", "") or ""
    # 实际用哪种图，以 analyze.aggregate 的决策为准（饼图遇负值会被降级成柱状图）。
    # 渲染层只负责画，不自己判断 —— 两处各自判断必然会有一处漏改。
    ctype = agg.get("effective_type") or spec.type
    opt["toolbox"] = _toolbox(th, ctype)

    if ctype == "scatter":
        opt["grid"] = GRID
        opt["tooltip"] = _tooltip(th, {"trigger": "item"})
        opt["xAxis"] = {"type": "value", "name": spec.x,
                        "nameTextStyle": {"color": th["text"]},
                        "axisLabel": {"color": th["axis_label"]},
                        "axisLine": {"lineStyle": {"color": th["axis_line"]}}}
        opt["yAxis"] = {"type": "value",
                        "name": _yname(spec.y[0] if spec.y else "",
                                       _unit_of(agg, spec.y[0] if spec.y else "")),
                        "nameTextStyle": {"color": th["text"]},
                        "axisLabel": {"color": th["axis_label"]},
                        "splitLine": {"lineStyle": {"color": th["split"]}},
                        "axisLine": {"lineStyle": {"color": th["axis_line"]}}}
        opt["series"] = [{"type": "scatter", "name": spec.y[0] if spec.y else "",
                          "data": agg["series"][0]["data"] if agg["series"] else [],
                          "symbolSize": 10}]
        return opt

    if ctype == "pie":
        # 百分比列底层存的是小数(0.125)，饼图扇区按 12.5 显示。
        # 单位取这个系列自己的，不是全图第一个指标的。
        su = _unit_of(agg, agg["series"][0]["name"]) if agg["series"] else unit
        scale = (lambda v: round(v * 100, 4)) if su == "%" else (lambda v: v)
        anom_idx = ({a["index"]: a for a in (agg["series"][0].get("anomalies") or [])}
                    ) if agg["series"] else {}
        data = []
        if agg["series"]:
            for i, (n, v) in enumerate(zip(agg["categories"], agg["series"][0]["data"])):
                item = {"name": n, "value": scale(v)}
                if i in anom_idx:
                    item["itemStyle"] = {"color": th["anom"]}
                data.append(item)
        opt["tooltip"] = _tooltip(th, {"trigger": "item",
                                       "formatter": "{b}: {c} ({d}%)"})
        opt["legend"] = {"top": 8, "type": "scroll",
                         "textStyle": {"color": th["text"]}}
        opt["series"] = [{
            "type": "pie",
            "radius": ["38%", "65%"],
            "center": ["50%", "58%"],
            "name": spec.y[0] if spec.y else "",
            "data": data,
            "label": {"formatter": "{b}\n{d}%", "color": th["text"]},
            "itemStyle": {"borderColor": T.card_bg(th["name"]), "borderWidth": 2},
        }]
        return opt

    opt["grid"] = GRID
    opt["xAxis"] = {
        "type": "category",
        "data": agg["categories"],
        "axisLabel": {"color": th["axis_label"],
                      "rotate": 30 if len(agg["categories"]) > 8 else 0},
        "axisLine": {"lineStyle": {"color": th["axis_line"]}},
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
        "nameTextStyle": {"color": th["text"]},
        "axisLabel": {"color": th["axis_label"],
                      "formatter": "{value}%" if _only_pct(agg, left_names) else None},
        "splitLine": {"lineStyle": {"color": th["split"]}},
    }]
    if spec.dual_axis and n_all > 1:
        y_axes.append({
            "type": "value",
            "name": _axis_name(agg, right_names),
            "nameTextStyle": {"color": th["text"]},
            "axisLabel": {"color": th["axis_label"],
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
        scale = (lambda v: round(v * 100, 4)) if su == "%" else (lambda v: v)
        # 异常点：原值检测、展示值标红。索引在 aggregate 里已对齐最终 categories。
        anom_idx = {a["index"]: a for a in (s.get("anomalies") or [])}
        data = []
        for di, v in enumerate(s["data"]):
            disp = scale(v)
            if di in anom_idx:
                data.append({"value": disp, "itemStyle": {"color": th["anom"]}})
            else:
                data.append(disp)
        one: dict = {
            "name": s["name"],
            "type": stype,
            "data": data,
            "yAxisIndex": yidx,
            "emphasis": {"focus": "series"},
        }
        if anom_idx:
            # 红色「异」标注钉在异常点上方，和柱体/折线的红点呼应
            one["markPoint"] = {
                "symbol": "pin", "symbolSize": 34,
                "itemStyle": {"color": th["anom"]},
                "label": {"color": "#fff", "fontSize": 10},
                "data": [{"coord": [agg["categories"][ai], scale(s["data"][ai])],
                          "value": "异"} for ai in anom_idx],
            }
        if stype == "bar":
            one["barMaxWidth"] = 42
            one["itemStyle"] = {"borderRadius": [3, 3, 0, 0]}
            if stacked:
                one["stack"] = "total"
        if stype == "line":
            one["smooth"] = True
            one["symbolSize"] = 6
            if area:
                one["areaStyle"] = {"opacity": 0.18}
        series.append(one)
    opt["series"] = series
    return opt


def build_option_pair(agg: dict, spec: ChartSpec) -> dict:
    """一次产出深浅两套 option。

    报告 / 看板是发给别人看的自包含单文件，切主题不能回服务端重渲 ——
    option 里只有数据没有图片资源，双份的体积代价很小，换来回看时随意切换。
    """
    return {"light": build_option(agg, spec, "light"),
            "dark": build_option(agg, spec, "dark")}
