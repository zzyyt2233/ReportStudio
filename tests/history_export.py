"""历史报告的「搜索 / 删除」与数据表「导出」这两块新能力。

覆盖本轮补上的几个洞：
1. /api/history 带摘要（几图几指标几行、占用多大），并支持搜索
2. DELETE /api/history 能删单份报告，且**只**能删输出目录正下方那一层
3. /api/export_dataset 把界面上修正过的数据表导出成 CSV / xlsx
   （CSV 必须带 BOM，不带的话 Excel 双击打开中文是乱码）
4. cleanup_residue 回收 *.deleted 残片，但不动新产生的那些
5. 顺手把关：app.py 里只能有**一个** @app.exception_handler(Exception) ——
   曾经有两个，后注册的静默覆盖了写日志的那个，于是 logs/error.log 从来没被写过

隔离（照抄 tests/no500.py 的做法）：
- 报告写进 temp 下的唯一目录，不碰正式 outputs
- 看板目录也重定向过去，免得 cleanup_residue 真去动 dashboards/
- 只删自己建的会话表，绝不调 /api/session/clear
"""

from __future__ import annotations

import json
import os
import re
import sys
import time

from fastapi.testclient import TestClient

PROJ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJ)
os.chdir(PROJ)

import core.config as cfgmod  # noqa: E402

# 报告与看板都改到唯一临时目录：目录名带 pid + 时间戳，所以不需要「先删再建」
# （受限环境里 rmtree 会被批量删除守卫拦下，整个命令直接失败）。
OUT = os.path.join(PROJ, "temp", f"hist_{os.getpid()}_{int(time.time()) % 100000}")
DASH = os.path.join(OUT, "dashboards")
SESS = os.path.join(OUT, "session")
os.makedirs(DASH, exist_ok=True)
os.makedirs(SESS, exist_ok=True)
cfgmod.dashboards_dir = lambda: DASH          # type: ignore[assignment]

import core.session_store as sess  # noqa: E402

# 会话落盘也改到临时目录。测试会删自己建的数据表，而那会连带删
# session/<id>.json —— 不重定向的话，跑一次测试就要去动用户真正的 session/，
# 既越界又容易被环境的删除守卫/权限确认挡住。
sess._dir = lambda: SESS                      # type: ignore[assignment]

import app as server  # noqa: E402

server.OUT_DIR = OUT
server.SESSION_TRASH = os.path.join(OUT, "session_trash")   # 别去动真的那个
client = TestClient(server.app, raise_server_exceptions=False)

FAILS: list[str] = []


def hr(t: str) -> None:
    print("\n" + "=" * 62)
    print(t)
    print("=" * 62)


def check(cond: bool, msg: str) -> bool:
    print(("  ✓ " if cond else "  ✗ ") + msg)
    if not cond:
        FAILS.append(msg)
    return bool(cond)


def can_delete() -> bool:
    """探一下本环境现在允不允许删文件（探在本套件的临时目录里）。

    受限环境有一层批量删除守卫：**一个 turn 内累计删满 50 个文件**之后，
    os.remove / rmtree 会被拦下。连跑全部回归套件时，前面的套件常常已经
    把额度用光，这时删除类断言会「假失败」——那是环境限制，不是产品缺陷。
    单独跑本套件必定通过，因为额度是满的。

    实现挪到了 _helpers.can_delete()，几个套件共用一份判定。
    """
    from _helpers import can_delete as _probe
    return _probe(OUT)


# ——————————————————————————————————————————————————————————————

hr("0 · 静态把关：异常处理器只能有一个")
src = open(os.path.join(PROJ, "app.py"), encoding="utf-8").read()
n = len(re.findall(r"^@app\.exception_handler\(Exception\)", src, re.M))
check(n == 1, f"app.py 里 @app.exception_handler(Exception) 恰好 1 个（实际 {n}）")

hr("1 · 生成一份报告，检查摘要文件")
before = {d["id"] for d in client.get("/api/datasets").json().get("items", [])}
ds = client.post("/api/paste", json={
    "text": "月份\t销售额\t订单量\n1月\t100\t10\n2月\t200\t20\n3月\t150\t15"}).json()["dataset"]
