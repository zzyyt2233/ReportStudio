"""同名列合并时的类型冲突回归测试。

修复前：两张表都有「金额」列，一张被误判 text、一张是 number，合并后列类型取
先来者的 text，导致另一张表的数字被当文本、出不了图、数据静默丢失。
修复后：合并列取较强类型（number），值按目标类型规整，并给出类型冲突提示。
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.model import Column, Dataset
from core.merge import merge_group, similarity


def _mk(name, cols, rows):
    return Dataset(id=name[:4], name=name, source_type="csv",
                   columns=[Column(c, t) for c, t in cols], rows=rows,
                   total_rows=len(rows))


def test_standard_merge_unchanged():
    A = _mk("华东", [("名称", "text"), ("金额", "number")],
            [{"名称": "a", "金额": 100}, {"名称": "b", "金额": 200}])
    B = _mk("华北", [("名称", "text"), ("金额", "number")],
            [{"名称": "c", "金额": 300}])
    m = merge_group([A, B])
    assert [c.name for c in m.columns] == ["名称", "金额", "来源"], m.columns
    assert m.total_rows == 3
    assert sum(r["金额"] for r in m.rows) == 600
    print("✓ 标准同构合并：列/行/数值正确")


def test_type_conflict_reconciled_to_number():
    # P 的「金额」是 text（比如因为混了脏单元格被误判），Q 是 number
    P = _mk("P", [("金额", "text")], [{"金额": "100"}])
    Q = _mk("Q", [("金额", "number")], [{"金额": 200}])
    m = merge_group([P, Q])
    amt = [c for c in m.columns if c.name == "金额"][0]
    # 合并列应取更强的 number，而不是先来者的 text
    assert amt.dtype == "number", f"合并列 dtype 应为 number，实际 {amt.dtype}"
    vals = [r["金额"] for r in m.rows]
    # 两表的数字都应是可参与计算的数值，不应有字符串
    assert all(isinstance(v, (int, float)) for v in vals), f"值应全为数字，实际 {vals}"
    assert sum(vals) == 300, f"两表金额合计应为 300，实际 {vals}"
    # 应给出类型冲突提示
    assert any("类型不一致" in w for w in m.warnings), m.warnings
    print(f"✓ 类型冲突合并：dtype=number，值={vals}，含冲突提示")


def test_dirty_text_number_coerced():
    # Q 是干净的 number 表，P 是 text 表但值其实都是干净数字字符串
    P = _mk("P", [("金额", "text")], [{"金额": "100"}, {"金额": "250"}])
    Q = _mk("Q", [("金额", "number")], [{"金额": 200}])
    m = merge_group([P, Q])
    amt = [c for c in m.columns if c.name == "金额"][0]
    assert amt.dtype == "number"
    vals = [r["金额"] for r in m.rows]
    assert vals == [100, 250, 200], f"文本数字应被规整为数值，实际 {vals}"
    print(f"✓ 文本数字「100/250」规整为数值：{vals}")


def test_source_name_collision_still_ok():
    A = _mk("X", [("来源", "text"), ("v", "number")], [{"来源": "self", "v": 1}])
    B = _mk("Y", [("v", "number")], [{"v": 2}])
    m = merge_group([A, B])
    names = [c.name for c in m.columns]
    # 原「来源」列保留，追踪列改名「来源_」
    assert "来源" in names and "来源_" in names, names
    print(f"✓ 来源列撞名仍正确：{names}")


if __name__ == "__main__":
    test_standard_merge_unchanged()
    test_type_conflict_reconciled_to_number()
    test_dirty_text_number_coerced()
    test_source_name_collision_still_ok()
    print("\n合并类型冲突回归测试全部通过 ✅")
