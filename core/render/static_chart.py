"""matplotlib 静态图：专供 Word / PDF。

网页版用 ECharts 交互图，这里用 matplotlib 画同一份数据，风格尽量贴近，
好处是不依赖 Node 也不依赖本机浏览器，装了就能跑。
"""

from __future__ import annotations

import os
import warnings

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
from matplotlib.ticker import FuncFormatter  # noqa: E402

from ..analyze import AGG_FUNCS  # noqa: E402
from ..model import ChartSpec  # noqa: E402

PALETTE = ["#378ADD", "#1D9E75", "#EF9F27", "#7F77DD",
           "#E24B4A", "#5DCAA5", "#888780", "#D4537E"]
GRID = "#E5E3DC"
INK = "#2C2C2A"
INK2 = "#5F5E5A"


_NO_CJK_WARNED = False


def _setup_font():
    """中文字体：装了什么就用什么，一个都没有就退回 matplotlib 的默认字体。

    原来找不到时硬写 `["Microsoft YaHei"]` —— 那正是「这台机器上没这个字体」
    的情况下必然不存在的一个家族。matplotlib 于是每画一个字都要 findfont 一次、
    失败一次、警告一次：CI 的 ubuntu 腿上一次出图刷出上千行 findfont 警告，
    出图时间也被拖到客户端 120 秒超时。
    现在补上 Linux 常见的几种开源中文字体，最后兜到 DejaVu Sans ——
    没装中文字体的环境里中文照样是方框（以前也是，只是被警告淹没），
    但不再逐字报错，也不再把报告拖死。
    """
    avail = {f.name for f in matplotlib.font_manager.fontManager.ttflist}
    for cand in ("Microsoft YaHei", "SimHei", "DengXian", "KaiTi", "SimSun",
                 "Noto Sans CJK SC", "Source Han Sans SC", "WenQuanYi Zen Hei"):
        if cand in avail:
            plt.rcParams["font.sans-serif"] = [cand, "DejaVu Sans"]
            break
    else:
        plt.rcParams["font.sans-serif"] = ["DejaVu Sans"]
        global _NO_CJK_WARNED
        if not _NO_CJK_WARNED:
            # 一句说明，替代成千上万行 "Glyph ... missing from font(s)"。
            # 方框这件事本身改不了（机器里确实没有中文字体），但用户应该被明确
            # 告知一次「为什么是方框、装什么能好」，而不是被警告刷屏。
            _NO_CJK_WARNED = True
            warnings.warn("本机没有可用的中文字体，静态图里的中文会画成方框；"
                          "装上微软雅黑或 Noto Sans CJK 之类即可。", stacklevel=2)
    plt.rcParams["axes.unicode_minus"] = False


