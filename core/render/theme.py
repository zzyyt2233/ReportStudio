"""统一主题源：工作台、生成的报告、生成的看板共用这一套色板。

为什么单独一层：以前工作台（web/style.css）、报告（render/html_report.py）、
看板（render/dashboard.py）各写各的 :root 变量，echarts 色板又是第四处硬编码，
改一次配色要同步四个地方，必然出现「报告和看板撞色但工作台不撞」。
现在深浅两套的取值只在这里定义一次，各处引用。

约定：
- light 是默认主题；深色用 html[data-theme="dark"] 覆盖同一组 CSS 变量。
- 生成的 HTML 两套主题变量都带，页面右上角可现场切换 —— 自包含单文件
  发给别人后也能切，不依赖服务端。
"""

from __future__ import annotations

# CSS 变量 → 值。报告 / 看板模板把 css_block() 的结果直接嵌进 <style>。
# 浅色底走米色系（bg/soft/th/row_alt/line 成套偏暖、卡片留纯白）；
# ink3 与工作台 style.css 的 --ink3 对齐，两边次级文字同色。
LIGHT = {
    "bg": "#F2EDE1",
    "card": "#FFFFFF",
    "ink": "#26282E",
    "ink2": "#5C616C",
    "ink3": "#67665F",
    "line": "#E4DECF",
    "accent": "#185FA5",
    "accent_soft": "#E9F2FA",
    "soft": "#F5F0E3",
    "th": "#EDE6D4",
    "row_alt": "#FBF7EC",
    "warn_bg": "#FDF3E2", "warn_line": "#F0C880", "warn_ink": "#7A4A0B",
    "ok_bg": "#E2F5EC", "ok_line": "#9FE1CB", "ok_ink": "#0F6E56",
    "anom": "#D64545",
}

DARK = {
    "bg": "#0F141C",
    "card": "#1A212C",
    "ink": "#E6EAF2",
    "ink2": "#A8B0BF",
    "ink3": "#8A93A5",
    "line": "#283141",
    "accent": "#5BA1E8",
    "accent_soft": "#17263A",
    "soft": "#141B25",
    "th": "#222B38",
    "row_alt": "#161D27",
    "warn_bg": "#3A2B14", "warn_line": "#7A5A22", "warn_ink": "#F0C880",
    "ok_bg": "#123029", "ok_line": "#2C5E4E", "ok_ink": "#69D9B5",
    "anom": "#F2817A",
}

# echarts 系列色板：light 沉稳、dark 提亮（深色底上低饱和会发灰看不清）。
PALETTE = {
    "light": ["#3E7BFA", "#12A594", "#F5A623", "#7C6FE0", "#E5675C",
              "#3FBF8F", "#6C8EBF", "#D868A8", "#8D94A6", "#C9A227"],
    "dark":  ["#6EA8FF", "#3FD2BC", "#F7C05B", "#A79BFF", "#FF8A80",
              "#5FD39A", "#8FB8E8", "#F28BC4", "#9AA3B5", "#E0C05A"],
}

# echarts 的轴 / 文字 / 网格颜色（这些不能走 CSS 变量，option 是 JS 数据）。
ECHARTS = {
    "light": {"text": "#444441", "axis_label": "#5F5E5A", "axis_line": "#B9B4A6",
              "split": "#EFEBE0", "tooltip_bg": "#FFFFFF", "tooltip_border": "#E4DECF",
              "tooltip_text": "#26282E", "icon": "#5C616C", "icon_border": "#E4DECF"},
    "dark":  {"text": "#A8B0BF", "axis_label": "#98A2B3", "axis_line": "#3A4453",
              "split": "#222B38", "tooltip_bg": "#222B38", "tooltip_border": "#334052",
              "tooltip_text": "#E6EAF2", "icon": "#98A2B3", "icon_border": "#334052"},
}

THEMES = ("light", "dark")

TOKEN_ORDER = ("bg", "card", "ink", "ink2", "ink3", "line", "accent", "accent_soft",
               "soft", "th", "row_alt", "warn_bg", "warn_line", "warn_ink",
               "ok_bg", "ok_line", "ok_ink", "anom")


def norm(theme) -> str:
    """外部传进来的主题名一律净化：认不出来就回 light，别让脏值拼进 CSS。"""
    t = (str(theme or "")).strip().lower()
    return t if t in THEMES else "light"


def tokens(theme: str) -> dict:
    return LIGHT if norm(theme) == "light" else DARK


def palette(theme: str) -> list[str]:
    return PALETTE[norm(theme)]


def echart_colors(theme: str) -> dict:
    return ECHARTS[norm(theme)]


def card_bg(theme: str) -> str:
    """导出 PNG 时的画布底色：和卡片同色，截图贴进深色页面才不留白边。"""
    return tokens(theme)["card"]


def css_block() -> str:
    """报告 / 看板共用的 :root 与 data-theme=dark 变量声明。"""
    def decls(d: dict) -> str:
        return "".join(f"--{k}:{d[k]};" for k in TOKEN_ORDER)
    return (":root{" + decls(LIGHT) + "}\n"
            "html[data-theme=dark]{" + decls(DARK) + "}")
