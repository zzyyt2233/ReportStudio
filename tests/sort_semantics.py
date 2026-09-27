"""排序语义回归。

**这个套件的由来**：用户反馈「我要求按什么指标降序也不能做到」。

实测发现后端排序是好的（109.3 → 69.4 → 62.8 确实在降），坏的是**需求解析**：
`rule_parse` 找横轴时「先在文本列里找，找不到就退到所有列」，于是一句
「按资产报酬率降序画柱状图」里，资产报酬率被抢去当了横轴；紧接着
「横轴不能等于数值」的保护又把它换成**另一个指标**（总资产增长率）——
用户想看的那个数在图上一次都没出现，而且全程不报错。

顺带还有两个缺口：
- `从高到低 / 由大到小 / 从多到少` 不在降序词表里 → 说了等于没说
- 图表卡建好后勾选数列不刷新排序下拉 → 手动路径下那个指标根本选不出来

所以这里分三层验：解析语义、词表覆盖、真跑一遍接口看数据顺序。

前两层可以直接断言「解析出来的 sort_by 是不是用户点的那个指标」，
最后一层必须验「报告里落地的数据真的降序」——只看 sort_by 字段会自欺，
字段对了但后端没接住同样是坏的。
"""

from __future__ import annotations

import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from _helpers import IsolatedSession, make_client  # noqa: E402

from core.model import Column, Dataset  # noqa: E402
from core import spec as S  # noqa: E402

FAILS: list[str] = []


def ok(label: str, cond: bool, detail: str = "") -> bool:
    print(f"  {'✓' if cond else '✗ 失败!'} {label}" + (f"  {detail}" if detail else ""))
    if not cond:
        FAILS.append(label)
    return bool(cond)


def _ds() -> Dataset:
    """一张有分类列也有多个指标的表——真实场景的样子。"""
    cols = [Column("上市公司代码", "text"), Column("最新公司全称", "text"),
            Column("资产报酬率", "number"), Column("总资产增长率", "number"),
            Column("资产负债率", "number"), Column("主营业务收入", "number")]
    return Dataset(id="t", name="t", source_type="excel", columns=cols, rows=[{}])


# ---------------------------------------------------------------- 1 · 解析语义

# (用户原话, 期望横轴, 期望数值, 期望排序依据, 期望方向, 期望前N)
PARSE_CASES = [
    # —— 就是用户报的那几句 ——
    ("按资产报酬率降序画柱状图", "上市公司代码", ["资产报酬率"], "资产报酬率", "desc", None),
    ("资产报酬率降序",           "上市公司代码", ["资产报酬率"], "资产报酬率", "desc", None),
    ("各公司主营业务收入降序",   "上市公司代码", ["主营业务收入"], "主营业务收入", "desc", None),
    ("按资产负债率升序",         "上市公司代码", ["资产负债率"], "资产负债率", "asc", None),
    # 点了名的分类列要当横轴，指标仍是那个指标
    ("按最新公司全称统计资产报酬率降序",
     "最新公司全称", ["资产报酬率"], "资产报酬率", "desc", None),
    # 前 N 条
    ("按资产报酬率从高到低排前10",
     "上市公司代码", ["资产报酬率"], "资产报酬率", "desc", 10),
    ("主营业务收入降序取前5",
     "上市公司代码", ["主营业务收入"], "主营业务收入", "desc", 5),
    # 散点图是例外：横轴本来就该是另一个数值列。
    # 「A和B的散点图」谁当横轴在中文里本身就是歧义的，这里按列出现顺序取，
    # 至少保证是确定的，用户可以在表单里换。
    ("主营业务收入和资产报酬率画散点图",
     "资产报酬率", ["主营业务收入"], "", "asc", None),
]


def test_parse_semantics() -> None:
    print("1 · 解析语义：被点名的指标必须当「数值」，不能被抢去当横轴")
    ds = _ds()
    for text, x, ys, sb, order, limit in PARSE_CASES:
        sp = S.rule_parse(text, ds)
        c = sp.charts[0] if sp and sp.charts else None
        if c is None:
            ok(f"{text} → 能解析出图", False, "返回了 None")
            continue
        good = (c.x == x and c.y == ys and c.sort_by == sb
                and c.sort_order == order and c.limit == limit)
        ok(f"{text}", good,
           f"x={c.x!r} y={c.y} sort_by={c.sort_by!r} order={c.sort_order} limit={c.limit}")
        # 无论怎么兜底，横轴都不许同时出现在数值里（散点图除外）
        if c.type != "scatter":
            ok(f"  └ 横轴不在数值里", c.x not in c.y, f"x={c.x!r} y={c.y}")


