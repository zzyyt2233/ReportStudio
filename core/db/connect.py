"""数据库连接层 —— 只读会话的兜底闸门。

即使 guard.py 的语句检查被绕过，这一层也让库本身写不进去：

| 库 | 只读手段 |
|---|---|
| SQLite | 用 `file:...?mode=ro` 打开，内核级只读，写操作直接报错 |
| MySQL / MariaDB | `SET SESSION TRANSACTION READ ONLY` |
| PostgreSQL | `SET SESSION CHARACTERISTICS AS TRANSACTION READ ONLY` |
| Oracle | `SET TRANSACTION READ ONLY` |
| SQL Server | 无会话级只读；连接串带 `ApplicationIntent=ReadOnly`，主要靠语句闸门 |

另外每个连接都设连接超时（避免填错主机把请求挂死）和语句超时（避免一条大查询拖死服务）。

所有异常都翻译成白话，不把驱动原始堆栈丢给用户。
"""

from __future__ import annotations

import os
import time
from dataclasses import dataclass, field
from urllib.parse import quote

# 每种库的界面描述：字段名、默认端口、是否要文件路径
SPECS: dict[str, dict] = {
    "sqlite": {
        "label": "SQLite（本地文件库）",
        "file": True, "network": False,
        "fields": ["path"],
        "hint": "填 E 盘上的 .db / .sqlite / .db3 文件完整路径，也可以用界面上的「上传库文件」。",
    },
    "mysql": {
        "label": "MySQL / MariaDB",
        "file": False, "network": True,
        "fields": ["host", "port", "user", "password", "database", "charset"],
        "default_port": 3306,
        "hint": "建议单独开一个只有 SELECT 权限的账号给这个工具用。",
    },
    "postgres": {
        "label": "PostgreSQL",
        "file": False, "network": True,
        "fields": ["host", "port", "user", "password", "database"],
        "default_port": 5432,
        "hint": "database 填库名；schema 默认 public，可以在 SQL 里写 schema.table 指定。",
    },
    "mssql": {
        "label": "SQL Server",
        "file": False, "network": True,
        "fields": ["host", "port", "user", "password", "database", "driver"],
        "default_port": 1433,
        "hint": "本机得先装「ODBC Driver for SQL Server」，没装的话连接会提示。",
    },
    "oracle": {
        "label": "Oracle",
        "file": False, "network": True,
        "fields": ["host", "port", "user", "password", "service"],
        "default_port": 1521,
        "hint": "service 填服务名（如 ORCLPDB1），不是 SID。",
    },
}

KINDS = tuple(SPECS.keys())


@dataclass
class DBConfig:
    """一份连接配置。password 只在内存/落盘时用，绝不回传给前端。"""

    kind: str = "sqlite"
    name: str = ""              # 显示名，留空就按 kind 自动起
    path: str = ""              # sqlite
    host: str = "127.0.0.1"
    port: int | None = None
    database: str = ""
    user: str = ""
    password: str = ""
    service: str = ""           # oracle 服务名
    charset: str = "utf8mb4"    # mysql
    driver: str = ""            # mssql 指定 ODBC 驱动
    connect_timeout: int = 8

    @classmethod
    def from_dict(cls, d: dict) -> "DBConfig":
        d = d or {}
        kind = str(d.get("kind") or "sqlite").strip().lower()
        if kind == "mariadb":
            kind = "mysql"
        if kind in ("postgresql", "pg"):
            kind = "postgres"
        if kind in ("sqlserver", "sql_server", "mssqlserver"):
            kind = "mssql"
        port = d.get("port")
        try:
            port = int(port) if port not in (None, "") else SPECS.get(kind, {}).get("default_port")
        except (TypeError, ValueError):
            port = SPECS.get(kind, {}).get("default_port")
        return cls(
            kind=kind,
            name=str(d.get("name") or "").strip(),
            path=str(d.get("path") or "").strip().strip('"'),
            host=str(d.get("host") or "127.0.0.1").strip(),
            port=port,
            database=str(d.get("database") or "").strip(),
            user=str(d.get("user") or "").strip(),
            password=str(d.get("password") or ""),
            service=str(d.get("service") or "").strip(),
            charset=str(d.get("charset") or "utf8mb4").strip() or "utf8mb4",
            driver=str(d.get("driver") or "").strip(),
            connect_timeout=int(d.get("connect_timeout") or 8),
        )

    def to_public(self) -> dict:
        """给前端的版本：密码只报「有没有」，不回显内容。"""
        out = {
            "kind": self.kind,
            "name": self.name or self.default_name(),
            "has_password": bool(self.password),
        }
        for k in ("path", "host", "port", "database", "user", "service", "charset", "driver"):
            v = getattr(self, k)
            if v not in ("", None):
                out[k] = v
        return out

    def to_store(self) -> dict:
        d = self.to_public()
        d["password"] = self.password
        return d

    def default_name(self) -> str:
        if self.kind == "sqlite":
            base = os.path.basename(self.path) or "本地库"
            return f"SQLite · {base}"
        if self.kind == "oracle":
            tail = self.service or self.database or ""
        else:
            tail = self.database or ""
        return f"{SPECS.get(self.kind, {}).get('label', self.kind)} · {self.host}" + (f"/{tail}" if tail else "")

    def describe(self) -> str:
        """给报告里写「数据来源」用的一行说明，不含密码。"""
        if self.kind == "sqlite":
            return f"SQLite 文件 {self.path}" if self.path else "SQLite"
        tail = self.service or self.database or ""
        where = f"{self.host}:{self.port or ''}".rstrip(":")
        return f"{SPECS.get(self.kind, {}).get('label', self.kind)} {where}" + (f"/{tail}" if tail else "")


