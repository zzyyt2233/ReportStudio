"""取数：执行只读 SQL、窥探库表结构、把结果转成 Dataset。

一个刻意的设计：查询结果不直接拼 Dataset，而是先还原成「表头 + 数据行」的网格，
再交给 parsers/tabular.build_dataset 构造。这样做的收益是——
字段类型推断、缺失值占位符、百分比列、标志列、重复列名去重、
「约1.2万」告警这些既有加固，对数据库取数**全部免费生效**，
和导入 Excel/CSV 的行为完全一致，不会因为来源不同而两套逻辑。
"""

from __future__ import annotations

import datetime as _dt
import decimal
import re
import time
from typing import Any

from ..model import Dataset
from ..parsers.tabular import build_dataset
from .connect import DBConfig, SPECS, Conn, connect
from .guard import SQLRejected, check

# 单次取数默认最多带回多少行。表太大时宁可截断并明确告知，也不把内存吃爆。
DEFAULT_MAX_ROWS = 20000
DEFAULT_TIMEOUT_MS = 30000

_SAFE_NAME_RE = re.compile(r"^[\w\u4e00-\u9fff #$@.\-]+$", re.UNICODE)


# ———————————————————————— 标识符 / 字面量 ————————————————————————

def quote_ident(kind: str, name: str) -> str:
    """按方言给标识符加引号。表名里带中文、空格、关键字时必需。"""
    n = str(name)
    if kind == "mysql":
        return "`" + n.replace("`", "``") + "`"
    if kind == "mssql":
        return "[" + n.replace("]", "]]") + "]"
    return '"' + n.replace('"', '""') + '"'


def _literal(s: Any) -> str:
    """拼一个 SQL 字符串字面量（用于内省查询的常量，不是用户输入拼接）。"""
    return "'" + str(s).replace("'", "''") + "'"


def qualified(kind: str, table: str, schema: str = "") -> str:
    if schema:
        return f"{quote_ident(kind, schema)}.{quote_ident(kind, table)}"
    return quote_ident(kind, table)


def safe_ident(name: str) -> bool:
    """表名/列名的形状检查：挡掉明显的注入尝试（内省查询里会拼进 SQL）。"""
    if not name or len(name) > 180:
        return False
    return bool(_SAFE_NAME_RE.match(str(name)))


# ———————————————————————— 值归一 ————————————————————————

def _norm_value(v: Any) -> Any:
    """把驱动返回的 Python 对象转成 build_dataset 能吃的类型。

    Decimal / datetime / bytes 不处理的话，后面类型推断会认不出来，
    整列被当成文本，白丢一列可画图的数据。
    """
    if v is None:
        return None
    if isinstance(v, bool):
        return 1 if v else 0
    if isinstance(v, (int, float)):
        return v
    if isinstance(v, decimal.Decimal):
        # 保留精度到 float 足够画图；NaN/Infinity 这类给 None
        try:
            f = float(v)
        except Exception:
            return str(v)
        return None if (f != f or f in (float("inf"), float("-inf"))) else f
    if isinstance(v, (_dt.datetime, _dt.date, _dt.time)):
        return v.isoformat(sep=" ") if isinstance(v, _dt.datetime) else v.isoformat()
    if isinstance(v, _dt.timedelta):
        return v.total_seconds()
    if isinstance(v, (bytes, bytearray, memoryview)):
        try:
            return bytes(v).decode("utf-8")
        except Exception:
            return f"<二进制 {len(bytes(v))} 字节>"
    # Oracle 的 LOB / 某些驱动的结果包装对象：有 read() 就试着自己读出来
    reader = getattr(v, "read", None)
    if callable(reader):
        try:
            inner = _norm_value(reader())
            if isinstance(inner, (str, int, float)) or inner is None:
                return inner
        except Exception:
            pass
    # 兜底：任何认不出来的对象都转字符串，绝不让它流到 JSON 序列化那一步炸掉整个响应
    try:
        return str(v)
    except Exception:
        return "<无法显示的值>"


# ———————————————————————— 执行查询 ————————————————————————