def test_sort_word_coverage() -> None:
    """降序/升序的每种说法都要认。用户换个说法就失效是最气人的。"""
    print("\n2 · 排序词表覆盖：换一种说法也要认")
    ds = _ds()
    for w in S.DESC_WORDS:
        sp = S.rule_parse(f"按资产报酬率{w}", ds)
        c = sp.charts[0] if sp and sp.charts else None
        ok(f"「{w}」判为降序", bool(c) and c.sort_order == "desc" and c.sort_by == "资产报酬率",
           f"→ {c.sort_order if c else '解析失败'}")
    for w in S.ASC_WORDS:
        sp = S.rule_parse(f"按资产报酬率{w}", ds)
        c = sp.charts[0] if sp and sp.charts else None
        ok(f"「{w}」判为升序", bool(c) and c.sort_order == "asc" and c.sort_by == "资产报酬率",
           f"→ {c.sort_order if c else '解析失败'}")


def test_vague_not_flooded() -> None:
    """没点名指标时只画一个数列，别一口气挂满（图会糊成一团）。"""
    print("\n3 · 含糊语句：兜底只挂一个数列")
    ds = _ds()
    for text in ("画个饼图", "按代码画条形图", "来个柱状图"):
        sp = S.rule_parse(text, ds)
        c = sp.charts[0] if sp and sp.charts else None
        ok(f"{text} 只挂 1 个数列", bool(c) and len(c.y) == 1, f"→ y={c.y if c else None}")


# ------------------------------------------------------------ 4 · 端到端顺序

PASTE = """省份\t销售额\t毛利率
广东\t4060.5\t0.44
江苏\t3090\t0.38
浙江\t3070\t0.465
山东\t2390\t0.42
四川\t1660\t0.355
北京\t1880\t0.51
湖北\t1420\t0.33"""


def _chart_order(html: str) -> tuple[list[str], list[float]]:
    """从报告 HTML 里抠出第一张图的横轴标签与数值。

    ECharts 的 xAxis 既能是对象也能是数组，两种都接住 —— 只处理一种的话，
    换个图型就会 KeyError，而那是测试自己炸了，不是被测代码有问题。
    """
    m = re.search(r"c0\.setOption\((\{.*?\})\);", html, re.S)
    if not m:
        return [], []
    opt = json.loads(m.group(1))
    xa = opt.get("xAxis")
    if isinstance(xa, list):
        xa = xa[0] if xa else {}
    cats = (xa or {}).get("data") or []
    ser = (opt.get("series") or [{}])[0] or {}
    data = ser.get("data") or []
    # 饼图/散点的 series.data 是 {name,value} 形式
    if data and isinstance(data[0], dict):
        return [d.get("name") for d in data], [d.get("value") for d in data]
    return list(cats), list(data)