@dataclass
class Conn:
    """一个打开的连接：原始 dbapi 连接 + 方言 + 只读是否真的生效。"""

    raw: object
    kind: str
    cfg: DBConfig
    readonly_enforced: bool = False
    warnings: list[str] = field(default_factory=list)
    _deadline: float = 0.0

    def close(self) -> None:
        try:
            self.raw.close()
        except Exception:
            pass


def _friendly_conn_error(kind: str, e: Exception, cfg: DBConfig) -> str:
    """把驱动异常翻译成用户能照着做的白话。"""
    msg = str(e)
    low = msg.lower()
    label = SPECS.get(kind, {}).get("label", kind)

    if kind == "sqlite":
        if not os.path.exists(cfg.path or ""):
            return f"找不到数据库文件：{cfg.path}。检查路径对不对，或先用「上传库文件」。"
        if "readonly" in low or "unable to open" in low:
            return f"打不开 {cfg.path}（只读方式）。确认文件没被占用、路径没写错。"
        return f"打开 SQLite 失败：{msg}"

    if "getaddrinfo" in low or "name or service not known" in low or "nodename nor servname" in low:
        return f"连不上主机 `{cfg.host}`：域名解析不了，检查主机名/网络。"
    if "timed out" in low or "timeout" in low or "10060" in low:
        return (f"连接 `{cfg.host}:{cfg.port}` 超时。检查地址、端口、"
                "以及防火墙/安全组是否放行。")
    if "refused" in low or "10061" in low:
        return f"`{cfg.host}:{cfg.port}` 拒绝连接：服务没起，或端口填错了。"
    if ("access denied" in low or "authentication" in low or "password" in low
            or "1045" in low or "28p01" in low or "ora-01017" in low or "login failed" in low):
        return f"{label} 账号或密码不对（用户 `{cfg.user}`）。"
    if ("unknown database" in low or "does not exist" in low or "1049" in low
            or "3d000" in low or "invalid catalog" in low or "ora-12514" in low
            or "cannot open database" in low):
        return (f"库/服务名不对：{cfg.database or cfg.service}。"
                "确认库名存在，Oracle 的 service 是服务名不是 SID。")
    if "drivers" in low or "im002" in low or "data source name not found" in low:
        return "本机没找到可用的 ODBC 驱动，先安装「ODBC Driver for SQL Server」。"
    if "ora-12541" in low:
        return f"Oracle 监听器没起或端口不对（{cfg.host}:{cfg.port}）。"
    if "certificate" in low or "ssl" in low:
        return f"SSL/证书校验失败：{msg}"

    return f"连接 {label} 失败：{msg}"