def run_sql(conn: Conn, sql: str, max_rows: int = DEFAULT_MAX_ROWS) -> dict:
    """执行一条**已经过闸门校验**的 SQL，最多取 max_rows 行。

    返回 {columns, rows, truncated, elapsed_ms}。行数上限用 fetchmany 控制：
    不去搬数据，但 DB 侧仍会执行整条语句 —— 所以语句超时（连接层）是配套的必需项。
    """
    cur = conn.raw.cursor()
    try:
        try:
            cur.arraysize = min(1000, max(1, max_rows))   # 批量回传，减少网络往返
        except Exception:
            pass
        t0 = time.time()
        cur.execute(sql)
        elapsed = (time.time() - t0) * 1000

        desc = cur.description or []
        columns = [str(d[0]) for d in desc] if desc else []

        if not columns:
            return {"columns": [], "rows": [], "truncated": False,
                    "elapsed_ms": round(elapsed, 1)}

        fetched = cur.fetchmany(max_rows + 1)
        truncated = len(fetched) > max_rows
        rows = [[_norm_value(v) for v in r] for r in fetched[:max_rows]]
        return {"columns": columns, "rows": rows, "truncated": truncated,
                "elapsed_ms": round((time.time() - t0) * 1000, 1)}
    finally:
        try:
            cur.close()
        except Exception:
            pass


def run_query(cfg: DBConfig, sql: str, max_rows: int = DEFAULT_MAX_ROWS,
              timeout_ms: int = DEFAULT_TIMEOUT_MS) -> dict:
    """带闸门 + 自动开关连接的完整取数。SQL 违规抛 SQLRejected。"""
    clean = check(sql)
    conn = connect(cfg, timeout_ms)
    try:
        out = run_sql(conn, clean, max_rows)
        out["readonly_enforced"] = conn.readonly_enforced
        out["warnings"] = list(conn.warnings)
        out["source"] = cfg.describe()
        return out
    finally:
        conn.close()


def run_to_dataset(cfg: DBConfig, sql: str, name: str = "",
                   max_rows: int = DEFAULT_MAX_ROWS,
                   timeout_ms: int = DEFAULT_TIMEOUT_MS) -> tuple[Dataset, list[list], dict]:
    """取数并转成 Dataset，可直接进出图/报告流程。

    返回 (Dataset, 网格, 元信息)。网格留着是为了让「改表头行/改列类型」可用。
    """
    out = run_query(cfg, sql, max_rows, timeout_ms)
    ds_name = name.strip() or f"SQL 查询结果 · {cfg.name or cfg.kind}"
    grid = [out["columns"]] + out["rows"]

    ds = build_dataset(grid, name=ds_name, source_type="database", header_row=0)

    extra = [f"数据来源：{out['source']}"]
    if not out["readonly_enforced"]:
        extra.append("该连接未能启用数据库级只读，仅靠语句闸门拦截写操作")
    if out["truncated"]:
        extra.append(
            f"查询结果超过 {max_rows} 行，只取回了前 {max_rows} 行"
            "（可在 SQL 里自己加聚合或 LIMIT 缩小范围）"
        )
    else:
        extra.append(f"共取回 {len(out['rows'])} 行")

    ds.warnings = ds.warnings + extra + list(out.get("warnings") or [])
    ds.raw_text = f"-- 数据来源：{out['source']}\n{sql.strip()}\n"
    if not ds.total_rows:
        ds.total_rows = len(out["rows"])

    meta = {
        "source": out["source"],
        "elapsed_ms": out["elapsed_ms"],
        "truncated": out["truncated"],
        "readonly_enforced": out["readonly_enforced"],
        "row_count": len(out["rows"]),
        "columns": out["columns"],
    }
    return ds, grid, meta


# ———————————————————————— 库表内省 ————————————————————————

