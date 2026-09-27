"""生成自包含的 HTML 报告（图表库内联，拷到别的电脑也能双击打开）。"""

from __future__ import annotations

import json
import os
from datetime import datetime

from ..analyze import detail_rows, detail_sort_label
from ..model import Dataset, ReportSpec

CSS = """
:root{--bg:#F7F7F5;--card:#FFFFFF;--ink:#2C2C2A;--ink2:#5F5E5A;--line:#E5E3DC;--accent:#185FA5}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--ink);
 font-family:"Microsoft YaHei","PingFang SC",system-ui,sans-serif;line-height:1.7}
.wrap{max-width:1080px;margin:0 auto;padding:32px 24px 64px}
header{border-bottom:1px solid var(--line);padding-bottom:20px;margin-bottom:24px}
h1{font-size:24px;font-weight:500;margin:0 0 8px}
.meta{color:var(--ink2);font-size:13px}
h2{font-size:17px;font-weight:500;margin:36px 0 14px;padding-left:10px;border-left:3px solid var(--accent)}
.card{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:20px}
.summary{background:var(--card);border:1px solid var(--line);border-radius:12px;
 padding:18px 20px;font-size:14px;color:#3A3A38}
/* 指标卡：数值是主角，口径标签（合计/均值）退成次要信息。
   原来数值和「合计」只隔 4px，渲染出来是「4.11万合计」一整坨，
   读着像连体词；现在三层各留出间距，扫一眼就能分开读。 */
.cards{display:grid;grid-template-columns:repeat(auto-fit,minmax(240px,1fr));gap:16px}
.metric .t{font-size:13px;color:var(--ink2)}
.metric .v{font-size:30px;font-weight:600;line-height:1.15;margin:14px 0 0}
.metric .u{font-size:13px;font-weight:400;color:#8A8880;margin-left:10px}
.metric .d{font-size:12px;color:var(--ink2);border-top:1px solid var(--line);
 margin-top:16px;padding-top:10px;line-height:1.75}
.chart{height:380px}
/* 图表版面：一行 1/2/3 张。并排时单张窄了，高度也得跟着降，否则又高又瘦 */
.chartgrid{display:grid;gap:16px;align-items:start}
.chartgrid.cols1{grid-template-columns:1fr}
.chartgrid.cols2{grid-template-columns:repeat(2,minmax(0,1fr))}
.chartgrid.cols3{grid-template-columns:repeat(3,minmax(0,1fr))}
.chartgrid.cols2 .chart{height:300px}
.chartgrid.cols3 .chart{height:250px}
.chartgrid .chcard{margin:0;page-break-inside:avoid;break-inside:avoid}
@media(max-width:860px){.chartgrid{grid-template-columns:1fr!important}}
table{width:100%;border-collapse:collapse;font-size:13px}
th,td{border-bottom:1px solid var(--line);padding:8px 10px;text-align:left;white-space:nowrap}
th{background:#F1EFE8;color:#444441;font-weight:500;position:sticky;top:0}
/* 排序标记：明细表按哪一列排的，要能一眼看出来。
   只排了不说，用户会对着「顺序怎么变了」怀疑数据被动过。 */
th .srt{color:var(--accent);font-weight:700;margin-left:4px}
th.sorted{color:var(--accent)}
.scroll{max-height:420px;overflow:auto;border:1px solid var(--line);border-radius:12px}
.warn{background:#FAEEDA;border:1px solid #FAC775;border-radius:10px;padding:12px 16px;
 font-size:13px;color:#633806;margin-top:10px}
ul.warn{margin:0;padding-left:20px}
pre.src{background:var(--card);border:1px solid var(--line);border-radius:12px;
 padding:14px;font-size:12px;color:var(--ink2);overflow:auto;max-height:260px;
 font-family:Consolas,"Courier New",monospace}
footer{margin-top:40px;color:var(--ink2);font-size:12px;text-align:center}
.ch.head{display:flex;align-items:center;gap:10px;font-size:14px;font-weight:500;
 margin-bottom:8px}
.ch.head span{flex:1}
button.dl{font:inherit;font-size:12px;font-weight:400;padding:3px 10px;cursor:pointer;
 border:1px solid var(--line);background:var(--bg);color:var(--ink2);border-radius:6px}
button.dl:hover{border-color:var(--accent);color:var(--accent)}
@media print{button.dl{display:none}}
"""


