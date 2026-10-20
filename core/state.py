"""跨请求共享状态与会话数据表的内存管理。

app.py 原来把这些（SESSION / REPORTS / _HIST_CACHE 和各种辅助函数）和
HTTP 处理函数混在一个文件里，导致：想拆路由就得先解决「这些符号归谁」。
现在集中到这里，路由模块从 core.state import，app.py 只留装配与 HTTP 层。

关键不变量（改这里前先读）：
- SESSION 是 LRU：重对象（整表行 + 网格）只留最近用的 MAX_SESSION_DATASETS 份；
- PERSISTED 是「落过盘、可重载」的全部 id，被淘汰的表靠它从 session_store 找回；
- 超过 50000 行的大表 session_store.save 返回 False，不进 PERSISTED，
  也就不会被淘汰——只能靠内存保住，这是刻意取舍（避免静默丢数据）。
"""
from __future__ import annotations

import os
import re
import threading
from collections import OrderedDict

from core import config as cfgmod
from core import session_store
from core.model import Dataset

# ---- 目录与常量 ----
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WEB_DIR = os.path.join(ROOT, "web")
OUT_DIR = cfgmod.outputs_dir()
TEMP_DIR = cfgmod.temp_dir()
VENDOR_ECHARTS = os.path.join(WEB_DIR, "vendor", "echarts.min.js")
LOG_DIR = os.path.join(ROOT, "logs")
SESSION_TRASH = os.path.join(ROOT, "session_trash")
MAX_SESSION_DATASETS = int(os.environ.get("RS_MAX_SESSION_DATASETS", "40"))
MAX_UPLOAD_MB = 60
RESIDUE_TMP_DAYS = 3
_STAMP_RE = re.compile(r"^(.*)_(\d{8})_(\d{6})$")

# ---- 注册表 ----
# dataset_id -> {"dataset": Dataset, "grid": list[list]}
SESSION: "OrderedDict[str, dict]" = OrderedDict()
# 落过盘、被淘汰后也能从 session_store 重载回来的全部 id
PERSISTED: set[str] = set()
# 输出目录 -> 生成报告所需的一切，供 Word / PDF 导出复用
REPORTS: dict[str, dict] = {}
# 目录名 -> {"mtime": float, "entry": dict}；整包响应指纹缓存见 _HIST_RESP
_HIST_CACHE: dict[str, dict] = {}
_HIST_RESP: dict = {"fp": None, "hits": {}}

# 保护上面几个注册表 + SESSION 的遍历。
#
# 为什么需要：这些端点原来是 async def，跑在单线程事件循环里，
# 遍历 SESSION 时不可能有别的请求插进来。改成同步 def 后由线程池并发执行，
# 「list_datasets 正在遍历、upload 正在往里塞」就可能同时发生，
# CPython 会直接抛 RuntimeError: dictionary changed size during iteration。
#
# 锁的粒度刻意只包住注册表的读写，**不包住**解析、渲染、SQL 这些重活 ——
# 否则并发度还是 1，改动就白做了。重活本身不共享状态，可以放心并行。
_LOCK = threading.RLock()

# 同步端点跑在 anyio 的默认线程池里，默认容量 40。对本地单机工具够用，
# 但 40 个 /api/generate 同时进来会要了命：每个请求都带着一份 pandas 数据集
# 和 matplotlib 的字体缓存，上百 MB 一个，40 个就是几个 GB，直接把机器拖进 swapping。
# 收到 4 个既能让两个请求真正并行（实测两个出图 0.52 倍耗时），
# 又不会让内存失控。多出来的请求排队等就行，反正它们本来也要等 CPU。
_MAX_WORKERS = 4


def _session_snapshot() -> list[tuple[str, dict]]:
    """取 SESSION 的快照，供遍历用。

    有了快照，遍历期间别人怎么改 SESSION 都无所谓；
    加锁只是为了拷贝这一刻的一致性，不为了把遍历全程串行化。
    """
    with _LOCK:
        return list(SESSION.items())


def _session_ids() -> list[str]:
    # 已落盘但被内存淘汰的也算「在场」：看板缺表提示、预览自动选表都靠这个并集
    with _LOCK:
        return list(PERSISTED | set(SESSION.keys()))


def _available_count() -> int:
    with _LOCK:
        return len(PERSISTED | set(SESSION.keys()))


def _report_key(out_dir: str) -> str:
    """报告注册表的键：路径统一归一化。

    同一个目录可能被写成 `E:/ReportStudio/outputs\\x` 或 `E:\\ReportStudio\\outputs/x`
    —— 都指向同一个地方，但作为 dict 键是三个不同的字符串。不归一化的话，
    调用方只要把分隔符换个写法，导出就会报「这份报告已过期」，很难排查。
    """
    return os.path.normcase(os.path.normpath(out_dir))


