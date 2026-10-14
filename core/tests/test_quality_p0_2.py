"""P0-2 数据质量闸门：脏数据进图前被体检，问题写进 warnings 并在结论给健康度，含反向验证。

反向验证：把 assess_quality 与 profile_dataset 都替换成空实现，断言必须全部失效，
证明 warnings 与结论健康度都是 P0-2 改动真正带来的，而非假绿。
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from core.model import Dataset, Column, ChartSpec
from core.analyze import template_summary
from core.chartset import build_blocks
import core.chartset as CH
import core.analyze as AZ


def _ds():
    cols = [Column("地区", "text"), Column("销售额", "number"),
            Column("备注", "text"), Column("标记", "number")]
    rows = [
        {"地区": "华东", "销售额": 100, "备注": "a", "标记": 1},
        {"地区": "华南", "销售额": 90, "备注": "", "标记": 1},
        {"地区": "华北", "销售额": 80, "备注": "", "标记": 1},
        {"地区": "西南", "销售额": 70, "备注": "", "标记": 1},
        {"地区": "华东", "销售额": 100, "备注": "a", "标记": 1},  # 与首行完全重复
    ]
    return Dataset(id="d2", name="脏数据测试", source_type="csv",
                   columns=cols, rows=rows, total_rows=len(rows))


def test_forward():
    ds = _ds()
    spec = ChartSpec(type="bar", x="地区", y=["销售额"], agg="sum")
    build_blocks([ds], [spec])
    assert any("数据质量" in w for w in ds.warnings), "正向：warnings 应写入质量问题"
    assert "健康度" in template_summary(ds, [spec], []), "正向：结论应出现健康度"


def test_reverse():
    ds = _ds()
    spec = ChartSpec(type="bar", x="地区", y=["销售额"], agg="sum")
    o1, o2 = CH.assess_quality, AZ.profile_dataset
    CH.assess_quality = lambda ds: None
    AZ.profile_dataset = lambda ds: {"n": 0, "columns": {}, "issues": [],
                                     "dup_rows": 0, "health": 100}
    build_blocks([ds], [spec])
    assert not any("数据质量" in w for w in ds.warnings), "反向：闸门关闭不应写质量警告"
    assert "健康度" not in template_summary(ds, [spec], []), "反向：闸门关闭结论不应提健康度"
    CH.assess_quality, AZ.profile_dataset = o1, o2


if __name__ == "__main__":
    test_forward()
    test_reverse()
    print("✓ P0-2 数据质量闸门全部通过（正向+反向）")
