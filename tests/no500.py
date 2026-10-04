"""接口 fuzz：用各种「奇怪但用户真会配出来」的参数把每个接口打一遍。

目标只有一个：**任何输入都不该让接口 5xx**。一旦 5xx，前端只会甩一句
「生成出错」，用户完全看不出原因。这里是那道底线。

- TestClient 用 raise_server_exceptions=False，后端真崩了会被当成 500 观察到。
- 报告输出被重定向到 temp/fuzz，固定目录名，不污染正式 outputs。
- 组合用抽样而非全笛卡尔积，否则几百次渲染会把测试拖死。
"""

from __future__ import annotations

import os
import shutil
import sys
import time

from fastapi.testclient import TestClient

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))   # 拿得到 _helpers
sys.path.insert(0, ROOT)
os.chdir(ROOT)

# 报告写到临时目录，别把正式 outputs 塞满。
# 目录名固定（fuzz），配合 app.py 里的 RS_FUZZ_OUT 开关，出报告时也用固定
# 子目录名 —— 否则报告目录名带秒级时间戳，每次跑都新开一批，temp/ 会一直涨。
# 之前这里既没清也没固定，temp/ 下积了 1300 多个这样的目录、总量 1.1GB。
#
# 为什么不用 rmtree 删整个目录：受限环境按「一个 turn 累计删除数」拦，
# 一次跑要出的报告有十几份，删不过来。固定目录名让占用天然有界，
# 比事后清理更可靠。
os.environ["RS_FUZZ_OUT"] = "1"

import app as server  # noqa: E402
from _helpers import can_delete  # noqa: E402

FUZZ_OUT = os.path.join(ROOT, "temp", "fuzz")
os.makedirs(FUZZ_OUT, exist_ok=True)
server.OUT_DIR = FUZZ_OUT

# 跑之前先把上一轮留下的清空。不做也没关系 —— 目录名固定，占用天然有界，
# 只是会多占一点盘。真正的问题是「硬试删除」：受限环境会弹拦截请求，
# 额度用尽时每跑一次就弹一次，很烦。所以先用 can_delete() 探一下，
# 额度还在才清，探不到就直接跳过。
_stale = 0
if can_delete(FUZZ_OUT):
    for _f in os.listdir(FUZZ_OUT):
        _p = os.path.join(FUZZ_OUT, _f)
        try:
            if os.path.isdir(_p):
                shutil.rmtree(_p, ignore_errors=True)
            else:
                os.remove(_p)
        except BaseException:      # noqa: BLE001 受删除额度限制就留着
            _stale += 1
    if _stale:
        print(f"[提示] temp/fuzz 里有 {_stale} 项没能删掉，不影响本轮")
else:
    print("[提示] 当前环境已到删除额度上限，跳过 fuzz 目录清理（不影响测试结果）")

client = TestClient(server.app, raise_server_exceptions=False)
SERVER_ERRORS: list[str] = []


def hr(t: str) -> None:
    print("\n" + "=" * 62)
    print(t)
    print("=" * 62)


def check(cond: bool, msg: str) -> None:
    print(("  ✓ " if cond else "  ✗ ") + msg)
    if not cond:
        raise AssertionError(msg)


def ok(r, label: str) -> None:
    if r.status_code >= 500:
        SERVER_ERRORS.append(f"{label} → HTTP {r.status_code} {(r.text or '')[:200]}")
        print(f"  ✗ {label} → HTTP {r.status_code}  {(r.text or '')[:240]}")


_BASELINE: set[str] | None = None


def clear() -> None:
    """只删本次测试自己建的表。

    不能用 /api/session/clear —— 那会把用户在界面上存着的表一起删掉，
    跑一次 fuzz 就丢一次数据。第一次调用时把用户原有的表记为基线，永不删除。
    """
    global _BASELINE
    try:
        now = {d["id"] for d in client.get("/api/datasets").json().get("items", [])}
    except Exception:
        return
    if _BASELINE is None:
        _BASELINE = now
    for did in now - _BASELINE:
        try:
            client.delete(f"/api/dataset/{did}")
        except Exception:
            pass


def paste(text: str) -> str:
    return client.post("/api/paste", json={"text": text}).json()["dataset"]["id"]


hr("准备：几张不同形态的表")
clear()
D_TEXT = paste("部门\t备注\n销售部\t很好\n市场部\t一般")               # 有文本无数值
D_NUM = paste("数值\t另一个\n1\t2\n3\t4")                              # 全数值
D_MIX = paste("月份\t销售额\t订单量\t毛利率\n1月\t100\t10\t0.31\n2月\t200\t20\t0.42\n3月\t150\t15\t0.28")
D_ONE = paste("公司\t营收\n甲公司\t999")                               # 只有 1 行
D_MANY_ID = paste("类别\t值\n" + "\n".join(f"类{i}\t{i}" for i in range(200)))  # 200 个类别
print("  表:", D_TEXT, D_NUM, D_MIX, D_ONE, D_MANY_ID)

