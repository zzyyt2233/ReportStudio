"""每个指标可以各选各的图型（Tableau 那种给每个度量单独设标记类型的效果）。

以前一张图表卡里的所有指标只能共用一种图型：想让「销售额」画柱子、
「毛利率」画折线，只能拆成两张卡分开配。现在每个指标自己带图型。

这套覆盖三件事：
  1. 能共存的图型（柱/线/面积/堆叠）合并成**一张**混搭图
  2. 不能共存的（饼图、散点图）自动单独成图，不用用户手动拆
  3. 报告和看板两边结果一致 —— 这条最容易漏，两个渲染入口以前就出过
     「同一张图名字不一样」的事
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.analyze import aggregate  # noqa: E402
from core.chartset import build_blocks, expand_split, split_groups  # noqa: E402
from core.model import ChartSpec  # noqa: E402
from core.parsers.tabular import build_dataset  # noqa: E402
from core.render.echarts import build_option  # noqa: E402
from tests._helpers import ok  # noqa: E402

FAILS: list[str] = []


def check(label: str, cond: bool, detail: str = "") -> None:
    if not ok(label, cond, detail):
        FAILS.append(label)


GRID = [
    ["省份", "销售额", "利润", "毛利率"],
    ["广东", "12000", "1800", "15.0%"],
    ["江苏", "9800", "1420", "14.5%"],
    ["浙江", "8700", "1310", "15.1%"],
    ["山东", "6400", "880", "13.8%"],
    ["四川", "4200", "520", "12.4%"],
]
DS = build_dataset(GRID, name="分省经营", source_type="csv")
TMP = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                   "temp", "pmt")


def blocks_for(spec):
    return build_blocks([DS], [spec])


def series_of(block):
    return block["option"].get("series") or []


# ---------------------------------------------------------------- 1
print("\n[1] 混搭：销售额柱状 + 利润折线 → 合并成一张图")
sp = ChartSpec(type="bar", x="省份", y=["销售额", "利润"], agg="sum",
               y_types={"利润": "line"})
blocks, _pending = blocks_for(sp)
got = [(s.get("name"), s.get("type")) for b in blocks for s in series_of(b)]
check("只出一张图", len(blocks) == 1, f"实际 {len(blocks)} 张")
check("第一个系列是柱状图", got[:1] == [("销售额", "bar")], str(got))
check("第二个系列是折线图", got[1:2] == [("利润", "line")], str(got))
check("两个系列在同一张图里", len(got) == 2, str(got))

# ---------------------------------------------------------------- 2
print("\n[2] 面积图 / 堆叠柱状图转成 echarts 的画法")
sp = ChartSpec(type="bar", x="省份", y=["销售额", "利润", "毛利率"], agg="sum",
               y_types={"销售额": "area", "利润": "stack_bar", "毛利率": "line"})
blocks, _ = blocks_for(sp)
by = {s.get("name"): s for b in blocks for s in series_of(b)}
check("面积图 = 折线 + 填充",
      by["销售额"].get("type") == "line" and bool(by["销售额"].get("areaStyle")),
      str(by["销售额"].get("type")))
check("堆叠柱状 = 柱子 + stack",
      by["利润"].get("type") == "bar" and by["利润"].get("stack") == "total",
      str(by["利润"].get("stack")))
check("普通折线不带填充", not by["毛利率"].get("areaStyle"))

# ---------------------------------------------------------------- 3
print("\n[3] 饼图混在柱状图里 → 自动单独成图")
sp = ChartSpec(type="bar", x="省份", y=["销售额", "利润", "毛利率"], agg="sum",
               y_types={"利润": "line", "毛利率": "pie"})
gs = split_groups(sp)
check("分成两组：混搭图 + 饼图", len(gs) == 2, str(gs))
blocks, _ = blocks_for(sp)
check("出两张图", len(blocks) == 2, f"实际 {len(blocks)} 张")
pie_blocks = [b for b in blocks
              if "pie" in [s.get("type") for s in series_of(b)]]
check("其中一张是饼图", len(pie_blocks) == 1)
check("饼图那张只有一个系列",
      len(series_of(pie_blocks[0])) == 1 if pie_blocks else False)

# ---------------------------------------------------------------- 4
print("\n[4] 只勾一个指标、给它设单独图型 → 真的按那个画")
# 这条最容易漏：卡片默认图型还是柱状图，指标单独设了饼图 / 散点图。
# aggregate 里「散点取点 / 饼图负值降级」看的是 spec.type，
# 不把组的图型归一化的话，这里会照着柱状图去聚合，饼图永远出不来。
sp = ChartSpec(type="bar", x="省份", y=["销售额"], agg="sum",
               y_types={"销售额": "pie"})
blocks, _ = blocks_for(sp)
got = [s.get("type") for b in blocks for s in series_of(b)]
check("出的是饼图而不是柱状图", got == ["pie"], str(got))

sp = ChartSpec(type="bar", x="销售额", y=["利润"], agg="sum",
               y_types={"利润": "scatter"})
sub = expand_split([sp])[0][0]
agg2 = aggregate(DS, sub)
check("散点图走的是取点分支（不按类别聚合）", agg2.get("scatter") is True,
      str(list(agg2)[:6]))

# ---------------------------------------------------------------- 5
print("\n[5] 老配置没有 y_types → 行为完全不变")
sp = ChartSpec(type="stack_bar", x="省份", y=["销售额", "利润"], agg="sum")
check("y_types 为空", sp.y_types == {}, str(sp.y_types))
check("两个都按默认画堆叠柱",
      [sp.series_style(y, i) for i, y in enumerate(sp.y)]
      == [("bar", True, False), ("bar", True, False)])
blocks, _ = blocks_for(sp)
ss = [s for b in blocks for s in series_of(b)]
check("仍是一张图、两个柱系列",
      len(blocks) == 1 and [s.get("type") for s in ss] == ["bar", "bar"])
check("两个系列都带 stack", all(s.get("stack") == "total" for s in ss))

sp = ChartSpec(type="combo", x="省份", y=["销售额", "利润"], agg="sum")
check("combo 仍是「首柱余线」",
      [sp.series_style(y, i) for i, y in enumerate(sp.y)]
      == [("bar", False, False), ("line", False, False)])

# ---------------------------------------------------------------- 6
print("\n[6] 勾了「拆成多张单指标图」→ 每张各用自己的图型")
sp = ChartSpec(type="bar", x="省份", y=["销售额", "利润", "毛利率"], agg="sum",
               split_series=True,
               y_types={"销售额": "line", "毛利率": "area"})
blocks, _ = blocks_for(sp)
check("出三张图", len(blocks) == 3, f"实际 {len(blocks)} 张")
got = {series_of(b)[0].get("name"): series_of(b)[0].get("type")
       for b in blocks if series_of(b)}
check("销售额按折线", got.get("销售额") == "line", str(got))
check("利润没单独指定 → 跟默认走柱状", got.get("利润") == "bar", str(got))
check("毛利率画成折线（面积图的基底）", got.get("毛利率") == "line", str(got))
check("毛利率那张确实带填充",
      any(series_of(b)[0].get("areaStyle") for b in blocks
          if series_of(b) and series_of(b)[0].get("name") == "毛利率"))

# ---------------------------------------------------------------- 7
print("\n[7] 脏数据净化：给没选中的指标、或认不出来的图型")
sp = ChartSpec(type="bar", x="省份", y=["销售额"],
               y_types={"被取消的指标": "pie", "销售额": "乱写的图型"})
check("不在 y 里的被丢掉", "被取消的指标" not in sp.y_types, str(sp.y_types))
check("认不出来的图型被丢掉", "销售额" not in sp.y_types, str(sp.y_types))
check("取值回落到默认柱状图", sp.type_of("销售额") == "bar")
blocks, _ = blocks_for(sp)
got = [s.get("type") for b in blocks for s in series_of(b)]
check("照常出图，没崩也没丢", got == ["bar"], str(got))

# ---------------------------------------------------------------- 8
print("\n[8] 两个渲染入口判定一致（ECharts 网页版 / matplotlib 导出件）")
from core.render.static_chart import render_chart  # noqa: E402

sp = ChartSpec(type="bar", x="省份", y=["销售额", "利润"], agg="sum",
               y_types={"利润": "line"})
agg = aggregate(DS, sp)
os.makedirs(TMP, exist_ok=True)
out = os.path.join(TMP, f"mix_{os.getpid()}.png")
render_chart(agg, sp, out)
check("混搭图能导出 PNG",
      os.path.exists(out) and os.path.getsize(out) > 1000,
      f"{os.path.getsize(out) if os.path.exists(out) else 0} 字节")
check("series_style 只返回两种基础图型（两个渲染器共用同一套判定）",
      set(sp.series_style(y, i)[0] for i, y in enumerate(sp.y)) <= {"bar", "line"})

# ---------------------------------------------------------------- 9
print("\n[9] ECharts option 结构没被改坏")
sp = ChartSpec(type="bar", x="省份", y=["销售额", "利润"], agg="sum",
               y_types={"利润": "line"}, dual_axis=True)
opt = build_option(aggregate(DS, sp), sp)
check("双轴时第二个系列走右轴",
      [s.get("yAxisIndex") for s in opt["series"]] == [0, 1],
      str([s.get("yAxisIndex") for s in opt["series"]]))
check("两条 Y 轴都在",
      isinstance(opt.get("yAxis"), list) and len(opt["yAxis"]) == 2)
check("图例 / 提示框照常",
      bool(opt.get("legend")) and bool(opt.get("tooltip")))

# ---------------------------------------------------------------- 10
print("\n[10] y_types 能穿过接口的白名单和看板存盘")
# app.py 的生成接口和 dashboard 的存盘都用「只认 ChartSpec 字段名」的方式过滤，
# 新字段没被白名单位覆盖的话，前端传了也会被静默丢掉：
# 界面上看着选好了，图出来还是老样子 —— 这种最费解。
import core.dashboard as dash  # noqa: E402

payload = {"type": "bar", "x": "省份", "y": ["销售额", "利润"], "agg": "sum",
           "y_types": {"利润": "line"}, "sort_by": "", "limit": None,
           "dual_axis": False, "exclude_summary": True, "split_series": False,
           "title": ""}
sp = ChartSpec(**{k: v for k, v in payload.items()
                  if k in ChartSpec.__dataclass_fields__})
check("接口白名单没有丢掉 y_types", sp.y_types == {"利润": "line"},
      str(sp.y_types))

cfg = dash.from_payload({"name": "k", "dataset_ids": [], "charts": [payload]})
check("看板存盘保留了 y_types",
      cfg.charts[0].get("y_types") == {"利润": "line"},
      str(cfg.charts[0].get("y_types")))
specs = dash._to_specs(cfg)
check("看板读回来还是能生效",
      specs[0].type_of("利润") == "line" and specs[0].type_of("销售额") == "bar")

# ---------------------------------------------------------------- 11
print("\n[11] 混搭时每个指标按自己的单位换算（百分比列 ×100）")
# 一条容易悄悄出错的：一张图里同时画「销售额」和「毛利率」。
# 单位只取第一个指标时，毛利率会按 0.15 画出来、贴着 0，
# 鼠标悬停还显示 0.15 —— 图上看着「正常」，数字是错的。
sp = ChartSpec(type="bar", x="省份", y=["销售额", "利润", "毛利率"], agg="sum",
               y_types={"利润": "line", "毛利率": "area"})
agg = aggregate(DS, sp)
check("每个指标的单位各算各的",
      agg["units"] == {"销售额": "", "利润": "", "毛利率": "%"}, str(agg["units"]))
by = {s["name"]: s["data"] for s in agg["series"]}
check("aggregate 里的原始值不动（0.15），换算只在渲染时做",
      by["毛利率"][0] == 0.15 and by["销售额"][0] == 12000.0,
      str(by["毛利率"][:2]))

opt = build_option(agg, sp)
opt_series = {s["name"]: s["data"] for s in opt["series"]}
check("渲染时毛利率 ×100 变成 15.0", opt_series["毛利率"][0] == 15.0,
      str(opt_series["毛利率"][:2]))
check("销售额没被误乘 100", opt_series["销售额"][0] == 12000.0,
      str(opt_series["销售额"][:2]))
check("纵轴名把共用这根轴的指标都写上",
      opt["yAxis"][0]["name"].startswith("销售额 / 利润 / 毛利率"),
      opt["yAxis"][0]["name"])
check("混合单位时轴标签不硬加 % 后缀",
      opt["yAxis"][0]["axisLabel"].get("formatter") is None)

# 全是百分比列时，% 后缀要保留（别修混搭修坏了单一单位的显示）
sp2 = ChartSpec(type="line", x="省份", y=["毛利率"], agg="mean")
agg2 = aggregate(DS, sp2)
opt2 = build_option(agg2, sp2)
check("纯百分比列仍然带 % 后缀",
      opt2["yAxis"][0]["axisLabel"].get("formatter") == "{value}%")
check("纯百分比列轴名带 (%)", opt2["yAxis"][0]["name"] == "毛利率 (%)",
      opt2["yAxis"][0]["name"])

# ---------------------------------------------------------------- 收尾
print()
if FAILS:
    print(f"✗ {len(FAILS)} 项未通过：")
    for f in FAILS:
        print("   -", f)
    sys.exit(1)
print("✓ per_metric_type 全部通过")
