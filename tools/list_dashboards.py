"""临时：列出 ReportStudio 里可导出的看板，以及每个看板用到的数据表与来源。

导出站点快照前先看这份清单，确认要发布的是示例数据而不是真实业务取数结果。
用法：.venv\\Scripts\\python.exe tools\\list_dashboards.py
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core import dashboard as dash  # noqa: E402
from core.state import _get  # noqa: E402

rows = dash.list_all()
print(f"看板 {len(rows)} 个")
for r in rows:
    cfg = dash.get(r["id"])
    if not cfg:
        continue
    ds_names = []
    for did in cfg.dataset_ids:
        it = _get(did)
        if not it:
            ds_names.append(f"{did}(会话里已不存在)")
            continue
        ds = it["dataset"]
        src = getattr(ds, "source", "") or getattr(ds, "file", "") or getattr(ds, "path", "")
        ds_names.append(f"{ds.name}｜{os.path.basename(str(src)) or '无来源'}｜{len(ds.rows or [])} 行")
    print(f"\n#{cfg.id}  {cfg.title or cfg.name}")
    print(f"   副标题：{cfg.subtitle or '（无）'}")
    print(f"   图表 {len(cfg.charts)} 张，指标列 {cfg.metrics or '自动'}")
    for n in ds_names:
        print(f"   数据表：{n}")
