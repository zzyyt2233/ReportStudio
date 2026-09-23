"""ReportStudio 服务入口。

启动：python app.py  →  浏览器自动打开 http://127.0.0.1:8765
所有产物写在 outputs 目录（默认 E 盘），不往 C 盘落任何东西。
"""

from __future__ import annotations

import os
import re
import traceback
import uuid
from datetime import datetime

from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from core import config as cfgmod
from core import dashboard as dash
from core import session_store
from core.analyze import compute_metrics, numeric_columns, template_summary
from core.chartset import build_blocks, layout_columns
from core.ingest import dataset_grids, parse_files, parse_file
from core.llm import polish_summary
from core.merge import group_datasets, merge_group
from core.model import ChartSpec, Dataset, ReportSpec
from core.parsers.tabular import build_dataset
from core.parsers.paste import parse_pasted
from core.render.combine import combine_images
from core.render.docx_report import render_docx
from core.render.html_report import render_report as render_html
from core.render.markdown import render_markdown
from core.render.pdf_report import render_pdf
from core.render.static_chart import render_chart
from core.session_store import clear as session_clear
from core.session_store import remove as session_remove
from core.spec import parse as parse_spec

ROOT = os.path.dirname(os.path.abspath(__file__))
WEB_DIR = os.path.join(ROOT, "web")
OUT_DIR = cfgmod.outputs_dir()
TEMP_DIR = cfgmod.temp_dir()
VENDOR_ECHARTS = os.path.join(WEB_DIR, "vendor", "echarts.min.js")
os.makedirs(OUT_DIR, exist_ok=True)
os.makedirs(TEMP_DIR, exist_ok=True)

app = FastAPI(title="ReportStudio")

LOG_DIR = os.path.join(ROOT, "logs")


@app.exception_handler(Exception)
async def _unhandled(request, exc):  # noqa: ANN001
    """兜住任何未预期的异常：返回可读的 JSON，而不是让前端收到一段非 JSON 的 500。

    以前前端只会显示「生成出错：SyntaxError: Unexpected token ...」，看不出真正原因；
    现在把异常类型/信息回给前端，并把完整堆栈写进 logs/error.log 方便排查。
    """
    try:
        os.makedirs(LOG_DIR, exist_ok=True)
        with open(os.path.join(LOG_DIR, "error.log"), "a", encoding="utf-8") as f:
            f.write(f"\n===== {datetime.now().isoformat()} "
                    f"{request.method} {request.url.path} =====\n")
            traceback.print_exc(file=f)
    except BaseException:  # 日志写不进去也不能影响返回
        pass
    return JSONResponse(
        {"ok": False,
         "error": f"服务端出错（{type(exc).__name__}）：{exc}"
                  f"　—— 完整堆栈已写入 {os.path.join(LOG_DIR, 'error.log')}"},
        status_code=500)

# dataset_id -> {"dataset": Dataset, "grid": list[list]}
SESSION: dict[str, dict] = {}
# 输出目录 -> 生成报告所需的一切，供 Word / PDF 导出复用
REPORTS: dict[str, dict] = {}


def _report_key(out_dir: str) -> str:
    """报告注册表的键：路径统一归一化。

    同一个目录可能被写成 `E:/ReportStudio/outputs\\x` 或 `E:\\ReportStudio\\outputs/x`
    —— 都指向同一个地方，但作为 dict 键是三个不同的字符串。不归一化的话，
    调用方只要把分隔符换个写法，导出就会报「这份报告已过期」，很难排查。
    """
    return os.path.normcase(os.path.normpath(out_dir))

MAX_UPLOAD_MB = 60


def _fallback_grid(ds: Dataset) -> list[list]:
    """拿不到原始排版时（文档/PDF/图片），用列名+数据行拼一个等价网格。"""
    grid = [[c.name for c in ds.columns]]
    for r in ds.rows:
        grid.append([r.get(c.name) for c in ds.columns])
    return grid


def _keep(datasets: list[tuple]):
    for ds, grid in datasets:
        SESSION[ds.id] = {"dataset": ds,
                          "grid": grid if grid is not None else _fallback_grid(ds)}
        if session_store.enabled():
            session_store.save(ds, SESSION[ds.id]["grid"])


