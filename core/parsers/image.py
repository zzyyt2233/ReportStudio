"""图片 / 扫描件解析。

主路径走大模型视觉接口（结构化表格识别最准）；
没配视觉模型时，退一路本地 OCR（若用户装了 tesseract）至少把文字抽出来；
都不可用就返回一个带明确操作指引的空 Dataset，界面上把指引显示出来。

为什么图片必须靠模型：纯 OCR 只能给出一串文字，分不出「哪格是哪列」，
构不成数据表；只有视觉模型能同时理解版式和数字。所以图片结构化识别
本质上依赖一个支持 vision 的 LLM 接口（在 config.local.yaml 里配）。
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


def _ocr_text(path: str) -> str | None:
    """本地 OCR 兜底：装了 pytesseract + tesseract 二进制才可用。返回纯文本或 None。

    显式指向常见安装位置，避免依赖 PATH；优先中文+英文，缺中文语言包时退英文。
    """
    try:
        import pytesseract
        from PIL import Image
    except ImportError:
        return None
    # 让 tesseract 去常见安装位置找语言包（含中文 chi_sim）。
    # TESSDATA_PREFIX 必须直接指向「含 .traineddata 文件」的目录本身。
    # 这里只列通用候选路径，不写死任何一台机器的盘符；
    # 用户自己设了 TESSDATA_PREFIX 则完全以用户为准。
    if not os.environ.get("TESSDATA_PREFIX"):
        for _td in (r"C:\Program Files\Tesseract-OCR\tessdata",
                    os.path.expandvars(r"%LOCALAPPDATA%\Tesseract-OCR\tessdata"),
                    os.path.expandvars(r"%LOCALAPPDATA%\Programs\Tesseract-OCR\tessdata"),
                    r"C:\tools\tesseract\tessdata"):
            if os.path.isdir(_td):
                os.environ["TESSDATA_PREFIX"] = _td
                break
    cand = [os.environ.get("TESSERACT_CMD"),
           r"C:\Program Files\Tesseract-OCR\tesseract.exe",
           os.path.expandvars(r"%LOCALAPPDATA%\Tesseract-OCR\tesseract.exe")]
    for c in cand:
        if c and os.path.exists(c):
            pytesseract.pytesseract.tesseract_cmd = c
            break
    try:
        try:
            return pytesseract.image_to_string(Image.open(path),
                                               lang="chi_sim+eng").strip() or None
        except Exception:
            return pytesseract.image_to_string(Image.open(path),
                                               lang="eng").strip() or None
    except Exception:
        return None


def parse_image(path: str) -> Dataset:
    name = os.path.basename(path)

    # ---- 主路径：视觉大模型 ----
    try:
        from ..llm import vision
        data = vision(path, VISION_PROMPT, want_json=True)
        header = data.get("header") or []
        rows = data.get("rows") or []
        if header and rows:
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
        # 模型说读不出表，但可能给了文字说明
        note = data.get("note")
        if note:
            return _text_ds(name, note)
    except Exception as e:
        msg = str(e)
        if "未启用大模型" in msg or "未启用" in msg:
            # 主路径不可用，转 OCR 兜底
            ocr = _ocr_text(path)
            if ocr:
                return _text_ds(name, "（未配置视觉模型，已用本地 OCR 识别文字）\n" + ocr)
            return _hint_ds(name)
        # 其它错误（模型返回异常等）也尝试 OCR
        ocr = _ocr_text(path)
        if ocr:
            return _text_ds(name, "（视觉识别失败，已用本地 OCR 兜底）\n" + ocr)
        return _hint_ds(name, f"图片识别失败：{msg}")

    # 模型返回空表且无说明：再试 OCR
    ocr = _ocr_text(path)
    if ocr:
        return _text_ds(name, "（未识别出表格，已用本地 OCR 识别文字）\n" + ocr)
    return _hint_ds(name)


def _text_ds(name: str, text: str) -> Dataset:
    return Dataset(id=uuid.uuid4().hex[:8], name=name, source_type="image",
                   columns=[Column("内容", "text")],
                   rows=[{"内容": ln} for ln in text.splitlines() if ln.strip()],
                   raw_text=text[:5000], total_rows=len(text.splitlines()))


def _hint_ds(name: str, msg: str | None = None) -> Dataset:
    if not msg:
        msg = ("图片结构化识别需要一个支持视觉（vision）的大模型接口。\n"
               "方式一（不改文件，适合分享给别人）：设环境变量后重启服务\n"
               "  RS_LLM_BASE_URL=https://你的模型服务/v1\n"
               "  RS_LLM_API_KEY=你的key\n"
               "  RS_LLM_MODEL=支持图片输入的模型名（如 gpt-4o / qwen-vl-max 等）\n"
               "方式二：在 config.local.yaml 的 llm 段填好下面几项后重启\n"
               "  llm:\n"
               "    enabled: true\n"
               "    base_url: \"https://你的模型服务/v1\"\n"
               "    api_key: \"你的key\"\n"
               "    model: \"支持图片输入的模型名\"\n"
               "    vision_model: \"可选：视觉专用模型名，留空则与 model 相同\"\n"
               "未配接口时，本地若装了 tesseract 也能把图片里的文字抽出来"
               "（纯文字、分不出表格结构）。")
    return Dataset(id=uuid.uuid4().hex[:8], name=name, source_type="image",
                   columns=[], rows=[], warnings=[msg], total_rows=0)
