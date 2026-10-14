"""P1-2 回归：趋势叙事（涨最快区间 / 降幅最大段 / 拐点）。

正向：时间序数据应冒出趋势句。
反向：关掉 analyze_trend 后趋势句必须消失（且其他结论保留），证明非假绿。
边界：非时间序（按值排序的柱子）不应误报趋势。
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from core.model import Column, ChartSpec, Dataset
from core import analyze
from core.analyze import template_summary


def _month_ds():
    months = [f"{i}月" for i in range(1, 13)]
    sales = [100, 110, 120, 180, 200, 150, 140, 160, 210, 230, 250, 300]
    cols = [Column("月份", "text"), Column("销售额", "number")]
    rows = [{"月份": m, "销售额": v} for m, v in zip(months, sales)]
    return Dataset(id="t1", name="月度销售", source_type="csv", columns=cols,
                   rows=rows, total_rows=len(rows))


def _month_spec():
    return ChartSpec(type="line", x="月份", y=["销售额"], agg="sum")


def test_positive():
    s = template_summary(_month_ds(), [_month_spec()], [])
    assert "增长最快" in s, "正向：应出现涨最快区间"
    assert "趋势拐点" in s, "正向：应出现拐点句"
    assert "3月→4月" in s and "+50.0%" in s, "正向：涨最快区间应为 3月→4月 +50%"
    print("  正向通过:", [l + "。" for l in s.split("。") if "增长最快" in l or "拐点" in l or "降幅最大" in l])


def test_negative():
    orig = analyze.analyze_trend
    analyze.analyze_trend = lambda *a, **k: None  # 模拟关掉改动
    try:
        s2 = template_summary(_month_ds(), [_month_spec()], [])
        assert "增长最快" not in s2, "反向：关闭趋势后不应出现涨最快句"
        assert "趋势拐点" not in s2, "反向：关闭趋势后不应出现拐点句"
        assert "最高" in s2 and "最低" in s2, "反向：非趋势结论应保留"
    finally:
        analyze.analyze_trend = orig
    print("  反向验证通过（改动确实生效，非假绿）")


def test_non_temporal_no_trend():
    cols = [Column("地区", "text"), Column("销售额", "number")]
    rows = [{"地区": r, "销售额": v}
            for r, v in zip(["华东", "华南", "华北", "西南"], [100, 200, 150, 300])]
    ds = Dataset(id="t2", name="地区销售", source_type="csv", columns=cols,
                 rows=rows, total_rows=len(rows))
    s = template_summary(ds, [ChartSpec(type="bar", x="地区", y=["销售额"], agg="sum")], [])
    assert "增长最快" not in s, "非时间序不应出现趋势句"
    assert "趋势拐点" not in s, "非时间序不应出现拐点句"
    print("  非时间序无趋势误报 通过")


if __name__ == "__main__":
    test_positive()
    test_negative()
    test_non_temporal_no_trend()
    print("ALL P1-2 TESTS PASSED")