def _echarts_tag(vendor_path: str) -> str:
    if vendor_path and os.path.exists(vendor_path):
        with open(vendor_path, "r", encoding="utf-8") as f:
            return "<script>" + f.read() + "</script>"
    return '<script src="https://cdn.jsdelivr.net/npm/echarts@5.5.0/dist/echarts.min.js"></script>'


def render_report(datasets: list[Dataset], spec: ReportSpec, chart_blocks: list[dict],
                  metric_cards: list[dict], summary: str, out_dir: str,
                  vendor_path: str) -> str:
    os.makedirs(out_dir, exist_ok=True)
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    # 标题、列名、来源名全部来自用户文件，一律先转义再拼 HTML。
    # 不转义的话，一个叫「部门<script>…」的列名就能把报告页面搞坏。
    title = _esc(spec.title)
    sources = _esc("、".join(d.name for d in datasets))

    parts: list[str] = []
    parts.append(f"""<!DOCTYPE html><html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{title}</title><style>{CSS}</style></head><body><div class="wrap">
<header><h1>{title}</h1>
<div class="meta">生成时间 {now} ｜ 数据来源：{sources} ｜ 共 {len(datasets)} 个数据表</div>
</header>""")

    if summary:
        parts.append(f'<h2>摘要结论</h2><div class="summary">{_esc(summary)}</div>')

    if metric_cards:
        parts.append('<h2>关键指标</h2><div class="cards">')
        for c in metric_cards:
            parts.append(
                f'<div class="card metric"><div class="t">{_esc(c["title"])}</div>'
                f'<div class="v">{_esc(c["value"])}'
                f'<span class="u">{_esc(c.get("unit", ""))}</span></div>'
                f'<div class="d">{_esc(c["detail"])}</div></div>'
            )
        parts.append("</div>")

    if chart_blocks:
        # 图表版面：一行 1/2/3 张。图比列数少时按实际数量收窄，
        # 免得只有一张图却只占半列（右边空一块，看着像图没出来）。
        try:
            cols = int(getattr(spec, "chart_layout", 1) or 1)
        except (TypeError, ValueError):
            cols = 1
        cols = min(cols if cols in (1, 2, 3) else 1, len(chart_blocks))
        parts.append('<h2>图表</h2>')
        parts.append(f'<div class="chartgrid cols{cols}">')
        for i, b in enumerate(chart_blocks):
            t = _esc(b.get("title") or f"图表 {i + 1}")
            parts.append(
                f'<div class="card chcard">'
                f'<div class="ch head"><span>{t}</span>'
                f'<button class="dl" onclick="dlChart({i})">保存为 PNG</button></div>'
                f'<div class="chart" id="chart{i}"></div></div>'
            )
        parts.append("</div>")

    if spec.template == "full":
        # 多张表就每张贴一份明细，只显示第一张的话等于把其他来源藏起来了
        # 列名取所有表的并集：排序列只要在其中一张表里存在，顶部说明就如实写出来；
        # 一张都不存在（比如「明细按不存在的列排」）则保持静默，不谎称排了序。
        all_cols = {c.name for d in datasets for c in d.columns}
        sort_note = detail_sort_label(spec, all_cols)
        sby = (getattr(spec, "detail_sort_by", "") or "").strip()
        sdesc = str(getattr(spec, "detail_sort_order", "asc") or "asc") == "desc"
        mark = '<span class="srt">↓</span>' if sdesc else '<span class="srt">↑</span>'
        parts.append('<h2>明细数据'
                     + (f'<span class="meta"> · {_esc(sort_note)}</span>'
                        if sort_note else '')
                     + '</h2>')
        for ds in datasets:
            cols = ds.columns
            if not cols:
                continue
            head = (f'{_esc(ds.name)}　<span class="meta">'
                    f'{ds.total_rows} 行 · {len(cols)} 列</span>')
            if len(datasets) > 1:
                parts.append(f'<div style="font-size:14px;font-weight:500;'
                             f'margin:16px 0 8px">{head}</div>')
            parts.append('<div class="scroll"><table><thead><tr>')
            for c in cols:
                # 被排的那一列表头加个箭头：只排了不说，顺序变了会让人以为数据被动过
                if sby and c.name == sby:
                    parts.append(f'<th class="sorted">{_esc(c.name)}{mark}</th>')
                else:
                    parts.append(f"<th>{_esc(c.name)}</th>")
            parts.append("</tr></thead><tbody>")
            # 先排序再截断：降序 + 只展示 300 行时，看到的必须是真的前 300 名
            for r in detail_rows(ds, spec)[:300]:
                parts.append("<tr>")
                for c in cols:
                    v = r.get(c.name)
                    v = "" if v is None else v
                    if isinstance(v, float):
                        v = f"{v:,.2f}"
                    parts.append(f"<td>{_esc(v)}</td>")
                parts.append("</tr>")
            parts.append("</tbody></table></div>")
            if ds.total_rows > 300:
                shown = (f'，按「{_esc(sby)}」{"降序" if sdesc else "升序"}'
                         f'展示前 300 行') if sby else '，此处展示前 300 行'
                parts.append(f'<div class="meta" style="margin-top:8px">'
                             f'共 {ds.total_rows} 行{shown}</div>')

    # 提示不随模板消失：数据来源、行数截断、缺失值处理、类型冲突这些
    # 恰恰是判断「数字可不可信」的依据，藏起来比不给还糟。
    warns = [w for d in datasets for w in d.warnings]
    if warns:
        parts.append('<h2>解析提示</h2><div class="warn" id="warns"><ul>')
        for w in warns:
            parts.append(f"<li>{_esc(w)}</li>")
        parts.append("</ul></div>")

    if spec.template == "full":
        for d in datasets:
            if d.raw_text:
                parts.append(f'<h2>原始数据摘录 · {_esc(d.name)}</h2><pre class="src">'
                             + _esc(d.raw_text[:3000]) + "</pre>")

    parts.append("<footer>由 ReportStudio 自动生成 · 所有数值由程序计算产出</footer></div>")
    parts.append(_echarts_tag(vendor_path))
    parts.append("<script>")
    for i, b in enumerate(chart_blocks):
        parts.append(
            f"var c{i}=echarts.init(document.getElementById('chart{i}'));"
            f"c{i}.setOption({json.dumps(b['option'], ensure_ascii=False)});"
            f"window.addEventListener('resize',function(){{c{i}.resize();}});"
        )
    parts.append(
        "function _safe(s){return String(s).replace(/[^\\w\\u4e00-\\u9fa5-]/g,'_')"
        ".slice(0,40)||'chart';}"
        "function dlChart(i){"
        "var el=document.getElementById('chart'+i);"
        "var c=echarts.getInstanceByDom(el);if(!c)return;"
        "var url=c.getDataURL({type:'png',pixelRatio:2,backgroundColor:'#fff'});"
        "var a=document.createElement('a');a.href=url;"
        "a.download=_safe(el.previousElementSibling.firstElementChild.textContent)"
        "+'.png';a.click();}"
    )
    parts.append("</script></body></html>")

    path = os.path.join(out_dir, "report.html")
    with open(path, "w", encoding="utf-8") as f:
        f.write("".join(parts))
    return path


def _esc(s) -> str:
    """HTML 转义。所有来自用户文件的文本，拼进 HTML 前都要过这一道。"""
    s = "" if s is None else str(s)
    return (s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
             .replace('"', "&quot;").replace("'", "&#39;"))
