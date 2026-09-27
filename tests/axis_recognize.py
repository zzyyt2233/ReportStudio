"""横轴/纵轴指标识别 + 横纵轴切换相关后端验证。

覆盖：
1. classify_columns：维度（文本列 + 低基数数值如年份）与度量（数值列）识别正确，
   高基数值（销售额）只当度量、不进横轴候选。
2. Dataset.to_dict 自动带出 roles，前端据此标注「维度/度量」。
3. rule_parse 横轴候选放宽：年份这类数值型维度能被认成横轴；高基数值指标
   （销售额）不会被抢去当横轴 —— 既「横轴纵轴都识别」又守住指标不被挪用。
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.model import Dataset, Column, classify_columns
from core.spec import rule_parse


def build_ds():
    return Dataset(
        id="t1", name="测试", source_type="excel",
        columns=[Column("年份", "number"), Column("地区", "text"),
                 Column("销售额", "number"), Column("利润", "number")],
        rows=[
            {"年份": 2020, "地区": "华东", "销售额": 100, "利润": 10},
            {"年份": 2020, "地区": "华北", "销售额": 200, "利润": 20},
            {"年份": 2021, "地区": "华东", "销售额": 150, "利润": 15},
            {"年份": 2021, "地区": "华北", "销售额": 250, "利润": 25},
        ],
    )


def test_classify():
    ds = build_ds()
    r = classify_columns(ds)
    dims, mea = r["dimensions"], r["measures"]
    assert "年份" in dims, dims
    assert "地区" in dims, dims
    # 高基数值只当度量，不当横轴候选
    assert "销售额" not in dims, dims
    assert "利润" not in dims, dims
    assert "年份" in mea and "销售额" in mea and "利润" in mea, mea
    print("PASS classify_columns")


def test_todict_roles():
    ds = build_ds()
    d = ds.to_dict()
    assert "roles" in d and d["roles"]["dimensions"], d
    assert "年份" in d["roles"]["dimensions"]
    print("PASS to_dict roles")


def test_rule_x():
    ds = build_ds()
    sp = rule_parse("按年份统计销售额", ds)
    assert sp and sp.charts and sp.charts[0].x == "年份", \
        (sp.charts[0].x if sp and sp.charts else None)
    sp2 = rule_parse("销售额按地区", ds)
    assert sp2.charts[0].x == "地区", sp2.charts[0].x
    # 高基数值不应被抢去当横轴
    sp3 = rule_parse("销售额降序", ds)
    assert sp3.charts[0].x != "销售额", sp3.charts[0].x
    print("PASS rule_parse x-axis")


if __name__ == "__main__":
    test_classify()
    test_todict_roles()
    test_rule_x()
    print("ALL axis_recognize PASS")