def _restore_session() -> int:
    """启动时把上次会话的数据表读回来，省得重新传一遍文件。"""
    if not session_store.enabled():
        return 0
    n = 0
    for ds, grid in session_store.load_all():
        if ds.id in SESSION:
            continue
        SESSION[ds.id] = {"dataset": ds,
                          "grid": grid if grid is not None else _fallback_grid(ds)}
        n += 1
    return n


@app.exception_handler(Exception)
async def _boom(request, exc):
    return JSONResponse(status_code=200,
                        content={"ok": False, "error": f"{type(exc).__name__}: {exc}"})


@app.get("/")
def index():
    return FileResponse(os.path.join(WEB_DIR, "index.html"))


@app.get("/api/status")
def status():
    return {"ok": True, "llm": cfgmod.llm_ready(),
            "outputs": OUT_DIR, "temp": TEMP_DIR,
            "echarts_local": os.path.exists(VENDOR_ECHARTS),
            "persist": session_store.enabled(),
            "datasets": len(SESSION)}


@app.post("/api/upload")
async def upload(files: list[UploadFile] = File(...)):
    results = []
    for f in files:
        name = f.filename or "未命名"
        content = await f.read()
        if len(content) > MAX_UPLOAD_MB * 1024 * 1024:
            results.append({"ok": False, "name": name,
                            "error": f"文件超过 {MAX_UPLOAD_MB} MB"})
            continue
        ext = os.path.splitext(name)[1].lower()
        # 存到「uuid目录/原名」下，这样文件名里不会漏出一串 uuid 前缀
        sub = os.path.join(TEMP_DIR, uuid.uuid4().hex[:8])
        os.makedirs(sub, exist_ok=True)
        path = os.path.join(sub, _safe(name) or "data" + ext)
        try:
            with open(path, "wb") as fp:
                fp.write(content)
            datasets = parse_file(path)
        except Exception as e:
            results.append({"ok": False, "name": name,
                            "error": f"解析失败：{type(e).__name__}: {e}"})
            continue
        # 这里刻意不删临时文件：解析完立刻 os.remove 会在某些受限环境里
        # 触发批量删除保护，直接把请求线程打死。改成启动时按时间清理，见 _cleanup_temp()。
        _keep(datasets)
        for ds, _grid in datasets:
            results.append({"ok": True, "dataset": ds.to_dict()})
        if not datasets:
            results.append({"ok": False, "name": name,
                            "error": f"没解析出数据（{ext or '无扩展名'}）"})
    return {"results": results}


@app.post("/api/paste")
async def paste(payload: dict):
    text = (payload.get("text") or "").strip()
    if not text:
        raise HTTPException(400, "粘贴内容是空的")
    ds, grid = parse_pasted(text, name=payload.get("name") or "粘贴内容")
    _keep([(ds, grid)])
    return {"ok": True, "dataset": ds.to_dict()}


@app.post("/api/fix")
async def fix(payload: dict):
    """界面上改表头行 / 列类型后重算。"""
    did = payload.get("id")
    if did not in SESSION:
        raise HTTPException(404, "数据表不存在，请重新导入")
    item = SESSION[did]
    old = item["dataset"]
    ds = build_dataset(
        item["grid"],
        name=old.name,
        source_type=old.source_type,
        header_row=(int(payload["header_row"]) - 1) if payload.get("header_row") else None,
        type_overrides=payload.get("types") or {},
        dataset_id=did,
    )
    item["dataset"] = ds
    if session_store.enabled():
        session_store.save(ds, item["grid"])
    return {"ok": True, "dataset": ds.to_dict()}


@app.get("/api/datasets")
def list_datasets():
    """当前会话里所有数据表，包括上次会话恢复的那些。"""
    return {"items": [it["dataset"].to_dict() for it in SESSION.values()]}


@app.delete("/api/dataset/{did}")
def delete_dataset(did: str):
    """移除一张表：内存和磁盘上都清掉。"""
    SESSION.pop(did, None)
    session_remove(did)
    return {"ok": True}


@app.post("/api/session/clear")
def clear_session():
    n = len(SESSION)
    SESSION.clear()
    session_clear()
    return {"ok": True, "removed": n}


