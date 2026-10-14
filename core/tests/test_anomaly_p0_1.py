"""P0-1 统计异常标记：aggregate 挂异常 → 图表染红+标注 → 结论提异常，含反向验证。

反向验证：把 detect_series_anomalies 替换成空实现，断言必须全部失效，
证明红点/标注/结论都是 P0-1 改动真正带来的，而非假绿。
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from core.model import Dataset, Column, ChartSpec
from core.analyze import aggregate, template_summary
import core.analyze as A
from core.render.echarts import build_option


def _ds():
    months = [f"{i}月" for i in range(1, 13)]
    vals = [100, 110, 105, 95, 120, 115, 800, 108, 98, 130, 125, 102]
    cols = [Column("月份", "text"), Column("销售额", "number")]
    rows = [{"月份": m, "销售额": v} for m, v in zip(months, vals)]
    return Dataset(id="d1", name="测试销售", source_type="csv",
                   columns=cols, rows=rows, total_rows=len(rows))


def _red(opt):
    return any(isinstance(d, dict) and d.get("itemStyle", {}).get("color") == "#E24B4A"
               for d in opt["series"][0]["data"])


def test_forward():
    ds = _ds()
    spec = ChartSpec(type="bar", x="月份", y=["销售额"], agg="sum")
    agg = aggregate(ds, spec)
    assert agg["series"][0]["anomalies"], "正向：应检出异常"
    opt = build_option(agg, spec)
    assert _red(opt), "正向：应有红色异常点"
    assert opt["series"][0].get("markPoint"), "正向：应有「异」标注"
    assert "异常" in template_summary(ds, [spec], []), "正向：结论应提异常"


def test_reverse():
    ds = _ds()
    spec = ChartSpec(type="bar", x="月份", y=["销售额"], agg="sum")
    orig = A.detect_series_anomalies
    A.detect_series_anomalies = lambda *a, **k: []
    agg = aggregate(ds, spec)
    opt = build_option(agg, spec)
    assert not _red(opt), "反向：无检测不应有红点"
    assert not opt["series"][0].get("markPoint"), "反向：无检测不应有标注"
    assert "异常" not in template_summary(ds, [spec], []), "反向：无检测结论不应提异常"
    A.detect_series_anomalies = orig


if __name__ == "__main__":
    test_forward()
    test_reverse()
    print("✓ P0-1 异常标记全部通过（正向+反向）")
