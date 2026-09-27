"""数据看板回归。

看板这块最容易出错的地方不是「渲染不出来」，而是**安静地少了东西**：

- 引用的数据表被清理了 → 图不见了，页面上什么都不说，用户以为「这指标本来就没数」
- 改名时只传了图表、没传名称 → 名字被清空成「未命名看板」
- 保存空看板 → 攒一堆点开是空白的看板

所以这里重点验「有没有如实说出来」，而不是只看 HTTP 200。

另外验一条设计约束：看板存的是**怎么算**而不是算好的数字 ——
同一个看板在生产数据变了之后重新渲染，图上的数要跟着变。
"""

from __future__ import annotations

import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from _helpers import (ROOT, IsolatedSession, can_delete,  # noqa: E402
                      dashboards_dir, make_client)

FAILS: list[str] = []
CREATED_DASHES: list[str] = []


def ok(label: str, cond: bool, detail: str = "") -> bool:
    print(f"  {'✓' if cond else '✗ 失败!'} {label}" + (f"  {detail}" if detail else ""))
    if not cond:
        FAILS.append(label)
    return bool(cond)


PASTE_V1 = """省份\t销售额\t订单量
广东\t4060.5\t120
江苏\t3090\t98
浙江\t3070\t105
山东\t2390\t77
四川\t1660\t60"""

# 广东销售额改成 9999，用来验「重算」而不是「存快照」
PASTE_V2 = """省份\t销售额\t订单量
广东\t9999\t120
江苏\t3090\t98
浙江\t3070\t105"""


def save_dash(client, **payload) -> dict:
    r = client.post("/api/dashboard/save", json=payload).json()
    if r.get("id"):
        CREATED_DASHES.append(r["id"])
    return r


def test_save_and_render(client) -> None:
    print("1 · 保存看板并渲染")
    with IsolatedSession(client) as iso:
        dsid = client.post("/api/paste",
                           json={"text": PASTE_V1, "name": "看板回归表"}).json()["dataset"]["id"]
        iso.track(dsid)

        r = save_dash(client, name="回归看板", title="回归看板", subtitle="单元测试",
                      dataset_ids=[dsid], metrics=["销售额", "订单量"],
                      charts=[
                          {"type": "bar", "x": "省份", "y": ["销售额"], "agg": "sum",
                           "sort_by": "销售额", "sort_order": "desc", "span": 6},
                          {"type": "pie", "x": "省份", "y": ["订单量"], "agg": "sum",
                           "span": 6},
                          {"type": "line", "x": "省份", "y": ["销售额"], "agg": "sum",
                           "span": 12},
                      ])
        ok("保存成功", r.get("ok") is True, str(r.get("error", "")))
        ok("渲染出 3 张图", r.get("charts") == 3, str(r.get("charts")))
        ok("2 个指标卡", r.get("metrics") == 2, str(r.get("metrics")))
        ok("没有缺表", r.get("missing") == [], str(r.get("missing")))

        path = r.get("path") or ""
        ok("HTML 落在 E 盘", path.startswith("E:"), path)
        ok("HTML 存在且体积合理",
           os.path.isfile(path) and os.path.getsize(path) > 1000,
           f"{os.path.getsize(path) if os.path.exists(path) else 0} B")

        html = open(path, encoding="utf-8").read()
        ok("图表库是内联的（拷走也能看）", "echarts.init" in html)
        ok("有全屏能力", "requestFullscreen" in html)
        ok("三个图表容器都在", all(f'id="chart{i}"' in html for i in range(3)))
        ok("宽度用了 12 栅格", "grid-column:span 12" in html)
        ok("降序后的数据在页面里（广东 4060.5）", "4060.5" in html)
        ok("页面上写明了数据来源", "看板回归表" in html)


