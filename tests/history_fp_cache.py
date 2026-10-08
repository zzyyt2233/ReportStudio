# -*- coding: utf-8 -*-
"""history 整包缓存回归：
- 同一份 outputs 连续两次 /api/history，第二次命中缓存（结果一致）
- 新增报告目录后指纹变化，缓存自动失效，列表更新
- 重新生成报告（往目录里写文件）也会推高子目录 mtime，缓存失效
- 搜索词与 limit 分别缓存，互不串
"""
import json
import os
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fastapi.testclient import TestClient  # noqa: E402

import app as app_mod  # noqa: E402
from tests._helpers import can_delete, ok, rmtree_quiet  # noqa: E402

PASS = FAIL = 0


def check(cond, msg):
    global PASS, FAIL
    if ok(cond, msg):
        PASS += 1
    else:
        FAIL += 1


def _mk_report(out, name, title="测试报告"):
    d = os.path.join(out, name)
    os.makedirs(d, exist_ok=True)
    with open(os.path.join(d, "meta.json"), "w", encoding="utf-8") as f:
        json.dump({"title": title, "charts": 1, "metrics": 1, "datasets": []}, f)
    with open(os.path.join(d, "report.html"), "w", encoding="utf-8") as f:
        f.write("<html></html>")
    return d


def main():
    # 每次开新目录：上一轮的残留可能删不掉（受限环境的删除守卫按额度拦），
    # 固定目录名会让「报告数 = 2」这类断言被上一次的残留污染。
    base = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "temp")
    os.makedirs(base, exist_ok=True)
    tmp = tempfile.mkdtemp(prefix="hist_fp_cache_", dir=base)
    old_out = app_mod.OUT_DIR
    app_mod.OUT_DIR = tmp
    app_mod._HIST_CACHE.clear()
    app_mod._HIST_RESP["fp"] = None
    app_mod._HIST_RESP["hits"] = {}
    client = TestClient(app_mod.app)
    try:
        _mk_report(tmp, "甲报告_20260101_000000")
        _mk_report(tmp, "乙报告_20260102_000000")

        r1 = client.get("/api/history").json()
        check(r1["grand_total"] == 2, f"初始两份报告（实际 {r1['grand_total']}）")
        check(len(app_mod._HIST_RESP["hits"]) == 1, "首次请求后缓存了一包")

        r2 = client.get("/api/history").json()
        check(r2 == r1, "第二次请求命中缓存，结果一致")
        check(len(app_mod._HIST_RESP["hits"]) == 1, "命中缓存不产生新条目")

        r3 = client.get("/api/history", params={"q": "甲"}).json()
        check(r3["total"] == 1 and r3["grand_total"] == 2,
              "搜索词单独缓存，grand 不受过滤影响")
        check(len(app_mod._HIST_RESP["hits"]) == 2, "搜索视图另存一包")

        _mk_report(tmp, "丙报告_20260103_000000")
        r4 = client.get("/api/history").json()
        check(r4["grand_total"] == 3, f"新增报告后缓存失效（实际 {r4['grand_total']}）")

        # 重新生成报告：往已存在的报告目录里写新文件，推高子目录 mtime
        time.sleep(0.05)  # mtime 精度兜底
        d = os.path.join(tmp, "甲报告_20260101_000000")
        with open(os.path.join(d, "report.docx"), "w") as f:
            f.write("x")
        os.utime(d, None)
        r5 = client.get("/api/history").json()
        names = {it["name"]: sorted(it["files"].keys()) for it in r5["items"]}
        check("report.docx" in names.get("甲报告", []),
              "报告目录内容变化（新增导出文件）后缓存失效，文件列表更新")

        # 删除报告目录后失效。整套回归连跑时 turn 内的删除额度可能已经用光，
        # 那时这一步删不掉，会误判成「缓存没失效」——先探一下，不行就如实跳过。
        if can_delete(tmp):
            rmtree_quiet(os.path.join(tmp, "乙报告_20260102_000000"))
            r6 = client.get("/api/history").json()
            check(r6["grand_total"] == 2, f"删除报告后缓存失效（实际 {r6['grand_total']}）")
        else:
            print("  ⚠ 本 turn 的删除额度已用尽，跳过「删除报告后缓存失效」这一项")
    finally:
        app_mod.OUT_DIR = old_out
        app_mod._HIST_CACHE.clear()
        app_mod._HIST_RESP["fp"] = None
        app_mod._HIST_RESP["hits"] = {}
        rmtree_quiet(tmp)

    print(f"\n{'=' * 40}\n结果: {PASS} 通过, {FAIL} 失败")
    sys.exit(1 if FAIL else 0)


if __name__ == "__main__":
    main()
