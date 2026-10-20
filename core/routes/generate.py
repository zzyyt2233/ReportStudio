"""报告生成路由：需求解析（/api/plan、/api/parse_nl）与出报告（/api/generate）。

三个路由共用一个前提：dataset_ids -> Dataset 列表，全部走 core.state 的
_resolve（内存没有就从 session_store 重载），所以这里不再直接碰 SESSION。
"""
from __future__ import annotations

import json
import os
import re
from datetime import datetime

from fastapi import APIRouter, HTTPException

from core.analyze import compute_metrics, numeric_columns, template_summary
from core.chartset import build_blocks, layout_columns
from core.llm import polish_summary
from core.merge import group_datasets, merge_group
from core.model import ChartSpec, ReportSpec
from core.render import theme as T
from core.render.html_report import render_report as render_html
from core.render.markdown import render_markdown
from core.render.static_chart import render_chart
from core.spec import parse as parse_spec
from core.state import (OUT_DIR, REPORTS, ROOT, VENDOR_ECHARTS, _LOCK,
                       _report_key, _resolve, _session_ids)
from core.topic import build_topic_chart

router = APIRouter(prefix="/api", tags=["generate"])

@router.post('/plan')
def plan(payload: dict):
    datasets = _resolve(payload.get("dataset_ids") or [])
    from core.merge import suggest_plan
    return suggest_plan(datasets)


@router.post('/parse_nl')
def parse_nl(payload: dict):
    datasets = _resolve([payload.get("dataset_id")])
    if not datasets:
        raise HTTPException(404, "数据表不存在")
    ds = datasets[0]
    spec, source = parse_spec((payload.get("text") or "").strip(), ds)
    if not spec.charts:
        return {"ok": False, "error": "没能理解这句话，请在下方手动选择"}
    return {"ok": True, "source": source, "spec": {
        "title": spec.title,
        "charts": [c.to_dict() for c in spec.charts],
        "metrics": spec.metrics,
    }}


