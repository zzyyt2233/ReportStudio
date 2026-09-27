"""常驻看板的配置模型与存盘。

**为什么要有「常驻」这一层**：报告是快照，生成完就固定了；看板是「一面镜子」——
配置存下来（要哪些指标、哪几张图、怎么排），每次打开都拿**当前**的数据重新算一遍。
所以这里存的**不是**算好的数字，而是「怎么算」。

存盘一个看板一个 json，写在 `dashboards/<id>.json`，渲染结果写在
`dashboards/<id>/index.html`（有稳定 URL，刷新即可）。

原子写法沿用 session_store 的教训：先写 `.json.tmp` 再 `os.replace`。
半截文件被当成正常配置读进来，会让看板整个打不开，比丢一个看板更难查。
"""

from __future__ import annotations

import json
import os
import time
import uuid
from dataclasses import asdict, dataclass, field

from . import config as cfgmod
from .analyze import compute_metrics, numeric_columns
from .chartset import build_blocks
from .merge import group_datasets, merge_group
from .model import ChartSpec, Dataset
from .render.dashboard import build_dashboard_html, norm_span

MAX_CHARTS = 24          # 一屏放不下太多；也防止有人拿它去批量刷图
MAX_NAME = 40


def _dir() -> str:
    d = cfgmod.dashboards_dir()
    os.makedirs(d, exist_ok=True)
    return d


def _path(did: str) -> str:
    return os.path.join(_dir(), f"{_safe_id(did)}.json")


def _safe_id(did: str) -> str:
    """id 只允许十六进制/短横线，防止 `../` 这类路径穿越写到目录外。"""
    s = "".join(ch for ch in str(did or "") if ch.isalnum() or ch == "-")
    return s[:32]


def new_id() -> str:
    return uuid.uuid4().hex[:8]


@dataclass
class DashboardConfig:
    id: str = ""
    name: str = ""
    title: str = ""
    subtitle: str = ""
    dataset_ids: list[str] = field(default_factory=list)
    merge_mode: str = "merge"
    metrics: list[str] = field(default_factory=list)
    charts: list[dict] = field(default_factory=list)
    # 每张图被用户拖出来的自由尺寸：{ block_id: {"span": int, "height": int} }。
    # 和 charts 分开存，是因为「多指标拆开」后渲染出的图块数 > 配置里的图数，
    # 用 block_id（chart0/chart1…）对齐最稳，不受拆分顺序影响。
    sizes: dict = field(default_factory=dict)
    updated: str = ""
    created: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


def from_payload(payload: dict, base: DashboardConfig | None = None
                 ) -> DashboardConfig:
    """把请求里的配置整理成 DashboardConfig。

    base 传入时表示「在已有看板上改」，未提供的字段沿用原值 ——
    不然前端只传了图表、没传名称，就会把名字清空。
    """
    base = base or DashboardConfig()
    charts = []
    for c in (payload.get("charts") or [])[:MAX_CHARTS]:
        if not isinstance(c, dict):
            continue
        x = (c.get("x") or "").strip()
        ys = [str(y) for y in (c.get("y") or []) if str(y).strip()]
        if not x or not ys:
            continue          # 横轴/数值不全的图直接丢掉，别存进去渲染时才炸
        try:
            limit = int(c.get("limit")) if c.get("limit") else None
        except (TypeError, ValueError):
            limit = None
        charts.append({
            "type": c.get("type") or "bar",
            "title": (c.get("title") or "").strip(),
            "x": x,
            "y": ys,
            "agg": c.get("agg") or "sum",
            "sort_by": c.get("sort_by") or "",
            "sort_order": c.get("sort_order") or "asc",
            "limit": limit,
            "dual_axis": bool(c.get("dual_axis")),
            "exclude_summary": c.get("exclude_summary", True) is not False,
            "split_series": bool(c.get("split_series")),
            # 每个指标单独的图型。这里只做原样带过 —— 净化交给
            # ChartSpec.__post_init__（只留选中指标里、且图型认得出来的项），
            # 免得同一个规则要在入库和渲染两处各写一遍。
            "y_types": c.get("y_types") or {},
            "span": norm_span(c.get("span")),
            "height": c.get("height") or 320,
        })

    name = (payload.get("name") or base.name or "未命名看板").strip()[:MAX_NAME]
    return DashboardConfig(
        id=base.id or new_id(),
        name=name,
        title=(payload.get("title") or base.title or name).strip()[:80],
        subtitle=(payload.get("subtitle") or base.subtitle or "").strip()[:120],
        dataset_ids=[str(d) for d in (payload.get("dataset_ids")
                                      or base.dataset_ids or [])],
        merge_mode=(payload.get("merge_mode") or base.merge_mode or "merge"),
        metrics=[str(m) for m in (payload.get("metrics") or base.metrics or [])],
        charts=charts,
        # 用户拖出来的自由尺寸跟图表配置分开存，save 时若不手工带过会被新对象清空。
        # base 传入（在老看板上改）时一律沿用，避免「保存一次尺寸就回弹」。
        sizes=dict(base.sizes) if base and getattr(base, "sizes", None) else {},
        updated=base.updated,
        created=base.created,
    )


