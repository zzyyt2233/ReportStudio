"""全局配置加载。

所有默认路径都基于「本项目自己所在的目录」计算，不写死盘符，
所以项目拷到任何位置、任何盘都能直接跑，配置缺失或留空时回落到默认值。
"""

from __future__ import annotations

import copy
import os
from functools import lru_cache

import yaml

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONFIG_PATH = os.path.join(ROOT, "config.yaml")
# 本地私有配置：用来放 API key、数据库连接这类不该跟着项目走的东西。
# 这个文件在 .gitignore 里，永远不会被提交，所以把项目发给别人时不会连着密钥一起发出去。
LOCAL_CONFIG_PATH = os.path.join(ROOT, "config.local.yaml")

DEFAULTS = {
    "paths": {
        "outputs": os.path.join(ROOT, "outputs").replace("\\", "/"),
        "temp": os.path.join(ROOT, "temp").replace("\\", "/"),
        "dashboards": os.path.join(ROOT, "dashboards").replace("\\", "/"),
    },
    "server": {"host": "127.0.0.1", "port": 8765, "auto_open_browser": True},
    "llm": {"enabled": False, "base_url": "", "api_key": "", "model": "",
            "vision_model": "", "timeout": 60},
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


def _apply_env(cfg: dict) -> dict:
    """允许用环境变量注入 / 覆盖 llm 配置，方便「把工具分享给别人」时对方不改文件、只设变量。

    变量名：RS_LLM_ENABLED / RS_LLM_BASE_URL / RS_LLM_API_KEY /
            RS_LLM_MODEL / RS_LLM_VISION_MODEL / RS_LLM_TIMEOUT
    只有显式设置了才覆盖；文件里的写法优先级低于环境变量（环境变量通常来自部署方）。
    这是「预留视觉模型接口 + 便于分享」的关键一环：别人拿到包不必编辑 config 文件，
    设一个环境变量就能接上自己的模型。
    """
    m = {
        "RS_LLM_ENABLED": ("enabled", lambda v: v.strip().lower() in ("1", "true", "yes", "on")),
        "RS_LLM_BASE_URL": ("base_url", str),
        "RS_LLM_API_KEY": ("api_key", str),
        "RS_LLM_MODEL": ("model", str),
        "RS_LLM_VISION_MODEL": ("vision_model", str),
        "RS_LLM_TIMEOUT": ("timeout", lambda v: int(v) if v.strip() else 60),
    }
    llm = cfg.setdefault("llm", {})
    for envk, (key, conv) in m.items():
        v = os.environ.get(envk)
        if not v:
            continue
        try:
            llm[key] = conv(v)
        except Exception:
            continue
    return cfg


@lru_cache(maxsize=1)
def load() -> dict:
    # deepcopy 而非直接引用：下面会就地补全 paths，引用会污染模块级 DEFAULTS
    cfg = copy.deepcopy(DEFAULTS)
    # config.yaml 是随项目走的公共模板；config.local.yaml 是本人的私密覆盖，后读的赢。
    # 任一文件缺失或写坏都只跳过它自己，不影响另一个 —— 密钥写在 local 里，分享时天然不带。
    for path in (CONFIG_PATH, LOCAL_CONFIG_PATH):
        if not os.path.exists(path):
            continue
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = yaml.safe_load(f) or {}
            cfg = _deep_merge(cfg, data)
        except Exception:
            continue
    cfg = _apply_env(cfg)
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