def connect(cfg: DBConfig, timeout_ms: int = 30000) -> Conn:
    """打开一个只读连接。失败抛 RuntimeError（message 是白话）。"""
    if cfg.kind not in SPECS:
        raise RuntimeError(f"不支持的数据库类型：{cfg.kind}。支持：" + "、".join(KINDS))
    if cfg.kind == "sqlite":
        return _connect_sqlite(cfg, timeout_ms)
    try:
        if cfg.kind == "mysql":
            return _connect_mysql(cfg, timeout_ms)
        if cfg.kind == "postgres":
            return _connect_postgres(cfg, timeout_ms)
        if cfg.kind == "mssql":
            return _connect_mssql(cfg, timeout_ms)
        if cfg.kind == "oracle":
            return _connect_oracle(cfg, timeout_ms)
    except Exception as e:
        raise RuntimeError(_friendly_conn_error(cfg.kind, e, cfg)) from e
    raise RuntimeError(f"不支持的数据库类型：{cfg.kind}")


# ———————————————————————— SQLite ————————————————————————

def sqlite_uri(path: str) -> str:
    """拼一个只读的 SQLite URI。mode=ro 是内核级只读，写操作会被 SQLite 自己拒掉。"""
    p = os.path.abspath(path).replace("\\", "/")
    if not p.startswith("/"):
        p = "/" + p          # E:/x → /E:/x
    return "file:" + quote(p) + "?mode=ro"


def _connect_sqlite(cfg: DBConfig, timeout_ms: int) -> Conn:
    import sqlite3

    if not cfg.path:
        raise RuntimeError("SQLite 要填数据库文件的完整路径。")
    if not os.path.exists(cfg.path):
        raise RuntimeError(f"找不到数据库文件：{cfg.path}")
    if os.path.isdir(cfg.path):
        raise RuntimeError(f"`{cfg.path}` 是个目录，不是数据库文件。")

    try:
        raw = sqlite3.connect(sqlite_uri(cfg.path), uri=True, timeout=cfg.connect_timeout)
    except Exception as e:
        raise RuntimeError(_friendly_conn_error("sqlite", e, cfg)) from e

    raw.row_factory = None
    conn = Conn(raw=raw, kind="sqlite", cfg=cfg, readonly_enforced=True)

    # 语句超时：sqlite3 没有 statement_timeout，用 progress handler 兜。
    # 回调返回非 0 会中断当前语句并抛 OperationalError: interrupted。
    deadline = time.time() + timeout_ms / 1000.0
    conn._deadline = deadline

    def _watchdog() -> int:
        return 1 if time.time() > deadline else 0

    try:
        raw.set_progress_handler(_watchdog, 20000)
    except Exception:
        pass
    return conn


# ———————————————————————— 网络库公共工具 ————————————————————————

def _apply_readonly(cur, conn: Conn, statements: list[str], warn: str) -> None:
    """跑一串只读设置语句。全失败才告警（不同版本支持的写法不一样，能中一条就行）。"""
    ok = False
    for s in statements:
        try:
            cur.execute(s)
            ok = True
            break
        except Exception:
            continue
    if ok:
        conn.readonly_enforced = True
    else:
        conn.warnings.append(warn)


def _set_autocommit(conn: Conn, flag: bool = True) -> None:
    try:
        conn.raw.autocommit = flag
    except Exception:
        pass


