"""全局配置加载。

所有默认路径都基于「本项目自己所在的目录」计算，不写死盘符，
所以项目拷到任何位置、任何盘都能直接跑，配置缺失或留空时回落到默认值。
"""

from __future__ import annotations

import os
from functools import lru_cache

import yaml

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONFIG_PATH = os.path.join(ROOT, "config.yaml")

DEFAULTS = {
    "paths": {
        "outputs": os.path.join(ROOT, "outputs").replace("\\", "/"),
        "temp": os.path.join(ROOT, "temp").replace("\\", "/"),
        "dashboards": os.path.join(ROOT, "dashboards").replace("\\", "/"),
    },
    "server": {"host": "127.0.0.1", "port": 8765, "auto_open_browser": True},
    "llm": {"enabled": False, "base_url": "", "api_key": "", "model": "",
            "timeout": 60},
    "report": {"default_template": "full", "theme": "light",
               "max_preview_rows": 200},
    "database": {"max_rows": 20000, "timeout_ms": 30000,
                 "connect_timeout": 8, "remember_password": True},
}


def _deep_merge(base: dict, over: dict) -> dict:
    out = dict(base)
    for k, v in (over or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = v
    return out


@lru_cache(maxsize=1)
def load() -> dict:
    cfg = DEFAULTS
    if os.path.exists(CONFIG_PATH):
        try:
            with open(CONFIG_PATH, "r", encoding="utf-8") as f:
                cfg = _deep_merge(DEFAULTS, yaml.safe_load(f) or {})
        except Exception:
            cfg = DEFAULTS
    # 配置里留空 = 用项目自己目录下的默认位置。
    # 这样项目 clone 到任意路径、任意盘符都能直接跑，不必改配置。
    for key in ("outputs", "temp", "dashboards"):
        if not cfg["paths"].get(key):
            cfg["paths"][key] = DEFAULTS["paths"][key]
        os.makedirs(cfg["paths"][key], exist_ok=True)
    return cfg


def llm_ready() -> bool:
    c = load()["llm"]
    return bool(c.get("enabled") and c.get("base_url") and c.get("api_key")
                and c.get("model"))


def db_settings() -> dict:
    """数据库取数的限额设置。只读是硬约束，不在这里给开关。"""
    d = load().get("database") or {}
    return {
        "max_rows": max(1, int(d.get("max_rows") or 20000)),
        "timeout_ms": max(1000, int(d.get("timeout_ms") or 30000)),
        "connect_timeout": max(1, int(d.get("connect_timeout") or 8)),
        "remember_password": bool(d.get("remember_password", True)),
    }


def outputs_dir() -> str:
    return load()["paths"]["outputs"]


def temp_dir() -> str:
    return load()["paths"]["temp"]


def dashboards_dir() -> str:
    """常驻看板的存盘目录（配置里可改，默认项目目录下的 dashboards）。"""
    return load()["paths"]["dashboards"]
