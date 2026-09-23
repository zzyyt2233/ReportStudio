"""图片 / 扫描件解析：走大模型视觉接口。

没配接口时不静默失败，而是返回一个带明确提示的空 Dataset，
界面上会把提示显示出来让用户知道该干什么。
"""

from __future__ import annotations

import base64
import os
import uuid

from ..model import Column, Dataset

SUPPORTED = {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".gif"}

EXTRACT_SYSTEM = "你是一个精确的数据提取助手，只输出 JSON。"

VISION_PROMPT = """请识别这张图片里的表格或数据，输出 JSON：
{
  "title": "图片标题（没有就空字符串）",
  "note": "一句话说明数据口径、单位或时间范围（没有就空字符串）",
  "header": ["列1", "列2"],
  "rows": [["值1", "值2"]]
}
规则：
1. 只输出图片里真实存在的数字和文字，禁止推测、补全、单位换算
2. 表头用原文，不要改写
3. 若图片中不是表格（比如柱状图），就按图例和坐标轴刻度整理成表格
4. 读不出数据时返回 {"title":"","note":"","header":[],"rows":[]}"""


def parse_image(path: str) -> Dataset:
    name = os.path.basename(path)
    try:
        from ..llm import IMAGE_PROMPT, LLMUnavailable, vision
        data = vision(path, IMAGE_PROMPT, want_json=True)
    except ImportError:
        return _hint_ds(name, "图片解析模块未加载")
    except Exception as e:
        msg = str(e)
        if "未启用大模型" in msg:
            return _hint_ds(name, "识别图片需要在 config.yaml 里配置大模型接口"
                                   "（图片/扫描件必须靠视觉模型才能看懂）")
        return _hint_ds(name, f"图片识别失败：{msg}")

    header = data.get("header") or []
    rows = data.get("rows") or []
    if not header or not rows:
        return _hint_ds(name, (data.get("note") or "图片里没有识别出表格数据"))

    # 用 tabular 的类型推断，避免模型给的中文数字/千分位把后面算错
    from .tabular import build_dataset
    grid = [list(header)] + [list(r) for r in rows]
    ds = build_dataset(grid, name=data.get("title") or name,
                       source_type="image")
    note = data.get("note")
    if note:
        ds.warnings.insert(0, f"图片说明：{note}")
    ds.raw_text = "\n".join(
        "\t".join("" if v is None else str(v) for v in r)
        for r in grid[:80]
    )
    return ds


def _hint_ds(name: str, msg: str) -> Dataset:
    return Dataset(id=uuid.uuid4().hex[:8], name=name, source_type="image",
                   columns=[], rows=[], warnings=[msg], total_rows=0)
