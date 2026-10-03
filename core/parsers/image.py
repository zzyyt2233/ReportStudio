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

VISION_PROMPT = """请识别这张图片里的数据，输出 JSON：
{
  "title": "图片标题（没有就空字符串）",
  "note": "一句话说明数据口径、单位或时间范围（没有就空字符串）",
  "x_axis": "仅当图片是图表（柱状图/折线图/饼图等）时填横轴标题，否则空字符串",
  "series": ["仅图表时填：图例中的系列名，与 header 除第一列外的列一一对应"],
  "header": ["列1", "列2"],
  "rows": [["值1", "值2"]]
}
规则：
1. 只输出图片里真实存在的数字和文字，禁止推测、补全
2. 图片是表格：表头用原文不改写，各行原样抽取
3. 图片是图表（柱状/折线/饼图等）：横轴类目作为第一列，列名用横轴标题
   （没有标题就叫「类目」）；每个图例系列各占一列，列名用图例原文
   （单系列没有图例时用「数值」）；饼图整理成「类目 / 数值」两列
4. 数值单元格只写数字本身（千分位逗号可保留），单位、% 写进 note；
   图表读数来自刻度估读，note 里注明「数值为图上估读」
5. 读不出数据时返回 {"title":"","note":"","x_axis":"","series":[],"header":[],"rows":[]}"""


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
        header = [str(h) for h in (data.get("header") or [])]
        rows = data.get("rows") or []
        if header and rows:
            # 图表图：模型认出了横轴标题但第一列表头写得很泛，换成真实轴名 ——
            # 列名是图表配置里横轴下拉的显示名，「类目」这种词用户认不出是哪来的。
            x_axis = str(data.get("x_axis") or "").strip()
            if x_axis and header[0].strip() in ("", "类目", "项目", "类别", "名称"):
                header[0] = x_axis
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