def _safe(name: str) -> str:
    return re.sub(r'[\\/:*?"<>|]+', "_", name)[:60]


def _fallback_grid(ds: Dataset) -> list[list]:
    """拿不到原始排版时（文档/PDF/图片），用列名+数据行拼一个等价网格。"""
    grid = [[c.name for c in ds.columns]]
    for r in ds.rows:
        grid.append([r.get(c.name) for c in ds.columns])
    return grid


def _keep(datasets: list[tuple]):
    # 落盘放在锁外：json.dump 大表要几百毫秒，锁内做会把并发请求全堵住。
    # 先把要写的内容备好，再进锁只做「登记 + 取出引用 + 淘汰」。
    prepared = []
    for ds, grid in datasets:
        g = grid if grid is not None else _fallback_grid(ds)
        prepared.append((ds, g))
    if session_store.enabled():
        for ds, g in prepared:
            # 只有真正落盘成功的才标记可重载；超过上限的大表 save 会返回 False，
            # 这类表不能进淘汰逻辑，否则会静默丢数据。
            if session_store.save(ds, g):
                with _LOCK:
                    PERSISTED.add(ds.id)
    with _LOCK:
        for ds, g in prepared:
            SESSION[ds.id] = {"dataset": ds, "grid": g}
            SESSION.move_to_end(ds.id)
        _evict_if_needed()


def _touch(did: str) -> None:
    with _LOCK:
        if did in SESSION:
            SESSION.move_to_end(did)


def _evict_if_needed() -> None:
    """超过上限就淘汰最久没用过的、且已落盘的项。

    只在内存里操作，磁盘上的会话文件不动 —— 被淘汰的重对象随时能从
    session_store 重载回来。没有可淘汰的落盘项（全是未落盘的大表）就停，
    不去碰那些只能靠内存保住的数据。
    """
    while len(SESSION) > MAX_SESSION_DATASETS:
        victim = None
        for k in SESSION.keys():
            if k in PERSISTED:
                victim = k
                break
        if victim is None:
            break
        SESSION.pop(victim)


def _restore_session() -> int:
    """启动时只把已落盘的数据集登记进 PERSISTED（轻量），不把整表行数据
    一次性灌进内存。

    重对象按需从 session_store 重载（见 _resolve / _get），启动不再随历史
    数据集增多而吃内存；列表展示用 meta_all 补回名称 / 列 / 行数。
    """
    if not session_store.enabled():
        return 0
    n = 0
    with _LOCK:
        for m in session_store.meta_all():
            PERSISTED.add(m["id"])
            n += 1
    return n


def _meta_dict(did: str) -> dict | None:
    """被内存淘汰的数据集，列表里只给展示字段（名称 / 列 / 行数），不加载整表行。"""
    parsed = session_store.load(did)
    if parsed is None:
        with _LOCK:
            PERSISTED.discard(did)
        return None
    ds, _ = parsed
    return ds.to_dict(preview_limit=0)


def _get(did: str) -> dict | None:
    """取单张表的内存条目；不在内存但已落盘则重载回来。拿不到返回 None。"""
    if not did:
        return None
    with _LOCK:
        it = SESSION.get(did)
        if it is not None:
            SESSION.move_to_end(did)
            return it
    if did in PERSISTED:
        return _reload(did)
    return None


def _reload(did: str) -> dict | None:
    """从 session_store 把重对象读回内存。失败（文件没了 / 解析错）就摘掉
    PERSISTED 标记，避免之后反复尝试一个已经不存在的数据集。"""
    parsed = session_store.load(did)
    if parsed is None:
        with _LOCK:
            PERSISTED.discard(did)
        return None
    ds, grid = parsed
    with _LOCK:
        SESSION[did] = {"dataset": ds,
                        "grid": grid if grid is not None else _fallback_grid(ds)}
        SESSION.move_to_end(did)
        _evict_if_needed()
    return SESSION.get(did)


def _resolve(ids: list[str]) -> list[Dataset]:
    """按 id 取数据集；在内存的直接给，被淘汰但已落盘的从 session_store 重载。

    常态下反复上传会让历史数据集越来越多，SESSION 用 LRU 收口只留最近用的
    重对象，这里保证「取得到」不依赖它正好在内存里。
    """
    out = []
    for i in (ids or []):
        it = _get(i)
        if it is not None:
            out.append(it["dataset"])
    return out