def render_chart(agg: dict, spec: ChartSpec, out_path: str) -> str | None:
    if agg.get("empty") or not agg.get("series"):
        return None
    _setup_font()

    cats = agg["categories"]
    series = agg["series"]
    unit = agg.get("unit", "") or ""
    # 实际用哪种图，以 analyze.aggregate 的决策为准（饼图遇负值会被降级成柱状图）。
    # 这里只负责画，不自己判断 —— 否则网页版和导出件会各画各的。
    ctype = agg.get("effective_type") or spec.type
    # 百分比列底层是小数(0.125)，展示时乘 100 变成 12.5，配合轴标签的 %
    # 单位按**每个系列自己的**算：一张图里同时有销售额和毛利率时，
    # 只取第一个指标的单位会让毛利率按 0.15 画出来、贴在 0 上。
    def _u(name: str) -> str:
        return (agg.get("units") or {}).get(name, unit) or ""

    def _scale_for(name: str):
        return (lambda v: v * 100) if _u(name) == "%" else (lambda v: v)

    def _yname(name: str) -> str:
        u = _u(name)
        if u == "%":
            return f"{name} (%)"
        if u:
            return f"{name} ({u})"
        return name

    def _axis_label(names: list[str]) -> str:
        """共用一根纵轴时把落在这根轴的指标都写上 —— 只写第一个会让人
        以为整根轴都是那个指标的刻度。"""
        parts: list[str] = []
        for n in names:
            t = _yname(n)
            if t not in parts:
                parts.append(t)
        return " / ".join(parts)[:70]

    def _only_pct(names: list[str]) -> bool:
        return bool(names) and all(_u(n) == "%" for n in names)

    scale = _scale_for(series[0]["name"]) if series else (lambda v: v)

    fig, ax = plt.subplots(figsize=(9, 4.2), dpi=160)
    fig.patch.set_facecolor("white")
    ax.set_facecolor("white")

    if ctype == "pie":
        vals = [scale(v) for v in series[0]["data"]]
        ax.pie(vals, labels=cats, autopct="%1.1f%%", startangle=90,
               colors=PALETTE[: max(len(vals), 1)],
               wedgeprops={"width": 0.42, "edgecolor": "white",
                           "linewidth": 1.5})
        ax.axis("equal")

    elif ctype == "scatter":
        pts = series[0]["data"]
        xs = [p[0] for p in pts]
        ys = [scale(p[1]) for p in pts]
        ax.scatter(xs, ys, s=42, color=PALETTE[0], alpha=.8,
                   edgecolors="white", linewidths=.6)
        ax.grid(True, color=GRID, linewidth=.7, alpha=.8)
        ax.set_xlabel(spec.x, color=INK2, fontsize=10)
        ax.set_ylabel(_yname(series[0]["name"]), color=INK2, fontsize=10)

    else:
        n = len(series)
        width = 0.8 / max(n, 1)
        bottom = None
        use_dual = spec.dual_axis and n > 1
        right_names = [s["name"] for i, s in enumerate(series) if use_dual and i > 0]
        left_names = [s["name"] for i, s in enumerate(series)
                      if not (use_dual and i > 0)]
        ax2 = ax.twinx() if use_dual else None
        if ax2 is not None:
            ax2.set_facecolor("white")
            ax2.tick_params(colors=INK2, labelsize=9)
            ax2.spines["top"].set_visible(False)
            ax2.spines["left"].set_visible(False)
            ax2.spines["right"].set_color(GRID)
            ax2.set_ylabel(_axis_label(right_names), color=INK2, fontsize=10)
            if _only_pct(right_names):
                ax2.yaxis.set_major_formatter(FuncFormatter(lambda v, _: f"{v:.1f}%"))
        for i, s in enumerate(series):
            target = ax2 if (use_dual and i > 0) else ax
            # 和网页版共用同一套判定：每个指标各画各的形状
            stype, stacked, area = spec.series_style(s["name"], i)
            xs = list(range(len(cats)))
            off = [x + (i - (n - 1) / 2) * width * 1.05 for x in xs]
            color = PALETTE[i % len(PALETTE)]
            kw = dict(label=s["name"], color=color)
            # 这个系列自己的单位决定要不要 ×100
            data = [_scale_for(s["name"])(v) for v in s["data"]]
            if stype == "line":
                target.plot(xs, data, marker="o", markersize=4,
                            linewidth=1.8, **kw)
                if area:
                    target.fill_between(xs, data, color=color, alpha=.18)
            elif stacked:
                if bottom is None:
                    bottom = [0.0] * len(cats)
                target.bar(xs, data, width=0.55, bottom=bottom, **kw)
                bottom = [b + v for b, v in zip(bottom, data)]
            else:
                target.bar(off, data, width=width, **kw)
        ax.set_xticks(list(range(len(cats))))
        ax.set_xticklabels([str(c) for c in cats],
                           rotation=30 if len(cats) > 8 else 0,
                           ha="right" if len(cats) > 8 else "center",
                           fontsize=9, color=INK2)
        ax.grid(True, axis="y", color=GRID, linewidth=.7, alpha=.8)
        ax.set_axisbelow(True)
        if _only_pct(left_names):
            ax.yaxis.set_major_formatter(FuncFormatter(lambda v, _: f"{v:.1f}%"))
        if n > 1 or use_dual:
            handles, labels = [], []
            for a in (ax, ax2):
                if a is None:
                    continue
                h, l = a.get_legend_handles_labels()
                handles += h
                labels += l
            if handles:
                ax.legend(handles, labels, frameon=False, fontsize=9,
                          loc="upper right")
        if series and not use_dual:
            ax.set_ylabel(_yname(" · ".join(s["name"] for s in series)[:40]),
                          color=INK2, fontsize=10)
        elif series:
            ax.set_ylabel(_yname(series[0]["name"][:40]), color=INK2, fontsize=10)

    for sp in ("top", "right"):
        ax.spines[sp].set_visible(False)
    for sp in ("left", "bottom"):
        ax.spines[sp].set_color(GRID)
    ax.tick_params(colors=INK2, labelsize=9)

    title = spec.title or f"{series[0]['name']} 按 {spec.x}"
    ax.set_title(title, fontsize=12, color=INK, pad=12)

    fig.tight_layout()
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    fig.savefig(out_path, facecolor="white", bbox_inches="tight")
    plt.close(fig)
    return out_path


def _fmt_axis(v: float, _pos=None) -> str:
    if abs(v) >= 1e8:
        return f"{v / 1e8:.1f}亿"
    if abs(v) >= 1e4:
        return f"{v / 1e4:.1f}万"
    return f"{v:,.0f}" if float(v).is_integer() else f"{v:,.1f}"
