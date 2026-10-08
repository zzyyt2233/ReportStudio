# -*- coding: utf-8 -*-
"""配置向导生成的 YAML，必须能被服务端原样读回。

setup_wizard.py 的存在理由就是「让用户手改 YAML 不再翻车」——缩进错、
冒号后少空格、特殊字符没引号，这三种错法报错都看不懂。可它自己拼 YAML
的 _write() 从没被测过：万一转义写漏一类字符（比如 api_key 里带引号、
反斜杠、=、#），向导就会生成一份自己都解析不对的配置，用户拿到的
正是它声称要解决的「看不懂的报错」。

这里做三方对账：向导生成 → yaml.safe_load 独立解析 → 服务端
core.config.load 真实加载，三者读出的值必须完全一致。
测试全程 monkeypatch 到临时目录，绝不碰真实的 config.local.yaml。
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import yaml  # noqa: E402
import core.config as core_config  # noqa: E402
# main 必须改名导入：本测试自己也有 main()，同名会让 main() 无限自递归
# （RecursionError，且在部分环境表现为进程被直接终止）。
from tools.setup_wizard import HEADER, _write  # noqa: E402
from tools.setup_wizard import main as wizard_main  # noqa: E402

# 临时目录建在项目 temp/ 下、用完不删 —— 与 secret_store 同一策略：
# 受限环境的删除额度按会话累计，测试自己删文件容易撞拦截；
# 项目 app 启动时的 cleanup_temp 会按时间把这些目录回收掉。
ROOT = Path(__file__).resolve().parents[1]

_ok = 0
_fail = 0


def ok(label: str, cond: bool, info: str = "") -> None:
    global _ok, _fail
    if cond:
        _ok += 1
        print("  ✓ " + label)
    else:
        _fail += 1
        print("  ✗ " + label + (f"  {info}" if info else ""))


# 实际使用中会出现在 API Key / URL / 模型名里的刁钻字符。
# 每一个都对应 _write 的 q() 转义逻辑里可能漏的一类。
TRICKY_KEYS = [
    "sk-abc123def",                    # 常规
    "sk-abc=def=ghi",                  # 等号（q 注释里点名的场景）
    "sk-abc#not-a-comment",            # 井号（YAML 注释符，不转义就截断）
    'sk-"quoted"-key',                 # 双引号
    "sk-abc\\def\\ghi",                # 反斜杠（Windows 路径风格）
    "sk:abc:def",                      # 冒号（YAML 键值分隔符）
    "sk-key-测试-图标",                 # 中文
    "sk key with spaces",              # 空格
    "sk-'single'-quoted",              # 单引号
    "sk-{brace}[bracket]",             # YAML 流记号
]

BASE_URL = "https://api.deepseek.com/v1/"
MODEL = "deepseek-chat"


def _roundtrip_one(key: str, tmp: Path, tag: str) -> None:
    out = tmp / f"local_{tag}.yaml"
    vision = "qwen-vl-max" if len(key) % 2 else ""
    _write(out, BASE_URL, key, MODEL, vision)

    # 1) 文件形态：HEADER 在最前，用户一眼看到「这文件不会外泄」的说明
    text = out.read_text(encoding="utf-8")
    ok(f"[{tag}] HEADER 位于文件开头", text.startswith(HEADER))

    # 2) 独立解析：yaml.safe_load 是服务端之外的第二双眼睛
    data = yaml.safe_load(text) or {}
    llm = data.get("llm") or {}
    ok(f"[{tag}] yaml 读回 api_key 一致", llm.get("api_key") == key,
       f"读回 {llm.get('api_key')!r}")

    # 3) 服务端加载：monkeypatch 后走真实的 load()
    core_config.LOCAL_CONFIG_PATH = str(out)
    core_config.load.cache_clear()
    try:
        cfg = core_config.load()
        got = cfg["llm"].get("api_key")
        ok(f"[{tag}] 服务端读回 api_key 一致", got == key, f"读回 {got!r}")
        ok(f"[{tag}] 服务端读回 base_url/model 一致",
           cfg["llm"].get("base_url") == BASE_URL
           and cfg["llm"].get("model") == MODEL)
        if vision:
            ok(f"[{tag}] vision_model 一并写对",
               cfg["llm"].get("vision_model") == vision)
        else:
            # 留空 = 不写该行 = 回落到 DEFAULTS 的空串，不得报错
            ok(f"[{tag}] vision_model 留空时安全回落",
               cfg["llm"].get("vision_model") == "")
    finally:
        core_config.load.cache_clear()


def main() -> int:
    print("== 配置向导 YAML 往返 ==")
    base = ROOT / "temp"
    base.mkdir(parents=True, exist_ok=True)
    tmp = Path(tempfile.mkdtemp(prefix="setup_wizard_", dir=str(base)))
    _run_all(tmp)
    print(f"\n通过 {_ok} 项，失败 {_fail} 项")
    return 1 if _fail else 0


def _run_all(tmp: Path) -> None:
    for i, key in enumerate(TRICKY_KEYS):
        _roundtrip_one(key, tmp, f"k{i}")

    # 常规组合：llm_ready() 应为 True（四项必填齐全）
    out = tmp / "ready.yaml"
    _write(out, BASE_URL, "sk-normal-key", MODEL, "qwen-vl-max")
    core_config.LOCAL_CONFIG_PATH = str(out)
    core_config.load.cache_clear()
    try:
        ok("常规配置下 llm_ready() 为 True", core_config.llm_ready() is True)
    finally:
        core_config.load.cache_clear()

    # --yes 非交互模式：走真实的 main()（含参数解析与环境变量读取）
    print("\n== --yes 非交互模式 ==")
    out2 = tmp / "yes.yaml"
    env = {
        "RS_WIZARD_BASE_URL": BASE_URL,
        "RS_WIZARD_API_KEY": "sk-from-env=with#specials",
        "RS_WIZARD_MODEL": MODEL,
        "RS_WIZARD_VISION_MODEL": "qwen-vl-max",
    }
    old = {k: os.environ.get(k) for k in env}
    old_argv = sys.argv
    old_lcp = core_config.LOCAL_CONFIG_PATH
    try:
        os.environ.update(env)
        sys.argv = ["setup_wizard.py", "--yes", "--out", str(out2)]
        rc = wizard_main()
        ok("main() 退出码 0", rc == 0)
        data = yaml.safe_load(out2.read_text(encoding="utf-8")) or {}
        ok("环境变量里的 api_key 原样落盘（含 = 和 #）",
           data["llm"]["api_key"] == "sk-from-env=with#specials",
           repr(data["llm"].get("api_key")))

        # key 为空：不炸，落成空串，llm_ready() 为 False ——
        # 「配了一半」是合法状态，服务用不了大模型但其他功能照常
        out3 = tmp / "empty.yaml"
        os.environ["RS_WIZARD_API_KEY"] = ""
        sys.argv = ["setup_wizard.py", "--yes", "--out", str(out3)]
        ok("key 为空时 main() 也返回 0（不炸）", wizard_main() == 0)
        core_config.LOCAL_CONFIG_PATH = str(out3)
        core_config.load.cache_clear()
        ok("空 key 时 llm_ready() 为 False",
           core_config.llm_ready() is False)
        ok("空 key 时 enabled 仍为 true（配置形态完整）",
           core_config.load()["llm"].get("enabled") is True)

        # 覆盖场景：重跑向导要备份旧文件（out3 上一轮写的是空 key）
        os.environ["RS_WIZARD_API_KEY"] = "sk-second"
        sys.argv = ["setup_wizard.py", "--yes", "--out", str(out3)]
        wizard_main()
        bak = out3.with_suffix(".yaml.bak")
        ok("覆盖时生成了 .bak 备份", bak.exists())
        ok(".bak 里是上一轮的内容（空 key）",
           (yaml.safe_load(bak.read_text(encoding="utf-8")) or {})
           .get("llm", {}).get("api_key") == "")
        ok("新文件里是新 key",
           yaml.safe_load(out3.read_text(encoding="utf-8"))["llm"]["api_key"]
           == "sk-second")
    finally:
        sys.argv = old_argv
        for k, v in old.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        core_config.LOCAL_CONFIG_PATH = old_lcp
        core_config.load.cache_clear()


if __name__ == "__main__":
    raise SystemExit(main())