def test_end_to_end_order() -> None:
    print("\n4 · 端到端：报告里落地的数据真的按指标降序")
    client = make_client()
    with IsolatedSession(client) as iso:
        r = client.post("/api/paste", json={"text": PASTE, "name": "排序语义_临时表"})
        ds = r.json()["dataset"]
        iso.track(ds["id"])
        cols = [c["name"] for c in ds["columns"]]
        ok("临时表建好了", ds["id"] not in iso.before and "销售额" in cols, str(cols))

        # 按销售额降序取前 5
        g = client.post("/api/generate", json={
            "dataset_ids": [ds["id"]], "title": "排序语义_降序", "template": "full",
            "charts": [{"type": "bar", "x": "省份", "y": ["销售额"], "agg": "sum",
                        "sort_by": "销售额", "sort_order": "desc", "limit": 5}],
            "metrics": []}).json()
        ok("生成成功", g.get("ok") is True, str(g.get("error", "")))
        html = open(os.path.join(g["dir"], "report.html"), encoding="utf-8").read()
        cats, vals = _chart_order(html)
        ok("取满 5 条", len(vals) == 5, f"→ {cats} / {vals}")
        ok("数值严格降序",
           len(vals) > 1 and all(vals[i] >= vals[i + 1] for i in range(len(vals) - 1)),
           str(vals))
        # 销售额排名：广东4060.5 > 江苏3090 > 浙江3070 > 山东2390 > 北京1880 > 四川1660
        ok("前 5 名的省份与数值都对",
           cats == ["广东", "江苏", "浙江", "山东", "北京"]
           and [round(v, 1) for v in vals] == [4060.5, 3090.0, 3070.0, 2390.0, 1880.0],
           f"→ {cats} {vals}")
        ok("最大值是广东(4060.5)", cats[:1] == ["广东"] and abs(vals[0] - 4060.5) < 1e-6,
           f"→ {cats[:1]} {vals[:1]}")
        ok("截断后四川没进来（它是第 6 名）", "四川" not in cats, f"→ {cats}")

        # 升序也要真的反过来
        g2 = client.post("/api/generate", json={
            "dataset_ids": [ds["id"]], "title": "排序语义_升序", "template": "full",
            "charts": [{"type": "bar", "x": "省份", "y": ["销售额"], "agg": "sum",
                        "sort_by": "销售额", "sort_order": "asc", "limit": 3}],
            "metrics": []}).json()
        html2 = open(os.path.join(g2["dir"], "report.html"), encoding="utf-8").read()
        cats2, vals2 = _chart_order(html2)
        ok("升序：数值递增",
           len(vals2) > 1 and all(vals2[i] <= vals2[i + 1] for i in range(len(vals2) - 1)),
           f"→ {cats2} {vals2}")
        ok("升序首条是最小值(湖北 1420)",
           cats2[:1] == ["湖北"], f"→ {cats2[:1]} {vals2[:1]}")

        # 「按横轴」排的是**轴标签**，不是数值 —— 所以验的是标签有序，
        # 以及标签和数值的对应关系没被打乱（值跟着标签一起搬）。
        g3 = client.post("/api/generate", json={
            "dataset_ids": [ds["id"]], "title": "排序语义_按横轴", "template": "full",
            "charts": [{"type": "bar", "x": "省份", "y": ["销售额"], "agg": "sum",
                        "sort_by": "__x__", "sort_order": "desc", "limit": None}],
            "metrics": []}).json()
        html3 = open(os.path.join(g3["dir"], "report.html"), encoding="utf-8").read()
        cats3, vals3 = _chart_order(html3)
        ok("按横轴降序：标签确实是降序",
           len(cats3) == 7 and cats3 == sorted(cats3, reverse=True), f"→ {cats3}")
        pair = dict(zip(cats3, vals3))
        ok("标签搬动后数值仍然跟着走（广东=4060.5）",
           abs(pair.get("广东", 0) - 4060.5) < 1e-6 and abs(pair.get("湖北", 0) - 1420.0) < 1e-6,
           f"→ 广东={pair.get('广东')} 湖北={pair.get('湖北')}")


def test_nl_paste_roundtrip() -> None:
    """从「一句话」到「报告里的顺序」整条链路，别只看中间字段。"""
    print("\n5 · 一句话 → 报告：解析结果直接喂给生成接口")
    client = make_client()
    with IsolatedSession(client) as iso:
        r = client.post("/api/paste", json={"text": PASTE, "name": "排序语义_一句话"})
        dsid = r.json()["dataset"]["id"]
        iso.track(dsid)

        # 降序前 3 = 广东4060.5 > 江苏3090 > 浙江3070；升序前 3 = 湖北1420 < 四川1660 < 北京1880
        for text, top in (("按销售额降序取前3", "广东"), ("按销售额升序取前3", "湖北")):
            nl = client.post("/api/parse_nl",
                             json={"dataset_id": dsid, "text": text}).json()
            ok(f"「{text}」解析成功", nl.get("ok") is True, str(nl.get("error", "")))
            charts = (nl.get("spec") or {}).get("charts") or []
            if not charts:
                ok(f"「{text}」有图", False)
                continue
            c = charts[0]
            ok(f"  └ 数值选对", c.get("y") == ["销售额"], f"→ {c.get('y')}")
            ok(f"  └ 横轴是省份", c.get("x") == "省份", f"→ {c.get('x')!r}")
            g = client.post("/api/generate", json={
                "dataset_ids": [dsid], "title": f"NL_{text}", "template": "full",
                "charts": charts, "metrics": []}).json()
            html = open(os.path.join(g["dir"], "report.html"), encoding="utf-8").read()
            cats, vals = _chart_order(html)
            ok(f"  └ 图上首条是 {top}", cats[:1] == [top], f"→ {cats[:1]}")


def main() -> int:
    test_parse_semantics()
    test_sort_word_coverage()
    test_vague_not_flooded()
    test_end_to_end_order()
    test_nl_paste_roundtrip()
    print()
    if FAILS:
        print(f"排序语义回归失败 {len(FAILS)} 项：")
        for f in FAILS:
            print("   -", f)
        return 1
    print("排序语义回归全部通过 ✅（解析归属 / 词表 / 端到端顺序）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