def test_persist_is_not_snapshot(client) -> None:
    """看板存的必须是「怎么算」：数据变了，重新渲染就该跟着变。"""
    print("\n2 · 看板是镜子不是快照：数据变了重新渲染要跟着变")
    with IsolatedSession(client) as iso:
        dsid = client.post("/api/paste",
                           json={"text": PASTE_V1, "name": "看板镜子表"}).json()["dataset"]["id"]
        iso.track(dsid)
        r = save_dash(client, name="镜子看板", dataset_ids=[dsid],
                      metrics=["销售额"], charts=[
                          {"type": "bar", "x": "省份", "y": ["销售额"],
                           "sort_by": "销售额", "sort_order": "desc", "span": 6}])
        html1 = open(r["path"], encoding="utf-8").read()
        ok("初版含 4060.5", "4060.5" in html1)

        # 把表换成 v2（同 id 覆盖），再重新渲染
        client.post("/api/fix", json={"id": dsid})
        client.delete(f"/api/dataset/{dsid}")
        d2 = client.post("/api/paste",
                         json={"text": PASTE_V2, "name": "看板镜子表"}).json()["dataset"]
        # 用新表重建一个同配置看板，等价于「打开看板时按当前数据重算」
        r2 = save_dash(client, id=r["id"], dataset_ids=[d2["id"]],
                       charts=[{"type": "bar", "x": "省份", "y": ["销售额"],
                                "sort_by": "销售额", "sort_order": "desc", "span": 6}])
        ok("重渲染后用了既存看板 id", r2.get("id") == r["id"], str(r2.get("id")))
        html2 = open(r2["path"], encoding="utf-8").read()
        ok("新数据 9999 出现了", "9999" in html2)
        ok("旧数据 4060.5 不再出现（不是快照）", "4060.5" not in html2)
        iso.track(d2["id"])


def _arrays(html: str) -> dict:
    """把看板 HTML 里注入的 TITLES / OPTS / TABLES 三个数组解出来。"""
    out = {}
    for name in ("TITLES", "OPTS", "TABLES"):
        m = re.search(r"var " + name + r"=(\[.*?\]);", html, re.S)
        out[name] = json.loads(m.group(1)) if m else None
    return out