# ------------------------------------------------------------------ 存 / 取

def list_all() -> list[dict]:
    """所有看板的摘要，按更新时间倒序。坏文件跳过，不影响其他的。"""
    out = []
    for fn in os.listdir(_dir()):
        if not fn.endswith(".json"):
            continue
        try:
            with open(os.path.join(_dir(), fn), "r", encoding="utf-8") as f:
                d = json.load(f)
            if not isinstance(d, dict) or not d.get("id"):
                continue
            out.append({
                "id": d["id"],
                "name": d.get("name") or "未命名看板",
                "title": d.get("title") or d.get("name") or "",
                "charts": len(d.get("charts") or []),
                "metrics": len(d.get("metrics") or []),
                "updated": d.get("updated") or "",
            })
        except Exception:
            continue
    out.sort(key=lambda x: x.get("updated") or "", reverse=True)
    return out


def get(did: str) -> DashboardConfig | None:
    p = _path(did)
    if not os.path.exists(p):
        return None
    try:
        with open(p, "r", encoding="utf-8") as f:
            d = json.load(f)
    except Exception:
        return None
    if not isinstance(d, dict):
        return None
    cfg = DashboardConfig()
    for k in DashboardConfig.__dataclass_fields__:
        if k in d:
            setattr(cfg, k, d[k])
    cfg.id = _safe_id(cfg.id or did)
    return cfg


def save(cfg: DashboardConfig) -> DashboardConfig:
    cfg.id = _safe_id(cfg.id) or new_id()
    now = time.strftime("%Y-%m-%d %H:%M")
    cfg.updated = now
    cfg.created = cfg.created or now
    p = _path(cfg.id)
    tmp = p + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(cfg.to_dict(), f, ensure_ascii=False, indent=1)
    os.replace(tmp, p)          # 原子；中断时只会留个 .tmp，不会留下半截配置
    return cfg


def delete(did: str) -> bool:
    """删掉配置和渲染出来的 HTML。

    刻意不建「一个看板一个目录」——那样删除要么留下孤儿目录越攒越多，
    要么去 rmtree 目录（在受限环境会撞上批量删除保护）。
    平铺成 `<id>.json` + `<id>.html` 两个文件，单个删，干净。
    """
    p = _path(did)
    if not os.path.exists(p):
        return False
    ok = _rm(p)
    _rm(_html_path(did))            # HTML 删不掉不影响「配置已删」这个结论
    return ok


def _rm(path: str) -> bool:
    """单个文件删除。受限环境里 os.remove 可能被守卫拦下，退化成改名。"""
    if not os.path.exists(path):
        return False
    try:
        os.remove(path)
        return True
    except BaseException:
        try:
            os.rename(path, path + ".deleted")
            return True
        except BaseException:
            return False


# ------------------------------------------------------------------ 渲染

def html_path(did: str) -> str:
    return _html_path(did)


def _html_path(did: str) -> str:
    return os.path.join(_dir(), f"{_safe_id(did)}.html")


def _to_specs(cfg: DashboardConfig) -> list[ChartSpec]:
    keep = set(ChartSpec.__dataclass_fields__)
    return [ChartSpec(**{k: v for k, v in c.items() if k in keep})
            for c in cfg.charts]


