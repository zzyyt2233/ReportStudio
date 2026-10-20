"""把一个看板算成 PulseBoard 站点的只读报表快照。

看板图表规格存在 dashboards\\ 下，数据表在会话里（重启就没了），
所以这里支持两种取数：优先用会话里现成的表，取不到就用 --sql 现查一份。
默认连的是 samples\\demo_shop.db —— 那是仓库自带的示例库，
不是任何真实业务数据，发布到公网的就是这份。

用法：
    .venv\\Scripts\\python.exe tools\\export_site.py                       # 第一个看板 + 会话里的表
    .venv\\Scripts\\python.exe tools\\export_site.py f91e5b5d              # 指定看板
    .venv\\Scripts\\python.exe tools\\export_site.py f91e5b5d ^
        --sql "SELECT 省份,渠道,商品类别,月份,销售额,毛利率,订单量 FROM 销售明细"
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from core import analyze as an  # noqa: E402
from core import chartset as cs  # noqa: E402
from core import dashboard as dash  # noqa: E402
from core.db.connect import DBConfig  # noqa: E402
from core.db.query import run_to_dataset  # noqa: E402
from core.state import _get  # noqa: E402

# 站点工程与本仓库同级：E:\ReportStudio → E:\PulseBoard
DEFAULT_OUT = ROOT.parent / "PulseBoard" / "web" / "data" / "report.json"
DEFAULT_DB = ROOT / "samples" / "demo_shop.db"


def pick_dashboard(want: str | None):
    if want:
        cfg = dash.get(want)
        if not cfg:
            sys.exit(f"看板 {want} 不存在；先跑 tools\\list_dashboards.py 看看有哪些")
        return cfg
    for r in dash.list_all():
        cfg = dash.get(r["id"])
        if cfg and cfg.charts:
            return cfg
    sys.exit("一个看板都没有，先在界面上配一张图。")


def load_datasets(cfg, sql: str | None, db_path: Path, table_name: str):
    """先取会话里现成的表；没有就按 SQL 现查一份。"""
    ds_list, missing = dash.resolve_datasets(cfg, {d: _get(d) for d in cfg.dataset_ids})
    ds_list = list(ds_list or [])
    if ds_list and not sql:
        return ds_list, "会话数据表"
    if not sql:
        names = "、".join(cfg.dataset_ids) or "（空）"
        sys.exit(f"看板用到的数据表 {names} 不在当前会话里（服务重启会清空）。"
                 f"带上 --sql 从示例库现查，或在界面上重新取一次数。")
    if not db_path.exists():
        sys.exit(f"找不到示例库 {db_path}")
    dbc = DBConfig(kind="sqlite", path=str(db_path), name=db_path.stem)
    ds, _grid, meta = run_to_dataset(dbc, sql, name=table_name)
    print(f"取数 {meta['row_count']} 行 · {len(meta['columns'])} 列 · {meta['source']}")
    return [ds], f"{db_path.name}（示例库）"


def main() -> None:
    ap = argparse.ArgumentParser(description="导出 PulseBoard 报表快照")
    ap.add_argument("dashboard", nargs="?", help="看板 id，默认取第一个有图的")
    ap.add_argument("--sql", help="现场取数的 SELECT（只读），用于会话里没有表时")
    ap.add_argument("--db", type=Path, default=DEFAULT_DB, help=f"sqlite 路径，默认 {DEFAULT_DB}")
    ap.add_argument("--name", default="示例销售明细", help="取数结果命名，会显示在快照来源里")
    ap.add_argument("--out", type=Path, default=DEFAULT_OUT, help="输出 JSON 路径")
    ap.add_argument("--rows", type=int, default=50, help="明细表最多带多少行")
    a = ap.parse_args()

    cfg = pick_dashboard(a.dashboard)
    datasets, how = load_datasets(cfg, a.sql, a.db, a.name)

    specs = dash._to_specs(cfg)
    blocks, pending = cs.build_blocks(datasets, specs)
    if not blocks:
        sys.exit("这些图在当前数据表上一个列都对不上，导不出任何图。"
                 "检查 --sql 选的列名是否就是看板里配的横轴/纵轴。")

    charts = []
    for b, p in zip(blocks, pending):
        agg = p["agg"]
        if agg.get("scatter") or agg.get("empty"):
            continue
        charts.append({
            "title": b["title"],
            "type": p["spec"].type,
            "x": [str(c) for c in agg.get("categories") or []],
            "series": [{"name": s.get("name", ""), "data": s.get("data") or []}
                       for s in agg.get("series") or []],
            "unit": agg.get("unit") or "",
            "span": int(b.get("span") or 6),
            "height": int(b.get("height") or 320),
        })

    metrics = []
    for ds in datasets:
        want = [m for m in (cfg.metrics or []) if m in an.numeric_columns(ds)] \
            or an.numeric_columns(ds)[:4]
        for c in an.compute_metrics(ds, want, True):
            metrics.append({"label": c.get("title", ""), "value": str(c.get("value", "")),
                            "foot": c.get("unit", "") or c.get("detail", "")})

    ds0 = datasets[0]
    table = {
        "title": f"{ds0.name} 明细",
        "columns": [c.name for c in ds0.columns],
        "rows": [[r.get(c.name, "") for c in ds0.columns] for r in ds0.rows[:a.rows]],
    }

    payload = {
        "meta": {
            "generated_at": time.time(),
            "exported_by": "ReportStudio tools/export_site.py",
            "dashboard_id": cfg.id,
            "title": cfg.title or cfg.name,
            "subtitle": cfg.subtitle or "",
            "dataset": how,
            "row_count": len(ds0.rows),
        },
        "metrics": metrics[:8],
        "charts": charts,
        "table": table,
    }
    a.out.parent.mkdir(parents=True, exist_ok=True)
    a.out.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"看板「{cfg.title or cfg.name}」：{len(charts)} 张图、{len(metrics)} 张指标卡，"
          f"数据表 {len(datasets)} 份（{how}）")
    print(f"已写出 {a.out}（{a.out.stat().st_size // 1024} KB）")


if __name__ == "__main__":
    main()
