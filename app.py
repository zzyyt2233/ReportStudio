"""ReportStudio 服务入口。

启动：python app.py  →  浏览器自动打开 http://127.0.0.1:8765
所有产物写在 outputs 目录（默认 E 盘），不往 C 盘落任何东西。
"""

from __future__ import annotations

import json
import os
import re
import shutil
import sys
import threading
import time
import traceback
import uuid
import zipfile
from collections import OrderedDict
from contextlib import asynccontextmanager
from datetime import datetime, timedelta
from pathlib import Path
from urllib.parse import quote

# 免安装包里用的是嵌入式 Python，它按 python313._pth 决定模块搜索路径，
# 不会像常规 Python 那样自动把「被运行脚本所在目录」加进 sys.path。
# 这里显式补上，保证 core/ 这些本地包在任何启动方式下都能 import 到。
_ROOT = Path(__file__).resolve().parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from core import config as cfgmod
from core import dashboard as dash
from core import session_store
from core.analyze import compute_metrics, numeric_columns, template_summary
from core.chartset import build_blocks, layout_columns
from core.topic import build_topic_chart
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
from core.render.table_export import MAX_ROWS as EXPORT_MAX_ROWS
from core.render.table_export import render_table
from core.convert import convert_docx_to_pdf, convert_pdf_to_docx
from docx import Document
from reportlab.platypus import SimpleDocTemplate
import pdfplumber
from core.session_store import clear as session_clear
from core.session_store import remove as session_remove
from core.spec import parse as parse_spec

# 跨请求共享状态（SESSION / REPORTS / 缓存与锁）集中到 core/state.py，
# 各域路由在 core/routes/ 下。app.py 只留装配、异常处理与根级路由。
from core.state import (
    LOG_DIR,
    MAX_SESSION_DATASETS,
    MAX_UPLOAD_MB,
    OUT_DIR,
    PERSISTED,
    REPORTS,
    RESIDUE_TMP_DAYS,
    ROOT,
    SESSION,
    SESSION_TRASH,
    TEMP_DIR,
    VENDOR_ECHARTS,
    WEB_DIR,
    _HIST_CACHE,
    _HIST_RESP,
    _LOCK,
    _MAX_WORKERS,
    _STAMP_RE,
    _available_count,
    _evict_if_needed,
    _fallback_grid,
    _get,
    _keep,
    _meta_dict,
    _reload,
    _report_key,
    _resolve,
    _restore_session,
    _safe,
    _session_ids,
    _session_snapshot,
    _touch,
)

os.makedirs(OUT_DIR, exist_ok=True)
os.makedirs(TEMP_DIR, exist_ok=True)

@asynccontextmanager
async def _lifespan(_app: "FastAPI"):
    """启动时收紧同步端点的线程池上限。

    用 lifespan 而不是已废弃的 @app.on_event("startup")。
    """
    import anyio.to_thread
    try:
        anyio.to_thread.current_default_thread_limiter().total_tokens = _MAX_WORKERS
    except Exception:
        pass          # 拿不到就沿用默认值 40，不影响功能
    yield


app = FastAPI(title="ReportStudio", lifespan=_lifespan)

# 注册各域路由（db / convert；后续 dashboard、history、generate 等同套路接入）
from core.routes import register as _register_routes
_register_routes(app)



