"""端到端冒烟测试：不走浏览器，直接用 TestClient 打全部接口。

覆盖：多 sheet Excel / GBK CSV / docx / md / PDF / 图片 / 粘贴，
以及 自然语言解析 → 生成报告 → 导出 Word / PDF。
"""

from __future__ import annotations

import json
import os
import sys

from fastapi.testclient import TestClient

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)

import app as server  # noqa: E402
from _helpers import install_autoclean  # noqa: E402

SAMPLES = os.path.join(ROOT, "samples")
client = TestClient(server.app)
install_autoclean(client)   # 进程退出时清掉本次建的表，别留在用户会话里


def hr(title):
    print("\n" + "=" * 62)
    print(title)
    print("=" * 62)


def upload(name):
    path = os.path.join(SAMPLES, name)
    with open(path, "rb") as f:
        r = client.post("/api/upload",
                        files={"files": (name, f.read(),
                                         "application/octet-stream")})
    data = r.json()
    for res in data.get("results", []):
        if res.get("ok"):
            ds = res["dataset"]
            print(f"  OK  {ds['name']:<28} {ds['source_type']:<8} "
                  f"{ds['total_rows']:>4}行 {len(ds['columns']):>2}列 "
                  f"列={[c['name'] for c in ds['columns']][:5]}")
            for w in ds["warnings"]:
                print(f"       提示: {w}")
        else:
            print(f"  ERR {res.get('name')}: {res.get('error')}")
    return data


hr("1 · 各类型文件导入")

FILES = ["销售明细（带合并标题）.xlsx", "库存台账（GBK分号分隔）.csv",
         "季度经营分析.docx", "会议纪要.md",
         "市场调研报告（带表格）.pdf", "竞品对比（图表截图）.png"]

all_ids = []
for fn in FILES:
    print(f"\n[ {fn} ]")
    d = upload(fn)
    for res in d.get("results", []):
        if res.get("ok"):
            all_ids.append(res["dataset"]["id"])

print(f"\n共导入 {len(all_ids)} 张数据表")

hr("2 · 粘贴导入")
r = client.post("/api/paste", json={"text": "\n".join([
    "部门\t营收\t成本",
    "直营店\t1280\t820",
    "加盟店\t960\t700",
    "电商\t1450\t930",
])})
paste_ds = r.json().get("dataset")
print("  粘贴表:", paste_ds["name"], paste_ds["total_rows"], "行,",
      [c["name"] for c in paste_ds["columns"]])
paste_id = paste_ds["id"]

hr("3 · 表头行修正（原始文件里第 3 行才是真表头）")
sales_id = all_ids[0]
for hr_ in (1, 3, 5):
    r = client.post("/api/fix", json={"id": sales_id, "header_row": hr_})
    ds = r.json()["dataset"]
    print(f"  指定第 {hr_} 行 → 列名 {[c['name'] for c in ds['columns']]} "
          f"· {ds['total_rows']} 行")
r = client.post("/api/fix", json={"id": sales_id, "header_row": 3})
sales_ds = r.json()["dataset"]
print("  采用第 3 行，数值列:",
      [c["name"] for c in sales_ds["columns"] if c["dtype"] == "number"])

hr("4 · 自然语言需求解析")
for q in ["按月统计销售额画柱状图降序", "各仓库库存数量求和，画饼图",
          "不同月份的销售额和订单量画折线图"]:
    r = client.post("/api/parse_nl", json={"dataset_id": sales_id, "text": q})
    d = r.json()
    if d.get("ok"):
        c0 = d["spec"]["charts"][0]
        print(f"  「{q}」\n     → 类型={c0['type']} x={c0['x']} y={c0['y']} "
              f"agg={c0['agg']} sort={c0['sort_by']}/{c0['sort_order']}  [{d['source']}]")
    else:
        print(f"  「{q}」 → 失败：{d.get('error')}")

hr("5 · 多文件整合建议")
r = client.post("/api/plan", json={"dataset_ids": all_ids})
p = r.json()
print("  建议:", p["suggestion"])
for i, g in enumerate(p["group_names"]):
    print(f"   第{i + 1}组: {g}")

hr("6 · 生成 HTML 报告（合并模式）")
payload = {
    "dataset_ids": [i for i in all_ids[:1]],
    "merge_mode": "merge",
    "title": "华东大区 2025 年度销售分析",
    "template": "full",
    "charts": [
        {"type": "bar", "x": "月份", "y": ["销售额"], "agg": "sum",
         "sort_by": "销售额", "sort_order": "desc", "limit": None},
        {"type": "line", "x": "月份", "y": ["销售额", "订单量"], "agg": "sum",
         "sort_by": "__x__", "sort_order": "asc", "limit": None,
         "dual_axis": True},
        {"type": "pie", "x": "月份", "y": ["销售额"], "agg": "sum"},
    ],
    "metrics": ["销售额", "订单量", "毛利率"],
}
r = client.post("/api/generate", json=payload)
gen = r.json()
if gen.get("ok"):
    print("  报告:", gen["report_url"])
    print("  图表:", gen["charts"], "张 · 指标卡:", gen["metrics"], "个")
    print("  输出目录:", gen["dir"])
    print("  结论节选:", gen["summary"][:180].replace("\n", " "))
else:
    print("  生成失败:", gen)
    sys.exit(1)

html_path = os.path.join(gen["dir"], "report.html")
print("  HTML 大小:", os.path.getsize(html_path), "字节")
_body = open(html_path, encoding="utf-8").read()
_BODY_COUNT = _body.count("echarts.init(document.getElementById")
print(f"  ECharts 已内联进 HTML: {len(_body) > 900000} · 图表初始化 {_BODY_COUNT} 处")

hr("7 · 导出 Word / PDF")
for kind in ("docx", "pdf"):
    r = client.post("/api/export", json={"dir": gen["dir"], "kind": kind})
    d = r.json()
    if d.get("ok"):
        print(f"  {kind.upper():<5} 导出成功 {os.path.getsize(d['path']):>9} 字节  {d['path']}")
    else:
        print(f"  {kind.upper():<5} 导出失败：{d}")

hr("8 · 多文件合并生成（Excel + CSV）")
payload2 = {
    "dataset_ids": [paste_id],
    "merge_mode": "merge",
    "title": "部门营收概览",
    "template": "simple",
    "charts": [{"type": "bar", "x": "部门", "y": ["营收", "成本"], "agg": "sum",
                "sort_by": "营收", "sort_order": "desc"}],
    "metrics": ["营收", "成本"],
}
r = client.post("/api/generate", json=payload2)
g2 = r.json()
print("  精简模板生成:", g2.get("ok"), g2.get("report_url") or g2)

print("\n" + "=" * 62)
print("冒烟测试结束")
print("=" * 62)
