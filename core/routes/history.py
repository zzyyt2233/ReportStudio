"""历史报告路由：列表（带缓存）、删除、按天数批量清理。

列表缓存的两级结构（_HIST_CACHE 按目录 mtime、_HIST_RESP 按整包指纹）
在 core/state.py 里，这里只负责读写；删报告的路径校验（必须正好是
OUT_DIR 下一层）是这个模块的安全线，改动前先读那段注释。
"""
from __future__ import annotations

import json
import os
import time
from datetime import datetime, timedelta

from fastapi import APIRouter

from core.fsutil import _dir_size, _rm_dir, _split_stamp
from core.state import (OUT_DIR, REPORTS, ROOT, _HIST_CACHE, _HIST_RESP,
                        _LOCK, _report_key, _STAMP_RE)

router = APIRouter(prefix="/api", tags=["history"])

def _read_meta(d: str) -> dict:
    """读报告目录里的 meta.json（生成时写的摘要）。老报告没有这个文件，返回空 dict。"""
    p = os.path.join(d, "meta.json")
    if not os.path.exists(p):
        return {}
    try:
        with open(p, "r", encoding="utf-8") as f:
            m = json.load(f)
        return m if isinstance(m, dict) else {}
    except Exception:
        return {}


def _outputs_fp() -> tuple:
    count, msum = 0, 0.0
    try:
        with os.scandir(OUT_DIR) as it:
            for e in it:
                if e.name.endswith((".deleted", ".tmp")) or not e.is_dir():
                    continue
                count += 1
                msum += e.stat().st_mtime
    except OSError:
        pass
    return (count, round(msum, 2))


def _history_entry(name: str, d: str) -> dict | None:
    """构建（或取缓存的）一个报告目录的列表条目。不是报告返回 None。"""
    try:
        mtime = os.path.getmtime(d)
    except OSError:
        return None
    with _LOCK:
        c = _HIST_CACHE.get(name)
        if c and c["mtime"] == mtime:
            return c["entry"]
    # 构建部分（stat + walk）在锁外跑，别堵住别的请求
    files = {}
    for fn in ("report.html", "report.md", "report.docx", "report.pdf"):
        p = os.path.join(d, fn)
        if os.path.exists(p):
            files[fn] = {"size": os.path.getsize(p),
                         "mtime": datetime.fromtimestamp(
                             os.path.getmtime(p)).strftime("%m-%d %H:%M")}
    # 没有 report.* 的目录不算报告（例如导出数据表用的 _exports）
    if not files:
        return None
    rname, stamp = _split_stamp(name)
    rel = os.path.relpath(d, ROOT).replace("\\", "/")
    entry = {
        # dir 统一 normpath：OUT_DIR 可能带正斜杠（配置或自动推导），
        # os.path.join 出来是 `E:/ReportStudio/outputs\x` 这种混合分隔符，
        # 而 /api/generate 回的是 normpath 后的纯反斜杠。同一个目录两种写法，
        # 调用方（前端、测试、别的接口）拿它做字符串比较就会对不上。
        "dir": os.path.normpath(d),
        "name": rname,
        "stamp": stamp,
        "files": {k: {"url": f"/{rel}/{k}", **v} for k, v in files.items()},
        "meta": _read_meta(d),
        "size": _dir_size(d),
    }
    with _LOCK:
        _HIST_CACHE[name] = {"mtime": mtime, "entry": entry}
    return entry


def _hist_cache_drop(name: str) -> None:
    with _LOCK:
        _HIST_CACHE.pop(name, None)
        _HIST_RESP["fp"] = None      # 有条目失效，整包缓存一并作废


@router.get('/history')
def history(q: str = "", limit: int = 50):
    """列出 outputs 下已有的报告，可以回看、重新导出、删除。

    q 走服务端过滤，不做前端本地筛：报告攒到几百份以后只回传前 N 条，
    前端再筛就筛不全 —— 用户搜一个明明存在的名字却查不到，最难解释。
    """
    if not os.path.isdir(OUT_DIR):
        return {"items": [], "total": 0, "filtered": False}
    kw = (q or "").strip().lower()
    try:
        limit = max(1, min(int(limit or 50), 300))
    except (TypeError, ValueError):
        limit = 50

    fp = _outputs_fp()
    with _LOCK:
        if _HIST_RESP["fp"] == fp:
            hit = _HIST_RESP["hits"].get((kw, limit))
            if hit is not None:
                return hit
        else:
            _HIST_RESP["fp"] = fp
            _HIST_RESP["hits"] = {}

    items, matched = [], 0
    # grand_* 是「outputs 里全部报告」的份数和体积，不受 q 过滤影响——
    # 前端要在搜索时也显示「共 1640 份 · 1.8 GB」，不然用户搜的时候
    # 看不出来存量到底有多大，也就想不到去清理。
    grand_total = grand_size = 0
    for name in sorted(os.listdir(OUT_DIR), reverse=True):
        # *.deleted / *.tmp 是删除或写入被中断后的残片，不该被当成报告列出来
        if name.endswith(".deleted") or name.endswith(".tmp"):
            continue
        d = os.path.join(OUT_DIR, name)
        if not os.path.isdir(d):
            continue
        entry = _history_entry(name, d)
        if entry is None:
            # 目录还在但已经不是报告了（或刚被删）——顺手把陈旧缓存清掉
            _hist_cache_drop(name)
            continue
        grand_total += 1
        grand_size += entry.get("size", 0)
        if kw and kw not in name.lower() and kw not in entry["name"].lower():
            continue
        matched += 1
        if len(items) >= limit:
            continue          # 已经够了，但继续数 matched，好让前端说「共 N 份」
        items.append(entry)
    resp = {"items": items, "total": matched, "filtered": bool(kw),
            "grand_total": grand_total, "grand_size": grand_size}
    with _LOCK:
        # 指纹在构建期间又变了（有报告增删）就不落缓存，宁缺毋滥
        if _HIST_RESP["fp"] == fp:
            _HIST_RESP["hits"][(kw, limit)] = resp
    return resp


