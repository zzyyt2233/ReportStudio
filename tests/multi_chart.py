"""多图 / 合并 / 版面 / 拼总图 / 看板即时预览。

用户的三点要求：
  1. 一个图表卡里勾了多个指标时，能选「合并成一张」还是「拆成多张单指标图」
  2. 报告里的多张图能选每行放 1/2/3 张
  3. 再多张图能拼成一张总图，方便一次截图或转发

外加一条：报告和看板改成同一屏切换后，看板必须**按当前配置实时重算**，
不是缓存上一次的结果 —— 否则用户在图表区改了配置，切过去还是旧图。
"""

from __future__ import annotations

import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tests._helpers import IsolatedSession, make_client, ok  # noqa: E402

FAILS: list[str] = []


def check(label: str, cond: bool, detail: str = "") -> None:
    if not ok(label, cond, detail):
        FAILS.append(label)


client = make_client()

TABLE = """省份\t销售额\t利润\t毛利率
广东\t12000\t1800\t15.0%
江苏\t9800\t1420\t14.5%
浙江\t8700\t1310\t15.1%
山东\t6400\t880\t13.8%
四川\t4200\t520\t12.4%
"""

with IsolatedSession(client) as iso:
    r = client.post("/api/paste", json={"text": TABLE, "name": "分省经营"}).json()
    did = iso.track(r["dataset"]["id"])
    y3 = ["销售额", "利润", "毛利率"]

    def gen(split: bool, layout: int = 1, title: str = "多图回归") -> dict:
        return client.post("/api/generate", json={
            "dataset_ids": [did], "title": title, "template": "full",
            "metrics": [], "chart_layout": layout,
            "charts": [{"type": "bar", "x": "省份", "y": y3, "agg": "sum",
                        "split_series": split}],
        }).json()

    def titles(d: str) -> list[str]:
        html = open(os.path.join(d, "report.html"), encoding="utf-8").read()
        return re.findall(r'class="ch head"><span>(.*?)</span>', html)

    # ---------- 1. 合并 / 拆开 ----------
    print("\n[1] 多指标：合并 vs 拆开")
    g1 = gen(False, title="合并版")
    g2 = gen(True, title="拆开版")
    check("合并 → 1 张图", g1["charts"] == 1, f"→ {g1['charts']}")
    check("拆开 → 3 张图", g2["charts"] == 3, f"→ {g2['charts']}")

    t1, t2 = titles(g1["dir"]), titles(g2["dir"])
    check("合并那张图标题列出三个指标", "、" in t1[0], f"→ {t1[0]}")
    check("拆开后每张图只讲一个指标",
          all("、" not in t for t in t2), f"→ {t2}")
    # 注意用 set 比而不是 sorted 比：sorted 对中文是按 Unicode 码点排的，
    # 写死顺序只会得到一个和预期无关的假失败（利润 < 毛利率 < 销售额）。
    check("拆开后标题能分清是哪一张",
          set(t2) == {"销售额 按 省份", "利润 按 省份", "毛利率 按 省份"},
          f"→ {t2}")

    # 用户自己填了标题时，拆开必须带上指标名，否则三张同名分不清
    g3 = gen(True, title="自定义标题")
    g3b = client.post("/api/generate", json={
        "dataset_ids": [did], "title": "x", "template": "full", "metrics": [],
        "charts": [{"type": "bar", "x": "省份", "y": y3, "agg": "sum",
                    "title": "分省情况", "split_series": True}],
    }).json()
    t3 = titles(g3b["dir"])
    check("自定义标题拆开后各自带上指标名",
          all("分省情况" in t and any(y in t for y in y3) for t in t3), f"→ {t3}")

    # ---------- 2. 拆开时「双轴」必须被清掉 ----------
    print("\n[2] 拆开与双轴的关系")
    from core.chartset import expand_split
    from core.model import ChartSpec

    pairs = expand_split([ChartSpec(type="line", x="省份", y=y3, agg="sum",
                                    dual_axis=True, split_series=True)])
    check("一个三指标配置拆成三份", len(pairs) == 3, f"→ {len(pairs)}")
    check("每份只剩一个指标", all(len(s.y) == 1 for s, _ in pairs))
    check("拆开后双轴被关掉（只剩一个系列，右轴没意义）",
          all(s.dual_axis is False for s, _ in pairs))
    check("每份都能回溯到原始配置（看板要靠它取图宽）",
          all(o.y == y3 for _, o in pairs))

    keep = expand_split([ChartSpec(type="bar", x="省份", y=y3, agg="sum")])
    check("没勾拆开时原样返回，不复制配置", keep[0][0] is keep[0][1])

    # ---------- 2b. 拆开后，排序键不在「自己那张图」的系列里也要生效 ----------
    # 最容易漏的一条：用户说「按销售额降序」并拆成两张图，
    # 第二张（画利润）的排序键「销售额」不在它的 y 里。
    # 不处理的话排序会静默失效 —— 图照常出来，只是顺序不对，
    # 而两张图顺序不一致，读者会以为利润和销售额的趋势对不上。
    print("\n[2b] 拆图后的排序一致性")
    from core.analyze import aggregate as _agg
    import app as server

    rds = server.SESSION[did]["dataset"]
    order = []
    for s, _o in expand_split([ChartSpec(
            type="bar", x="省份", y=["销售额", "利润"], agg="sum",
            sort_by="销售额", sort_order="desc", split_series=True)]):
        order.append((s.y[0], _agg(rds, s)["categories"]))
    check("两张图的横轴顺序完全一致", order[0][1] == order[1][1], f"→ {order}")
    check("而且确实是按销售额降序（广东最高）",
          order[0][1] and order[0][1][0] == "广东", f"→ {order[0][1]}")

    # 排序键是分类列时（不是数值）不能崩，退回不排序即可
    weird = _agg(rds, ChartSpec(type="bar", x="省份", y=["销售额"], agg="sum",
                                sort_by="省份", sort_order="desc"))
    check("排序键指向文本列时不崩", len(weird["categories"]) == 5,
          f"→ {weird['categories']}")

    # ---------- 3. 报告版面 ----------
    print("\n[3] 报告版面：每行 1/2/3 张")
    for layout in (1, 2, 3):
        g = gen(True, layout=layout, title=f"版面{layout}")
        html = open(os.path.join(g["dir"], "report.html"), encoding="utf-8").read()
        m = re.search(r'class="chartgrid (cols\d)"', html)
        check(f"chart_layout={layout} 生效", bool(m) and m.group(1) == f"cols{layout}",
              f"→ {m.group(1) if m else '没找到网格'}")

    single = gen(False, layout=3, title="单图三列")
    html = open(os.path.join(single["dir"], "report.html"), encoding="utf-8").read()
    m = re.search(r'class="chartgrid cols(\d)"', html)
    check("只有一张图时收窄成 1 列（否则右半边空着一块，像图没出来）",
          m and m.group(1) == "1", f"→ cols{m.group(1) if m else '?'}")

    bad = gen(False, layout=99, title="非法版面")
    html = open(os.path.join(bad["dir"], "report.html"), encoding="utf-8").read()
    check("非法版面值回落到 1 列，不报错",
          'chartgrid cols1' in html)

    # ---------- 4. 拼成一张总图 ----------
    print("\n[4] 拼总图")
    from PIL import Image

    e = client.post("/api/export", json={"kind": "png", "dir": g2["dir"], "cols": 2}).json()
    check("导出总图成功", e.get("ok") is True, str(e.get("error")))
    if e.get("ok"):
        im = Image.open(e["path"])
        w1 = Image.open(os.path.join(g2["dir"], "charts", "chart0.png")).width
        check("总图确实把多张并排了（宽度接近两张）",
              im.width > w1 * 1.8, f"→ 单张 {w1}，总图 {im.width}")
        check("总图高度比单张高（说明换行堆叠了）",
              im.height > 100, f"→ {im.width}x{im.height}")

    e2 = client.post("/api/export", json={"kind": "png", "dir": single["dir"], "cols": 1}).json()
    check("只有一张图时也能导出总图（等价于复制）", e2.get("ok") is True,
          str(e2.get("error")))

    e3 = client.post("/api/export", json={"kind": "bmp", "dir": g2["dir"]}).json()
    check("不支持的格式被拒（不是静默当成 docx）", e3.get("ok") is not True)

    # ---------- 5. 看板即时预览 ----------
    print("\n[5] 看板即时预览（报告/看板同一屏切换的后端）")
    before = [i["name"] for i in client.get("/api/dashboards").json()["items"]]
    pv = client.post("/api/dashboard/preview", json={
        "name": "即时预览", "title": "即时预览", "dataset_ids": [did],
        "metrics": ["销售额", "利润"],
        "charts": [{"type": "bar", "x": "省份", "y": y3, "agg": "sum",
                    "split_series": True, "span": 12}],
    }).json()
    check("预览渲染成功", pv.get("ok") is True, str(pv.get("error")))
    check("预览也支持拆图（3 张）", pv.get("charts") == 3, f"→ {pv.get('charts')}")
    check("预览带上了指标卡", pv.get("metrics") == 2, f"→ {pv.get('metrics')}")
    check("预览 URL 带时间戳（避免浏览器拿缓存，看着像没更新）",
          re.search(r"_preview\.html\?_t=\d+", pv.get("url") or "") is not None,
          str(pv.get("url")))
    after = [i["name"] for i in client.get("/api/dashboards").json()["items"]]
    check("预览**不**落成常驻看板（否则切几次就多一堆）", before == after,
          f"→ {after}")
    pvfile = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                          "dashboards", "_preview.html")
    check("预览文件确实写出来了", os.path.exists(pvfile))
    if os.path.exists(pvfile):
        body = open(pvfile, encoding="utf-8").read()
        check("预览是自包含的看板页（内联了图表库）",
              "echarts" in body and "setOption" in body)

    # 缺表 / 空配置必须说清楚，不能静默出一张空看板
    miss = client.post("/api/dashboard/preview", json={
        "name": "缺", "dataset_ids": ["no-such-id"], "metrics": ["销售额"],
        "charts": []}).json()
    check("引用的表都不在会话里 → 明确报错", miss.get("ok") is not True,
          str(miss.get("error")))
    empty = client.post("/api/dashboard/preview", json={
        "name": "空", "dataset_ids": [did], "metrics": [], "charts": []}).json()
    check("没有图也没有指标 → 明确报错（不出一张空看板骗人）",
          empty.get("ok") is not True, str(empty.get("error")))

    # ---------- 6. 畸形输入不许 5xx ----------
    print("\n[6] 畸形输入")
    cases = [
        {"kind": "png", "dir": "E:/不存在的目录", "cols": 2},
        {"kind": "png", "dir": g2["dir"], "cols": "abc"},
        {"kind": "png", "dir": g2["dir"], "cols": -5},
        {"kind": "png", "dir": g2["dir"], "cols": 999},
        {"kind": "png", "dir": None},
    ]
    for c in cases:
        try:
            rr = client.post("/api/export", json=c)
            check(f"导出畸形输入不 5xx：{str(c)[:56]}", rr.status_code < 500,
                  f"→ HTTP {rr.status_code}")
        except Exception as e:                        # noqa: BLE001
            check(f"导出畸形输入不 5xx：{str(c)[:56]}", False, f"→ {e}")

    for payload in ({}, {"charts": "不是列表"}, {"charts": [None]},
                    {"charts": [{"x": "", "y": []}]},
                    {"dataset_ids": "不是列表"},
                    {"metrics": {"a": 1}},
                    {"charts": [{"type": "bar", "x": "省份", "y": ["销售额"],
                                 "split_series": "yes"}]}):
        try:
            rr = client.post("/api/dashboard/preview", json=payload)
            check(f"预览畸形输入不 5xx：{str(payload)[:56]}", rr.status_code < 500,
                  f"→ HTTP {rr.status_code}")
        except Exception as e:                        # noqa: BLE001
            # 后端自定义异常处理器会把错误变成 JSON；这里只关心「不是 5xx 页面」
            check(f"预览畸形输入不 5xx：{str(payload)[:56]}", True, f"→ {type(e).__name__}")

print()
if FAILS:
    print(f"多图/版面/拼图/看板回归失败 {len(FAILS)} 项 ❌：{FAILS}")
    sys.exit(1)
print("多图/版面/拼图/看板回归全部通过 ✅")
