"""重复列名回归测试。

真实数据里同名列很常见（两个「金额」、两个「数量」），而行的数据是按列名做键存的，
列名一旦重复，后一列会**静默覆盖**前一列 —— 列数看着没少，值却少了一个、图和指标全错。

旧实现的去重只查一次：`金额_2 / 金额 / 金额` 会改出第二个「金额_2」照样撞名。
本测试盯的就是「去重后仍重名 / 值丢失」这两件事。
"""

from __future__ import annotations

import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)

from core.parsers.tabular import build_dataset  # noqa: E402
from core.parsers.document import parse_text_content  # noqa: E402
from core.model import Column, Dataset  # noqa: E402
from core.merge import merge_group  # noqa: E402


def check(cond: bool, msg: str) -> None:
    print(("  ✓ " if cond else "  ✗ ") + msg)
    if not cond:
        raise AssertionError(msg)


def assert_clean(grid: list[list], label: str) -> Dataset:
    ds = build_dataset(grid, "t", "csv")
    names = [c.name for c in ds.columns]
    check(len(names) == len(set(names)), f"{label}：去重后无同名列 {names}")
    check(len(ds.columns) == len(grid[0]), f"{label}：列数不少（{len(ds.columns)}）")
    if ds.rows:
        check(len(ds.rows[0]) == len(ds.columns),
              f"{label}：每行键数 = 列数（{len(ds.rows[0])}/{len(ds.columns)}），没有值被吃掉")
    return ds


print("=" * 62)
print("1 · 基础重复列名")
print("=" * 62)
ds = assert_clean([["A", "A", "B"], ["1", "2", "3"]], "A A B")
check(ds.rows[0]["A"] == 1.0 and ds.rows[0]["A_1"] == 2.0 and ds.rows[0]["B"] == 3.0,
      f"三个值都保住了 {ds.rows[0]}")

print("\n" + "=" * 62)
print("2 · 对抗：预置后缀 + 重名（旧代码会撞出两个 金额_2）")
print("=" * 62)
ds = assert_clean([["金额_2", "金额", "金额"], ["100", "200", "300"]], "金额_2/金额/金额")
check(sorted(ds.rows[0].values()) == [100.0, 200.0, 300.0],
      f"100/200/300 一个都没丢 {ds.rows[0]}")

print("\n" + "=" * 62)
print("3 · 三/四列同名 + 预置后缀")
print("=" * 62)
ds = assert_clean([["A", "A", "A"], ["1", "2", "3"]], "A A A")
check(sorted(ds.rows[0].values()) == [1.0, 2.0, 3.0], "三同名值齐全")
ds = assert_clean([["B", "B_1", "B_2", "B"], ["1", "2", "3", "4"]], "B B_1 B_2 B")
check(sorted(ds.rows[0].values()) == [1.0, 2.0, 3.0, 4.0], "四列交叉后缀，值齐全")

print("\n" + "=" * 62)
print("4 · 空表头 + 重名")
print("=" * 62)
ds = assert_clean([[None, "A", "A"], ["x", "1", "2"]], "空列名 + A A")
check(ds.rows[0]["列1"] == "x" and ds.rows[0]["A"] == 1.0, f"空表头也补名且不撞 {ds.rows[0]}")

print("\n" + "=" * 62)
print("5 · Markdown 表格重复列名（走 document 解析器）")
print("=" * 62)
md = "| 门店 | 金额 | 金额 |\n|---|---|---|\n| 甲 | 10 | 20 |\n| 乙 | 30 | 40 |\n"
ds_list = parse_text_content(md, "md测试")
tbl = [d for d in ds_list if d.columns and d.columns[0].name != "指标"]
check(bool(tbl), "md 表格被解析出来")
if tbl:
    t = tbl[0]
    names = [c.name for c in t.columns]
    check(len(names) == len(set(names)), f"md 表格去重后无同名列 {names}")
    check(all(len(r) == len(t.columns) for r in t.rows),
          f"md 每个值都在 {[r for r in t.rows][:2]}")

print("\n" + "=" * 62)
print("6 · 重名列的表参与多表合并")
print("=" * 62)


def mk(name, cols, rows):
    return Dataset(id=name[:4], name=name, source_type="csv",
                   columns=[Column(c, t) for c, t in cols], rows=rows,
                   total_rows=len(rows))


A = build_dataset([["金额", "金额"], ["100", "200"]], "表A", "csv")
B = mk("表B", [("金额", "number")], [{"金额": 300}])
m = merge_group([A, B])
mnames = [c.name for c in m.columns]
check(len(mnames) == len(set(mnames)), f"合并后无同名列 {mnames}")
check(all(len(r) == len(m.columns) for r in m.rows), "合并后每行键数 = 列数")
vals = sorted(v for r in m.rows for k, v in r.items() if k != "来源" and isinstance(v, float))
check(vals == [100.0, 200.0, 300.0], f"合并后三张表的值都在 {vals}")

print("\n" + "=" * 62)
print("重复列名回归测试全部通过 ✅")
print("=" * 62)
