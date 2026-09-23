"""全格式兼容矩阵：不假设「一定有图片」，逐一把每种来源单独跑通，再跑组合与极端空场景。

核心诉求（用户明确要求）：
  不一定有图片，所有内容都要能兼容 —— 缺任何一类都不能崩；没有可出图数据时
  也必须降级出一份「数据摘要报告」，而不是直接报错。

覆盖：
  A 单类型：xlsx / csv(GBK) / docx / md / txt / pdf / png(无模型) / 粘贴文本
  B 「没有图片」场景：只有表格、只有纯文本、只有图片、空输入、坏 id、空文件
  C 全类型混合 → 报告须包含所有来源
  D 「没有图片」场景下 HTML/MD 落盘 + Word/PDF 导出都要成功
"""

from __future__ import annotations

import os
import sys

from fastapi.testclient import TestClient

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)

import app as server  # noqa: E402
from _helpers import datasets as _all_datasets  # noqa: E402

SAMPLES = os.path.join(ROOT, "samples")
client = TestClient(server.app)

FAILS: list[str] = []

# 用户原有的数据表基线：第一次清场时记下，之后永不删除。
# 原来的实现是直接调 /api/session/clear —— 那会把用户在界面上存着的表全删掉，
# 跑一次回归就丢一次数据。分节清场的语义用「删本次新建的」来满足就够了。
_BASELINE: set[str] | None = None


def hr(t: str) -> None:
    print("\n" + "=" * 64)
    print(t)
    print("=" * 64)


def check(cond: bool, msg: str) -> None:
    print(("  ✓ " if cond else "  ✗ ") + msg)
    if not cond:
        FAILS.append(msg)


def upload(name: str) -> list[str]:
    path = os.path.join(SAMPLES, name)
    with open(path, "rb") as f:
        r = client.post("/api/upload",
                        files={"files": (name, f.read(), "application/octet-stream")})
    ids = []
    for res in r.json().get("results", []):
        if res.get("ok"):
            ds = res["dataset"]
            ids.append(ds["id"])
            print(f"  OK  {name:<30} {ds['source_type']:<8} {ds['total_rows']:>4}行")
            for w in ds.get("warnings", []):
                print(f"        提示: {w}")
        else:
            print(f"  ERR {name}: {res.get('error')}")
    return ids


def clear() -> None:
    """分节清场：只删本次测试自己建的表，用户原有的那张表不动。

    绝不调用 /api/session/clear（那会连用户界面上的数据一起清掉）。
    """
    global _BASELINE
    now = {d["id"] for d in _all_datasets(client)}
    if _BASELINE is None:
        _BASELINE = now
    for did in now - _BASELINE:
        try:
            client.delete(f"/api/dataset/{did}")
        except Exception:
            pass


def cols_of(ds_id: str) -> list[dict]:
    """注意响应键是 items —— 早先这里读的是 datasets，一直拿到空列表，
    导致 auto_charts 永远返回空、assert 静默失效。"""
    for d in _all_datasets(client):
        if d["id"] == ds_id:
            return d.get("columns") or []
    return []


def auto_charts(ds_id: str) -> list[dict]:
    """有文本列+数值列就给一张柱状图，否则返回空（走降级报告）。"""
    cols = cols_of(ds_id)
    x = next((c["name"] for c in cols if c["dtype"] == "text"), None)
    y = next((c["name"] for c in cols if c["dtype"] == "number"), None)
    if x and y:
        return [{"type": "bar", "x": x, "y": [y], "agg": "sum"}]
    return []


def generate(payload: dict) -> dict:
    r = client.post("/api/generate", json=payload)
    return r.json() if r.status_code == 200 else {"ok": False, "error": f"HTTP {r.status_code}"}


def json_preview(r) -> str:
    try:
        s = str(r.json())
    except Exception:
        s = r.text
    return s[:220]


# ---------------------------------------------------------------- A 单类型
hr("A · 每种来源单独跑一遍（有图片就跑，没有也不影响）")

SINGLE = [
    ("销售明细（带合并标题）.xlsx", "xlsx"),
    ("库存台账（GBK分号分隔）.csv", "csv"),
    ("季度经营分析.docx", "docx"),
    ("会议纪要.md", "md"),
    ("市场调研报告（带表格）.pdf", "pdf"),
    ("竞品对比（图表截图）.png", "png（无模型，应降级不崩）"),
]
for fn, label in SINGLE:
    clear()
    print(f"\n[ {label} ]")
    ids = upload(fn)
    check(bool(ids), f"{label} 能导入出至少一张表")
    if not ids:
        continue
    charts = auto_charts(ids[0])
    g = generate({"dataset_ids": ids, "merge_mode": "merge", "title": f"{label} 单测",
                  "template": "simple", "charts": charts, "metrics": []})
    check(bool(g.get("ok")), f"{label} 一定能生成报告（有图出图 / 无图降级）")
    if g.get("ok") and not charts:
        check(bool(g.get("degraded")), f"{label} 无图时明确标记为降级报告")
        if g.get("dir") and os.path.exists(os.path.join(g["dir"], "report.html")):
            body = open(os.path.join(g["dir"], "report.html"), encoding="utf-8").read()
            check("数据摘要报告" in body, f"{label} 降级说明写进了报告")