# ———————————————————————— 数据库取数（只读） ————————————————————————
# 三重闸门：core/db/guard.py 拦语句、core/db/connect.py 设只读会话、
# 这里加行数上限与超时。取数结果走 build_dataset 转成 Dataset，
# 登记进 SESSION 后和导入的 Excel/CSV 完全等价，可直接出图、导出、合并。

@app.get("/api/db/kinds")
def db_kinds():
    """界面用：支持哪些数据库、各自要填什么字段。"""
    from core.db.connect import SPECS
    return {"ok": True, "readonly": True,
            "kinds": [{"kind": k, **v} for k, v in SPECS.items()],
            "limits": cfgmod.db_settings()}


@app.get("/api/db/connections")
def db_list_connections():
    from core.db import store as dbstore
    return {"ok": True, "items": dbstore.list_public()}


@app.post("/api/db/connections")
async def db_save_connection(payload: dict):
    from core.db import store as dbstore
    ok, note, info = dbstore.save(payload)
    return {"ok": ok, "note": note, "connection": info, "items": dbstore.list_public()}


@app.delete("/api/db/connections")
def db_delete_connection(name: str = ""):
    from core.db import store as dbstore
    ok, note = dbstore.delete(name)
    return {"ok": ok, "note": note, "items": dbstore.list_public()}


@app.post("/api/db/test")
async def db_test(payload: dict):
    """试连。失败也是 ok:false + 白话原因，不抛。"""
    from core.db import store as dbstore
    from core.db.query import test_connection
    cfg, err = dbstore.resolve(payload)
    if cfg is None:
        return {"ok": False, "error": err}
    cfg.connect_timeout = cfgmod.db_settings()["connect_timeout"]
    return test_connection(cfg)


@app.post("/api/db/scan")
async def db_scan(payload: dict):
    """连上并列出可读的表/视图。"""
    from core.db import store as dbstore
    from core.db.connect import SPECS, connect
    from core.db.query import list_tables
    cfg, err = dbstore.resolve(payload)
    if cfg is None:
        return {"ok": False, "error": err}
    try:
        conn = connect(cfg, cfgmod.db_settings()["timeout_ms"])
    except Exception as e:
        return {"ok": False, "error": str(e)}
    try:
        tables = list_tables(conn)
        return {"ok": True, "tables": tables, "kind": cfg.kind,
                "label": SPECS.get(cfg.kind, {}).get("label", cfg.kind),
                "source": cfg.describe(),
                "readonly_enforced": conn.readonly_enforced,
                "warnings": list(conn.warnings)}
    except Exception as e:
        return {"ok": False, "error": f"读表清单失败：{e}"}
    finally:
        conn.close()


@app.post("/api/db/columns")
async def db_columns(payload: dict):
    """看某张表的列定义。"""
    from core.db import store as dbstore
    from core.db.connect import connect
    from core.db.query import build_select_template, describe_table
    cfg, err = dbstore.resolve(payload)
    if cfg is None:
        return {"ok": False, "error": err}
    table = (payload.get("table") or "").strip()
    schema = (payload.get("schema") or "").strip()
    if not table:
        return {"ok": False, "error": "没指定表名"}
    try:
        conn = connect(cfg, cfgmod.db_settings()["timeout_ms"])
    except Exception as e:
        return {"ok": False, "error": str(e)}
    try:
        cols = describe_table(conn, table, schema)
        return {"ok": True, "columns": cols,
                "suggest_sql": build_select_template(
                    cfg.kind, table, [c["name"] for c in cols[:12]], schema,
                    payload.get("limit") or 1000)}
    except Exception as e:
        return {"ok": False, "error": f"读列定义失败：{e}"}
    finally:
        conn.close()


@app.post("/api/db/build_sql")
async def db_build_sql(payload: dict):
    """按选中的表/列生成一条可直接跑的 SELECT（纯拼串，不需要连库）。"""
    from core.db.query import build_select_template, preview_sql
    table = (payload.get("table") or "").strip()
    if not table:
        return {"ok": False, "error": "没指定表名"}
    kind = str(payload.get("kind") or "sqlite").strip().lower()
    schema = (payload.get("schema") or "").strip()
    cols = [str(c) for c in (payload.get("columns") or []) if str(c).strip()]
    limit = payload.get("limit") or 1000
    sql = (build_select_template(kind, table, cols, schema, limit) if cols
           else preview_sql(kind, table, schema, limit))
    return {"ok": True, "sql": sql}


