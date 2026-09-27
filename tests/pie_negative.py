"""饼图 + 负值：会崩的和会静默画错的，一起钉死。

用户报错原文：Wedge sizes 'x' must be non negative values

这个 bug 的特别之处在于它**只在导出时才暴露**：
  · matplotlib（Word/PDF）直接抛异常 —— 用户看得见，所以来报了
  · ECharts（网页 / 看板）根本不报错，但负值不参与圆周分配，扇区占比是错的
    —— 网页上那张图早就是错的，只是一直没响

所以两头都要验：既不能崩，也不能画一张数字错但看着正常的图。

修法是把决策收到 analyze.aggregate（饼图遇负值自动改用柱状图），
两个渲染器都听它的。这样以后再加渲染后端也不会漏。
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tests._helpers import IsolatedSession, make_client, ok  # noqa: E402

FAILS: list[str] = []


def check(label: str, cond: bool, detail: str = "") -> None:
    if not ok(label, cond, detail):
        FAILS.append(label)


client = make_client()

# 有正有负的典型业务数据：净利润
TABLE = """公司\t净利润
甲公司\t1200
乙公司\t860
丙公司\t-340
丁公司\t520
戊公司\t-180
"""

with IsolatedSession(client) as iso:
    r = client.post("/api/paste", json={"text": TABLE, "name": "净利润"}).json()
    did = iso.track(r["dataset"]["id"])

    # ---------- 1. 分析层：决策只有一处 ----------
    print("\n[1] 分析层决策")
    from core.model import ChartSpec
    from core import analyze
    # 直接用会话里的数据集对象（含类型推断结果），贴近真实调用路径
    import app as server
    rds = server.SESSION[did]["dataset"]

    pie = ChartSpec(type="pie", x="公司", y=["净利润"], agg="sum")
    agg = analyze.aggregate(rds, pie)
    check("含负值时不再用饼图", agg["effective_type"] == "bar",
          f"→ {agg['effective_type']}")
    check("负值分类被点名记录下来", agg["pie_negative"] == ["丙公司", "戊公司"],
          f"→ {agg['pie_negative']}")
    check("数据一个没丢", agg["series"][0]["data"] == [1200.0, 860.0, -340.0, 520.0, -180.0],
          str(agg["series"][0]["data"]))

    pos = ChartSpec(type="pie", x="地区", y=["收入"], agg="sum")
    r2 = client.post("/api/paste",
                     json={"text": "地区\t收入\n甲\t100\n乙\t200\n丙\t300",
                           "name": "全正收入"}).json()
    did2 = iso.track(r2["dataset"]["id"])
    rds2 = server.SESSION[did2]["dataset"]
    agg_pos = analyze.aggregate(rds2, pos)
    check("全正数时照常是饼图", agg_pos["effective_type"] == "pie",
          f"→ {agg_pos['effective_type']}")
    check("全正数时没有负值记录", agg_pos["pie_negative"] == [])

    # ---------- 2. ECharts 层不能再画错图 ----------
    print("\n[2] ECharts（网页 / 看板）")
    from core.render.echarts import build_option

    opt = build_option(agg, pie)
    s0 = opt["series"][0]
    check("ECharts 实际画的是柱状图", s0["type"] == "bar", f"→ {s0['type']}")
    check("柱状图数据保留负值", -340.0 in s0["data"], str(s0["data"]))
    check("横轴是分类轴", opt["xAxis"]["type"] == "category")

    # ---------- 3. matplotlib 层不许崩 ----------
    print("\n[3] matplotlib（Word / PDF 导出）")
    from core.render.static_chart import render_chart

    out = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                       "temp", "pie_negative_check.png")
    try:
        p = render_chart(agg, pie, out)
        check("含负值的饼图不再抛异常", bool(p), f"→ {p}")
    except Exception as e:                       # noqa: BLE001
        check("含负值的饼图不再抛异常", False, f"→ {type(e).__name__}: {e}")

    # ---------- 4. 端到端：生成 + 摘要里必须写明图被换过 ----------
    print("\n[4] 端到端生成报告")
    g = client.post("/api/generate", json={
        "dataset_ids": [did], "title": "饼图负值回归", "template": "full",
        "metrics": [],
        "charts": [{"type": "pie", "x": "公司", "y": ["净利润"], "agg": "sum"}],
    }).json()
    check("生成成功", g.get("ok") is True, str(g.get("error")))
    check("仍然出了 1 张图", g.get("charts") == 1, f"→ {g.get('charts')}")

    s = g.get("summary") or ""
    check("摘要里写明了负值导致图型被换", "负值" in s and "柱状图" in s,
          f"→ {s[:80]}")
    check("摘要里点名了是哪几个分类",
          "丙公司" in s or "戊公司" in s, f"→ {s[:120]}")

    html = open(os.path.join(g["dir"], "report.html"), encoding="utf-8").read()
    check("报告里那张图是柱状图（不是抛错后空白）",
          '"type": "bar"' in html, "")

    # ---------- 5. 用户报的正是导出这一步 ----------
    print("\n[5] 导出（用户就是在这里撞到的）")
    for kind in ("docx", "pdf"):
        e = client.post("/api/export", json={"kind": kind, "dir": g["dir"]}).json()
        check(f"导出 {kind} 不再崩",
              e.get("ok") is True,
              "" if e.get("ok") else f"→ {e.get('error')}")

    # ---------- 6. 其它图型不受这次改动影响 ----------
    print("\n[6] 其它图型不受影响")
    for t in ("bar", "line", "stack_bar", "scatter"):
        sp = ChartSpec(type=t, x="公司", y=["净利润"], agg="sum")
        a = analyze.aggregate(rds, sp)
        check(f"{t} 的图型没有被改动", a["effective_type"] == t,
              f"→ {a['effective_type']}")
        try:
            render_chart(a, sp, out.replace(".png", f"_{t}.png"))
            check(f"{t} 渲染正常", True)
        except Exception as e:                   # noqa: BLE001
            check(f"{t} 渲染正常", False, f"→ {type(e).__name__}: {e}")

print()
if FAILS:
    print(f"饼图负值回归失败 {len(FAILS)} 项 ❌：{FAILS}")
    sys.exit(1)
print("饼图负值回归全部通过 ✅")