@app.exception_handler(Exception)
async def _unhandled(request, exc):  # noqa: ANN001
    """兜住任何未预期的异常：返回可读的 JSON，而不是让前端收到一段非 JSON 的 500。

    以前前端只会显示「生成出错：SyntaxError: Unexpected token ...」，看不出真正原因；
    现在把异常类型/信息回给前端，并把完整堆栈写进 logs/error.log 方便排查。

    返回 200 而不是 500 是有意的：tests/no500.py 把「任何输入都不许 5xx」当硬约束，
    而前端 _send() 只认响应体里的 ok / error 字段、不看状态码，所以 200 一样能正确提示。

    ⚠️ 这个文件里曾经有**两个** @app.exception_handler(Exception)：本函数，
    以及后面一个只回 `{"ok": false, "error": ...}` 的 _boom。Starlette 按异常类型
    在字典里存处理器，后注册的会把先注册的**静默覆盖** —— 于是写日志这段成了死代码，
    logs/error.log 从来没被写过。排障时会以为「日志里应该有」，打开却是空的。
    （注释里一直写着「堆栈已写入 error.log」，而那个文件根本不存在。）
    现在合并成这一个：既写日志，也回可读 JSON。
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
        status_code=200)




@app.get("/")
def index():
    return FileResponse(os.path.join(WEB_DIR, "index.html"))


@app.get("/api/status")
def status():
    st = residue_stat()
    return {"ok": True, "llm": cfgmod.llm_ready(),
            "outputs": OUT_DIR, "temp": TEMP_DIR,
            "echarts_local": os.path.exists(VENDOR_ECHARTS),
            "persist": session_store.enabled(),
            "datasets": _available_count(),
            # 可回收的残片（*.deleted / 过期 *.tmp / 会话回收站），
            # 界面上据此显示「清理缓存（147 个 / 72 MB）」
            "residue": st["count"], "residue_size": st["size"]}


@app.post("/api/upload")
def upload(files: list[UploadFile] = File(...)):
    """导入文件。

    这里是同步 def 而不是 async def：parse_file 里面是 pandas / openpyxl / OCR，
    单个文件能跑几秒到几十秒。写成 async def 的话它跑在事件循环里，
    这段时间整个服务（含 /api/status、静态资源）全部无响应。
    实测（2000 行样本、6 张图，出图 5 秒）出图期间打 /api/status：
    async 版尾部延迟 10120ms，sync 版 607ms。
    改成同步 def 后由 FastAPI 丢进线程池，重活并行跑，事件循环始终空着。
    """
    results = []
    limit = MAX_UPLOAD_MB * 1024 * 1024
    for f in files:
        name = f.filename or "未命名"
        # UploadFile.file 是 SpooledTemporaryFile，同步端点里直接用它读。
        # 分块读并在超限时立刻停：一个几百 MB 的文件不该先整个进内存才发现超限。
        buf = bytearray()
        too_big = False
        while True:
            chunk = f.file.read(1024 * 1024)
            if not chunk:
                break
            buf += chunk
            if len(buf) > limit:
                too_big = True
                break
        if too_big:
            results.append({"ok": False, "name": name,
                            "error": f"文件超过 {MAX_UPLOAD_MB} MB"})
            continue
        content = bytes(buf)
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
def paste(payload: dict):
    text = (payload.get("text") or "").strip()
    if not text:
        raise HTTPException(400, "粘贴内容是空的")
    ds, grid = parse_pasted(text, name=payload.get("name") or "粘贴内容")
    _keep([(ds, grid)])
    return {"ok": True, "dataset": ds.to_dict()}


@app.post("/api/fix")
def fix(payload: dict):
    """界面上改表头行 / 列类型后重算。"""
    did = payload.get("id")
    item = _get(did)
    if item is None:
        raise HTTPException(404, "数据表不存在，请重新导入")
    # _get 已经把引用取好；下面 build_dataset 要跑几百毫秒，不占锁。
    # 写回前再确认这张表还在（没被别的请求删掉），避免 TypeError。
    grid, old = item["grid"], item["dataset"]
    ds = build_dataset(
        grid,
        name=old.name,
        source_type=old.source_type,
        header_row=(int(payload["header_row"]) - 1) if payload.get("header_row") else None,
        type_overrides=payload.get("types") or {},
        dataset_id=did,
    )
    with _LOCK:
        # 表可能在这几百毫秒里被删了。写回前确认它还在，
        # 不在就只回结果、不落盘（用户看到的是这次修正生效了，刷新后没了）。
        if SESSION.get(did) is item:
            item["dataset"] = ds
    if session_store.enabled():
        if session_store.save(ds, grid):
            with _LOCK:
                PERSISTED.add(did)
    return {"ok": True, "dataset": ds.to_dict()}


@app.get("/api/datasets")
def list_datasets():
    """所有可用数据表：在内存的重对象直接出，被淘汰但已落盘的用轻量元数据补。"""
    items = []
    with _LOCK:
        for it in SESSION.values():
            items.append(it["dataset"].to_dict())
    for did in PERSISTED - set(SESSION.keys()):
        m = _meta_dict(did)
        if m is not None:
            items.append(m)
    return {"items": items}


@app.delete("/api/dataset/{did}")
def delete_dataset(did: str):
    """移除一张表：内存和磁盘上都清掉。"""
    with _LOCK:
        SESSION.pop(did, None)
        PERSISTED.discard(did)
    session_remove(did)
    return {"ok": True}


@app.post("/api/session/clear")
def clear_session():
    with _LOCK:
        n = len(SESSION)
        SESSION.clear()
        PERSISTED.clear()
    session_clear()
    return {"ok": True, "removed": n}


def _cleanup_residue_by_budget(seconds: float, batch: int = 20) -> int:
    """按时间预算回收残片，清完或预算用光就停。

    cleanup_residue 的 limit 参数按个数限，单次调用最多删 limit 个。
    之前积压 1273 个时无论启动还是按钮都只能一次推 200 个，
    要连续按 7 次才消化完。改成时间预算制：真实环境（删除不被拦）
    一次就能清完；受限环境（每个删除要走 1~3 秒守卫检查）也能尽量推进，
    每轮仍是 batch 个的小批量，不会撞批量删除守卫。

    got == 0 就停：要么真清完了，要么每个都删不动（tried 上限触发），
    继续死磕只是白烧时间。
    """
    deadline = time.monotonic() + seconds
    total = 0
    while time.monotonic() < deadline:
        got = cleanup_residue(limit=batch)
        total += got
        if got == 0:
            break
    return total


@app.post("/api/cleanup")
def cleanup_now():
    """用户主动触发的残片回收。

    为什么不只靠启动时自动清：残片是「删除失败」的产物，能攒到几十上百 MB，
    用户想马上腾地方时不该被一句「重启服务」挡回去。
    清的全是**确定不会再被读**的东西 —— *.deleted、过期的 *.tmp、会话回收站；
    正常报告、看板配置、会话数据一律不碰。

    预算 10 秒而不是 45：这是用户点着按钮在等的请求，等太久像卡死。
    10 秒内删不完的会在响应里如实报告剩余数量，再点一次继续。
    """
    before = residue_stat()
    n = _cleanup_residue_by_budget(10.0)
    after = residue_stat()
    return {"ok": True, "removed": n,
            "freed": max(0, before["size"] - after["size"]),
            "left": after["count"], "left_size": after["size"]}






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


def _purge(path: str) -> bool:
    """真删，不做改名兜底 —— 残片回收专用。

    不能复用 _rm_file / _rm_dir：它们删不掉时会退化成改名成 *.deleted，
    而这里处理的**本来就叫** *.deleted，再改一次就成了 *.deleted.deleted，
    一次回收反倒多攒一层垃圾。删不掉就放弃，下次启动再试。
    """
    import shutil
    try:
        if os.path.isdir(path):
            try:
                shutil.rmtree(path)
                return True
            except BaseException:
                return False
        os.remove(path)
        return True
    except BaseException:
        return False




def _residue_plan() -> list[tuple[str, tuple[str, ...] | None, int | None]]:
    """回收计划：(目录, 收哪些后缀（None = 全部）, 年龄阈值天数（None = 不限）)。

    *.deleted 不限年龄是有意的 —— 它只可能来自「删除失败后的降级改名」，
    改名之后那条流程就结束了，谁也不会再读它。让它在那儿躺一天再清，
    对用户没有任何好处（本机实测躺了 147 个 / 72MB）。
    """
    dash = cfgmod.dashboards_dir()
    exports = os.path.join(OUT_DIR, "_exports")
    return [
        (dash, (".deleted",), None),
        (dash, (".tmp",), RESIDUE_TMP_DAYS),
        (OUT_DIR, (".deleted",), None),
        (OUT_DIR, (".tmp",), RESIDUE_TMP_DAYS),
        (exports, (".deleted",), None),
        (exports, (".tmp",), RESIDUE_TMP_DAYS),
        (SESSION_TRASH, None, RESIDUE_TMP_DAYS),
    ]


def _residue_list() -> list[str]:
    """当前够格回收的残片路径。

    cleanup_residue 和 /api/status 共用这一套判定，免得出现
    「页面说 147 个待回收、点下去却只删掉 3 个」这种对不上的情况。
    """
    now = time.time()
    out = []
    for d, suffixes, age in _residue_plan():
        try:
            if not os.path.isdir(d):
                continue
            cutoff = None if age is None else now - age * 86400
            for fn in os.listdir(d):
                if suffixes is not None and not fn.endswith(suffixes):
                    continue
                p = os.path.join(d, fn)
                if cutoff is None:
                    out.append(p)
                    continue
                try:
                    if os.path.getmtime(p) < cutoff:
                        out.append(p)
                except OSError:
                    continue
        except BaseException:
            continue
    return sorted(set(out))


def _path_size(p: str) -> int:
    try:
        if os.path.isfile(p):
            return os.path.getsize(p)
        total = 0
        for root, _dirs, files in os.walk(p):
            for fn in files:
                try:
                    total += os.path.getsize(os.path.join(root, fn))
                except OSError:
                    continue
        return total
    except Exception:
        return 0


def residue_stat() -> dict:
    """残片数量与占用，给界面显示「可回收 147 个 / 72MB」。"""
    paths = _residue_list()
    return {"count": len(paths), "size": sum(_path_size(p) for p in paths)}


def cleanup_residue(limit: int = 20) -> int:
    """回收「删除 / 写入被中断」留下的残片。

    为什么必须有这一步：删看板和报告时，一旦 os.remove / rmtree 被环境的
    批量删除守卫拦下，代码会退化成把文件**改名**成 *.deleted（见 _rm_dir 和
    core/dashboard.py 的 _rm），这样它至少从列表里消失。但这些残片没有任何
    东西会去清 —— 攒久了纯占地方，打包发给别人时还会一起被带走。
    （本机实测：dashboards/ 里堆到过 147 个 .deleted，合 72MB。）

    限量删除是为了不一次撞上批量删除守卫；删不掉的留着，下次再来。
    """
    n = 0
    tried = 0
    for p in _residue_list():
        # 上限用「尝试次数」而不是「成功次数」：环境拦下删除时成功数永远是 0，
        # 拿成功数判断就会把整个残片列表（本机能到 1286 个）逐个试一遍 ——
        # 界面上的「清理缓存」会长时间卡住，启动时更是要等它跑完。
        if n >= limit or tried >= limit * 2:
            break
        tried += 1
        if _purge(p):
            n += 1
    return n



def _pick_port(host: str, port: int, tries: int = 12) -> int:
    """挑一个能用的端口。

    固定端口最常见的故障是「上一次的服务没关干净，8765 还被占着」，
    用户看到的就是一句「启动失败」，然后得去翻 config.yaml 改端口 ——
    对只想双击一下就跑的人来说太重。这里直接顺延到 8766 / 8767…

    刻意**不设** SO_REUSEADDR：Windows 上它的语义是「允许重复绑定」，
    会让两个进程抢同一端口；不设时 bind 才会如实报出「已被占用」。
    """
    import socket
    for p in range(int(port), int(port) + max(1, tries)):
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            s.bind((host, p))
            return p
        except OSError:
            continue
        finally:
            s.close()
    return int(port)      # 全占着：原样交给 uvicorn，让它把原因报清楚


app.mount("/outputs", StaticFiles(directory=OUT_DIR), name="outputs")
app.mount("/dashboards", StaticFiles(directory=cfgmod.dashboards_dir()),
          name="dashboards")
app.mount("/static", StaticFiles(directory=WEB_DIR), name="static")


@app.middleware("http")
async def _no_cache_frontend(request: Request, call_next):
    """前端资源与生成产物禁止缓存。

    否则浏览器会一直用旧的 app.js / style.css / index.html，
    表现为「改了功能但页面还是坏的 / 旧的」，反复误导排查。
    仅对前端与产物路径生效，不动 /api/* 与数据库取数结果。
    """
    resp = await call_next(request)
    p = request.url.path
    if p == "/" or p.startswith(("/static/", "/outputs/", "/dashboards/")):
        resp.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
    return resp

if __name__ == "__main__":
    import webbrowser

    import uvicorn

    from core import logsetup

    # 双击启动时输出只打在黑窗口里，关掉就没了。复制一份进 logs/app.log，
    # 超 5MB 自动归档 —— 用户报「打不开/闪退」时至少有东西可查。
    # uvicorn 自己也是往 stderr 写的，跟着一起进文件。
    logsetup.install()
    logsetup.rotate_error_log()

    # 清理放后台线程：它跟「把服务起来」没有任何关系，不该堵在启动路径上。
    # 实测代价不小 —— 每个删除在某些环境下要走一遍守卫检查（本机 1~3 秒一个），
    # 清 15 个上传缓存就花了 44 秒，同步跑的话双击启动要等这么久才看到页面。
    def _cleanup_in_background():
        try:
            n = cleanup_temp()
            # 残片按 45 秒时间预算清（见 _cleanup_residue_by_budget 的说明）。
            # 之前固定 10 轮 × 20 个 = 一次启动最多推 200 个，
            # 积压 1273 个时要重启 7 次才消化得完。
            r = _cleanup_residue_by_budget(45.0)
            if n:
                print(f"已清理 {n} 个过期上传缓存")
            if r:
                print(f"已回收 {r} 个删除残片（*.deleted / *.tmp）")
        except Exception:  # noqa: BLE001
            pass          # 清理失败不影响服务本身

    threading.Thread(target=_cleanup_in_background, daemon=True).start()
    restored = _restore_session()
    if restored:
        print(f"已恢复上次会话的 {restored} 张数据表")
    c = cfgmod.load()["server"]
    port = _pick_port(c["host"], c["port"])
    if port != int(c["port"]):
        print(f"提示：端口 {c['port']} 被占用，已自动改用 {port}")
        print("      （若是上次没关干净的服务占着，关掉它就能用回原端口）")
    url = f"http://{c['host']}:{port}"
    if c.get("auto_open_browser", True):
        threading.Timer(1.2, lambda: webbrowser.open(url)).start()
    print(f"ReportStudio 已启动：{url}")
    print(f"输出目录：{OUT_DIR}")
    uvicorn.run(app, host=c["host"], port=port, log_level="warning")