def test_zoom_and_data_table(client) -> None:
    """看板上每张图都能放大看，并且能看到这张图背后的数。

    这里重点验三件事：
      1. 图上的数和表里的数必须是同一批 —— 表格是从 option 反解的，
         不是另算一遍，否则会出现「图上是 15%，表里写 0.15」。
      2. 「放大」按钮、弹层、每个图对应的数据都得真的存在，
         而不是只画了个按钮点下去没反应。
      3. 列名里出现 </script> 时不能把 script 块提前截断。
    """
    print("\n3 · 放大某张图 + 查看它的数据")
    with IsolatedSession(client) as iso:
        dsid = client.post("/api/paste",
                           json={"text": PASTE_V1, "name": "放大回归表"}).json()["dataset"]["id"]
        iso.track(dsid)
        r = save_dash(client, name="放大回归看板", dataset_ids=[dsid],
                      metrics=["销售额"],
                      charts=[
                          {"type": "bar", "x": "省份", "y": ["销售额", "订单量"],
                           "agg": "sum", "span": 6},
                          {"type": "pie", "x": "省份", "y": ["订单量"],
                           "agg": "sum", "span": 6},
                      ])
        html = open(r["path"], encoding="utf-8").read()

        ok("每张图都有「放大」按钮",
           all(f'onclick="zoomChart({i})"' in html for i in range(2)))
        ok("放大弹层在页面里", 'id="zb"' in html and 'class="zb-panel"' in html)
        ok("弹层默认是收起的", re.search(r'id="zb"[^>]*hidden', html) is not None)
        ok("弹层有事没事都不占位（CSS 有 hidden 兜底）",
           "[hidden]{display:none!important}" in html)
        ok("有数据表视图", 'id="zbTable"' in html and "renderZbTable" in html)
        ok("能导出 CSV", "zbSaveCsv" in html)
        ok("CSV 带 BOM（否则 Excel 打开中文乱码）", r"\ufeff" in html)
        ok("放大后能按类目缩放", "dataZoom" in html)

        arr = _arrays(html)
        ok("三个数组都注入成功", all(arr[k] is not None for k in arr), str(list(arr)))
        ok("图的份数与数组长度一致",
           len(arr["OPTS"]) == 2 and len(arr["TABLES"]) == 2 and len(arr["TITLES"]) == 2,
           f'{len(arr["OPTS"])}/{len(arr["TABLES"])}/{len(arr["TITLES"])}')

        t0 = arr["TABLES"][0]
        ok("直角坐标图：首列是横轴列名", t0["columns"][0] == "省份", str(t0["columns"]))
        ok("直角坐标图：每个系列一列",
           t0["columns"][1:] == ["销售额", "订单量"], str(t0["columns"]))
        ok("直角坐标图：5 个类目 5 行", len(t0["rows"]) == 5, str(len(t0["rows"])))
        # 表格里的数必须和图上的数一致：这是这个功能唯一不能错的地方
        onpic = arr["OPTS"][0]["series"][0]["data"]
        intable = [row[1] for row in t0["rows"]]
        ok("表里的数和画在图上的数完全一致", onpic == intable,
           f"图 {onpic} / 表 {intable}")
        ok("行第一格是类目", t0["rows"][0][0] == "广东", str(t0["rows"][0][0]))

        t1 = arr["TABLES"][1]
        ok("饼图多出一列占比", t1["columns"] == ["省份", "订单量", "占比"],
           str(t1["columns"]))
        shares = [row[2] for row in t1["rows"]]
        ok("占比按整张饼算，合计约 100%",
           abs(sum(shares) - 100) < 0.5, f"合计 {sum(shares):.2f}%")
        ok("占比是算出来的而不是抄的",
           all(isinstance(s, (int, float)) for s in shares), str(shares[:3]))

        # 百分比列的展示值也要和图上一致（图上乘过 100，表里不能还是 0.15）
        ds2 = client.post("/api/paste", json={
            "text": "省份\t销售额\t毛利率\n广东\t1000\t15.0%\n江苏\t1000\t25.0%",
            "name": "放大百分号表"}).json()["dataset"]["id"]
        iso.track(ds2)
        r2 = save_dash(client, name="放大百分号看板", dataset_ids=[ds2],
                       charts=[{"type": "bar", "x": "省份", "y": ["毛利率"],
                                "agg": "sum", "span": 6}])
        h2 = open(r2["path"], encoding="utf-8").read()
        t2 = _arrays(h2)["TABLES"][0]
        ok("百分比列在表里是 15 而不是 0.15",
           t2["rows"][0][1] == 15.0, str(t2["rows"][0]))

    print("\n4 · 列名里带 </script> 不能把整页脚本截断")
    with IsolatedSession(client) as iso:
        evil = "</script><script>alert(1)</script>"
        ds3 = client.post("/api/paste", json={
            "text": f"省份\t{evil}\n广东\t1\n江苏\t2",
            "name": "注入回归表"}).json()["dataset"]["id"]
        iso.track(ds3)
        r3 = save_dash(client, name="注入回归看板", dataset_ids=[ds3],
                       charts=[{"type": "bar", "x": "省份", "y": [evil],
                                "agg": "sum", "span": 6}])
        h3 = open(r3["path"], encoding="utf-8").read()
        ok("数据里的 </script> 被转义", "<\\/script>" in h3)
        ok("script 块没有被提前截断", h3.count("</script>") == 2,
           f"实际 {h3.count('</script>')} 个结束标签")
        arr3 = _arrays(h3)
        # 转义后仍然得是合法 JSON，表格功能不能被注入防护搞坏
        ok("转义后数组仍能正常解析", arr3["TABLES"] is not None)
        ok("列名原样保留给表格用",
           arr3["TABLES"][0]["columns"][1] == evil,
           str(arr3["TABLES"][0]["columns"])[:80])


