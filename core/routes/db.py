"""数据库只读抽取路由（SQLite / MySQL / PostgreSQL / SQL Server / Oracle）。

三重安全门禁在 core.db.guard / core.db.query 里，这里只做参数解析与响应拼装。
所有路由都是同步 def：底下是真实网络握手与查询，动辄几秒到几十秒，
写成 async def 会把整个服务卡住。
"""
from __future__ import annotations

import os
import uuid

from fastapi import APIRouter, File, UploadFile

from core import config as cfgmod
from core.state import MAX_UPLOAD_MB, TEMP_DIR, _keep, _safe

router = APIRouter(prefix="/api", tags=["db"])


@router.get("/db/kinds")
def db_kinds():
    """界面用：支持哪些数据库、各自要填什么字段。"""
    from core.db.connect import SPECS
    return {"ok": True, "readonly": True,
            "kinds": [{"kind": k, **v} for k, v in SPECS.items()],
            "limits": cfgmod.db_settings()}


@router.get("/db/connections")
def db_list_connections():
    from core.db import store as dbstore
    return {"ok": True, "items": dbstore.list_public()}


@router.post("/db/connections")
def db_save_connection(payload: dict):
    from core.db import store as dbstore
    ok, note, info = dbstore.save(payload)
    return {"ok": ok, "note": note, "connection": info, "items": dbstore.list_public()}


@router.delete("/db/connections")
def db_delete_connection(name: str = ""):
    from core.db import store as dbstore
    ok, note = dbstore.delete(name)
    return {"ok": ok, "note": note, "items": dbstore.list_public()}


@router.post("/db/test")
def db_test(payload: dict):
    """试连。失败也是 ok:false + 白话原因，不抛。"""
    from core.db import store as dbstore
    from core.db.query import test_connection
    cfg, err = dbstore.resolve(payload)
    if cfg is None:
        return {"ok": False, "error": err}
    cfg.connect_timeout = cfgmod.db_settings()["connect_timeout"]
    return test_connection(cfg)


@router.post("/db/scan")
def db_scan(payload: dict):
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


@router.post("/db/columns")
def db_columns(payload: dict):
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


@router.post("/db/build_sql")
def db_build_sql(payload: dict):
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


@router.post("/db/query")
def db_query(payload: dict):
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


@router.post("/db/upload")
def db_upload(file: UploadFile = File(...)):
    """上传一个 SQLite 库文件，返回可直接用的 path（省得手敲路径）。

    同步 def（配合 f.file.read 分块读，跟 /api/upload 同一个理由）：
    库文件上限 2 GB，一次性 await file.read() 会把整个文件塞进内存。
    """
    name = file.filename or "uploaded.db"
    ext = os.path.splitext(name)[1].lower()
    if ext not in (".db", ".sqlite", ".sqlite3", ".db3"):
        return {"ok": False, "error": f"只接受 SQLite 库文件（.db/.sqlite/.sqlite3/.db3），"
                                     f"这个是 {ext or '无扩展名'}"}
    cap = MAX_UPLOAD_MB * 1024 * 1024 * 10
    buf = bytearray()
    while True:
        chunk = file.file.read(4 * 1024 * 1024)
        if not chunk:
            break
        buf += chunk
        if len(buf) > cap:
            return {"ok": False, "error": "库文件太大（上限 2 GB）"}
    content = bytes(buf)
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