def _connect_mysql(cfg: DBConfig, timeout_ms: int) -> Conn:
    import pymysql

    raw = pymysql.connect(
        host=cfg.host, port=cfg.port or 3306, user=cfg.user, password=cfg.password,
        database=cfg.database or None, charset=cfg.charset or "utf8mb4",
        connect_timeout=cfg.connect_timeout,
        read_timeout=max(cfg.connect_timeout, timeout_ms // 1000 + 5),
        write_timeout=cfg.connect_timeout,
        cursorclass=pymysql.cursors.Cursor,
    )
    conn = Conn(raw=raw, kind="mysql", cfg=cfg)
    _set_autocommit(conn, True)
    cur = raw.cursor()
    # 只读会话（MySQL 5.7+ / MariaDB 10.x 都认这条）
    _apply_readonly(
        cur, conn,
        ["SET SESSION TRANSACTION READ ONLY"],
        "这个 MySQL/MariaDB 版本没接受只读会话设置，已用语句闸门兜底；"
        "强烈建议改用只有 SELECT 权限的账号。",
    )
    # 语句超时：MySQL 与 MariaDB 的变量名不一样，挨个试
    try:
        cur.execute(f"SET SESSION MAX_EXECUTION_TIME = {int(timeout_ms)}")
    except Exception:
        try:
            cur.execute(f"SET SESSION max_statement_time = {int(timeout_ms) / 1000.0}")
        except Exception:
            pass
    try:
        cur.close()
    except Exception:
        pass
    return conn


def _connect_postgres(cfg: DBConfig, timeout_ms: int) -> Conn:
    import pg8000.dbapi

    raw = pg8000.dbapi.connect(
        user=cfg.user or None, password=cfg.password or None,
        host=cfg.host, port=cfg.port or 5432,
        database=cfg.database or None,
        timeout=cfg.connect_timeout,
    )
    conn = Conn(raw=raw, kind="postgres", cfg=cfg)
    _set_autocommit(conn, True)
    cur = raw.cursor()
    _apply_readonly(
        cur, conn,
        ["SET SESSION CHARACTERISTICS AS TRANSACTION READ ONLY",
         "SET default_transaction_read_only = on"],
        "没能把 PostgreSQL 会话设成只读（可能是权限不足），已用语句闸门兜底；"
        "建议改用只有 SELECT 权限的账号。",
    )
    try:
        cur.execute(f"SET statement_timeout = {int(timeout_ms)}")
    except Exception:
        pass
    try:
        cur.close()
    except Exception:
        pass
    return conn


def _pick_odbc_driver(preferred: str = "") -> str:
    import pyodbc

    drivers = list(pyodbc.drivers())
    if preferred and preferred in drivers:
        return preferred
    # 从新到旧挑一个可用的
    for want in ("ODBC Driver 18 for SQL Server", "ODBC Driver 17 for SQL Server",
                 "ODBC Driver 13 for SQL Server", "SQL Server Native Client 11.0",
                 "SQL Server"):
        if want in drivers:
            return want
    return ""


def _connect_mssql(cfg: DBConfig, timeout_ms: int) -> Conn:
    import pyodbc

    driver = _pick_odbc_driver(cfg.driver)
    if not driver:
        have = pyodbc.drivers()
        raise RuntimeError(
            "本机没装 SQL Server 的 ODBC 驱动。装一个「Microsoft ODBC Driver for SQL Server」"
            "就能连了。"
            + (f"（当前已装的驱动：{'、'.join(have) if have else '无'}）" if have else "")
        )

    parts = [
        f"DRIVER={{{driver}}}",
        f"SERVER={cfg.host},{cfg.port or 1433}",
        f"UID={cfg.user}",
        f"PWD={cfg.password}",
        "Encrypt=no",
        "TrustServerCertificate=yes",
        "ApplicationIntent=ReadOnly",   # 主要对 AlwaysOn 只读副本生效，普通库无副作用
    ]
    if cfg.database:
        parts.append(f"DATABASE={cfg.database}")
    connstr = ";".join(parts) + ";"

    raw = pyodbc.connect(connstr, timeout=cfg.connect_timeout, autocommit=True)
    conn = Conn(raw=raw, kind="mssql", cfg=cfg)
    conn.warnings.append(
        "SQL Server 没有会话级只读开关，只读主要靠语句闸门保障；"
        "建议给这个工具单独开一个只读账号。"
    )
    cur = raw.cursor()
    try:
        cur.execute(f"SET ROWCOUNT 0")
    except Exception:
        pass
    try:
        cur.close()
    except Exception:
        pass
    return conn


def _connect_oracle(cfg: DBConfig, timeout_ms: int) -> Conn:
    import oracledb

    service = cfg.service or cfg.database
    if not service:
        raise RuntimeError("Oracle 要填服务名（service），如 ORCLPDB1。")
    dsn = oracledb.makedsn(cfg.host, cfg.port or 1521, service_name=service)
    raw = oracledb.connect(user=cfg.user, password=cfg.password, dsn=dsn)
    conn = Conn(raw=raw, kind="oracle", cfg=cfg)
    _set_autocommit(conn, True)
    cur = raw.cursor()
    _apply_readonly(
        cur, conn,
        ["SET TRANSACTION READ ONLY",
         "ALTER SESSION SET ISOLATION_LEVEL = SERIALIZABLE"],
        "没能把 Oracle 会话设成只读，已用语句闸门兜底；"
        "建议改用只有 SELECT 权限的账号。",
    )
    try:
        cur.close()
    except Exception:
        pass
    return conn
