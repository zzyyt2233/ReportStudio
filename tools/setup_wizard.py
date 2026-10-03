# -*- coding: utf-8 -*-
"""首次配置向导：帮终端用户生成 config.local.yaml（大模型接入）。

为什么要有这个：让用户手改 YAML，最常见的翻车是缩进错、冒号后少空格、
key 里带特殊字符没加引号 —— 三种错法报错都看不懂。向导用问答的方式收齐
四个值，自己拼 YAML，还顺手测一下接口通不通，配完就知道能不能用。

用法：
    python tools/setup_wizard.py            # 交互式
    python tools/setup_wizard.py --out 路径  # 写到指定文件（测试用）
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LOCAL_CONFIG = ROOT / "config.local.yaml"

HEADER = """# 本文件由「配置向导」生成，只在你本机生效。
# 它不会被 git 追踪、也不会被打进分发包 —— api_key 写在这里是安全的。
# 想重新配置，再双击一次「配置向导.bat」即可，会覆盖本文件。
"""


def _ask(prompt: str, default: str = "", secret: bool = False) -> str:
    hint = f"（默认 {default}）" if default else ""
    val = input(f"{prompt}{hint}：").strip()
    return val or default


def _write(path: Path, base_url: str, api_key: str, model: str,
           vision_model: str, timeout: int = 30) -> None:
    # api_key 可能带 = / # 这类字符，统一双引号 + 转义，怎么写都不会炸
    def q(s: str) -> str:
        return '"' + s.replace("\\", "\\\\").replace('"', '\\"') + '"'

    lines = [
        HEADER,
        "llm:\n",
        "  enabled: true\n",
        f"  base_url: {q(base_url)}\n",
        f"  api_key: {q(api_key)}\n",
        f"  model: {q(model)}\n",
    ]
    if vision_model:
        lines.append(f"  vision_model: {q(vision_model)}\n")
    lines.append(f"  timeout: {timeout}\n")
    path.write_text("".join(lines), encoding="utf-8")


def _probe(base_url: str, api_key: str, model: str) -> str:
    """拿 /models 试一下接口通不通。失败不阻断，只把原因说清楚。"""
    try:
        import requests
        r = requests.get(base_url.rstrip("/") + "/models",
                         headers={"Authorization": f"Bearer {api_key}"},
                         timeout=5)
        if r.status_code == 200:
            names = [m.get("id", "") for m in r.json().get("data", [])]
            if model in names:
                return f"连接成功，模型列表里有「{model}」。"
            return (f"连接成功，但模型列表里没看到「{model}」。"
                    f"可用的有：{('、'.join(names[:5])) or '（列表为空）'}。"
                    "如果生成时报模型不存在，回来改 model。")
        return f"接口返回 {r.status_code}：{r.text[:120]}。key 或地址可能有误。"
    except Exception as e:  # noqa: BLE001
        return f"连不上接口（{type(e).__name__}）。配置已保存，联网后再试。"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=str(LOCAL_CONFIG))
    ap.add_argument("--yes", action="store_true",
                    help="非交互：从环境变量 RS_WIZARD_* 读值（测试/脚本用）")
    args = ap.parse_args()
    out = Path(args.out)

    print()
    print("  ReportStudio 大模型配置向导")
    print("  " + "-" * 40)
    print("  配置会写入 config.local.yaml，只在本机生效，不会被打包分享。")
    print("  不配大模型也能用工具，只是图片识别和智能分析不可用。")
    print()

    if args.yes:
        base_url = os.environ.get("RS_WIZARD_BASE_URL", "")
        api_key = os.environ.get("RS_WIZARD_API_KEY", "")
        model = os.environ.get("RS_WIZARD_MODEL", "")
        vision_model = os.environ.get("RS_WIZARD_VISION_MODEL", "")
    else:
        base_url = _ask("接口地址 base_url", "https://api.deepseek.com/v1")
        api_key = _ask("API Key")
        if not api_key:
            print("没填 API Key，已取消。")
            return 1
        model = _ask("对话模型 model", "deepseek-chat")
        vision_model = _ask("视觉模型 vision_model（图片识别用，留空则与对话模型相同）")

    if out.exists():
        bak = out.with_suffix(".yaml.bak")
        bak.write_bytes(out.read_bytes())
        print(f"已有旧配置，备份到 {bak.name}")

    _write(out, base_url, api_key, model, vision_model)
    print(f"\n配置已写入：{out}")

    if not args.yes:
        print("\n正在测试接口连通性...")
        print("  " + _probe(base_url, api_key, model))
        print("\n重启服务（关掉黑窗口重新双击 启动.bat）后生效。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
