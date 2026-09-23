"""数据看板渲染：一屏网格概览。

和「报告」的区别：
- 报告是**长文档**——标题 + 摘要段落 + 逐张图 + 明细表，适合往下滚着读、打印成册。
- 看板是**一屏**——指标卡一行 + 图表按网格平铺，打开就一眼看完，适合挂在屏幕上、
  或者汇报时截图。

所以这里不做摘要段落、不做明细表，只把「数字」和「图」摆整齐。
但**解析提示照留**——看板上最容易出事的就是「某个数其实只取回了一部分」，
把提示藏起来等于让人对着半截数据下判断。

视觉沿用报告的浅色商务配色（同一套 CSS 变量），保证看板截图贴进 Word 不违和。
"""

from __future__ import annotations

import json
import os
from datetime import datetime

from ..model import Dataset

# 每张图占几列（12 栅格）。存盘里存的是 span 数字，这里只认这四档，
# 认不出来的一律按半宽处理 —— 免得存盘文件被手改坏后整个看板排不出版。
SPANS = (12, 6, 4, 3)

CSS = """
:root{--bg:#F7F7F5;--card:#FFFFFF;--ink:#2C2C2A;--ink2:#5F5E5A;--line:#E5E3DC;--accent:#185FA5}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--ink);
 font-family:"Microsoft YaHei","PingFang SC",system-ui,sans-serif;line-height:1.6}
.wrap{max-width:1600px;margin:0 auto;padding:20px 20px 40px}
header{display:flex;align-items:flex-end;gap:16px;flex-wrap:wrap;
 border-bottom:1px solid var(--line);padding-bottom:14px;margin-bottom:18px}
h1{font-size:22px;font-weight:500;margin:0}
.sub{color:var(--ink2);font-size:12px}
.grow{flex:1}
button.dl{font:inherit;font-size:12px;font-weight:400;padding:4px 12px;cursor:pointer;
 border:1px solid var(--line);background:var(--card);color:var(--ink2);border-radius:6px}
button.dl:hover{border-color:var(--accent);color:var(--accent)}
/* 指标卡：看板是一屏概览，间距比报告略收敛，但仍然把「标题 / 数值 /
   口径 / 明细」四层分开 —— 之前 4px 的间距让数值和「合计」粘成一个词。 */
.cards{display:grid;grid-template-columns:repeat(auto-fit,minmax(200px,1fr));
 gap:14px;margin-bottom:18px}
.card{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:16px}
.metric .t{font-size:12px;color:var(--ink2)}
.metric .v{font-size:27px;font-weight:600;line-height:1.15;margin:10px 0 0}
.metric .u{font-size:12px;font-weight:400;color:#8A8880;margin-left:8px}
.metric .d{font-size:11px;color:var(--ink2);border-top:1px solid var(--line);
 margin-top:12px;padding-top:8px;line-height:1.7}
.grid{display:grid;grid-template-columns:repeat(12,1fr);gap:12px}
.chart-card{grid-column:span 6;display:flex;flex-direction:column}
.chart-card .hd{display:flex;align-items:center;gap:10px;font-size:13px;font-weight:500;
 margin-bottom:6px}
.chart-card .hd span{flex:1}
.chart{width:100%}
.warn{background:#FAEEDA;border:1px solid #FAC775;border-radius:10px;padding:10px 14px;
 font-size:12px;color:#633806;margin-bottom:14px}
.warn ul{margin:4px 0 0;padding-left:18px}
footer{margin-top:26px;color:var(--ink2);font-size:11px;text-align:center}
.empty{background:var(--card);border:1px dashed var(--line);border-radius:12px;
 padding:40px;text-align:center;color:var(--ink2);font-size:13px}
@media (max-width:1080px){.chart-card{grid-column:span 12}}
@media print{button.dl{display:none}.wrap{max-width:none;padding:0}}
"""


def _esc(s) -> str:
    """来自用户文件的文本一律转义后再拼 HTML。"""
    s = "" if s is None else str(s)
    return (s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
             .replace('"', "&quot;").replace("'", "&#39;"))


def _echarts_tag(vendor_path: str) -> str:
    if vendor_path and os.path.exists(vendor_path):
        with open(vendor_path, "r", encoding="utf-8") as f:
            return "<script>" + f.read() + "</script>"
    return ('<script src="https://cdn.jsdelivr.net/npm/echarts@5.5.0/'
            'dist/echarts.min.js"></script>')