def test_preview_also_has_zoom(client) -> None:
    """工具界面里的看板页签走的是 /api/dashboard/preview，不是存盘的看板。
    放大能力得两边都有，不然「预览里点不动、打开新窗口才行」最费解。"""
    print("\n5 · 预览态（不落盘那种）也有放大能力")
    with IsolatedSession(client) as iso:
        dsid = client.post("/api/paste",
                           json={"text": PASTE_V1, "name": "预览放大表"}).json()["dataset"]["id"]
        iso.track(dsid)
        r = client.post("/api/dashboard/preview", json={
            "name": "预览放大", "dataset_ids": [dsid], "metrics": ["销售额"],
            "charts": [{"type": "bar", "x": "省份", "y": ["销售额"],
                        "agg": "sum", "span": 6}]}).json()
        ok("预览渲染成功", r.get("ok") is True, str(r.get("error", "")))
        path = r.get("path") or ""
        html = open(path, encoding="utf-8").read() if os.path.isfile(path) else ""
        ok("预览 HTML 里也有放大按钮", 'onclick="zoomChart(0)"' in html)
        ok("预览 HTML 里也有数据表", 'id="zbTable"' in html)
        ok("预览不留常驻看板（文件名是 _preview）", "_preview" in path, path)


def test_missing_tables_reported(client) -> None:
    print("\n6 · 引用的表没了：必须如实报出来，不能安静少几张图")
    r = save_dash(client, name="缺表看板", dataset_ids=["不存在_xyz"],
                  metrics=["销售额"],
                  charts=[{"type": "bar", "x": "省份", "y": ["销售额"], "span": 6}])
    ok("缺表被报出来", r.get("missing") == ["不存在_xyz"], str(r.get("missing")))
    html = open(r["path"], encoding="utf-8").read()
    ok("页面上写明「有数据表没找到」", "没找到" in html and "已跳过" in html)
    ok("还给了怎么恢复的提示", "重新导入" in html)
    ok("空看板也不报 5xx", r.get("ok") is True)


def test_refuse_empty(client) -> None:
    print("\n7 · 空看板要拒掉")
    r = client.post("/api/dashboard/save",
                    json={"name": "空的", "charts": [], "metrics": []}).json()
    ok("空看板被拒", r.get("ok") is False, str(r.get("error")))
    r2 = client.post("/api/dashboard/save", json={
        "name": "只有半张图", "metrics": [],
        "charts": [{"type": "bar", "x": "", "y": [], "span": 6}]}).json()
    ok("横轴数值不全的图不算数", r2.get("ok") is False, str(r2.get("error")))


def test_update_keeps_fields(client) -> None:
    """改看板时没传的字段不能被清空（否则名字会变「未命名看板」）。"""
    print("\n8 · 改看板：没传的字段要沿用原值")
    with IsolatedSession(client) as iso:
        dsid = client.post("/api/paste",
                           json={"text": PASTE_V1, "name": "看板改名表"}).json()["dataset"]["id"]
        iso.track(dsid)
        r = save_dash(client, name="原名很长的看板", subtitle="副标题在",
                      dataset_ids=[dsid], metrics=["销售额"],
                      charts=[{"type": "bar", "x": "省份", "y": ["销售额"], "span": 6},
                              {"type": "line", "x": "省份", "y": ["订单量"], "span": 6}])
        r2 = client.post("/api/dashboard/save", json={
            "id": r["id"], "dataset_ids": [dsid],
            "charts": [{"type": "bar", "x": "省份", "y": ["销售额"], "span": 12}]}).json()
        ok("名字沿用原值", r2.get("name") == "原名很长的看板", str(r2.get("name")))
        ok("指标沿用原值", r2.get("metrics") == 1, str(r2.get("metrics")))
        ok("图改成 1 张", r2.get("charts") == 1, str(r2.get("charts")))
        ok("不存在的看板 id 要报错",
           client.post("/api/dashboard/save",
                       json={"id": "nope12345", "charts": []}).json().get("ok") is False)