def list_tables(conn: Conn, limit: int = 500) -> list[dict]:
    """列出可读的表/视图。返回 [{schema, name, type}]。"""
    kind = conn.kind
    if kind == "sqlite":
        sql = ("SELECT NULL, name, type FROM sqlite_master "
               "WHERE type IN ('table','view') AND name NOT LIKE 'sqlite_%' "
               f"ORDER BY name LIMIT {int(limit)}")
    elif kind == "mysql":
        sql = ("SELECT table_schema, table_name, table_type FROM information_schema.tables "
               "WHERE table_schema NOT IN ('information_schema','mysql','performance_schema','sys') "
               f"ORDER BY table_schema, table_name LIMIT {int(limit)}")
    elif kind == "postgres":
        sql = ("SELECT table_schema, table_name, table_type FROM information_schema.tables "
               "WHERE table_schema NOT IN ('pg_catalog','information_schema') "
               f"ORDER BY table_schema, table_name LIMIT {int(limit)}")
    elif kind == "mssql":
        sql = ("SELECT TABLE_SCHEMA, TABLE_NAME, TABLE_TYPE FROM INFORMATION_SCHEMA.TABLES "
               f"ORDER BY TABLE_SCHEMA, TABLE_NAME")
    else:  # oracle
        sql = ("SELECT owner, table_name, 'TABLE' FROM all_tables "
               "WHERE owner NOT IN ('SYS','SYSTEM','XDB','MDSYS','CTXSYS','ORDSYS',"
               "'OUTLN','DBSNMP','WMSYS','APPQOSSYS','DVSYS','GSMADMIN_INTERNAL',"
               "'AUDSYS','OJVMSYS','LBACSYS','DBSFWUSER','REMOTE_SCHEDULER_AGENT') "
               "AND ROWNUM <= " + str(int(limit)) + " ORDER BY owner, table_name")

    try:
        out = run_sql(conn, sql, max_rows=limit)
    except Exception as e:
        if kind == "oracle":   # 权限不够看 all_tables 时退回「自己拥有的表」
            out = run_sql(conn, "SELECT NULL, table_name, 'TABLE' FROM user_tables "
                                "ORDER BY table_name", max_rows=limit)
        else:
            raise RuntimeError(f"读表清单失败：{e}") from e

    items = []
    for r in out["rows"]:
        schema = r[0] if len(r) > 0 else None
        name = r[1] if len(r) > 1 else None
        ttype = (r[2] if len(r) > 2 else "TABLE") or "TABLE"
        if not name:
            continue
        items.append({"schema": str(schema) if schema not in (None, "") else "",
                      "name": str(name),
                      "type": "view" if "VIEW" in str(ttype).upper() else "table"})
    return items


def describe_table(conn: Conn, table: str, schema: str = "") -> list[dict]:
    """列定义。返回 [{name, type, nullable, key}]。"""
    if not safe_ident(table) or (schema and not safe_ident(schema)):
        raise RuntimeError(f"表名不合法：{schema + '.' if schema else ''}{table}")
    kind = conn.kind

    if kind == "sqlite":
        out = run_sql(conn, f"PRAGMA table_info({_literal(table)})", max_rows=500)
        cols = []
        for r in out["rows"]:
            cols.append({"name": str(r[1]), "type": str(r[2] or ""),
                         "nullable": not bool(r[3]), "key": "PK" if r[5] else ""})
        return cols

    if kind == "mysql":
        cond = f"table_schema = {_literal(schema)}" if schema else "table_schema = DATABASE()"
        sql = ("SELECT column_name, column_type, is_nullable, column_key, column_comment "
               f"FROM information_schema.columns WHERE {cond} "
               f"AND table_name = {_literal(table)} ORDER BY ordinal_position")
        out = run_sql(conn, sql, max_rows=500)
        return [{"name": str(r[0]), "type": str(r[1]),
                 "nullable": str(r[2]).upper() == "YES",
                 "key": str(r[3] or ""),
                 "comment": str(r[4] or "")} for r in out["rows"]]

    if kind == "postgres":
        cond = f"table_schema = {_literal(schema)}" if schema else "table_schema NOT IN ('pg_catalog','information_schema')"
        sql = ("SELECT column_name, data_type, is_nullable FROM information_schema.columns "
               f"WHERE {cond} AND table_name = {_literal(table)} ORDER BY ordinal_position")
        out = run_sql(conn, sql, max_rows=500)
        return [{"name": str(r[0]), "type": str(r[1]),
                 "nullable": str(r[2]).upper() == "YES", "key": ""} for r in out["rows"]]

    if kind == "mssql":
        cond = f"TABLE_SCHEMA = {_literal(schema)}" if schema else "1=1"
        sql = ("SELECT COLUMN_NAME, DATA_TYPE, IS_NULLABLE FROM INFORMATION_SCHEMA.COLUMNS "
               f"WHERE {cond} AND TABLE_NAME = {_literal(table)} ORDER BY ORDINAL_POSITION")
        out = run_sql(conn, sql, max_rows=500)
        return [{"name": str(r[0]), "type": str(r[1]),
                 "nullable": str(r[2]).upper() == "YES", "key": ""} for r in out["rows"]]

    # oracle
    cond = f"owner = {_literal(schema)}" if schema else "1=1"
    sql = ("SELECT column_name, data_type, nullable, data_length, data_precision, data_scale "
           f"FROM all_tab_columns WHERE {cond} AND table_name = {_literal(table)} "
           "ORDER BY column_id")
    out = run_sql(conn, sql, max_rows=500)
    cols = []
    for r in out["rows"]:
        t = str(r[1])
        if t in ("NUMBER",) and r[4] is not None:
            t = f"NUMBER({r[4]},{r[5] if r[5] is not None else 0})"
        cols.append({"name": str(r[0]), "type": t,
                     "nullable": str(r[2]).upper() == "Y", "key": ""})
    return cols


