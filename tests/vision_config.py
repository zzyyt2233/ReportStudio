"""视觉模型接口的「预留」验证：环境变量注入、vision_model 单独指定、不离开本地。

这些测试只验证「配置如何被读取」，不发任何网络请求。
"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from core import config, llm

_ENV_KEYS = ("RS_LLM_ENABLED", "RS_LLM_BASE_URL", "RS_LLM_API_KEY",
             "RS_LLM_MODEL", "RS_LLM_VISION_MODEL", "RS_LLM_TIMEOUT")


def _clear():
    for k in _ENV_KEYS:
        os.environ.pop(k, None)
    config.load.cache_clear()


def test_env_overrides_config():
    _clear()
    os.environ["RS_LLM_ENABLED"] = "true"
    os.environ["RS_LLM_BASE_URL"] = "https://env.example/v1"
    os.environ["RS_LLM_API_KEY"] = "env-key"
    os.environ["RS_LLM_MODEL"] = "env-model"
    os.environ["RS_LLM_VISION_MODEL"] = "env-vision"
    os.environ["RS_LLM_TIMEOUT"] = "120"
    cfg = config.load()["llm"]
    assert cfg["enabled"] is True, cfg
    assert cfg["base_url"] == "https://env.example/v1", cfg
    assert cfg["api_key"] == "env-key", cfg
    assert cfg["model"] == "env-model", cfg
    assert cfg["vision_model"] == "env-vision", cfg
    assert cfg["timeout"] == 120, cfg
    print("PASS env_overrides_config")


def test_vision_model_fallback():
    _clear()
    os.environ["RS_LLM_ENABLED"] = "true"
    os.environ["RS_LLM_BASE_URL"] = "https://env.example/v1"
    os.environ["RS_LLM_API_KEY"] = "env-key"
    os.environ["RS_LLM_MODEL"] = "chat-model"
    # 不设 vision_model -> 视觉识别回落到 model
    cfg = config.load()["llm"]
    assert cfg["vision_model"] == "", cfg
    assert (cfg.get("vision_model") or cfg["model"]) == "chat-model"
    # 设了 vision_model -> 用专用模型
    os.environ["RS_LLM_VISION_MODEL"] = "vision-model"
    config.load.cache_clear()
    cfg = config.load()["llm"]
    assert (cfg.get("vision_model") or cfg["model"]) == "vision-model"
    print("PASS vision_model_fallback")


def test_env_enables_llm():
    _clear()
    # 无配置时不应就绪（本机若已有 config.local.yaml 含 key 则跳过该断言）
    if not os.path.exists(config.LOCAL_CONFIG_PATH):
        assert config.llm_ready() is False
    _clear()  # 清掉上面的缓存，避免下面设 env 后读到旧缓存
    os.environ["RS_LLM_ENABLED"] = "true"
    os.environ["RS_LLM_BASE_URL"] = "https://env.example/v1"
    os.environ["RS_LLM_API_KEY"] = "env-key"
    os.environ["RS_LLM_MODEL"] = "m"
    assert config.llm_ready() is True
    # _cfg() 只校验配置齐了，不发网络请求
    c = llm._cfg()
    assert c["base_url"].endswith("/v1"), c
    assert c["api_key"] == "env-key"
    print("PASS env_enables_llm")


if __name__ == "__main__":
    _clear()
    try:
        test_env_overrides_config()
        test_vision_model_fallback()
        test_env_enables_llm()
    finally:
        _clear()
    print("\n全部视觉模型配置测试通过 ✅")