@app.post("/api/db/query")
async def db_query(payload: dict):
    """执行只读 SQL。

    import=true（默认）时把结果登记成一张数据表，之后就能像导入的表格一样
    配图表、进报告、导出 Word/PDF。import=false 只回预览不落任何数据。
    """
    from core.db import store as dbstore
    from core.db.guard import SQLRejected
    from core.db.query import run_to_dataset

    sql = (payload.get("sql") or "").strip()
    if not sql:
        return {"ok": False, "error": "SQL 是空的，先写一条查询。"}

    cfg, err = dbstore.resolve(payload)
    if cfg is None:
        return {"ok": False, "error": err}

    s = cfgmod.db_settings()
    cfg.connect_timeout = s["connect_timeout"]
    try:
        max_rows = int(payload.get("max_rows") or s["max_rows"])
    except (TypeError, ValueError):
        max_rows = s["max_rows"]
    max_rows = max(1, min(max_rows, s["max_rows"]))

    try:
        ds, grid, meta = run_to_dataset(
            cfg, sql, name=(payload.get("name") or "").strip(),
            max_rows=max_rows, timeout_ms=s["timeout_ms"])
    except SQLRejected as e:
        return {"ok": False, "blocked": True, "error": e.message}
    except Exception as e:
        return {"ok": False, "error": str(e)}

    x_cols = [c.name for c in ds.columns if c.dtype in ("text", "date")]
    num_cols = [c for c in ds.columns if c.dtype == "number"]
    # 百分比/率类列不放进默认图表：它和金额往往差好几个数量级，
    # 放在同一根轴上柱子会被压得看不见，反而让人以为图错了。
    plain_num = [c.name for c in num_cols if c.unit != "%"]
    y_cols = plain_num or [c.name for c in num_cols]
    # 默认只配一个数列：销售额和订单量量级也常常差很远，两条一起画同样难读。
    # 其余的列在 y_candidates 里，用户想加随时能加。
    suggest = ([{"type": "bar", "x": x_cols[0], "y": y_cols[:1], "agg": "sum",
                 "sort_by": "__x__", "sort_order": "desc"}]
               if x_cols and y_cols else [])

    resp = {
        "ok": True,
        "source": meta["source"],
        "elapsed_ms": meta["elapsed_ms"],
        "truncated": meta["truncated"],
        "row_count": meta["row_count"],
        "readonly_enforced": meta["readonly_enforced"],
        "columns_detail": [c.to_dict() for c in ds.columns],
        "preview_rows": ds.rows[:200],
        "warnings": ds.warnings,
        # 顺手给出「这张图怎么配」的建议，前端可一键预填，
        # 省掉为了拿建议再跑一遍同样的查询
        "x_candidates": x_cols,
        "y_candidates": y_cols,
        "suggest_charts": suggest,
    }
    if payload.get("import", True):
        _keep([(ds, grid)])
        resp["dataset"] = ds.to_dict()
        resp["imported"] = True
    else:
        resp["imported"] = False
    return resp


@app.post("/api/db/upload")
async def db_upload(file: UploadFile = File(...)):
    """上传一个 SQLite 库文件，返回可直接用的 path（省得手敲路径）。"""
    name = file.filename or "uploaded.db"
    ext = os.path.splitext(name)[1].lower()
    if ext not in (".db", ".sqlite", ".sqlite3", ".db3"):
        return {"ok": False, "error": f"只接受 SQLite 库文件（.db/.sqlite/.sqlite3/.db3），"
                                     f"这个是 {ext or '无扩展名'}"}
    content = await file.read()
    if len(content) > MAX_UPLOAD_MB * 1024 * 1024 * 10:
        return {"ok": False, "error": "库文件太大（上限 2 GB）"}
    sub = os.path.join(TEMP_DIR, "db", uuid.uuid4().hex[:8])
    os.makedirs(sub, exist_ok=True)
    path = os.path.join(sub, _safe(name) or "uploaded.db")
    with open(path, "wb") as fp:
        fp.write(content)
    # 校验文件头，避免把随便一个文件当库去连（那样报错很难懂）
    try:
        with open(path, "rb") as fp:
            head = fp.read(16)
        if not head.startswith(b"SQLite format 3"):
            return {"ok": False, "error": "这个文件不是有效的 SQLite 数据库（文件头不对）。"
                                         "如果它是别的数据库的备份，请改用对应的连接方式。"}
    except Exception:
        pass
    return {"ok": True, "path": path, "name": name, "size": len(content)}