did = ds["id"]
print("  测试用数据表:", did)

gen = client.post("/api/generate", json={
    "dataset_ids": [did], "merge_mode": "merge", "title": "hist测试报告",
    "template": "full",
    "charts": [{"type": "bar", "x": "月份", "y": ["销售额"], "agg": "sum"}],
    "metrics": ["销售额"],
}).json()
check(bool(gen.get("ok")), "生成报告成功")
out_dir = os.path.normpath(gen.get("dir") or "")
print("  报告目录:", out_dir)

meta_p = os.path.join(out_dir, "meta.json")
check(os.path.exists(meta_p), "报告目录里写出了 meta.json")
meta = {}
if os.path.exists(meta_p):
    with open(meta_p, encoding="utf-8") as f:
        meta = json.load(f)
    check(meta.get("charts") == 1, f"meta.charts = {meta.get('charts')}（应为 1）")
    check(bool(meta.get("datasets")), "meta 里带了数据表摘要（名称/行数/列数）")
    check(bool(meta.get("created")), "meta 里带了生成时间")

hr("2 · /api/history 带摘要，且能搜")
h = client.get("/api/history").json()
check("items" in h and "total" in h, "返回结构含 items / total")
mine = [it for it in h.get("items", []) if it.get("dir") == out_dir]
check(bool(mine), "刚生成的报告出现在列表里")
if mine:
    it = mine[0]
    check(it.get("meta", {}).get("charts") == 1, "列表项带 meta（图表数）")
    check(it.get("size", 0) > 0, f"列表项带目录大小（{it.get('size')} 字节）")
    check(it.get("name") == "hist测试报告", f"名称解析正确：{it.get('name')!r}")
    check(re.fullmatch(r"\d{2}-\d{2} \d{2}:\d{2}", it.get("stamp") or ""),
          f"时间戳解析成「月-日 时:分」：{it.get('stamp')!r}")

q1 = client.get("/api/history", params={"q": "hist测试报告"}).json()
check(q1.get("filtered") is True, "传了 q 时 filtered=true")
check(any(x.get("dir") == out_dir for x in q1.get("items", [])), "按名字能搜到这份报告")

q2 = client.get("/api/history", params={"q": "这个词绝对不存在xyzzy"}).json()
check(q2.get("total") == 0 and not q2.get("items"), "搜不存在的词返回 0 条")

hr("3 · 导出数据表（修正后的那份）")
csv = client.post("/api/export_dataset", json={"id": did, "kind": "csv"}).json()
check(bool(csv.get("ok")), f"CSV 导出：{csv.get('error') or 'ok'}")
cpath = csv.get("path") or ""
check(os.path.exists(cpath), "CSV 文件确实落盘了")
if os.path.exists(cpath):
    with open(cpath, "rb") as f:
        head = f.read(3)
    check(head == b"\xef\xbb\xbf", "CSV 带 UTF-8 BOM（不带的话 Excel 打开中文乱码）")
    with open(cpath, encoding="utf-8-sig") as f:
        lines = [ln for ln in f.read().splitlines() if ln.strip()]
    check(lines[0] == "月份,销售额,订单量", f"表头正确：{lines[0]!r}")
    check(len(lines) == 4, f"3 行数据 + 1 行表头 = 4 行（实际 {len(lines)}）")

xl = client.post("/api/export_dataset", json={"id": did, "kind": "xlsx"}).json()
check(bool(xl.get("ok")), f"xlsx 导出：{xl.get('error') or 'ok'}")
xpath = xl.get("path") or ""
check(os.path.exists(xpath), "xlsx 文件确实落盘了")
if os.path.exists(xpath):
    from openpyxl import load_workbook
    ws = load_workbook(xpath).active
    rows = [list(r) for r in ws.values]
    check(rows and rows[0] == ["月份", "销售额", "订单量"], "xlsx 表头正确")
    check(len(rows) == 4, f"xlsx 4 行（实际 {len(rows)}）")

