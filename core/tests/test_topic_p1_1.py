"""P1-1 回归：文本主题分布图（分类打标 / TF-IDF+KMeans 聚类 → 派生「条数」柱状图）。

正向：文本类 Dataset 应产出主题图，且 aggregate 后「条数」按真实数值求和（不为 0）。
反向：去掉派生列的 flag=False 标记后，应退回 is_flag_column 启发式误判为 0/1/2 标记列，
      聚合结果变 0 —— 证明 flag=False 这个修复点确实生效，非假绿。
边界：非文本来源、片段数不足时返回 None，不污染报告。
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from core.model import Column, ChartSpec, Dataset
from core.topic import build_topic_chart, extract_segments
from core.analyze import aggregate


def _meeting_ds():
    """一段会务纪要式文本：10 行，主题分散在转化率/性能/成本/功能/合规几个方向。"""
    lines = [
        "转化率提升需要重新梳理预算分配",
        "预算需要在下季度前完成审批",
        "性能优化是本期重点，留存率要稳住",
        "留存率下滑说明新功能体验有问题",
        "交付成本超支，需要复盘供应商报价",
        "复盘发现成本可控但人力投入偏大",
        "功能上线优先级要重新排序",
        "新功能优先级应让位于稳定性",
        "合规审查满意度不达标需要整改",
        "要提升合规满意度先补流程文档",
    ]
    cols = [Column("行内容", "text")]
    rows = [{"行内容": l} for l in lines]
    return Dataset(id="mtg1", name="项目周会纪要", source_type="text", columns=cols,
                   rows=rows, raw_text="\n".join(lines), total_rows=len(rows))


def test_positive_topic_chart():
    ds = _meeting_ds()
    res = build_topic_chart(ds)
    assert res is not None, "正向：文本类应产出主题图"
    tds, spec = res
    assert spec.x == "主题" and spec.y == ["条数"], "正向：规格应为 主题×条数"
    assert spec.agg == "sum", "正向：聚合方式应为 sum"
    # 派生列必须显式声明 flag=False，否则会被当成 0/1/2 标记列
    cnt_col = next(c for c in tds.columns if c.name == "条数")
    assert cnt_col.flag is False, "正向：条数列必须 flag=False 以走真实数值聚合"
    # 每个片段都被计入：行数之和 == 原始片段数
    total = sum(r["条数"] for r in tds.rows)
    assert total == 10, f"正向：条数之和应等于片段数 10，实际 {total}"
    print(f"  正向通过：发现 {len(tds.rows)} 个主题，条数合计 {total}")


def test_positive_aggregate_sum():
    """核心 bug 修复验证：aggregate 后条数必须按真实计数求和，不能是 0。"""
    ds = _meeting_ds()
    res = build_topic_chart(ds)
    assert res is not None
    tds, spec = res
    agg = aggregate(tds, spec)
    data = agg["series"][0]["data"]
    assert all(isinstance(v, (int, float)) and v > 0 for v in data), f"正向：每个主题条数应 >0，实际 {data}"
    assert sum(data) == 10, f"正向：聚合后条数合计应 ==10，实际 {sum(data)}"
    # flag 标记不应被当成标记列（否则 series.flag 为 True 且数值变 0）
    assert agg["series"][0]["flag"] is False, "正向：条数列不应被判为标记列"
    print(f"  聚合验证通过：categories={agg['categories']}, data={data}")


def test_negative_flag_marker_matters():
    """反向验证：构造退化的「条数全为 2」派生表（重现原始 bug 触发条件），
    不带 flag=False 时 is_flag_column 会把它判成 0/1/2 标记列，聚合计数 ==1 得 0；
    加上 flag=False 后退回真实数值求和。删掉修复点必须变红，证明非假绿。"""
    # 五个主题，每个恰好出现 2 次 —— 这正是当初触发 bug 的退化分布
    rows = [{"主题": f"主题{i}", "条数": 2} for i in range(5)]
    # 不带 flag 标记：模拟修复前的旧行为
    no_flag = Dataset(
        id="neg1", name="退化主题分布", source_type="derived",
        columns=[Column("主题", "text"), Column("条数", "number")],  # flag 默认 None
        rows=rows, total_rows=len(rows),
    )
    agg0 = aggregate(no_flag, ChartSpec(type="bar", x="主题", y=["条数"], agg="sum"))
    data0 = agg0["series"][0]["data"]
    assert sum(data0) == 0, f"反向预期：无 flag 标记时误判为标记列→全 0，实际 {data0}"

    # 加上 flag=False：修复后的真实行为
    with_flag = Dataset(
        id="neg2", name="退化主题分布", source_type="derived",
        columns=[Column("主题", "text"), Column("条数", "number", flag=False)],
        rows=rows, total_rows=len(rows),
    )
    agg1 = aggregate(with_flag, ChartSpec(type="bar", x="主题", y=["条数"], agg="sum"))
    data1 = agg1["series"][0]["data"]
    assert sum(data1) == 10, f"修复后：条数应如实求和=10，实际 {data1}"
    print("  反向验证通过（flag=False 修复点确实生效，非假绿）")


def test_boundary_non_text():
    cols = [Column("地区", "text"), Column("销售额", "number")]
    rows = [{"地区": r, "销售额": v} for r, v in zip(["华东", "华南"], [100, 200])]
    ds = Dataset(id="t3", name="地区销售", source_type="csv", columns=cols,
                 rows=rows, total_rows=len(rows))
    assert build_topic_chart(ds) is None, "边界：非文本来源不应产出主题图"
    print("  非文本来源返回 None 通过")


def test_boundary_too_few_segments():
    cols = [Column("行内容", "text")]
    rows = [{"行内容": "只有一句话"}]  # 片段数 < _MIN_SEG
    ds = Dataset(id="t4", name="短文本", source_type="text", columns=cols,
                 rows=rows, raw_text="只有一句话", total_rows=len(rows))
    assert build_topic_chart(ds) is None, "边界：片段过少不应强行聚类"
    print("  片段过少返回 None 通过")


if __name__ == "__main__":
    test_positive_topic_chart()
    test_positive_aggregate_sum()
    test_negative_flag_marker_matters()
    test_boundary_non_text()
    test_boundary_too_few_segments()
    print("ALL P1-1 TESTS PASSED")
