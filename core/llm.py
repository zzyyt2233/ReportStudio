"""大模型适配层：OpenAI 兼容协议，填 config.yaml 就能换厂商。

这个模块只做三件事（见各函数文档），并且有一条铁律：
**任何由 AI 参与的环节都不得自行编造数值** —— 数字必须先由 analyze.py 算好，
再作为素材交给模型润色。
"""

from __future__ import annotations

import base64
import json
import os
import re

import requests

from .config import llm_ready, load

TIMEOUT_FALLBACK = 60


class LLMUnavailable(Exception):
    """没配key、调不通、返回格式不对，一律抛这个，调用方自行降级。"""


def _cfg() -> dict:
    c = load()["llm"]
    if not (c.get("enabled") and c.get("base_url") and c.get("api_key")
            and c.get("model")):
        raise LLMUnavailable("未启用大模型接口（config.yaml 里 llm 段为空）")
    return c


def chat(system: str, user: str, want_json: bool = False,
         temperature: float = 0.2):
    """通用对话。want_json=True 时尝试把返回内容解析成 dict。"""
    cfg = _cfg()
    url = cfg["base_url"].rstrip("/") + "/chat/completions"
    payload = {
        "model": cfg["model"],
        "messages": [{"role": "system", "content": system},
                     {"role": "user", "content": user}],
        "temperature": temperature,
    }
    if want_json:
        payload["response_format"] = {"type": "json_object"}
    headers = {"Authorization": f"Bearer {cfg['api_key']}",
               "Content-Type": "application/json"}
    try:
        r = requests.post(url, json=payload, headers=headers,
                          timeout=cfg.get("timeout") or TIMEOUT_FALLBACK)
    except Exception as e:
        raise LLMUnavailable(f"请求失败：{e}")
    if r.status_code != 200:
        raise LLMUnavailable(f"接口返回 {r.status_code}：{r.text[:200]}")
    try:
        content = r.json()["choices"][0]["message"]["content"]
    except Exception:
        raise LLMUnavailable("返回结构无法解析")
    if not want_json:
        return content.strip()
    return _safe_json(content)


def _safe_json(text: str) -> dict:
    """模型经常把 JSON 包在 ```json 代码块里，这里剥掉再解析。"""
    t = text.strip()
    t = re.sub(r"^```(?:json)?\s*|\s*```$", "", t, flags=re.MULTILINE).strip()
    try:
        return json.loads(t)
    except Exception:
        m = re.search(r"\{[\s\S]*\}", t)
        if m:
            try:
                return json.loads(m.group(0))
            except Exception:
                pass
        raise LLMUnavailable("返回的不是合法 JSON")


def vision(image_path: str, prompt: str, want_json: bool = True):
    """图片/扫描件识别。图片转 base64 后随 prompt 一起发。

    视觉模型可与对话模型分开：config 里写了 llm.vision_model 就优先用它，
    否则回落到 llm.model（很多账号对话和视觉是不同模型名）。
    """
    cfg = _cfg()
    model = cfg.get("vision_model") or cfg["model"]
    url = cfg["base_url"].rstrip("/") + "/chat/completions"
    mime = "image/png"
    if image_path.lower().endswith((".jpg", ".jpeg")):
        mime = "image/jpeg"
    with open(image_path, "rb") as f:
        b64 = base64.b64encode(f.read()).decode()
    payload = {
        "model": model,
        "temperature": 0.1,
        "messages": [{
            "role": "user",
            "content": [
                {"type": "image_url",
                 "image_url": {"url": f"data:{mime};base64,{b64}"}},
                {"type": "text", "text": prompt},
            ],
        }],
    }
    if want_json:
        payload["response_format"] = {"type": "json_object"}
    headers = {"Authorization": f"Bearer {cfg['api_key']}",
               "Content-Type": "application/json"}
    try:
        r = requests.post(url, json=payload, headers=headers,
                          timeout=cfg.get("timeout") or TIMEOUT_FALLBACK)
    except Exception as e:
        raise LLMUnavailable(f"请求失败：{e}")
    if r.status_code != 200:
        raise LLMUnavailable(f"接口返回 {r.status_code}：{r.text[:200]}")
    try:
        content = r.json()["choices"][0]["message"]["content"]
    except Exception:
        raise LLMUnavailable("返回结构无法解析")
    return _safe_json(content) if want_json else content.strip()


IMAGE_PROMPT = (
    "你是一个数据提取助手。请仔细阅读这张图片，把里面能看到的数据整理成结构化表格。\n"
    "只输出 JSON，格式严格如下：\n"
    '{"title":"这张图的标题","note":"一句话说明数据口径或单位",'
    '"header":["列名1","列名2"],"rows":[["值1","值2"],["值3","值4"]]}\n'
    "规则：\n"
    "1. 必须只输出图片中真实存在的数字和文字，绝对不要推测、补全或计算\n"
    "2. 数字保留原始写法，不要换算单位（图片写「120万」就写「120万」，不要写1200000）\n"
    "3. 表头用原文，不要改写\n"
    "4. 如果图片里没有表格，就按你看到的文字整理成两列「项目/内容」\n"
    "5. 如果图片模糊到读不出任何数据，输出 {\"title\":\"\",\"note\":\"无法识别\",\"header\":[],\"rows\":[]}"
)

NL2SPEC_PROMPT = (
    "你是报表配置助手。用户会用中文口语描述想要什么图表，"
    "你需要把它翻译成固定的 JSON 配置。\n"
    "可用字段：\n"
    "- type: bar(柱状) / line(折线) / pie(饼图) / scatter(散点) / stack_bar(堆叠柱) / combo(柱线双轴)\n"
    "- x: 维度列名；y: 数值列名数组\n"
    "- agg: sum / mean / count / max / min\n"
    "- sort_by: 排序列名；sort_order: asc / desc\n"
    "- limit: 保留前几条；title: 图表标题\n"
    "- detail_sort_by: 若用户要求「明细表/数据表按某列排序」则填该列名，否则留空字符串\n"
    "- detail_sort_order: 明细排序方向 asc / desc\n"
    "输出格式：{\"title\":\"报告标题\",\"charts\":[{...}],\"metrics\":[\"数值列\"],"
    "\"detail_sort_by\":\"\",\"detail_sort_order\":\"asc\"}\n"
    "规则：\n"
    "1. 列名必须与用户给的候选列名**完全一致**，不允许自己造列名\n"
    "2. 拿不准就用候选里最贴近的那个，宁可选错列也不要返回不存在的列\n"
    "3. 只输出 JSON\n"
)

POLISH_PROMPT = (
    "你是资深数据分析师。下面是一份由程序精确计算出来的数据摘要，"
    "请你把它改写成一段通顺、专业、可直接放进汇报材料的中文分析结论。\n"
    "严格要求：\n"
    "1. **不得修改、增删、重新计算任何数字**，只能用自然语言重新组织\n"
    "2. 不要凭空补充摘要里没有的原因、趋势判断或建议\n"
    "3. 控制在 250 字以内，不要写小标题，不要列点，写成连贯段落\n"
    "4. 语气客观克制，不要出现「值得注意的是」「综上所述」这类套话\n"
)


def polish_summary(summary: str) -> str:
    """把 rules 算出的摘要润色成通顺段落。失败时返回原文。"""
    if not summary.strip():
        return summary
    try:
        out = chat(POLISH_PROMPT, summary, temperature=0.3)
        return out or summary
    except LLMUnavailable:
        return summary


def ready() -> bool:
    return llm_ready()