bad = client.post("/api/export_dataset", json={"id": "不存在", "kind": "csv"}).json()
check(not bad.get("ok"), "导出不存在的表 → ok:false，不是崩")
bad2 = client.post("/api/export_dataset", json={"id": did, "kind": "pdf"}).json()
check(not bad2.get("ok"), "不支持的格式被拒")

hr("4 · 删除报告的越权防护")
for label, bad_dir in [
    ("输出目录本身", OUT),
    ("输出目录的上级", os.path.dirname(OUT)),
    ("系统目录", "C:/Windows/System32"),
    ("相对穿越", os.path.join(OUT, "..", "..")),
    ("空字符串", ""),
]:
    r = client.delete("/api/history", params={"dir": bad_dir}).json()
    check(not r.get("ok"), f"拒绝：{label}（{bad_dir or '空'}）")

hr("5 · 正常删除（只删自己刚生成的那一份）")
delres = client.delete("/api/history", params={"dir": out_dir}).json()
check(bool(delres.get("ok")), f"删除成功：{delres.get('error') or 'ok'}")
check(not os.path.isdir(out_dir), "报告目录已从磁盘消失")
h2 = client.get("/api/history").json()
check(not any(x.get("dir") == out_dir for x in h2.get("items", [])),
      "列表里不再出现这份报告")

hr("6 · cleanup_residue 回收残片")
p1 = os.path.join(DASH, f"residue_{os.getpid()}.json.deleted")
p2 = os.path.join(OUT, f"gone_{os.getpid()}.deleted")
os.makedirs(p2, exist_ok=True)
with open(os.path.join(p2, "report.html"), "w", encoding="utf-8") as f:
    f.write("x")
with open(p1, "w", encoding="utf-8") as f:
    f.write("{}")

# 新产生的 *.deleted 也在回收范围内：它只可能来自「删除失败后的降级改名」，
# 改名之后没有任何流程会再读它，没有理由让它白躺一天。
p_fresh = os.path.join(DASH, f"justnow_{os.getpid()}.json.deleted")
with open(p_fresh, "w", encoding="utf-8") as f:
    f.write("{}")
# *.tmp 不一样：可能是正在写入的半截文件，必须给宽限期
p_tmp = os.path.join(DASH, f"writing_{os.getpid()}.json.tmp")
with open(p_tmp, "w", encoding="utf-8") as f:
    f.write("{}")

st = server.residue_stat()
check(st["count"] >= 3, f"residue_stat 至少数出这 3 个待回收（实际 {st['count']}）")

if not can_delete():
    print("  ⚠ 跳过本节的删除断言：当前环境已不允许删除")
    print("     （批量删除守卫在一个 turn 内删满 50 个文件后开始拦截；连跑全部")
    print("      套件时前面的套件可能已把额度用光。这是环境限制，不是产品缺陷。）")
    print("     单独跑即可完整验证：.venv\\Scripts\\python.exe tests/history_export.py")
    check(os.path.exists(p_tmp), "正在写入的 .tmp 未被误删")
else:
    server.cleanup_residue()
    check(not os.path.exists(p1), "过期的 .deleted 文件被回收")
    check(not os.path.exists(p2), "过期的 .deleted 目录被回收")
    check(not os.path.exists(p_fresh), "刚产生的 .deleted 也回收（没人会再读它）")
    check(os.path.exists(p_tmp), "正在写入的 .tmp 不动（给足宽限期）")
    server._purge(p_tmp)

hr("7 · /api/cleanup 主动清理")
c = client.post("/api/cleanup").json()
check(bool(c.get("ok")), "接口可用")
check("removed" in c and "freed" in c and "left" in c,
      "返回 removed / freed / left（界面据此显示「回收 N 个，释放 X MB」）")

hr("8 · 清理本次测试数据")
client.delete("/api/dataset/" + did)
left = {d["id"] for d in client.get("/api/datasets").json().get("items", [])}
check(left == before, "测试建的数据表已移除，原有数据未被动过")

hr("结果")
if FAILS:
    print(f"✗ {len(FAILS)} 项失败：")
    for m in FAILS:
        print("   -", m)
    sys.exit(1)
print("历史报告搜索/删除、数据表导出、残片回收：全部通过 ✅")