hr("A+ · txt 纯文本单独导入（无任何表格）")
clear()
os.makedirs(os.path.join(ROOT, "temp"), exist_ok=True)
txt_path = os.path.join(ROOT, "temp", "matrix_sample.txt")
with open(txt_path, "w", encoding="utf-8") as f:
    f.write("2025 年第三季度经营摘要\n\n营收同比 +12%，主要来自华东。\n成本上升 4%。\n")
with open(txt_path, "rb") as f:
    r = client.post("/api/upload", files={"files": ("matrix_sample.txt", f.read(), "text/plain")})
txt_ids = [res["dataset"]["id"] for res in r.json().get("results", []) if res.get("ok")]
check(bool(txt_ids), "txt 能导入")
if txt_ids:
    g = generate({"dataset_ids": txt_ids, "merge_mode": "merge", "title": "txt 单测",
                  "template": "simple", "charts": [], "metrics": []})
    check(bool(g.get("ok")) and g.get("degraded"), "纯 txt（无表格）也能出降级报告，不崩")

hr("A+ · 粘贴文本单独导入")
clear()
r = client.post("/api/paste", json={"text": "部门\t营收\n直营\t1280\n加盟\t960\n电商\t1450"})
pid = r.json().get("dataset", {}).get("id")
check(bool(pid), "粘贴能导入")
if pid:
    g = generate({"dataset_ids": [pid], "merge_mode": "merge", "title": "粘贴单测",
                  "template": "simple",
                  "charts": [{"type": "bar", "x": "部门", "y": ["营收"], "agg": "sum"}],
                  "metrics": ["营收"]})
    check(bool(g.get("ok")) and g.get("charts", 0) >= 1, "粘贴文本能正常出图")

# ---------------------------------------------------------------- B 极端场景
hr("B1 · 只有表格、完全没有图片（最常见场景）")
clear()
tids = upload("销售明细（带合并标题）.xlsx")
g = generate({"dataset_ids": tids, "merge_mode": "merge", "title": "无图片·仅表格",
              "template": "full",
              "charts": [{"type": "bar", "x": "月份", "y": ["销售额"], "agg": "sum"}],
              "metrics": ["销售额"]})
check(bool(g.get("ok")) and g.get("charts", 0) >= 1, "无图片时图表照常出")

hr("B2 · 只有纯文本、没有任何表格结构")
clear()
did = upload("会议纪要.md")
g = generate({"dataset_ids": did, "merge_mode": "merge", "title": "无图片·仅文本",
              "template": "full", "charts": [], "metrics": []})
check(bool(g.get("ok")), "只有文本时也能出报告（降级，不报错）")
if g.get("ok"):
    check(bool(g.get("degraded")), "明确标记为降级报告")
    check(os.path.exists(os.path.join(g["dir"], "report.html")), "只有文本时 HTML 已落盘")

hr("B3 · 只有图片、且未接大模型（llm=false）")
clear()
iid = upload("竞品对比（图表截图）.png")
check(bool(iid), "图片能导入（不因无模型而报错）")
if iid:
    g = generate({"dataset_ids": iid, "merge_mode": "merge", "title": "无模型·仅图片",
                  "template": "simple", "charts": [], "metrics": []})
    check(bool(g.get("ok")), "只有图片且无模型时也能出报告（降级提示）")
    if g.get("ok"):
        body = open(os.path.join(g["dir"], "report.html"), encoding="utf-8").read()
        check(("图片" in body) or ("模型" in body) or ("识别" in body),
              "报告中出现了图片/模型相关的降级提示")

hr("B4 · 什么都没有（空输入）")
clear()
r = client.post("/api/generate", json={"dataset_ids": [], "merge_mode": "merge",
                                       "title": "空", "template": "simple",
                                       "charts": [], "metrics": []})
check(r.status_code < 500, f"空输入不应 5xx（实际 HTTP {r.status_code}）")
print(f"        返回: {json_preview(r)}")

hr("B5 · 引用了不存在的表 id")
clear()
r = client.post("/api/generate", json={"dataset_ids": ["deadbeef"], "merge_mode": "merge",
                                       "title": "坏id", "template": "simple",
                                       "charts": [], "metrics": []})
check(r.status_code < 500, f"坏 id 不应 5xx（实际 HTTP {r.status_code}）")
print(f"        返回: {json_preview(r)}")

hr("B6 · 空文件/0 字节文件")
clear()
empty_path = os.path.join(ROOT, "temp", "empty.csv")
with open(empty_path, "wb") as f:
    pass
