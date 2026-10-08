"""「载入示例数据」按钮的内置示例表回归。

拿到分享包的人第一次打开是空界面，看不出这个工具能干什么，所以前端塞了一份
保费月报（DEMO_TABLE，写在 web/app.js 里）。这份数据是硬编码在 JS 里的字符串，
和真正解析它的后端之间没有任何编译期约束 —— JS 里手滑多打一个竖线、把某一列
写成中文括号，前端照样跑，只是导入后列名对不上、图表画不出来。

所以这里把 JS 里的常量原样抠出来，走真实 /api/paste 跑一遍，验前后端对得上：
- 36 行 × 6 列
- 列名与类型识别正确（月份是日期、区域/产品线是文本、三个指标是数值）
- 数据本身完整（6 个月 × 3 区域 × 2 产品线），没有缺月、没有解析警告
"""

from __future__ import annotations

import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

FAILS: list[str] = []


def ok(label: str, cond: bool, detail: str = "") -> bool:
    print(f"  {'✓' if cond else '✗ 失败!'} {label}" + (f"  {detail}" if detail else ""))
    if not cond:
        FAILS.append(label)
    return bool(cond)


ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def read_demo_table() -> str:
    """从 web/app.js 里抠出 DEMO_TABLE 的每一行。

    只认单引号字符串且不含转义字符 —— 示例数据里不该出现引号，
    真出现了说明有人改了写法，这里会直接报出来而不是悄悄解析错。
    """
    src = open(os.path.join(ROOT, "web", "app.js"), encoding="utf-8").read()
    m = re.search(r"const DEMO_TABLE = \[(.*?)\]\.join\(", src, re.S)
    assert m, "web/app.js 里找不到 DEMO_TABLE"
    lines = re.findall(r"'([^'\\]*)'", m.group(1))
    return "\n".join(lines)


def main() -> int:
    table = read_demo_table()
    ok("从 app.js 抠到示例数据", bool(table.strip()), f"{table.count(chr(10)) + 1} 行字面量")

    from fastapi.testclient import TestClient
    import app as A

    client = TestClient(A.app)

    print("\n1 · 通过粘贴导入链路解析")
    r = client.post("/api/paste", json={"text": table, "name": "示例数据 · 保费月报"})
    ok("导入返回 200", r.status_code == 200, f"HTTP {r.status_code}")
    d = r.json()
    if not ok("导入成功（ok=true）", d.get("ok") is True, str(d)[:160]):
        return 1

    ds = d["dataset"]
    cols = ds["columns"]
    names = [c["name"] for c in cols]

    print("\n2 · 列名与类型")
    ok("6 列", len(cols) == 6, str(names))
    ok("列名与表头一致",
       names == ["月份", "区域", "产品线", "保费收入(万元)", "保单件数", "赔付率(%)"],
       str(names))
    types = {c["name"]: c["dtype"] for c in cols}
    ok("月份识别成日期", types.get("月份") == "date", types.get("月份"))
    ok("区域、产品线识别成文本",
       types.get("区域") == "text" and types.get("产品线") == "text",
       f"{types.get('区域')} / {types.get('产品线')}")
    nums = [n for n in names if types.get(n) == "number"]
    ok("三个指标识别成数值", nums == ["保费收入(万元)", "保单件数", "赔付率(%)"], str(nums))

    print("\n3 · 数据完整度")
    ok("36 行", ds["total_rows"] == 36, str(ds["total_rows"]))
    # warnings 里正常会带一条「按 Markdown 表格解析」的说明，那是告知解析方式，
    # 不是问题。这里挡的是「疑似表头识别错」「有列无法解析」这类真警告。
    bad = [w for w in (ds.get("warnings") or [])
           if any(k in w for k in ("失败", "无法", "异常", "疑似", "有误", "请检查", "缺失"))]
    ok("没有报错类解析警告（只可有解析方式说明）", not bad,
       f"警告={ds.get('warnings')}")
    rows = ds["rows"]
    months = sorted({str(r["月份"]) for r in rows})
    ok("6 个月份", len(months) == 6, str(months))
    ok("3 个区域", len({str(r["区域"]) for r in rows}) == 3,
       str(sorted({str(r["区域"]) for r in rows})))
    ok("2 条产品线", len({str(r["产品线"]) for r in rows}) == 2,
       str(sorted({str(r["产品线"]) for r in rows})))
    ok("每月都是 6 条（3 区域 × 2 产品线，没有缺行）",
       all(sum(1 for r in rows if str(r["月份"]) == m) == 6 for m in months),
       str([sum(1 for r in rows if str(r["月份"]) == m) for m in months]))
    vals = [r["保费收入(万元)"] for r in rows]
    ok("三个指标都是数字（不是被当成文本的字符串）",
       all(isinstance(v, (int, float)) for v in vals), str(vals[:3]))
    ok("保费收入是正数且有量级（不是全 0）",
       all(isinstance(v, (int, float)) and 1000 <= v <= 6000 for v in vals),
       f"{min(vals)} ~ {max(vals)}")
    rates = [r["赔付率(%)"] for r in rows]
    ok("赔付率落在合理区间（30~80）",
       all(isinstance(v, (int, float)) and 30 <= v <= 80 for v in rates),
       f"{min(rates)} ~ {max(rates)}")

    print("\n4 · 前端常量本身没被改坏")
    ok("示例数据是 Markdown 表格（首行是表头、第二行是分隔线）",
       table.splitlines()[0].startswith("| 月份 |")
       and set(table.splitlines()[1].replace("|", "").replace("-", "").strip()) == set(),
       table.splitlines()[0][:40])
    ok("正文每行都是 6 个单元格",
       all(len(l.split("|")) == 8 for l in table.splitlines()[2:]),
       str([len(l.split("|")) for l in table.splitlines()[2:]][:3]))

    print()
    if FAILS:
        print(f"示例数据回归失败 {len(FAILS)} 项：")
        for f in FAILS:
            print("   -", f)
        return 1
    print("示例数据回归全部通过 ✓")
    return 0


if __name__ == "__main__":
    sys.exit(main())