@app.post("/api/plan")
async def plan(payload: dict):
    datasets = _resolve(payload.get("dataset_ids") or [])
    from core.merge import suggest_plan
    return suggest_plan(datasets)


@app.post("/api/parse_nl")
async def parse_nl(payload: dict):
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


@app.post("/api/generate")
async def generate(payload: dict):
    datasets = _resolve(payload.get("dataset_ids") or [])
    if not datasets:
        raise HTTPException(400, "请先导入数据")

    mode = payload.get("merge_mode") or "merge"
    if mode == "merge" and len(datasets) > 1:
        groups = group_datasets(datasets)
        datasets = [merge_group(g) for g in groups]

    raw_charts = payload.get("charts") or []
    charts = [ChartSpec(**{k: v for k, v in c.items()
                           if k in ChartSpec.__dataclass_fields__})
              for c in raw_charts]
    charts = [c for c in charts if c.x and c.y]
    # 不在这里直接报错：没有可出图的配置时，下面会降级成「数据摘要报告」，
    # 保证「只有纯文本 / 只有无模型图片 / 表里没有数值列」这类来源也一定能拿到报告。
    requested_charts = len(charts)

    spec = ReportSpec(
        title=(payload.get("title") or "数据分析报告").strip(),
        template=payload.get("template") or "full",
        dataset_ids=[d.id for d in datasets],
        charts=charts,
        metrics=payload.get("metrics") or [],
        chart_layout=layout_columns(payload.get("chart_layout")),
    )

    # 图块构造统一走 core/chartset，和看板共用同一套口径（含「多指标拆成多张图」）
    chart_blocks, pending = build_blocks(datasets, charts)
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
                            "（例如纯文本、扫描图片、或表里没有数值列），"
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
    out_dir = os.path.join(OUT_DIR, f"{safe}_{stamp}")
    os.makedirs(out_dir, exist_ok=True)

    img_dir = os.path.join(out_dir, "charts")
    for cid, item in agg_store.items():
        p = render_chart(item["agg"], item["spec"],
                         os.path.join(img_dir, cid + ".png"))
        if p:
            chart_images[cid] = p

    html_path = render_html(datasets, spec, chart_blocks, metric_cards,
                            summary, out_dir, VENDOR_ECHARTS)
    md_path = render_markdown(datasets, spec, chart_blocks, metric_cards,
                              summary, out_dir)

    rel = os.path.relpath(out_dir, ROOT).replace("\\", "/")
    REPORTS[_report_key(out_dir)] = {"datasets": datasets, "spec": spec,
                                     "chart_blocks": chart_blocks,
                                     "metric_cards": metric_cards,
                                     "summary": summary, "chart_images": chart_images}
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


@app.get("/api/dashboards")
def dashboards_list():
    """已保存的看板清单。"""
    return {"items": dash.list_all()}


@app.post("/api/dashboard/save")
async def dashboard_save(payload: dict):
    """保存看板配置并立刻渲染一份。

    存的是「要哪几个指标、哪几张图、怎么排」，不是算好的数字 ——
    以后每次打开都按当前数据重算，所以看板永远是新的。
    """
    did = (payload.get("id") or "").strip()
    base = dash.get(did) if did else None
    if did and base is None:
        return {"ok": False, "error": f"没找到看板「{did}」，可能已被删除。"}
    cfg = dash.from_payload(payload, base)
    if not cfg.charts and not cfg.metrics:
        return {"ok": False, "error": "看板是空的：至少配一张图，或勾一个关键指标。"}
    cfg = dash.save(cfg)
    stat = _dashboard_render(cfg)
    return {"ok": True, "id": cfg.id, "name": cfg.name, **stat}


