"""看板路由：保存 / 渲染 / 预览 / 删除 / 记录每张图的拖拽尺寸。

存的是「要哪几个指标、哪几张图、怎么排」，不是算好的数字 ——
每次打开按当前数据重算，看板永远是新的。
"""
from __future__ import annotations

from fastapi import APIRouter

from core import dashboard as dash
from core.state import VENDOR_ECHARTS, _get, _session_ids

router = APIRouter(prefix="/api", tags=["dashboards"])

@router.get('/dashboards')
def dashboards_list():
    """已保存的看板清单。"""
    return {"items": dash.list_all()}


@router.post('/dashboard/save')
def dashboard_save(payload: dict):
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


@router.post('/dashboard/render')
def dashboard_render(payload: dict):
    """按当前数据重新渲染一个看板。"""
    did = (payload.get("id") or "").strip()
    cfg = dash.get(did)
    if cfg is None:
        return {"ok": False, "error": "没找到这个看板，可能已被删除。"}
    stat = _dashboard_render(cfg)
    return {"ok": True, "id": cfg.id, "name": cfg.name, **stat}


@router.post('/dashboard/preview')
def dashboard_preview(payload: dict):
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
        cfg.dataset_ids = _session_ids()
    ds_map = {}
    for did in cfg.dataset_ids:
        it = _get(did)
        if it:
            ds_map[did] = it
    datasets, missing = dash.resolve_datasets(cfg, ds_map)
    if not datasets:
        return {"ok": False,
                "error": "当前会话里没有数据表，先在左边导入文件或用 SQL 取数。"}
    stat = dash.render_preview(datasets, cfg, VENDOR_ECHARTS, missing)
    stat["missing"] = list(missing)
    return {"ok": True, "name": cfg.name, **stat}


@router.delete('/dashboard/{did}')
def dashboard_delete(did: str):
    ok = dash.delete(did)
    return {"ok": ok, "error": "" if ok else "没找到这个看板。"}


@router.post('/dashboard/resize')
def dashboard_resize(payload: dict):
    """记录用户在看板里拖出来的每张图尺寸（宽度 span / 高度 px）。

    为什么单独一个接口而不是塞进 save：拖动是高频的、且只动尺寸不动图表配置，
    混进 save 会让「保存看板」带上一堆无关字段、还容易和正在编辑的配置打架。
    这里只增量更新 cfg.sizes（按 block_id 对齐），再重新渲染那份 HTML，
    下回打开看板尺寸还在。
    """
    did = (payload.get("id") or "").strip()
    cfg = dash.get(did)
    if cfg is None:
        return {"ok": False, "error": "没找到这个看板，可能已被删除。"}
    sizes = payload.get("sizes") or {}
    if not isinstance(sizes, dict):
        return {"ok": False, "error": "sizes 格式不对。"}
    cleaned: dict = {}
    for k, v in sizes.items():
        if not isinstance(v, dict):
            continue
        span = v.get("span")
        height = v.get("height")
        try:
            span = int(span) if span else None
            height = int(height) if height else None
        except (TypeError, ValueError):
            continue
        if span is not None or height is not None:
            cleaned[k] = {"span": span, "height": height}
    if not cleaned:
        return {"ok": True, "id": did, "note": "没有可更新的尺寸"}
    cfg.sizes.update(cleaned)
    dash.save(cfg)
    # 重新渲染：让落盘的 HTML 也带上新尺寸（否则下次打开会回弹到旧大小）
    stat = _dashboard_render(cfg)
    return {"ok": True, "id": did, **stat}


def _dashboard_render(cfg) -> dict:
    """渲染看板并把「哪几张表没了」如实带回去。"""
    ds_map = {}
    for did in cfg.dataset_ids:
        it = _get(did)
        if it:
            ds_map[did] = it
    datasets, missing = dash.resolve_datasets(cfg, ds_map)
    names_for_missing = list(missing)
    stat = dash.render_to_file(datasets, cfg, VENDOR_ECHARTS, missing)
    stat["missing"] = names_for_missing
    stat["updated"] = cfg.updated
    if not datasets:
        stat["warning"] = ("这个看板引用的数据表都不在当前会话里，"
                           "已渲染成空看板。请把数据重新导入后再刷新。")
    return stat