@router.post('/generate')
def generate(payload: dict):
    """生成报告。

    同步 def：这里面有 pandas 聚合、matplotlib 出图、HTML/Markdown 渲染，
    实测一张多图报告要跑 2~8 秒。外加 polish_summary 会发一次 LLM 请求
    （配了模型才发，没配直接跳过）。写成 async def 时这些全部占着事件循环，
    期间 /api/status、静态资源、看板全都超时 —— 界面表现为「整个卡死」。

    改成同步 def 后由线程池执行，重活期间事件循环空着，
    轻量接口照常几十毫秒返回。
    """
    want_ids = payload.get("dataset_ids") or []
    datasets = _resolve(want_ids)
    if not datasets:
        # 两种「拿不到数据」要分开说。以前一律回「请先导入数据」，
        # 但还有第三种情况：表是在这个请求发出之后、跑到这里之前被别人删掉的
        # （界面上删表、另一个标签页删表、或者上一步刚生成的报告目录被清理）。
        # 这时让用户去「导入数据」是误导 —— 他明明导入过。
        alive = _session_ids()
        if want_ids and not any(i in alive for i in want_ids):
            missing = [i for i in want_ids if i not in alive]
            raise HTTPException(
                404, f"选中的 {len(missing)} 张数据表已经不在了"
                     f"（可能刚被删除）。请重新选择要用的数据表。")
        raise HTTPException(400, "请先导入数据")

    mode = payload.get("merge_mode") or "merge"
    if mode == "merge" and len(datasets) > 1:
        groups = group_datasets(datasets)
        datasets = [merge_group(g) for g in groups]

    # charts 里的元素必须是 dict，否则下面 c.items() 直接抛 AttributeError，
    # 界面上就变成「服务端出错（AttributeError）」—— 用户看到的应该是
    # 「配置不合法」，而不是一段内部异常。实测：charts:["柱状图"] 会走到这条路径。
    raw_charts = payload.get("charts") or []
    if not isinstance(raw_charts, list):
        raise HTTPException(400, "图表配置格式不对，应为数组")
    # 顺手滤掉非 dict：前端永远传 dict，这里只挡手工调用 / 旧版前端，
    # 滤掉后剩下的逻辑照旧降级成「数据摘要报告」，不会打断出报告。
    raw_charts = [c for c in raw_charts if isinstance(c, dict)]
    charts = [ChartSpec(**{k: v for k, v in c.items()
                           if k in ChartSpec.__dataclass_fields__})
              for c in raw_charts]
    charts = [c for c in charts if c.x and c.y]
    # 不在这里直接报错：没有可出图的配置时，下面会降级成「数据摘要报告」，
    # 保证「只有纯文本 / 只有无模型图片 / 表里没有数值列」这类来源也一定能拿到报告。
    requested_charts = len(charts)

    # 明细表排序：默认空串 = 保持原始顺序（不强制排序）。
    # 来自用户的「明细按销售额降序」这类要求在这里落地，四个渲染器都读它。
    detail_by = (payload.get("detail_sort_by") or "").strip()
    detail_order = (payload.get("detail_sort_order") or "asc").strip().lower()
    if detail_order not in ("asc", "desc"):
        detail_order = "asc"

    # 初版主题跟随工作台当前主题；报告单文件里仍可现场切换（两套 option 都随页下发）
    theme = T.norm(payload.get("theme"))

    spec = ReportSpec(
        title=(payload.get("title") or "数据分析报告").strip(),
        template=payload.get("template") or "full",
        dataset_ids=[d.id for d in datasets],
        charts=charts,
        metrics=payload.get("metrics") or [],
        chart_layout=layout_columns(payload.get("chart_layout")),
        detail_sort_by=detail_by,
        detail_sort_order=detail_order,
    )

    # 图块构造统一走 core/chartset，和看板共用同一套口径（含「多指标拆成多张图」）
    chart_blocks, pending = build_blocks(datasets, charts, theme=theme)
    # 文本类数据自动补「主题分布」图：聚类打标 → 各主题条数柱状图（走同一套 build_blocks）
    for ds in datasets:
        res = build_topic_chart(ds)
        if res:
            tb, tp = build_blocks([res[0]], [res[1]], theme=theme)
            chart_blocks += tb
            pending += tp
    agg_store = {it["id"]: it for it in pending}
    chart_images: dict[str, str] = {}

    # 没有任何可绘制的图时，不报错——降级出「数据摘要报告」（结论 + 指标 + 数据明细仍完整）
    degrade_note = ""
    if not chart_blocks:
        if requested_charts:
            degrade_note = ("按当前配置没有算出可绘制的数据（横轴或数值列可能对不上），"
                            "已改为生成「数据摘要报告」——关键数据与明细表仍然完整。")
        else:
            degrade_note = ("这份内容里没有可用于出图的横轴与数值列"
                            "（片段太少的短文本、没接模型时的扫描图片、或表里没有数值列），"
                            "已改为生成「数据摘要报告」——关键数据与明细表仍然完整。")

    metric_cards = []
    excl_summary = all(getattr(c, "exclude_summary", True) for c in charts)
    for ds in datasets:
        want = [m for m in spec.metrics
                if m in numeric_columns(ds)] or numeric_columns(ds)[:4]
        cards = compute_metrics(ds, want, excl_summary)
        if len(datasets) > 1:
            for c in cards:
                c["title"] = f"{c['title']}（{ds.name[:14]}）"
        metric_cards += cards

    summary_lines = []
    for ds in datasets:
        s = template_summary(ds, spec.charts, metric_cards)
        summary_lines.append(f"【{ds.name}】{s}" if len(datasets) > 1 else s)
    summary = "".join(summary_lines)
    polished = polish_summary(summary)
    summary = polished or summary
    if degrade_note:
        # 降级说明放在最前面，四种导出格式都会带出，用户一眼能看懂为什么没有图
        summary = degrade_note + summary

    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    safe = re.sub(r'[\\/:*?"<>|]+', "_", spec.title)[:40] or "report"
    # 测试模式下目录名固定：no500 一次跑要出十几份报告，名字都带秒级时间戳的话
    # 每次跑都新开一批目录，temp/ 会一直涨（实测积到 1.1GB）。固定名字后
    # 后续跑会直接覆盖同名目录，占用天然有界。正常出报告不受影响。
    if os.environ.get("RS_FUZZ_OUT"):
        out_dir = os.path.join(OUT_DIR, "fuzz")
    else:
        out_dir = os.path.join(OUT_DIR, f"{safe}_{stamp}")
    os.makedirs(out_dir, exist_ok=True)

    img_dir = os.path.join(out_dir, "charts")
    for cid, item in agg_store.items():
        p = render_chart(item["agg"], item["spec"],
                         os.path.join(img_dir, cid + ".png"))
        if p:
            chart_images[cid] = p

    html_path = render_html(datasets, spec, chart_blocks, metric_cards,
                            summary, out_dir, VENDOR_ECHARTS, theme=theme)
    md_path = render_markdown(datasets, spec, chart_blocks, metric_cards,
                              summary, out_dir)

    # 报告摘要落在报告目录里，供「历史报告」列表显示。
    # 为什么不留到读的时候现算：列表要显示「几张图 / 几个指标 / 多少行」，
    # 而这些东西在生成完之后只看目录是推不出来的（charts/ 里数 PNG 不等于图数，
    # 「多指标拆图」会把一张配置拆成多张）。生成时顺手写下来最省事也最准。
    # 读不到 meta.json 的老报告由 history() 兜底，不会因此消失。
    try:
        meta = {
            "title": spec.title,
            "charts": len(chart_blocks),
            "metrics": len(metric_cards),
            "degraded": bool(degrade_note),
            "created": datetime.now().strftime("%Y-%m-%d %H:%M"),
            "datasets": [{"name": d.name, "rows": d.total_rows,
                          "cols": len(d.columns)} for d in datasets],
        }
        with open(os.path.join(out_dir, "meta.json"), "w", encoding="utf-8") as f:
            json.dump(meta, f, ensure_ascii=False, indent=1)
    except Exception:
        pass          # 摘要写不进去不影响报告本身，列表退化成「只有文件名」

    rel = os.path.relpath(out_dir, ROOT).replace("\\", "/")
    with _LOCK:
        REPORTS[_report_key(out_dir)] = {"datasets": datasets, "spec": spec,
                                         "chart_blocks": chart_blocks,
                                         "metric_cards": metric_cards,
                                         "summary": summary,
                                         "chart_images": chart_images}
    return {
        "ok": True,
        "report_url": f"/{rel}/report.html",
        "md_url": f"/{rel}/report.md",
        "dir": os.path.normpath(out_dir),
        "charts": len(chart_blocks),
        "metrics": len(metric_cards),
        "degraded": bool(degrade_note),
        "note": degrade_note.strip(),
        "summary": summary[:400],
    }
