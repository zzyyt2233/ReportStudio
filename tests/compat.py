"""兼容性回归：危险列名 / 多表分章节 / 图片降级，四种格式都要稳住。"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fastapi.testclient import TestClient
import app as server, re, base64
from _helpers import install_autoclean

c = TestClient(server.app)
install_autoclean(c)   # 进程退出时清掉本次建的表，别留在用户会话里

print("=" * 60)
print("场景 A：危险列名（含 <script> | & 引号）— 四种格式必须不崩")
evil = ("部门<script>alert(1)</script>\t营收&成本\t备\"注\n"
        "A&B\t100\t50\nX<Y\t200\t80\nZ|W\t150\t60\n")
r = c.post("/api/paste", json={"text": evil, "name": "危险<列>"})
ds = r.json()["dataset"]
sid = ds["id"]
print("  解析列名:", [x["name"] for x in ds["columns"]], "| 行:", ds["total_rows"])

g = c.post("/api/generate", json={
    "dataset_ids": [sid], "merge_mode": "merge", "title": "转义<b>测试</b>",
    "template": "full",
    "charts": [{"type": "bar", "x": ds["columns"][0]["name"],
                "y": [ds["columns"][1]["name"]], "agg": "sum"}],
    "metrics": [ds["columns"][1]["name"]]}).json()
print("  生成:", g.get("ok"))
d = g["dir"]
html = open(os.path.join(d, "report.html"), encoding="utf-8").read()
md = open(os.path.join(d, "report.md"), encoding="utf-8").read()
print("  HTML 含未转义 <script>alert:", "<script>alert" in html,
      "| 标题转义 &lt;b&gt;:", "&lt;b&gt;" in html)
# Markdown 明细表里 col2 含 & 应写成 &amp;，col3 含 " 应存活（不破坏表格）
print("  MD 列名转义正确:", "&amp;" in md and "部门\\<script\\>" not in md)
for k in ("docx", "pdf"):
    e = c.post("/api/export", json={"dir": d, "kind": k}).json()
    print(f"  {k} 导出:", e.get("ok"), os.path.getsize(e["path"]) if e.get("ok") else e)

print("=" * 60)
print("场景 B：多张异构表 separate — 四种格式都应含全部表")
ids = []
names = []
for fn in ["销售明细（带合并标题）.xlsx", "库存台账（GBK分号分隔）.csv", "季度经营分析.docx"]:
    with open("samples/" + fn, "rb") as f:
        up = c.post("/api/upload", files={"files": (fn, f.read())}).json()
    for x in up["results"]:
        if x.get("ok"):
            ids.append(x["dataset"]["id"])
            names.append(x["dataset"]["name"])
print("  实际导入表数:", len(ids))
g2 = c.post("/api/generate", json={
    "dataset_ids": ids, "merge_mode": "separate", "title": "异构分章节",
    "template": "full",
    "charts": [{"type": "bar", "x": "月份", "y": ["销售额"], "agg": "sum",
                "sort_by": "__x__", "sort_order": "asc"}],
    "metrics": []}).json()
print("  生成:", g2.get("ok"))
d2 = g2["dir"]
h2 = open(os.path.join(d2, "report.html"), encoding="utf-8").read()
m2 = open(os.path.join(d2, "report.md"), encoding="utf-8").read()
short = [n[:4] for n in names]
print("  HTML 含全部表名:", all(s in h2 for s in short),
      "| 缺席:", [s for s in short if s not in h2])
print("  MD 小节数(###):", m2.count("\n### "), "| MD 含全部表名:", all(s in m2 for s in short))
for k in ("docx", "pdf"):
    e = c.post("/api/export", json={"dir": d2, "kind": k}).json()
    print(f"  {k} 导出:", e.get("ok"))

print("=" * 60)
print("场景 C：图片无模型 + 正常 Excel — 降级不阻塞，Excel 照常出图")
png = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg==")
open("temp/_t.png", "wb").write(png)
with open("temp/_t.png", "rb") as f:
    up = c.post("/api/upload", files={"files": ("截图.png", f.read())}).json()
img_ds = [x["dataset"] for x in up["results"] if x.get("ok")][0]
print("  图片导入:", img_ds["name"], "| 仅警告无数据:", img_ds["total_rows"] == 0)
# 用 Excel（第一个 sheet）+ 图片，图表用 Excel 的 月份/销售额
g3 = c.post("/api/generate", json={
    "dataset_ids": ids[:1] + [img_ds["id"]], "merge_mode": "separate",
    "title": "含图片的混合报告", "template": "full",
    "charts": [{"type": "bar", "x": "月份", "y": ["销售额"], "agg": "sum",
                "sort_by": "__x__", "sort_order": "asc"}],
    "metrics": ["销售额"]}).json()
print("  混合生成:", g3.get("ok"), "| 图:", g3.get("charts"),
      "| 明细含图片警告:", g3.get("ok") and "识别图片" in open(
          os.path.join(g3["dir"], "report.md"), encoding="utf-8").read())
print("\n全部场景跑完，无崩溃")