def preview_sql(kind: str, table: str, schema: str = "", n: int = 100) -> str:
    """生成「看一眼这张表」的 SQL，按方言写对了分页语法。"""
    q = qualified(kind, table, schema)
    n = max(1, min(int(n or 100), 5000))
    if kind == "mssql":
        return f"SELECT TOP {n} *\nFROM {q}"
    if kind == "oracle":
        return f"SELECT *\nFROM {q}\nFETCH FIRST {n} ROWS ONLY"
    return f"SELECT *\nFROM {q}\nLIMIT {n}"


def build_select_template(kind: str, table: str, columns: list[str],
                          schema: str = "", n: int = 1000) -> str:
    """按选中的列生成一条可直接跑的查询语句，省得用户从零手写。"""
    q = qualified(kind, table, schema)
    if columns:
        col_sql = ",\n       ".join(quote_ident(kind, c) if not str(c).isdigit()
                                    and str(c) != "*" else str(c) for c in columns)
    else:
        col_sql = "*"
    n = max(1, min(int(n or 1000), DEFAULT_MAX_ROWS))
    if kind == "mssql":
        return f"SELECT TOP {n} {col_sql}\nFROM {q}"
    if kind == "oracle":
        return f"SELECT {col_sql}\nFROM {q}\nFETCH FIRST {n} ROWS ONLY"
    return f"SELECT {col_sql}\nFROM {q}\nLIMIT {n}"


# ———————————————————————— 连接测试 ————————————————————————

def test_connection(cfg: DBConfig) -> dict:
    """试连并报出「能不能用、能不能只读、有多少表」。失败不抛，返回 ok=False。"""
    t0 = time.time()
    try:
        conn = connect(cfg, DEFAULT_TIMEOUT_MS)
    except Exception as e:
        return {"ok": False, "error": str(e)}

    try:
        info = {
            "ok": True,
            "kind": cfg.kind,
            "label": SPECS.get(cfg.kind, {}).get("label", cfg.kind),
            "source": cfg.describe(),
            "readonly_enforced": conn.readonly_enforced,
            "warnings": list(conn.warnings),
            "elapsed_ms": round((time.time() - t0) * 1000, 1),
        }
        # 顺手查一张表验证「真的能取数」，而不只是能握手
        try:
            probe = run_sql(conn, "SELECT 1", max_rows=1)
            info["can_query"] = bool(probe["columns"])
        except Exception as e:
            info["can_query"] = False
            info["warnings"] = info["warnings"] + [f"连接成功但试查失败：{e}"]
        try:
            info["table_count"] = len(list_tables(conn, limit=1000))
        except Exception:
            info["table_count"] = None
        return info
    finally:
        conn.close()