@router.delete('/history')
def history_delete(dir: str = ""):
    """删掉一份报告（整个目录，含 charts 里的图）。

    路径是前端传上来的字符串，必须校验它**正好落在 OUT_DIR 的下一层** ——
    否则这就等于给了一个「删任意目录」的接口，一个 `../../` 就能出事。
    """
    if not dir:
        return {"ok": False, "error": "没指定要删的报告"}
    root = os.path.normcase(os.path.normpath(os.path.abspath(OUT_DIR)))
    target = os.path.normcase(os.path.normpath(os.path.abspath(dir)))
    # 必须**正好**是 OUT_DIR 的下一层：dirname 相等挡住 `../../`；
    # target == root 那一半挡住「把 outputs 整个删掉」——只比 dirname 的话，
    # 传输出目录本身时 dirname(OUT) 恰好等于 root，会被放行，
    # 一删就是所有报告一起没。
    if target == root or os.path.dirname(target) != root:
        return {"ok": False, "error": "这个路径不是一份报告，拒绝删除"}
    if not os.path.isdir(target):
        return {"ok": False, "error": "这份报告已经不在了"}
    with _LOCK:
        REPORTS.pop(_report_key(dir), None)   # 顺手清掉导出用的注册表，免得占内存
        _HIST_CACHE.pop(os.path.basename(target), None)   # 列表缓存同步清
    ok = _rm_dir(target)
    return {"ok": ok,
            "error": "" if ok else "删除失败（目录可能正被占用：关掉预览它的窗口再试）"}


@router.post('/history/delete')
def history_delete_post(payload: dict):
    """POST 别名：DELETE 方法会被部分代理 / 预览转发层拦下（405 或不转发）。
    前端统一走这个入口，逻辑与 DELETE /api/history 完全一致。"""
    return history_delete(str(payload.get("dir") or ""))


@router.post('/history/cleanup')
def history_cleanup(payload: dict):
    """按天数批量清理旧报告。

    两步走：apply=False 只统计不删，前端拿这个数弹确认框；
    apply=True 才真删。删报告不可逆，必须让用户在看到
    「将删 N 份 · X GB」之后自己点头，不能一个按钮直接删。

    安全线：
    - days 下限 7 天——低于这个误删刚生成的报告的风险，比省下的空间值钱；
    - 只碰目录名带标准时间戳的目录，没有时间戳的（老格式 / 手动建的）一律不动，
      宁可留着也不能误删；
    - 60 秒时间预算：存量上千份时全删完可能要几分钟，超预算就停，
      把没处理到的留在 left 里让用户再点一次。
    """
    try:
        days = int(payload.get("days") or 0)
    except (TypeError, ValueError):
        return {"ok": False, "error": "天数得是个数字"}
    if days < 7:
        return {"ok": False, "error": "最少保留 7 天——时间太近的报告不该被批量清掉"}
    apply = bool(payload.get("apply"))
    cut = datetime.now() - timedelta(days=days)
    if not os.path.isdir(OUT_DIR):
        return {"ok": True, "removed": 0, "freed": 0, "left": 0}

    removed = freed = 0
    left = 0
    deadline = time.monotonic() + 60.0
    for name in os.listdir(OUT_DIR):
        if name.endswith(".deleted") or name.endswith(".tmp"):
            continue
        d = os.path.join(OUT_DIR, name)
        if not os.path.isdir(d):
            continue
        m = _STAMP_RE.match(name)
        if not m:
            continue
        try:
            dt = datetime.strptime(m.group(2) + m.group(3), "%Y%m%d%H%M%S")
        except ValueError:
            continue
        if dt >= cut:
            continue
        if time.monotonic() > deadline:
            left += 1        # 预算用光，剩下的这轮不碰
            continue
        size = _dir_size(d)
        if not apply:
            removed += 1
            freed += size
            continue
        if _rm_dir(d):
            removed += 1
            freed += size
            with _LOCK:
                _HIST_CACHE.pop(name, None)
                REPORTS.pop(_report_key(d), None)
        else:
            left += 1
    return {"ok": True, "removed": removed, "freed": freed, "left": left}