with open(empty_path, "rb") as f:
    r = client.post("/api/upload", files={"files": ("empty.csv", f.read(), "text/csv")})
check(r.status_code < 500, f"空文件上传不应 5xx（实际 HTTP {r.status_code}）")
print(f"        返回: {json_preview(r)}")

hr("B7 · 非法图表类型不应把整份报告拖死")
clear()
vid = upload("销售明细（带合并标题）.xlsx")
r = client.post("/api/generate", json={"dataset_ids": vid, "merge_mode": "merge",
                                       "title": "非法图型", "template": "simple",
                                       "charts": [{"type": "3d-bubble-totally-fake",
                                                   "x": "月份", "y": ["销售额"]}],
                                       "metrics": []})
check(r.status_code < 500, f"非法图表类型不应 5xx（实际 HTTP {r.status_code}）")
print(f"        返回: {json_preview(r)}")

# ---------------------------------------------------------------- C 全类型混合
hr("C · 全类型混合（表格 + 文档 + PDF + 图片 + 粘贴 一起）")
clear()
mixed: list[str] = []
for fn in ["销售明细（带合并标题）.xlsx", "季度经营分析.docx",
           "市场调研报告（带表格）.pdf", "竞品对比（图表截图）.png"]:
    mixed += upload(fn)
r = client.post("/api/paste", json={"text": "部门\t营收\n直营\t1280\n加盟\t960"})
pds = r.json().get("dataset")
if pds:
    mixed.append(pds["id"])
check(len(mixed) >= 4, f"混合导入拿到 {len(mixed)} 张表")

plan = client.post("/api/plan", json={"dataset_ids": mixed}).json()
check(bool(plan.get("group_names")), f"混合来源给出整合建议：{plan.get('suggestion')}")

g = generate({"dataset_ids": mixed, "merge_mode": "separate", "title": "全类型混合整合",
              "template": "full",
              "charts": [{"type": "bar", "x": "月份", "y": ["销售额"], "agg": "sum"}],
              "metrics": ["销售额"]})
check(bool(g.get("ok")), "混合来源（章节模式）能生成报告")
if g.get("ok"):
    body = open(os.path.join(g["dir"], "report.html"), encoding="utf-8").read()
    for kw in ["销售明细", "季度经营分析", "市场调研报告", "竞品对比"]:
        check(kw in body, f"报告包含来源「{kw}」")

# ---------------------------------------------------------------- D 导出（无图片场景）
hr("D · 「没有图片」场景：HTML/MD 落盘 + Word/PDF 导出")
clear()
sids = upload("销售明细（带合并标题）.xlsx")
g = generate({"dataset_ids": sids, "merge_mode": "merge", "title": "无图片·导出",
              "template": "full",
              "charts": [{"type": "bar", "x": "月份", "y": ["销售额"], "agg": "sum"},
                         {"type": "line", "x": "月份", "y": ["销售额", "订单量"],
                          "agg": "sum", "dual_axis": True},
                         {"type": "pie", "x": "月份", "y": ["销售额"], "agg": "sum"}],
              "metrics": ["销售额", "订单量", "毛利率"]})
check(bool(g.get("ok")), "生成基准报告（无图片）")
if g.get("ok"):
    for fname in ("report.html", "report.md"):
        p = os.path.join(g["dir"], fname)
        check(os.path.exists(p), f"{fname} 已落盘")
    for kind in ("docx", "pdf"):
        r = client.post("/api/export", json={"dir": g["dir"], "kind": kind})
        d = r.json() if r.status_code == 200 else {"ok": False}
        ok = bool(d.get("ok")) and d.get("path") and os.path.exists(d["path"])
        check(ok, f"导出 {kind.upper()} 成功 {('('+str(os.path.getsize(d['path']))+' 字节)') if ok else d}")

hr("D2 · 「降级报告」也能导出 Word/PDF（无图也不能崩）")
clear()
did2 = upload("会议纪要.md")
g = generate({"dataset_ids": did2, "merge_mode": "merge", "title": "降级导出",
              "template": "full", "charts": [], "metrics": []})
check(bool(g.get("ok")) and g.get("degraded"), "降级报告已生成")
if g.get("ok"):
    for kind in ("docx", "pdf"):
        r = client.post("/api/export", json={"dir": g["dir"], "kind": kind})
        d = r.json() if r.status_code == 200 else {"ok": False}
        ok = bool(d.get("ok")) and d.get("path") and os.path.exists(d["path"])
        check(ok, f"降级报告导出 {kind.upper()} 成功 {('('+str(os.path.getsize(d['path']))+' 字节)') if ok else d}")

clear()

# ---------------------------------------------------------------- 汇总
hr("结果")
if FAILS:
    print(f"✗ {len(FAILS)} 项未通过：")
    for m in FAILS:
        print("   -", m)
    sys.exit(1)
print("全格式兼容矩阵全部通过 ✅（含「没有图片」「没有表格」各场景）")
