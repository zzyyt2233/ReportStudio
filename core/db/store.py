"""连接配置落盘：存在项目目录下的 db_connections.json。

两条硬规则：
1. **密码只进不出** —— 文件里存、内存里用，但任何 API 响应都不回显，
   前端只会看到 has_password: true/false。
2. **改配置不清空密码** —— 界面上密码框永远是空的（因为不回显），
   用户改个端口号保存时不能顺手把密码抹掉，所以要按「未提供则沿用旧值」处理。

另：这份文件是明文存密码的。这是本地单机工具，图的是不用每次重输；
如果不想留在磁盘上，保存时把「记住密码」取消勾选即可，密码就只留在当次请求里。
"""

from __future__ import annotations

import json
import os
import time

from .connect import DBConfig

MAX_CONNECTIONS = 50


def _path() -> str:
    root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    return os.path.join(root, "db_connections.json")


def _read() -> list[dict]:
    p = _path()
    if not os.path.exists(p):
        return []
    try:
        with open(p, "r", encoding="utf-8") as f:
            d = json.load(f)
    except Exception:
        return []
    items = d.get("connections") if isinstance(d, dict) else d
    return [x for x in (items or []) if isinstance(x, dict)]


def _write(items: list[dict]) -> bool:
    p = _path()
    try:
        tmp = p + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump({"connections": items[:MAX_CONNECTIONS],
                       "saved_at": time.time()}, f, ensure_ascii=False, indent=2)
        os.replace(tmp, p)
        return True
    except Exception:
        return False


def list_public() -> list[dict]:
    """给前端的清单：不含密码。"""
    out = []
    for d in _read():
        cfg = DBConfig.from_dict(d)
        item = cfg.to_public()
        item["remember_password"] = bool(d.get("remember_password", True))
        out.append(item)
    return out


def get(name: str) -> DBConfig | None:
    """按显示名取配置（含密码），用于执行查询。"""
    key = (name or "").strip()
    if not key:
        return None
    for d in _read():
        cfg = DBConfig.from_dict(d)
        if (cfg.name or cfg.default_name()) == key:
            return cfg
    return None


def save(payload: dict) -> tuple[bool, str, dict]:
    """新增或更新一份配置。返回 (ok, 说明, 保存后的公开信息)。"""
    cfg = DBConfig.from_dict(payload)
    if cfg.kind not in ("sqlite", "mysql", "postgres", "mssql", "oracle"):
        return False, f"不支持的数据库类型：{cfg.kind}", {}

    name = cfg.name or cfg.default_name()
    cfg.name = name
    items = _read()

    # 已在同名配置里存过密码，而这次没带密码 → 沿用旧的（避免改端口时把密码洗掉）
    for d in items:
        old = DBConfig.from_dict(d)
        if (old.name or old.default_name()) == name:
            if not cfg.password and d.get("password"):
                cfg.password = str(d["password"])
            break

    remember = payload.get("remember_password", True)
    if remember is False:
        cfg.password = ""

    stored = cfg.to_store()
    stored["remember_password"] = bool(remember)

    # 同名覆盖，新的放最前
    items = [d for d in items
             if (DBConfig.from_dict(d).name or DBConfig.from_dict(d).default_name()) != name]
    items.insert(0, stored)
    if not _write(items):
        return False, "配置写盘失败（检查 E 盘是否可写）", {}
    note = f"已保存连接「{name}」"
    if remember is False:
        note += "（未记住密码，每次执行时需重新填写）"
    return True, note, cfg.to_public()


def delete(name: str) -> tuple[bool, str]:
    key = (name or "").strip()
    if not key:
        return False, "没指定要删除的连接名"
    items = _read()
    left = [d for d in items
            if (DBConfig.from_dict(d).name or DBConfig.from_dict(d).default_name()) != key]
    if len(left) == len(items):
        return False, f"没找到连接「{key}」"
    if not _write(left):
        return False, "写盘失败"
    return True, f"已删除连接「{key}」"


def resolve(payload: dict) -> tuple[DBConfig | None, str]:
    """从请求里取出连接配置：优先用「已保存的连接名」，否则用请求里现填的。

    返回 (配置, 错误信息)。现填时允许不带密码 —— 但如果同名配置里存过，
    就把密码补上（界面上密码框是空的，不能因此报「密码不对」）。
    """
    name = (payload.get("connection_name") or "").strip()
    inline = payload.get("connection") or {}

    if name and not inline:
        cfg = get(name)
        if cfg is None:
            return None, f"没找到已保存的连接「{name}」，可能已被删除。"
        return cfg, ""

    if not inline and name:
        inline = {"name": name}
    if not inline:
        return None, "没提供数据库连接信息。"

    cfg = DBConfig.from_dict(inline)
    cfg.name = cfg.name or name or cfg.default_name()

    if not cfg.password:
        old = get(cfg.name)
        if old is not None and old.password:
            cfg.password = old.password
    return cfg, ""