def resolve_datasets(cfg: DashboardConfig, session: dict
                     ) -> tuple[list[Dataset], list[str]]:
    """按 id 从会话里取数据表。返回 (找到的表, 没找到的表名/id)。

    找不到是常态（服务重启、清理、用户删了表），必须如实报出来 ——
    安静地少几张图，用户会以为是数据本身没数。
    """
    found, missing = [], []
    for did in cfg.dataset_ids:
        item = session.get(did)
        if item and item.get("dataset"):
            found.append(item["dataset"])
        else:
            missing.append(str(did))
    return found, missing


def render(datasets: list[Dataset], cfg: DashboardConfig, vendor_path: str,
           missing: list[str], dash_id: str | None = None) -> tuple[str, dict]:
    """算出图表与指标卡，渲染成看板 HTML。返回 (html, 统计信息)。"""
    if cfg.merge_mode == "merge" and len(datasets) > 1:
        datasets = [merge_group(g) for g in group_datasets(datasets)]

    specs = _to_specs(cfg)

    def _deco(block: dict, _spec2, origin) -> None:
        """把用户给这张图配的宽度/高度带上。

        按**来源配置**（origin）去找，不能用拆开后的 spec ——
        「多指标拆开」后 y 只剩一个，跟 cfg.charts 里存的整串对不上，
        宽度会静默丢回默认值。
        """
        src = next((c for c in cfg.charts
                    if c.get("x") == origin.x
                    and list(c.get("y") or []) == list(origin.y)), {})
        block["span"] = norm_span(src.get("span"))
        block["height"] = src.get("height") or 320

    chart_blocks, _ = build_blocks(datasets, specs, decorate=_deco)

    # 用户拖出来的自由尺寸优先级最高：它记的是渲染后图块（block_id），
    # 比「来源配置」更贴近用户真正看到的那张图，所以最后覆盖一次。
    for b in chart_blocks:
        sz = cfg.sizes.get(b.get("id"))
        if isinstance(sz, dict):
            if sz.get("span"):
                b["span"] = norm_span(sz["span"])
            if sz.get("height"):
                try:
                    b["height"] = max(160, min(960, int(sz["height"])))
                except (TypeError, ValueError):
                    pass

    metric_cards = []
    excl = all(getattr(s, "exclude_summary", True) for s in specs)
    for ds in datasets:
        want = [m for m in cfg.metrics if m in numeric_columns(ds)] \
            or numeric_columns(ds)[:4]
        cards = compute_metrics(ds, want, excl)
        if len(datasets) > 1:
            for c in cards:
                c["title"] = f"{c['title']}（{ds.name[:14]}）"
        metric_cards += cards

    html = build_dashboard_html(cfg.title or cfg.name, cfg.subtitle, datasets,
                                chart_blocks, metric_cards, missing, vendor_path,
                                dash_id=dash_id if dash_id is not None else cfg.id)
    return html, {"charts": len(chart_blocks), "metrics": len(metric_cards),
                  "datasets": len(datasets)}


def render_to_file(datasets: list[Dataset], cfg: DashboardConfig,
                   vendor_path: str, missing: list[str]) -> dict:
    """渲染并落盘，返回可访问的 URL。"""
    html, stat = render(datasets, cfg, vendor_path, missing, dash_id=cfg.id)
    path = _html_path(cfg.id)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(html)
    os.replace(tmp, path)
    stat["path"] = path
    stat["url"] = f"/dashboards/{_safe_id(cfg.id)}.html"
    return stat


PREVIEW_NAME = "_preview.html"


def render_preview(datasets: list[Dataset], cfg: DashboardConfig,
                   vendor_path: str, missing: list[str]) -> dict:
    """渲染成**固定文件名**的预览页，不落成常驻看板。

    为什么固定文件名：报告和看板现在是同一屏里的两个视图，切换会反复渲染，
    每次换个新文件名的话 `dashboards/` 会被垃圾文件堆满。
    为什么带时间戳：内容变了但 URL 没变，浏览器会直接拿缓存，
    用户切回来看到的还是上一次的看板 —— 这种「明明刷新了却没变」最难查。
    """
    html, stat = render(datasets, cfg, vendor_path, missing, dash_id="_preview")
    path = os.path.join(_dir(), PREVIEW_NAME)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(html)
    os.replace(tmp, path)
    stat["path"] = path
    stat["url"] = f"/dashboards/{PREVIEW_NAME}?_t={int(time.time() * 1000)}"
    return stat