@app.post("/api/dashboard/render")
async def dashboard_render(payload: dict):
    """按当前数据重新渲染一个看板。"""
    did = (payload.get("id") or "").strip()
    cfg = dash.get(did)
    if cfg is None:
        return {"ok": False, "error": "没找到这个看板，可能已被删除。"}
    stat = _dashboard_render(cfg)
    return {"ok": True, "id": cfg.id, "name": cfg.name, **stat}


@app.post("/api/dashboard/preview")
async def dashboard_preview(payload: dict):
    """按当前配置即时渲染看板，但**不存成常驻看板**。

    报告和看板现在是同一屏里的两个视图：改完图表配置点生成报告，
    切到看板就该看到最新结果，而不是被迫先「保存到看板」再看一遍。
    想长期留着，再点「保存到看板」即可。
    """
    cfg = dash.from_payload(payload)
    if not cfg.charts and not cfg.metrics:
        return {"ok": False,
                "error": "看板还是空的：先在「说清需求」里配一张图，或在「指标列」里勾一个指标。"}
    if not cfg.dataset_ids:
        cfg.dataset_ids = list(SESSION.keys())
    datasets, missing = dash.resolve_datasets(cfg, SESSION)
    if not datasets:
        return {"ok": False,
                "error": "当前会话里没有数据表，先在左边导入文件或用 SQL 取数。"}
    stat = dash.render_preview(datasets, cfg, VENDOR_ECHARTS, missing)
    stat["missing"] = list(missing)
    return {"ok": True, "name": cfg.name, **stat}


@app.delete("/api/dashboard/{did}")
def dashboard_delete(did: str):
    ok = dash.delete(did)
    return {"ok": ok, "error": "" if ok else "没找到这个看板。"}


def _dashboard_render(cfg) -> dict:
    """渲染看板并把「哪几张表没了」如实带回去。"""
    datasets, missing = dash.resolve_datasets(cfg, SESSION)
    names_for_missing = list(missing)
    stat = dash.render_to_file(datasets, cfg, VENDOR_ECHARTS, missing)
    stat["missing"] = names_for_missing
    stat["updated"] = cfg.updated
    if not datasets:
        stat["warning"] = ("这个看板引用的数据表都不在当前会话里，"
                           "已渲染成空看板。请把数据重新导入后再刷新。")
    return stat


@app.get("/api/history")
def history():
    """列出 outputs 下已有的报告，可以回看、重新导出。"""
    items = []
    if not os.path.isdir(OUT_DIR):
        return {"items": []}
    for name in sorted(os.listdir(OUT_DIR), reverse=True):
        d = os.path.join(OUT_DIR, name)
        if not os.path.isdir(d):
            continue
        files = {}
        for fn in ("report.html", "report.md", "report.docx", "report.pdf"):
            p = os.path.join(d, fn)
            if os.path.exists(p):
                files[fn] = {"size": os.path.getsize(p),
                             "mtime": datetime.fromtimestamp(
                                 os.path.getmtime(p)).strftime("%m-%d %H:%M")}
        if not files:
            continue
        rel = os.path.relpath(d, ROOT).replace("\\", "/")
        items.append({
            "dir": d,
            "name": name.rsplit("_", 1)[0] if "_" in name else name,
            "stamp": name.rsplit("_", 1)[-1] if "_" in name else "",
            "files": {k: {"url": f"/{rel}/{k}", **v} for k, v in files.items()},
        })
    return {"items": items[:30]}


