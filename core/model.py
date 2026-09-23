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

    def to_dict(self, preview_limit: int = 200):
        return {
            "id": self.id,
            "name": self.name,
            "source_type": self.source_type,
            "columns": [c.to_dict() for c in self.columns],
            "rows": self.rows[:preview_limit],
            "total_rows": self.total_rows or len(self.rows),
            "warnings": self.warnings,
        }


@dataclass
class ChartSpec:
    """一张图的完整规格。"""

    type: str = "bar"           # bar / line / pie / scatter / stack_bar / combo
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
