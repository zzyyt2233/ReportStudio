"""类别轴（x）含空值/缺失值的回归测试。

修复前：分类列为空的行会被当成一个「空」分组，图上凭空多一根柱子，
且数值是聚合出来的，极易误导。修复后：这类行被剔除并计数，结论里提示。
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.model import Column, Dataset, ChartSpec
from core.analyze import aggregate, template_summary


def _ds():
    cols = [Column("省份", "text"), Column("营收", "number")]
    # 广东/江苏/山东 有值；河南 缺失（None）；浙江 空字符串；福建 有值
    rows = [
        {"省份": "广东", "营收": 100},
        {"省份": "江苏", "营收": 200},
        {"省份": "山东", "营收": 150},
        {"省份": None, "营收": 999},        # 缺失分类
        {"省份": "", "营收": 888},          # 空分类
        {"省份": "福建", "营收": 50},
    ]
    return Dataset(id="t1", name="销售表", source_type="csv",
                   columns=cols, rows=rows, total_rows=len(rows))


def test_no_phantom_empty_category():
    ds = _ds()
    spec = ChartSpec(type="bar", x="省份", y=["营收"], agg="sum", sort_by="__x__")
    agg = aggregate(ds, spec)
    assert "" not in agg["categories"], f"不应出现空分类，实际={agg['categories']}"
    assert agg["skipped_empty_category"] == 2, f"应剔除 2 行空分类，实际={agg['skipped_empty_category']}"
    # 有效 4 行求和 = 100+200+150+50 = 500（不含 999/888 两个空分类行）
    total = sum(agg["series"][0]["data"])
    assert total == 500, f"聚合总和应为 500（剔除空分类），实际={total}"
    print(f"✓ 无空分类幽灵柱；剔除 2 行；有效合计={total}")


def test_summary_mentions_skipped():
    ds = _ds()
    spec = ChartSpec(type="bar", x="省份", y=["营收"], agg="sum")
    summary = template_summary(ds, [spec], metrics=[])
    assert "为空未计入图表" in summary, f"结论应提示空分类剔除，实际：{summary}"
    print(f"✓ 结论含空分类提示：{summary.split('。')[-2] if '。' in summary else summary}")


def test_all_missing_x():
    cols = [Column("类别", "text"), Column("量", "number")]
    rows = [{"类别": None, "量": 1}, {"类别": "", "量": 2}]
    ds = Dataset(id="t2", name="空表", source_type="csv", columns=cols,
                 rows=rows, total_rows=2)
    spec = ChartSpec(type="bar", x="类别", y=["量"], agg="sum")
    agg = aggregate(ds, spec)
    assert agg["categories"] == [], f"全空分类应无类别，实际={agg['categories']}"
    assert agg["skipped_empty_category"] == 2
    print("✓ 整列都是空分类时类别为空、剔除 2 行（不崩）")


if __name__ == "__main__":
    test_no_phantom_empty_category()
    test_summary_mentions_skipped()
    test_all_missing_x()
    print("\n类别轴空值回归测试全部通过 ✅")