hr("1 · 图表类型 × 聚合方式 × 单双轴（抽样）")
CTYPES = ["bar", "line", "pie", "scatter", "stack_bar", "combo", "", "table", "3d-pie", "BAR", None]
AGGS = ["sum", "mean", "count", "max", "", "median", None]
n1 = 0
for i, ctype in enumerate(CTYPES):
    agg = AGGS[i % len(AGGS)]
    dual = (i % 2 == 0)
    payload = {"dataset_ids": [D_MIX], "merge_mode": "merge", "title": "fuzz",
               "template": "simple",
               "charts": [{"type": ctype, "x": "月份", "y": ["销售额", "订单量"],
                           "agg": agg, "dual_axis": dual, "limit": None}],
               "metrics": []}
    try:
        ok(client.post("/api/generate", json=payload), f"type={ctype!r} agg={agg!r} dual={dual}")
    except Exception as e:
        SERVER_ERRORS.append(f"type={ctype!r} 抛异常 {type(e).__name__}: {e}")
        print(f"  ✗ type={ctype!r} 抛异常 {type(e).__name__}: {e}")
    n1 += 1
# 再补几个特定的双轴/多系列组合
for agg, dual in [("sum", True), ("mean", True), ("count", False), ("sum", False)]:
    ok(client.post("/api/generate", json={
        "dataset_ids": [D_MIX], "merge_mode": "merge", "title": "fuzz", "template": "full",
        "charts": [{"type": "combo", "x": "月份", "y": ["销售额", "订单量", "毛利率"],
                    "agg": agg, "dual_axis": dual}], "metrics": []}), f"combo agg={agg} dual={dual}")
    n1 += 1
check(not SERVER_ERRORS, f"图表类型/聚合/双轴 {n1} 种组合无 5xx")

hr("2 · 排序 / 截断 / 模板（抽样）")
n2 = 0
for sort_by, limit, tpl in [
        ("__x__", None, "full"), ("", 0, "simple"), ("值", 1, "full"),
        ("不存在的列", 5, "simple"), (None, -1, ""), ("__x__", 99999, None),
        ("值", 1, "weird-template"), ("", None, "full"), ("__x__", 3, "simple"),
        ("不存在的列", 0, ""),
]:
    ok(client.post("/api/generate", json={
        "dataset_ids": [D_MANY_ID], "merge_mode": "merge", "title": "fuzz", "template": tpl,
        "charts": [{"type": "bar", "x": "类别", "y": ["值"], "agg": "sum",
                    "sort_by": sort_by, "sort_order": "desc", "limit": limit}],
        "metrics": []}), f"sort_by={sort_by!r} limit={limit} tpl={tpl!r}")
    n2 += 1
# 升序 + 非法排序方向
ok(client.post("/api/generate", json={
    "dataset_ids": [D_MANY_ID], "merge_mode": "merge", "title": "fuzz", "template": "full",
    "charts": [{"type": "bar", "x": "类别", "y": ["值"], "agg": "sum",
                "sort_by": "值", "sort_order": "侧面"}], "metrics": []}), "非法排序方向")
n2 += 1
check(not SERVER_ERRORS, f"排序/截断/模板 {n2} 种组合无 5xx")

hr("3 · 轴与列的各种错配")
CASES = [
    ("x 数值 / y 数值", {"x": "数值", "y": ["另一个"]}, D_NUM),
    ("x 文本 / y 文本", {"x": "部门", "y": ["备注"]}, D_TEXT),
    ("x 与 y 同列", {"x": "数值", "y": ["数值"]}, D_NUM),
    ("y 不存在", {"x": "月份", "y": ["没有这列"]}, D_MIX),
    ("x 不存在", {"x": "没有这列", "y": ["销售额"]}, D_MIX),
    ("x 为空", {"x": "", "y": ["销售额"]}, D_MIX),
    ("y 为空列表", {"x": "月份", "y": []}, D_MIX),
    ("只有 1 行", {"x": "公司", "y": ["营收"]}, D_ONE),
    ("200 个类别", {"x": "类别", "y": ["值"]}, D_MANY_ID),
    ("全文本表", {"x": "部门", "y": ["备注"]}, D_TEXT),
]
for label, xy, dsid in CASES:
    ok(client.post("/api/generate", json={
        "dataset_ids": [dsid], "merge_mode": "merge", "title": "fuzz", "template": "full",
        "charts": [{"type": "bar", **xy, "agg": "sum"}], "metrics": []}), label)
check(not SERVER_ERRORS, f"轴列错配 {len(CASES)} 种无 5xx")