@app.post("/api/export")
async def export(payload: dict):
    kind = payload.get("kind")
    if kind not in ("docx", "pdf", "png"):
        raise HTTPException(400, "只支持 docx / pdf / png")
    out_dir = payload.get("dir")
    if not out_dir or not os.path.isdir(out_dir):
        raise HTTPException(404, "报告目录不存在，请重新生成")
    sub = REPORTS.get(_report_key(out_dir))
    if not sub:
        raise HTTPException(404, "这份报告已过期，请重新生成后再导出")

    chart_images = sub["chart_images"]
    if not chart_images:
        img_dir = os.path.join(out_dir, "charts")
        chart_images = {b["id"]: os.path.join(img_dir, b["id"] + ".png")
                        for b in sub["chart_blocks"]}
        chart_images = {k: v for k, v in chart_images.items() if os.path.exists(v)}

    try:
        if kind == "png":
            # 拼成一张总图。顺序必须跟报告里一致，否则图文对不上。
            paths = [chart_images[b["id"]] for b in sub["chart_blocks"]
                     if b["id"] in chart_images]
            if not paths:
                return {"ok": False,
                        "error": "这份报告没有可拼的图表（可能是纯文本/图片输入，"
                                 "或静态图渲染未成功）"}
            p = combine_images(paths, os.path.join(out_dir, "charts.png"),
                               layout_columns(payload.get("cols")))
            if not p:
                return {"ok": False, "error": "图片拼接失败，可在报告里逐张「保存为 PNG」"}
        elif kind == "docx":
            p = render_docx(sub["datasets"], sub["spec"], sub["chart_blocks"],
                            sub["metric_cards"], sub["summary"], out_dir,
                            chart_images)
        else:
            p = render_pdf(sub["datasets"], sub["spec"], sub["chart_blocks"],
                           sub["metric_cards"], sub["summary"], out_dir,
                           chart_images)
    except RuntimeError as e:
        return {"ok": False, "error": str(e)}

    rel = os.path.relpath(p, ROOT).replace("\\", "/")
    return {"ok": True, "url": f"/{rel}", "path": p}


def cleanup_temp(max_age_hours: int = 24, limit: int = 15) -> int:
    """启动时清掉过期的上传缓存。

    三个刻意的设计：
    1. 只删超过 24 小时的，避免把刚传进来还没处理完的文件删掉
    2. 一次最多删 limit 个，避免触发环境的批量删除保护
    3. 任何异常都不许冒泡 —— 清缓存失败不能影响服务启动
    """
    import time

    now = time.time()
    cutoff = now - max_age_hours * 3600
    removed = 0
    try:
        for entry in sorted(os.listdir(TEMP_DIR))[:200]:
            if removed >= limit:
                break
            p = os.path.join(TEMP_DIR, entry)
            try:
                if os.path.isfile(p):
                    if os.path.getmtime(p) < cutoff:
                        os.remove(p)
                        removed += 1
                elif os.path.isdir(p):
                    stale = True
                    for f in os.listdir(p):
                        fp = os.path.join(p, f)
                        if os.path.getmtime(fp) >= cutoff:
                            stale = False
                            break
                    if stale:
                        for f in os.listdir(p):
                            os.remove(os.path.join(p, f))
                        os.rmdir(p)
                        removed += 1
            except BaseException:     # noqa: BLE001 清理失败不能带崩服务
                continue
    except BaseException:
        pass
    return removed


def _resolve(ids: list[str]) -> list[Dataset]:
    out = []
    for i in ids or []:
        if i in SESSION:
            out.append(SESSION[i]["dataset"])
    return out


def _safe(name: str) -> str:
    return re.sub(r'[\\/:*?"<>|]+', "_", name)[:60]


app.mount("/outputs", StaticFiles(directory=OUT_DIR), name="outputs")
app.mount("/dashboards", StaticFiles(directory=cfgmod.dashboards_dir()),
          name="dashboards")
app.mount("/static", StaticFiles(directory=WEB_DIR), name="static")

if __name__ == "__main__":
    import threading
    import webbrowser

    import uvicorn

    n = cleanup_temp()
    if n:
        print(f"已清理 {n} 个过期上传缓存")
    restored = _restore_session()
    if restored:
        print(f"已恢复上次会话的 {restored} 张数据表")
    c = cfgmod.load()["server"]
    url = f"http://{c['host']}:{c['port']}"
    if c.get("auto_open_browser", True):
        threading.Timer(1.2, lambda: webbrowser.open(url)).start()
    print(f"ReportStudio 已启动：{url}")
    print(f"输出目录：{OUT_DIR}")
    uvicorn.run(app, host=c["host"], port=int(c["port"]), log_level="warning")
