"""报告「明细数据」按用户要求排序的回归。

之前排序只作用于图表，明细表永远按原始行序铺出来 —— 用户说「按销售额降序」，
图上降了、明细一动不动，看着像没照做。现在明细表也要能升序/降序。

重点验：
- 数值升序/降序
- 空值无论升降序都沉底（界面提示写了「空值排最后」，得兑现）
- 文本/月份走自然序（"1月" 在 "10月" 前，不是字符串序）
- 排序列在某张表里不存在时原样返回，不排序也不报错
- 整份报告的 HTML 里明细表的行序真的跟着排了，且标了排序说明
"""

from __future__ import annotations

import os
import re
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.model import Column, Dataset            # noqa: E402
from core.analyze import detail_rows, detail_sort_label  # noqa: E402
from core.render.html_report import render_report  # noqa: E402
from core.model import ReportSpec                 # noqa: E402

FAILS: list[str] = []


def ok(label: str, cond: bool, detail: str = "") -> bool:
    print(f"  {'✓' if cond else '✗ 失败!'} {label}" + (f"  {detail}" if detail else ""))
    if not cond:
        FAILS.append(label)
    return bool(cond)


def mk(rows, cols=None):
    cols = cols or [Column("name", "text"), Column("sales", "number"),
                   Column("rate", "number")]
    return Dataset(id="d1", name="销量表", source_type="csv",
                   columns=cols, rows=rows, total_rows=len(rows))


class Spec:
    """只取 detail_sort_by / detail_sort_order 的轻量替身，detail_rows 只读这两个。"""
    def __init__(self, by="", order="asc"):
        self.detail_sort_by = by
        self.detail_sort_order = order


def test_numeric_order():
    print("1 · 数值升/降序 + 空值沉底")
    rows = [
        {"name": "甲", "sales": 30, "rate": 0.1},
        {"name": "乙", "sales": None, "rate": 0.5},
        {"name": "丙", "sales": 10, "rate": 0.9},
        {"name": "丁", "sales": 20, "rate": None},
    ]
    ds = mk(rows)
    s = Spec("", "asc")
    ok("不排序时保持原始顺序", [r["name"] for r in detail_rows(ds, s)] == ["甲", "乙", "丙", "丁"])

    s.detail_sort_by = "sales"; s.detail_sort_order = "desc"
    got = [r["name"] for r in detail_rows(ds, s)]
    ok("销售额降序", got == ["甲", "丁", "丙", "乙"], str(got))
    ok("降序说明正确", detail_sort_label(s) == "按「sales」降序（空值排最后）", detail_sort_label(s))

    s.detail_sort_order = "asc"
    got = [(r["name"], r["sales"]) for r in detail_rows(ds, s)]
    ok("销售额升序：空值沉底", got == [("丙", 10), ("丁", 20), ("甲", 30), ("乙", None)], str(got))
    ok("升序说明正确", detail_sort_label(s).endswith("升序（空值排最后）"))


def test_natural_text_order():
    print("\n2 · 文本/月份走自然序")
    rows = [{"name": f"{m}月", "sales": m, "rate": 1} for m in [10, 2, 1, 12]]
    ds = mk(rows)
    s = Spec("name", "asc")
    ok("月份自然升序（1月<10月<12月）",
       [r["name"] for r in detail_rows(ds, s)] == ["1月", "2月", "10月", "12月"])
    s.detail_sort_order = "desc"
    ok("月份自然降序",
       [r["name"] for r in detail_rows(ds, s)] == ["12月", "10月", "2月", "1月"])


def test_missing_column_keeps_order():
    print("\n3 · 排序列不存在时原样返回")
    rows = [{"name": "甲", "sales": 3}, {"name": "乙", "sales": 1}]
    ds = mk(rows)
    s = Spec("不存在的列", "desc")
    ok("列不存在 → 不排序", [r["name"] for r in detail_rows(ds, s)] == ["甲", "乙"])
    # 没给列名集合时，函数无法判断，只能信任字段（返回说明）；
    # 给了列名集合、且列不在其中时，必须返回空串 —— 否则会谎称排了序。
    ok("列不存在 → 传入列名集合时说明为空",
       detail_sort_label(s, {"sales", "name", "rate"}) == "")
    ok("列存在 → 仍给出说明",
       detail_sort_label(Spec("sales", "desc"), {"sales", "name"}) ==
       "按「sales」降序（空值排最后）")


def _tbody_rows(html: str) -> list[list[str]]:
    """取出明细表 tbody 里每行的单元格文本。"""
    m = re.search(r"<tbody>(.*?)</tbody>", html, re.S)
    if not m:
        return []
    body = m.group(1)
    # 按 </tr> 切行，每行里抽 <td>...</td>
    rows = []
    for tr in re.findall(r"<tr>(.*?)</tr>", body, re.S):
        cells = re.findall(r"<td>(.*?)</td>", tr, re.S)
        rows.append([re.sub(r"<[^>]+>", "", c).strip() for c in cells])
    return rows


def test_render_obeys_sort():
    print("\n4 · 报告 HTML 里的明细表跟着排序走")
    rows = [
        {"name": "甲", "sales": 30, "rate": 0.1},
        {"name": "乙", "sales": None, "rate": 0.5},
        {"name": "丙", "sales": 10, "rate": 0.9},
        {"name": "丁", "sales": 20, "rate": None},
    ]
    ds = mk(rows)
    spec = ReportSpec(title="排序验证", template="full", dataset_ids=["d1"],
                      detail_sort_by="sales", detail_sort_order="desc")
    out = tempfile.mkdtemp(prefix="rs_detsort_")
    path = render_report([ds], spec, [], [], "", out, "")
    html = open(path, encoding="utf-8").read()

    ok("标题是排序验证", "排序验证" in html)
    ok("标了排序说明", "按「sales」降序" in html)
    ok("表头标了降序箭头", 'class="sorted"' in html and "↓" in html)

    rows_tbl = _tbody_rows(html)
    ok("明细表有 4 行", len(rows_tbl) == 4, str(len(rows_tbl)))
    first_col = [r[0] for r in rows_tbl]
    ok("明细表首列按销售额降序排列", first_col == ["甲", "丁", "丙", "乙"], str(first_col))

    # 升序：空值沉底
    spec.detail_sort_order = "asc"
    path2 = render_report([ds], spec, [], [], "", out, "")
    html2 = open(path2, encoding="utf-8").read()
    rows_tbl2 = _tbody_rows(html2)
    ok("升序时空值沉底", [r[0] for r in rows_tbl2] == ["丙", "丁", "甲", "乙"],
       str([r[0] for r in rows_tbl2]))


def main() -> int:
    test_numeric_order()
    test_natural_text_order()
    test_missing_column_keeps_order()
    test_render_obeys_sort()
    print()
    if FAILS:
        print(f"明细排序回归失败 {len(FAILS)} 项：")
        for f in FAILS:
            print("   -", f)
        return 1
    print("明细排序回归全部通过 ✓")
    return 0


if __name__ == "__main__":
    sys.exit(main())
