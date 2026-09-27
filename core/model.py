"""统一数据结构：所有输入最终都归一成 Dataset。"""

from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import Literal

DType = Literal["number", "date", "text"]


@dataclass
class Column:
    name: str
    dtype: DType
    unit: str | None = None

    def to_dict(self):
        return {"name": self.name, "dtype": self.dtype, "unit": self.unit}


@dataclass
class Dataset:
    id: str
    name: str
    source_type: str            # excel / csv / text / pdf / image / paste
    columns: list[Column]
    rows: list[dict]
    raw_text: str = ""
    warnings: list[str] = field(default_factory=list)
    total_rows: int = 0
    # 列角色识别结果：{dimensions:[适合横轴的列], measures:[适合纵轴的数值列]}。
    # 由 classify_columns 算出，随 to_dict 一并带出，前端据此标注「维度/度量」
    # 并放开数值列也能作横轴。默认空，to_dict 时懒算一次缓存。
    roles: dict = field(default_factory=dict)

    def to_dict(self, preview_limit: int = 200):
        if not self.roles:
            self.roles = classify_columns(self)
        return {
            "id": self.id,
            "name": self.name,
            "source_type": self.source_type,
            "columns": [c.to_dict() for c in self.columns],
            "rows": self.rows[:preview_limit],
            "total_rows": self.total_rows or len(self.rows),
            "warnings": self.warnings,
            "roles": self.roles,
        }


# 只能「一个指标独占一张图」的图型。
# 饼图一张图就一个圆环，塞第二个指标没有对应的形状；散点图的横轴得是数值列，
# 跟柱状/折线的分类横轴不是一回事，混在一张图里两边都对不上。
SOLO_TYPES = frozenset({"pie", "scatter"})

# 能在同一张直角坐标系图里共存、且每个系列可以各用各的形状的图型。
# 这就是「每个指标选自己图型」能成立的前提：ECharts / matplotlib 都支持
# 同一个坐标系里 series[0] 是柱、series[1] 是线。
CARTESIAN_TYPES = frozenset({"bar", "line", "area", "stack_bar", "combo"})


@dataclass
class ChartSpec:
    """一张图的完整规格。"""

    type: str = "bar"           # 默认图型：指标没单独指定时用它
    title: str = ""
    x: str = ""
    y: list[str] = field(default_factory=list)
    agg: str = "sum"            # sum / mean / count / max / min / none
    sort_by: str = ""           # 列名，或 "__x__" 表示按维度
    sort_order: str = "asc"     # asc / desc
    limit: int | None = None
    dual_axis: bool = False
    exclude_summary: bool = True   # 是否剔除「合计/总计/小计」行
    split_series: bool = False     # 多指标：True 拆成多张单指标图，False 合并成一张多系列图
    # 每个指标各自的图型：{指标名: 图型}。没列出来的指标沿用上面的 type。
    # 这样「销售额用柱状、毛利率用折线」这种混搭才有地方存；
    # 老配置（含以前存过的看板）没有这个字段，取值时自然回落到 type，行为不变。
    y_types: dict[str, str] = field(default_factory=dict)

    def __post_init__(self):
        """只保留「确实被选中的指标」且「图型认得出来」的单独图型。

        前端会传来配了一半的卡（给某个指标选了饼图、又把那个指标取消了），
        脏数据留着会让渲染时出现「没有对应系列」的图型配置；
        拆图时也会把其它组的图型带进本不该有的一张图里。
        放在这里而不是每个入口各净一遍，是因为 ChartSpec 有五处构造点，
        漏一处就会攒下说不清的配置。
        """
        known = CARTESIAN_TYPES | SOLO_TYPES
        self.y_types = {
            str(k): str(v) for k, v in (self.y_types or {}).items()
            if str(k) in self.y and str(v) in known
        }

    def type_of(self, y: str) -> str:
        """某个指标实际用的图型。没单独指定就用整张卡的默认图型。"""
        t = (self.y_types or {}).get(y)
        return t if t else self.type

    def solo_ys(self) -> list[str]:
        """必须单独成图的指标（饼图 / 散点图）。"""
        return [y for y in self.y if self.type_of(y) in SOLO_TYPES]

    def series_style(self, y: str, index: int = 0) -> tuple[str, bool, bool]:
        """某个指标最终画成什么样：(基础图型, 是否堆叠, 是否填充面积)。

        基础图型只返回 bar / line 两种 —— 面积图是「折线 + 填充」、堆叠柱状是
        「柱子 + 堆叠」，归到这两种之后 ECharts 和 matplotlib 就不用各自再认一遍
        area / stack_bar / combo，也就不会出现网页版和导出件画得不一样。
        """
        t = self.type_of(y)
        if t == "combo":
            # 组合图：第一条柱子打底，其余折线叠上去看趋势
            t = "bar" if index == 0 else "line"
        if t == "area":
            return "line", False, True
        if t == "stack_bar":
            return "bar", True, False
        if t == "line":
            return "line", False, False
        # 其余（bar，以及任何没认出来的值）一律按柱状图，不静默丢图
        return "bar", False, False

    def to_dict(self):
        return asdict(self)


@dataclass
class ReportSpec:
    title: str = "数据分析报告"
    template: str = "full"      # full / simple
    dataset_ids: list[str] = field(default_factory=list)
    charts: list[ChartSpec] = field(default_factory=list)
    metrics: list[str] = field(default_factory=list)   # 需要在指标卡里展示的数值列
    summary: str = ""
    chart_layout: int = 1       # 报告里一行放几张图：1 / 2 / 3
    # 明细数据表（报告末尾那张原始数据表）的排序。
    # 以前排序只作用于图表，明细永远按原始行序铺出来 —— 用户说「按销售额降序」，
    # 图上降了、明细一动不动，看着像没照做。空字符串 = 保持原始顺序。
    # 排序在**取前 N 行之前**做，所以「降序 + 只展示前 300 行」取到的是真正的前 300。
    detail_sort_by: str = ""
    detail_sort_order: str = "asc"     # asc / desc


def classify_columns(ds: "Dataset") -> dict:
    """识别每列的角色，供「横轴/纵轴都能识别 + 可切换 + 可选取」使用。

    返回 {dimensions:[适合作横轴的列名], measures:[适合作纵轴的数值列名]}。
    - measures：所有数值列（高基数数值必然是指标）。
    - dimensions：非数值列（文本/日期类别）+ 低基数数值列（年份、月份、评分档、
      等级这类「看着像数、其实当维度用」的列，用户常拿它作横轴）。
      高基数数值（销售额、人数）只留在 measures，不当横轴候选，避免被误当维度。

    两个集合允许重叠：低基数数值列既适合横轴也能当纵轴，前端两种候选都列它，
    由用户在卡片里自选 —— 这正是「横轴纵轴指标都识别」想要的。
    """
    measures = [c.name for c in ds.columns if c.dtype == "number"]
    dims = [c.name for c in ds.columns if c.dtype != "number"]
    n = len(ds.rows)
    for c in ds.columns:
        if c.dtype != "number" or c.name in dims:
            continue
        vals = [r.get(c.name) for r in ds.rows if r.get(c.name) is not None]
        if not vals:
            continue
        distinct = len(set(map(str, vals)))
        # 基数很低（像类别 / 年份 / 档位）才算维度；阈值取 30 与「半数行」的较小者，
        # 这样「100 行的评分档(1-5)」算维度，「1000 行的订单号」不算。
        if distinct <= min(30, max(1, n // 2)):
            dims.append(c.name)
    return {"dimensions": dims, "measures": measures}