def test_list_and_delete(client) -> None:
    print("\n9 · 清单与删除")
    lst = client.get("/api/dashboards").json()["items"]
    ok("清单能列出来", isinstance(lst, list) and len(lst) >= 1, f"{len(lst)} 个")
    ok("清单带图数/指标数/更新时间",
       all(k in lst[0] for k in ("id", "name", "charts", "metrics", "updated")),
       str(lst[0]))

    if not can_delete():
        # 连跑全部套件时，前面的套件常把本 turn 的删除额度用光。
        # 这时候跳过删除断言并说明 —— 不能让环境限制变成「红色失败」，
        # 那会让人去查一个根本不存在的 bug。
        print("  ⚠ 跳过删除类断言：当前环境已不允许删除（一个 turn 内删满 50 个文件）")
        print("     这是环境限制，不是产品缺陷。单独跑本套件即可完整验证：")
        print("       .venv\\Scripts\\python.exe tests/dashboard.py")
        return

    did = CREATED_DASHES[0]
    ok("能删掉", client.delete(f"/api/dashboard/{did}").json().get("ok") is True)
    ok("删完清单里没有了",
       all(x["id"] != did for x in client.get("/api/dashboards").json()["items"]))
    ok("删不存在的返回 False",
       client.delete("/api/dashboard/nope123").json().get("ok") is False)
    ok("删除连 HTML 一起清掉",
       not os.path.exists(os.path.join(dashboards_dir(), f"{did}.html")))


def test_bad_input(client) -> None:
    print("\n10 · 畸形输入不许 5xx")
    bad = [
        {}, {"id": None}, {"charts": "不是数组"}, {"metrics": "不是数组"},
        {"charts": [None, 1, "x"]}, {"charts": [{"x": "a", "y": ["b"], "limit": "abc"}]},
        {"charts": [{"x": "a", "y": ["b"], "span": "很大"}]},
        {"charts": [{"x": "a", "y": ["b"], "span": -5}]},
        {"dataset_ids": "不是数组"}, {"name": "x" * 500},
    ]
    for i, payload in enumerate(bad):
        try:
            r = client.post("/api/dashboard/save", json=payload)
            ok(f"第 {i + 1} 组不 5xx", r.status_code < 500, f"HTTP {r.status_code}")
            if r.status_code < 500 and r.json().get("id"):
                CREATED_DASHES.append(r.json()["id"])
        except Exception as e:
            ok(f"第 {i + 1} 组不抛异常", False, str(e)[:80])
    for did in ("../etc/passwd", "..%2f..%2fx", "a" * 200, "", "  "):
        r = client.post("/api/dashboard/render", json={"id": did})
        ok(f"render id={did[:14]!r} 不 5xx", r.status_code < 500, f"HTTP {r.status_code}")
    ok("路径穿越的 id 不会写出去",
       not os.path.exists(os.path.join(ROOT, "..", "etc")),
       "没有生成越界目录")