hr("4 · 指标卡的各种取值")
for metrics in [["销售额"], ["备注"], ["不存在的列"], [], None,
                ["销售额", "订单量", "毛利率", "备注", "不存在"]]:
    ok(client.post("/api/generate", json={
        "dataset_ids": [D_MIX], "merge_mode": "merge", "title": "fuzz", "template": "full",
        "charts": [{"type": "bar", "x": "月份", "y": ["销售额"]}],
        "metrics": metrics}), f"metrics={metrics!r}")
check(not SERVER_ERRORS, "指标卡 6 种取值无 5xx")

hr("5 · 生成接口的畸形载荷")
for payload in [
    {},
    {"dataset_ids": None},
    {"dataset_ids": []},
    {"dataset_ids": ["不存在"]},
    {"dataset_ids": [D_MIX], "charts": None},
    {"dataset_ids": [D_MIX], "charts": [{}]},
    {"dataset_ids": [D_MIX], "charts": [{"x": "月份"}]},
    {"dataset_ids": [D_MIX], "charts": "不是列表"},
    {"dataset_ids": [D_MIX], "merge_mode": "乱写", "charts": [{"type": "bar", "x": "月份", "y": ["销售额"]}]},
    {"dataset_ids": [D_MIX], "title": "x" * 500, "charts": [{"type": "bar", "x": "月份", "y": ["销售额"]}]},
    {"dataset_ids": [D_MIX], "charts": [{"type": "bar", "x": "<script>", "y": ["销售额"]}]},
    {"dataset_ids": [D_MIX], "charts": [{"type": "bar", "x": "月份", "y": ["销售额"]}], "template": None},
]:
    ok(client.post("/api/generate", json=payload), f"payload 键={sorted(k for k in payload)}")
check(not SERVER_ERRORS, "12 种畸形载荷无 5xx")

hr("6 · 其它接口")
for label, path, payload in [
    ("/fix 越界行号", "/api/fix", {"id": D_MIX, "header_row": 999}),
    ("/fix 负数行号", "/api/fix", {"id": D_MIX, "header_row": -5}),
    ("/fix 坏 id", "/api/fix", {"id": "不存在", "header_row": 1}),
    ("/fix 空载荷", "/api/fix", {}),
    ("/plan 空", "/api/plan", {"dataset_ids": []}),
    ("/plan 坏 id", "/api/plan", {"dataset_ids": ["无"]}),
    ("/plan 单表", "/api/plan", {"dataset_ids": [D_MIX]}),
    ("/plan 空载荷", "/api/plan", {}),
    ("/parse_nl 空文本", "/api/parse_nl", {"dataset_id": D_MIX, "text": ""}),
    ("/parse_nl 泛泛", "/api/parse_nl", {"dataset_id": D_MIX, "text": "画个图"}),
    ("/parse_nl 坏 id", "/api/parse_nl", {"dataset_id": "无", "text": "按月画柱状图"}),
    ("/parse_nl 空载荷", "/api/parse_nl", {}),
    ("/export 坏目录", "/api/export", {"dir": "E:/不存在", "kind": "docx"}),
    ("/export 空载荷", "/api/export", {}),
    ("/export 不支持格式", "/api/export", {"dir": "x", "kind": "xlsx"}),
    ("/paste 空载荷", "/api/paste", {}),
    ("/paste 空文本", "/api/paste", {"text": ""}),
    ("/paste 控制字符", "/api/paste", {"text": "a\tb\n<scri\x00pt>\t1"}),
    ("/paste 超长单行", "/api/paste", {"text": "a\tb\n" + "x" * 50000 + "\t1"}),
]:
    ok(client.post(path, json=payload), label)
check(not SERVER_ERRORS, "其它接口 19 种异常输入无 5xx")

hr("7 · 上传的畸形文件")
tmp = os.path.join(ROOT, "temp")
os.makedirs(tmp, exist_ok=True)
for fname, content in [
    ("bad.xlsx", b"this is not a real xlsx at all"),
    ("bad.pdf", b"%PDF-1.4 broken"),
    ("bad.docx", b"PK\x03\x04 broken docx"),
    ("noext", b"plain bytes"),
    ("empty.xlsx", b""),
]:
    p = os.path.join(tmp, fname)
    with open(p, "wb") as f:
        f.write(content)
    with open(p, "rb") as f:
        r = client.post("/api/upload",
                        files={"files": (fname, f.read(), "application/octet-stream")})
    ok(r, f"上传坏文件 {fname}")
check(not SERVER_ERRORS, "5 种坏文件无 5xx（应降级为「解析出错」提示）")

clear()

hr("结果")
if SERVER_ERRORS:
    print(f"✗ {len(SERVER_ERRORS)} 处 5xx：")
    for m in SERVER_ERRORS:
        print("   -", m)
    sys.exit(1)
print("接口 fuzz 全部通过 ✅：任何输入都不 5xx")
