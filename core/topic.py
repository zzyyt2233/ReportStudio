"""文本 Dataset → 主题分布图（派生 Dataset + ChartSpec）。

接入 build_blocks 即可出图，无需改任何渲染层：
- 文本类数据（txt / md 落成 source_type="text"，docx 落成 "docx"）按段/行切成片段；
  粘贴导入的内容是 source_type="paste"，不在下面那道闸门里，暂时不出主题图；
- 用 tagger 做关键词或 TF-IDF+KMeans 打标；
- 统计各标签条数，生成一张「主题分布」柱状图，自动追加进报告/看板。
"""

from __future__ import annotations

from collections import Counter

from .model import ChartSpec, Column, Dataset
from .tagger import tag_texts

# 片段太少不足以聚类，强行分簇只会得到噪声 → 直接不画主题图
_MIN_SEG = 4


def extract_segments(ds: Dataset) -> list[str]:
    """从文本类 Dataset 抽出可打标的文本片段。

    优先用「行内容」这种逐行结构（粘贴/纯文本回退），否则退回 raw_text 按行切。
    """
    rows = getattr(ds, "rows", []) or []
    line_rows = [str(r.get("行内容", "")) for r in rows
                 if isinstance(r, dict) and str(r.get("行内容", "")).strip()]
    if line_rows:
        return [s for s in line_rows if s.strip()]
    rt = getattr(ds, "raw_text", "") or ""
    return [s.strip() for s in rt.splitlines() if s.strip()]


def build_topic_chart(ds: Dataset, taxonomy: dict | None = None):
    """返回 (派生 Dataset, ChartSpec)；不适合画主题图时返回 None。

    - 仅对 text / docx 来源生效；
    - 片段数 < _MIN_SEG 不画；
    - taxonomy 给定则走关键词打标，否则自动聚类。
    """
    if ds.source_type not in ("text", "docx"):
        return None
    segs = extract_segments(ds)
    if len(segs) < _MIN_SEG:
        return None
    try:
        tags, _meta = tag_texts(segs, taxonomy)
    except ImportError:
        # 自动聚类走 sklearn（函数内延迟 import）。缺这个包时只该少一张主题图，
        # 不该把整份报告带走 —— 报告链路本身还有降级那条路，缺包属于环境问题。
        return None
    if not tags:
        return None
    cnt = Counter(tags)
    rows = [{"主题": k, "条数": v} for k, v in cnt.most_common()]
    tds = Dataset(
        id=ds.id + "_topic",
        name=f"{ds.name}·主题分布",
        source_type="derived",
        columns=[Column("主题", "text"), Column("条数", "number", flag=False)],
        rows=rows,
        total_rows=len(rows),
    )
    spec = ChartSpec(
        type="bar",
        x="主题",
        y=["条数"],
        agg="sum",
        title=f"{ds.name} 主题分布",
    )
    return tds, spec
