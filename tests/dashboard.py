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

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from _helpers import ROOT, IsolatedSession, make_client  # noqa: E402

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


def test_missing_tables_reported(client) -> None:
    print("\n3 · 引用的表没了：必须如实报出来，不能安静少几张图")
    r = save_dash(client, name="缺表看板", dataset_ids=["不存在_xyz"],
                  metrics=["销售额"],
                  charts=[{"type": "bar", "x": "省份", "y": ["销售额"], "span": 6}])
    ok("缺表被报出来", r.get("missing") == ["不存在_xyz"], str(r.get("missing")))
    html = open(r["path"], encoding="utf-8").read()
    ok("页面上写明「有数据表没找到」", "没找到" in html and "已跳过" in html)
    ok("还给了怎么恢复的提示", "重新导入" in html)
    ok("空看板也不报 5xx", r.get("ok") is True)


def test_refuse_empty(client) -> None:
    print("\n4 · 空看板要拒掉")
    r = client.post("/api/dashboard/save",
                    json={"name": "空的", "charts": [], "metrics": []}).json()
    ok("空看板被拒", r.get("ok") is False, str(r.get("error")))
    r2 = client.post("/api/dashboard/save", json={
        "name": "只有半张图", "metrics": [],
        "charts": [{"type": "bar", "x": "", "y": [], "span": 6}]}).json()
    ok("横轴数值不全的图不算数", r2.get("ok") is False, str(r2.get("error")))


def test_update_keeps_fields(client) -> None:
    """改看板时没传的字段不能被清空（否则名字会变「未命名看板」）。"""
    print("\n5 · 改看板：没传的字段要沿用原值")
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
    print("\n6 · 清单与删除")
    lst = client.get("/api/dashboards").json()["items"]
    ok("清单能列出来", isinstance(lst, list) and len(lst) >= 1, f"{len(lst)} 个")
    ok("清单带图数/指标数/更新时间",
       all(k in lst[0] for k in ("id", "name", "charts", "metrics", "updated")),
       str(lst[0]))
    did = CREATED_DASHES[0]
    ok("能删掉", client.delete(f"/api/dashboard/{did}").json().get("ok") is True)
    ok("删完清单里没有了",
       all(x["id"] != did for x in client.get("/api/dashboards").json()["items"]))
    ok("删不存在的返回 False",
       client.delete("/api/dashboard/nope123").json().get("ok") is False)
    ok("删除连 HTML 一起清掉",
       not os.path.exists(os.path.join(ROOT, "dashboards", f"{did}.html")))


def test_bad_input(client) -> None:
    print("\n7 · 畸形输入不许 5xx")
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


def main() -> int:
    client = make_client()
    before = {d["id"] for d in client.get("/api/datasets").json().get("items", [])}

    test_save_and_render(client)
    test_persist_is_not_snapshot(client)
    test_missing_tables_reported(client)
    test_refuse_empty(client)
    test_update_keeps_fields(client)

    # 清掉中途留下的看板，再验清单与删除
    for did in set(CREATED_DASHES[1:]):
        try:
            client.delete(f"/api/dashboard/{did}")
        except Exception:
            pass
    test_list_and_delete(client)
    test_bad_input(client)

    print("\n8 · 收尾")
    for did in set(CREATED_DASHES):
        try:
            client.delete(f"/api/dashboard/{did}")
        except Exception:
            pass
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