def norm_span(v) -> int:
    try:
        n = int(v)
    except (TypeError, ValueError):
        return 6
    return n if n in SPANS else 6


def build_dashboard_html(title: str, subtitle: str, datasets: list[Dataset],
                         chart_blocks: list[dict], metric_cards: list[dict],
                         missing: list[str], vendor_path: str) -> str:
    """拼出看板 HTML。

    chart_blocks 的每项要带 option / title / span / height；
    missing 是「看板里引用了、但当前会话里找不到」的数据表名字 ——
    这种情况必须明说，否则看板会安静地少几张图，没人知道为什么。
    """
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    sources = "、".join(d.name for d in datasets) or "（无）"

    parts: list[str] = []
    parts.append(f"""<!DOCTYPE html><html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{_esc(title)}</title><style>{CSS}</style></head><body><div class="wrap">
<header><div><h1>{_esc(title)}</h1>
<div class="sub">{_esc(subtitle) if subtitle else ""}
刷新时间 {now} ｜ 数据来源：{_esc(sources)}</div></div>
<div class="grow"></div>
<button class="dl" onclick="fs()">全屏</button>
<button class="dl" onclick="window.print()">打印 / 存 PDF</button>
</header>""")

    if missing:
        parts.append('<div class="warn"><b>有数据表没找到，对应的图已跳过：</b><ul>')
        for m in missing:
            parts.append(f"<li>{_esc(m)}</li>")
        parts.append("</ul>这些表可能被清理了，或服务重启后没恢复。"
                     "把原始文件/数据重新导入一次，再刷新看板即可。</div>")

    if metric_cards:
        parts.append('<div class="cards">')
        for c in metric_cards:
            parts.append(
                f'<div class="card metric"><div class="t">{_esc(c.get("title", ""))}</div>'
                f'<div class="v">{_esc(c.get("value", ""))}'
                f'<span class="u">{_esc(c.get("unit", ""))}</span></div>'
                f'<div class="d">{_esc(c.get("detail", ""))}</div></div>')
        parts.append("</div>")

    if chart_blocks:
        parts.append('<div class="grid">')
        for i, b in enumerate(chart_blocks):
            span = norm_span(b.get("span"))
            height = b.get("height") or 320
            try:
                height = max(200, min(720, int(height)))
            except (TypeError, ValueError):
                height = 320
            parts.append(
                f'<div class="card chart-card" style="grid-column:span {span}">'
                f'<div class="hd"><span>{_esc(b.get("title") or f"图表 {i + 1}")}</span>'
                f'<button class="dl" onclick="dlChart({i})">存 PNG</button></div>'
                f'<div class="chart" id="chart{i}" style="height:{height}px"></div></div>')
        parts.append("</div>")
    else:
        parts.append('<div class="empty">这个看板还没有配置任何图表。<br>'
                     '回到工具里配好图，再「保存到看板」。</div>')

    parts.append("<footer>由 ReportStudio 生成 · 所有数值由程序计算产出</footer>"
                 "</div>")
    parts.append(_echarts_tag(vendor_path))

    parts.append("<script>")
    parts.append("function fs(){var d=document.documentElement;"
                 "if(document.fullscreenElement){document.exitFullscreen();}"
                 "else if(d.requestFullscreen){d.requestFullscreen();}}")
    parts.append("function _safe(s){return String(s).replace(/[^\\w\\u4e00-\\u9fa5-]/g,'_')"
                 ".slice(0,40)||'chart';}")
    for i, b in enumerate(chart_blocks):
        opt = json.dumps(b.get("option") or {}, ensure_ascii=False)
        parts.append(
            f"var c{i}=echarts.init(document.getElementById('chart{i}'));"
            f"c{i}.setOption({opt});"
            f"window.addEventListener('resize',function(){{c{i}.resize();}});")
    parts.append(
        "function dlChart(i){"
        "var el=document.getElementById('chart'+i);"
        "var c=echarts.getInstanceByDom(el);if(!c)return;"
        "var url=c.getDataURL({type:'png',pixelRatio:2,backgroundColor:'#fff'});"
        "var a=document.createElement('a');a.href=url;"
        "a.download=_safe(el.previousElementSibling.firstElementChild.textContent)"
        "+'.png';a.click();}")
    parts.append("</script></body></html>")
    return "".join(parts)