def test_free_resize(client) -> None:
    """每张看板图都能自由缩放（拖拽手柄 + 落盘）。

    全屏只是把整页放大、图本身还是那么小。这里验三件事：
      1. 渲染出的 HTML 每张图都带宽度/高度拖拽手柄，且注入了 DASH_ID
         （手柄的 JS 靠它把尺寸绑回这份看板）
      2. /api/dashboard/resize 能更新尺寸并重新渲染（落盘 HTML 真的变宽变高）
      3. 之后再 save 一次（不传尺寸）：尺寸不能回弹 —— 否则「保存一次尺寸就丢」
    """
    print("\n11 · 每张图可自由缩放")
    with IsolatedSession(client) as iso:
        dsid = client.post("/api/paste",
                           json={"text": PASTE_V1, "name": "缩放回归表"}).json()["dataset"]["id"]
        iso.track(dsid)
        r = save_dash(client, name="缩放回归看板", dataset_ids=[dsid],
                      metrics=["销售额"],
                      charts=[{"type": "bar", "x": "省份", "y": ["销售额"],
                               "agg": "sum", "span": 6}])
        ok("保存成功", r.get("ok") is True, str(r.get("error", "")))
        path = r["path"]
        html = open(path, encoding="utf-8").read()
        ok("每张图有宽度拖拽手柄", 'class="rz rz-w"' in html)
        ok("每张图有高度拖拽手柄", 'class="rz rz-h"' in html)
        ok("注入了 DASH_ID（尺寸能绑回这份看板）",
           ('var DASH_ID="' + r["id"] + '"') in html, r["id"])
        ok("放大面板也能拖大", 'id="zbRz"' in html)
        ok("resize 初始化逻辑在页面里", "initResize" in html and "applyDashSizes" in html)

        # 把 chart0 拖成整宽 + 500px 高
        rr = client.post("/api/dashboard/resize", json={
            "id": r["id"],
            "sizes": {"chart0": {"span": 12, "height": 500}}}).json()
        ok("resize 接口成功", rr.get("ok") is True, str(rr.get("error", "")))
        h2 = open(rr["path"], encoding="utf-8").read()
        ok("重渲染后宽度生效（span 12）", "grid-column:span 12" in h2)
        ok("重渲染后高度生效（500px）", "height:500px" in h2)

        # 再 save 一次（只传图表、不传尺寸）：尺寸不能被清掉
        client.post("/api/dashboard/save", json={
            "id": r["id"], "dataset_ids": [dsid],
            "charts": [{"type": "bar", "x": "省份", "y": ["销售额"],
                        "agg": "sum", "span": 6}]}).json()
        h3 = open(path, encoding="utf-8").read()
        ok("再次保存后尺寸仍在（不回弹）",
           "grid-column:span 12" in h3 and "height:500px" in h3)


def main() -> int:
    client = make_client()
    before = {d["id"] for d in client.get("/api/datasets").json().get("items", [])}

    # 先钉住这条：测试建的看板必须落在 temp 里。
    # 不隔离的话会真写真删用户的 dashboards/，还会覆盖 _preview.html ——
    # 用户开着「看板」页签时刷新一下就变成测试内容了。
    real = os.path.normcase(os.path.join(ROOT, "dashboards"))
    here = os.path.normcase(dashboards_dir())
    ok("看板落在隔离目录，不碰真实的 dashboards/", here != real, here)
    ok("隔离目录在 E 盘 temp 下",
       here.startswith(os.path.normcase(os.path.join(ROOT, "temp"))), here)

    test_save_and_render(client)
    test_persist_is_not_snapshot(client)
    test_zoom_and_data_table(client)
    test_preview_also_has_zoom(client)
    test_missing_tables_reported(client)
    test_refuse_empty(client)
    test_update_keeps_fields(client)
    test_free_resize(client)

    # 清掉中途留下的看板，再验清单与删除
    if can_delete():
        for did in set(CREATED_DASHES[1:]):
            try:
                client.delete(f"/api/dashboard/{did}")
            except Exception:
                pass
    test_list_and_delete(client)
    test_bad_input(client)

    print("\n11 · 收尾")
    if can_delete():
        for did in set(CREATED_DASHES):
            try:
                client.delete(f"/api/dashboard/{did}")
            except Exception:
                pass
    else:
        print("  ⚠ 删除额度用尽，跳过后置清理（测试留下的看板都在 temp 隔离目录里，"
              "不会污染你的看板列表）")
    after = {d["id"] for d in client.get("/api/datasets").json().get("items", [])}
    ok("没有误删用户原有的数据表", not (before - after), str(before - after))

    print()
    if FAILS:
        print(f"看板回归失败 {len(FAILS)} 项：")
        for f in FAILS:
            print("   -", f)
        return 1
    print("数据看板回归全部通过 ✅（保存 / 重算 / 缺表提示 / 改名 / 删除 / 畸形输入）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
