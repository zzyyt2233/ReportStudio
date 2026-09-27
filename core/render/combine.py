"""把多张图拼成一张总图。

用途：报告和看板里的图逐张存太碎，发给别人要发一堆文件。拼成一张长图（或网格图）
一次截图/一次转发就完事。

实现上刻意只用 matplotlib 已经画好的那些 PNG —— 不重新绘图。
原因是重新绘图意味着「网页看到的图」和「拼出来的图」可能不一致
（ECharts 和 matplotlib 的排版差异），而用户会觉得是同一个东西。
"""

from __future__ import annotations

import math
import os

from PIL import Image

MAX_WIDTH = 1800        # 单张图的统一宽度上限，避免长图宽到没法看
GAP = 18                # 图与图之间的间距
MARGIN = 18             # 画布四周留白


def combine_images(paths: list[str], out_path: str, cols: int = 1) -> str | None:
    """按 cols 列拼图，返回产物路径；没有可用输入时返回 None。

    所有图统一到同一宽度再排，否则不同数据算出来的图宽窄不一，
    拼出来会参差不齐。
    """
    paths = [p for p in paths if p and os.path.exists(p)]
    if not paths:
        return None
    if len(paths) == 1 and cols <= 1:
        # 只有一张图，直接复制比重新编码更省事，也不会有二次压缩损失
        os.makedirs(os.path.dirname(out_path), exist_ok=True)
        with Image.open(paths[0]) as im:
            im.convert("RGB").save(out_path, "PNG")
        return out_path

    try:
        cols = int(cols)
    except (TypeError, ValueError):
        cols = 1
    cols = max(1, min(cols, len(paths)))

    images = []
    for p in paths:
        try:
            im = Image.open(p)
            im.load()
            images.append(im.convert("RGB"))
        except Exception:
            # 单张读不出来（半截文件、格式不对）不该让整张总图失败
            continue
    if not images:
        return None

    target_w = min(max(im.width for im in images), MAX_WIDTH)

    def _scaled(im) -> Image.Image:
        if im.width == target_w:
            return im
        h = max(1, round(im.height * target_w / im.width))
        return im.resize((target_w, h), Image.LANCZOS)

    scaled = [_scaled(im) for im in images]
    rows = math.ceil(len(scaled) / cols)
    row_h = []
    for r in range(rows):
        chunk = scaled[r * cols:(r + 1) * cols]
        row_h.append(max(im.height for im in chunk) if chunk else 0)

    canvas_w = MARGIN * 2 + cols * target_w + (cols - 1) * GAP
    canvas_h = MARGIN * 2 + sum(row_h) + (rows - 1) * GAP
    canvas = Image.new("RGB", (canvas_w, canvas_h), (255, 255, 255))

    y = MARGIN
    for r in range(rows):
        chunk = scaled[r * cols:(r + 1) * cols]
        x = MARGIN
        for im in chunk:
            # 行内纵向居中，几行拼下来看着才齐
            canvas.paste(im, (x, y + (row_h[r] - im.height) // 2))
            x += target_w + GAP
        y += row_h[r] + GAP

    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    canvas.save(out_path, "PNG", optimize=True)
    for im in images + scaled:
        try:
            im.close()
        except Exception:
            pass
    return out_path
