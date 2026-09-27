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
/* hidden 必须有兜底：浏览器的 [hidden]{display:none} 属于 UA 样式表，
   作者样式里的 display 一定赢过它。放大层靠 hidden 切换图表/数据表两个视图，
   没有这条 .zb>*{display:flex} 会把数据表照样显示出来。 */
[hidden]{display:none!important}
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
.chart-card{grid-column:span 6;display:flex;flex-direction:column;position:relative}
.chart-card .hd{display:flex;align-items:center;gap:10px;font-size:13px;font-weight:500;
 margin-bottom:6px}
.chart-card .hd span{flex:1}
.chart{width:100%}
/* 每张图自由缩放：右沿拖宽度（改 grid 列数）、下沿拖高度（改 .chart 高度）。
   手柄做在卡片边缘外 5px，靠 grid 的 12px 间隙容得下，不会挡到图本身。
   hidden 兜底那条 [hidden]{display:none!important} 也守着这里 —— 别给手柄加 display。 */
.rz{position:absolute;z-index:6}
.rz-w{right:-6px;top:30px;bottom:14px;width:12px;cursor:col-resize}
.rz-h{left:8px;right:8px;bottom:-6px;height:12px;cursor:row-resize}
.rz:hover{background:linear-gradient(var(--accent),var(--accent)) center/2px 100% no-repeat}
.rz-h:hover{background:linear-gradient(var(--accent),var(--accent)) center/100% 2px no-repeat}
body.rsz,body.rsz *{user-select:none!important;cursor:inherit}
.warn{background:#FAEEDA;border:1px solid #FAC775;border-radius:10px;padding:10px 14px;
 font-size:12px;color:#633806;margin-bottom:14px}
.warn ul{margin:4px 0 0;padding-left:18px}
footer{margin-top:26px;color:var(--ink2);font-size:11px;text-align:center}
.empty{background:var(--card);border:1px dashed var(--line);border-radius:12px;
 padding:40px;text-align:center;color:var(--ink2);font-size:13px}

/* —— 放大某一张图：遮罩 + 面板 —— */
.zb{position:fixed;inset:0;z-index:80;display:flex;align-items:center;
 justify-content:center}
.zb-mask{position:absolute;inset:0;background:rgba(44,44,42,.5)}
.zb-panel{position:relative;width:min(1400px,94vw);height:min(92vh,940px);
 min-width:420px;min-height:320px;
 background:var(--bg);border-radius:14px;display:flex;flex-direction:column;
 overflow:hidden;box-shadow:0 18px 48px rgba(0,0,0,.3)}
/* 「放大了还是小」的兜底：放大面板右下角也能拖大，想看多细看多细 */
.zb-rz{position:absolute;right:0;bottom:0;width:18px;height:18px;cursor:nwse-resize;
 z-index:9;background:linear-gradient(135deg,transparent 50%,var(--line) 50%,var(--line) 60%,transparent 60%,transparent 72%,var(--line) 72%,var(--line) 82%,transparent 82%)}
.zb-hd{display:flex;align-items:center;gap:8px;flex-wrap:wrap;padding:11px 14px;
 background:var(--card);border-bottom:1px solid var(--line)}
.zb-hd b{font-size:15px;font-weight:600}
.zb-sub{font-size:11px;color:var(--ink2)}
.zb-hd .grow{flex:1}
.zb-bd{flex:1;min-height:0;position:relative}
#zbChart{position:absolute;inset:12px}
#zbTable{position:absolute;inset:0;overflow:auto;padding:12px 14px}
button.dl.on{background:var(--accent);color:#fff;border-color:var(--accent)}
button.dl:disabled{opacity:.4;cursor:not-allowed}
button.dl:disabled:hover{border-color:var(--line);color:var(--ink2)}
button.zo{padding:2px 9px;font-size:11px}
.zb table{border-collapse:collapse;font-size:12px;background:var(--card);width:100%}
.zb th,.zb td{border:1px solid var(--line);padding:5px 10px;white-space:nowrap}
.zb th{position:sticky;top:0;background:#F1EFE8;text-align:left;z-index:1}
.zb td.n,.zb th.n{text-align:right;font-variant-numeric:tabular-nums}
.zb tbody tr:nth-child(even){background:#FAFAF8}
.zb .empty{padding:28px}

@media (max-width:1080px){.chart-card{grid-column:span 12}}
@media print{button.dl{display:none}.wrap{max-width:none;padding:0}.zb{display:none}}
"""

JS = r"""
function fs(){var d=document.documentElement;
 if(document.fullscreenElement){document.exitFullscreen();}
 else if(d.requestFullscreen){d.requestFullscreen();}}
function _safe(s){return String(s).replace(/[^\w\u4e00-\u9fa5-]/g,'_').slice(0,40)||'chart';}
function _esc(s){return String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');}

/* ---------------- 放大某一张图 ----------------
   为什么自己画遮罩而不是用 Fullscreen API：看板出现在预览 iframe 里时，
   requestFullscreen 需要父页面给 allowfullscreen，拿不到就报错。
   自绘层在 iframe 里也能铺满整个 iframe 视口，哪儿都可用。 */
var zbInst = null, zbCur = -1;

function _clone(o){ return JSON.parse(JSON.stringify(o)); }

function _zoomOpt(o){
  var c = _clone(o), s = (c.series||[])[0] || {}, t = s.type;
  if (t === 'pie') return c;            // 饼图没有轴，缩放条无从谈起
  c.grid = _clone(c.grid || {});
  c.grid.bottom = Math.max(c.grid.bottom || 60, 96);   // 给滑动条让出位置
  if (t === 'scatter'){
    c.grid.right = Math.max(c.grid.right || 40, 86);
    c.dataZoom = [
      {type:'slider', xAxisIndex:0, filterMode:'none', height:18, bottom:16},
      {type:'slider', yAxisIndex:0, filterMode:'none', width:18, right:14},
      {type:'inside', filterMode:'none'}];
    return c;
  }
  var cats = ((c.xAxis||{}).data || []).length || 0;
  var zs = [{type:'inside'},{type:'slider', height:20, bottom:14}];
  if (cats > 12){
    /* 类目多到一定程度，全塞进视野里标签还是挤成一团 —— 那就白放大了。
       默认让视野里大约落 12 个类目，剩下的用下面的滑动条拖过去。
       （这里给的是百分比：12 个 / 总数 = 视野占比） */
    var end = Math.max(12, Math.round(1200 / cats));
    zs[0].start = 0; zs[0].end = end;
    zs[1].start = 0; zs[1].end = end;
  }
  c.dataZoom = zs;
  return c;
}

/* 表格单元格怎么显示。既要 15.0 别写成 15.0000，也别冒出
   0.30000000000000004 这种浮点尾巴 —— 十进制对二进制做不到精确，得自己截。 */
function _cell(v){
  if (v === null || v === undefined || v === '') return '—';
  if (typeof v === 'boolean') return v ? '是' : '否';
  if (typeof v === 'number'){
    if (!isFinite(v)) return String(v);
    if (Math.abs(v) >= 1e15) return v.toExponential(3);
    return String(Math.round(v * 1e6) / 1e6);
  }
  return String(v);
}

/* 屏幕上显示用的一版：数值加千分位，1280000 一眼能看出是百万还是十万。
   刻意不用 toLocaleString —— 它的输出跟浏览器语言绑定，同一份看板在
   不同机器上可能一个显示 12,000 一个显示 12.000（德语区小数点是逗号）。
   自己插逗号，结果在哪台机器上都一样。 */
function _show(v){
  var s = _cell(v);
  if (typeof v !== 'number' || !isFinite(v) || Math.abs(v) < 1000) return s;
  var neg = (s.charAt(0) === '-'), body = neg ? s.slice(1) : s;
  var dot = body.indexOf('.');
  var int = dot >= 0 ? body.slice(0, dot) : body;
  var frac = dot >= 0 ? body.slice(dot) : '';
  return (neg ? '-' : '') + int.replace(/\B(?=(\d{3})+(?!\d))/g, ',') + frac;
}

function zoomChart(i){
  zbCur = i;
  var zb = document.getElementById('zb');
  var t = TABLES[i] || {columns:[], rows:[]};
  document.getElementById('zbTitle').textContent = TITLES[i] || ('图表 ' + (i+1));
  document.getElementById('zbSub').textContent =
    t.rows.length + ' 行 × ' + t.columns.length + ' 列';
  zb.hidden = false;        // 先显示再 init：容器宽高还是 0 时画不出东西
  zbShow('chart');
  if (zbInst) { zbInst.dispose(); zbInst = null; }
  zbInst = echarts.init(document.getElementById('zbChart'));
  zbInst.setOption(_zoomOpt(OPTS[i]), true);
  zbInst.resize();
}

function zbShow(v){
  var isC = (v === 'chart');
  document.getElementById('zbChart').hidden = !isC;
  document.getElementById('zbTable').hidden = isC;
  document.getElementById('zbTabChart').className = 'dl' + (isC ? ' on' : '');
  document.getElementById('zbTabData').className = 'dl' + (isC ? '' : ' on');
  document.getElementById('zbPng').disabled = !isC;
  if (isC) { if (zbInst) zbInst.resize(); }
  else renderZbTable();
}

function zbClose(){
  document.getElementById('zb').hidden = true;
  if (zbInst) { zbInst.dispose(); zbInst = null; }   // 反复放大别堆 echarts 实例
}

function renderZbTable(){
  var t = TABLES[zbCur] || {columns:[], rows:[]};
  var box = document.getElementById('zbTable');
  if (!t.rows.length){
    box.innerHTML = '<div class="empty">这张图没有可查看的数据</div>'; return;
  }
  var h = '<table><thead><tr><th class="n">#</th>';
  t.columns.forEach(function(c){ h += '<th>' + _esc(c) + '</th>'; });
  h += '</tr></thead><tbody>';
  t.rows.forEach(function(r, i){
    h += '<tr><td class="n" style="color:#8A8880">' + (i+1) + '</td>';
    r.forEach(function(v, j){
      var txt = _show(v);
      if (t.columns[j] === '占比' && typeof v === 'number') txt += '%';
      var cls = (j === 0 || typeof v !== 'number') ? '' : 'n';
      h += '<td class="' + cls + '">' + _esc(txt) + '</td>';
    });
    h += '</tr>';
  });
  box.innerHTML = h + '</tbody></table>';
}

function zbSavePng(){
  if (!zbInst) return;
  var a = document.createElement('a');
  a.href = zbInst.getDataURL({type:'png', pixelRatio:2, backgroundColor:'#fff'});
  a.download = _safe(TITLES[zbCur] || 'chart') + '.png';
  a.click();
}

function zbSaveCsv(){
  var t = TABLES[zbCur];
  if (!t || !t.rows.length) return;
  var cols = t.columns;
  var rows = t.rows.map(function(r){
    return r.map(function(v, j){
      if (v === null || v === undefined) return '';
      if (cols[j] === '占比' && typeof v === 'number') return _cell(v) + '%';
      return String(v);
    });
  });
  var q = function(v){ return /[",\r\n]/.test(v) ? '"' + v.replace(/"/g,'""') + '"' : v; };
  var lines = [cols.map(q).join(',')];
  rows.forEach(function(r){ lines.push(r.map(q).join(',')); });
  /* \ufeff 是 UTF-8 BOM。不带它 Excel 双击打开中文全是乱码，
     用户第一反应会以为「导出坏了」，想不到是编码。 */
  var blob = new Blob(['\ufeff' + lines.join('\r\n')], {type:'text/csv;charset=utf-8;'});
  var a = document.createElement('a');
  a.href = URL.createObjectURL(blob);
  a.download = _safe(TITLES[zbCur] || 'chart') + '.csv';
  a.click();
  setTimeout(function(){ URL.revokeObjectURL(a.href); }, 3000);
}

document.addEventListener('keydown', function(e){
  var zb = document.getElementById('zb');
  if (e.key === 'Escape' && zb && !zb.hidden) zbClose();
});
window.addEventListener('resize', function(){
  var zb = document.getElementById('zb');
  if (zb && !zb.hidden && zbInst) zbInst.resize();
});

/* ================= 每张图自由缩放 =================
   全屏只是把整页放大，图本身还是那点大小 —— 用户要的是「每张图能自己拖大拖小」。
   右沿拖宽度（改 grid 的 span 列数），下沿拖高度（改 .chart 高度）。
   落盘：常驻看板（DASH_ID 是真实 id）多写一份后端，刷新/重渲染都记得；
   预览页（DASH_ID="_preview"）和拷出去的单文件（"_standalone"）只走 localStorage。 */
var _rszLS = 'reportstudio.dashsize.' + (DASH_ID || '_standalone');

function _rszLoad(){ try { return JSON.parse(localStorage.getItem(_rszLS) || 'null'); } catch(e){ return null; } }
function _rszSave(o){ try { localStorage.setItem(_rszLS, JSON.stringify(o)); } catch(e){} }

/* 看板渲染完、echarts 实例就绪后，把上次拖好的尺寸还原回去 */
function applyDashSizes(){
  var s = _rszLoad(); if (!s || !s.span) return;
  for (var i=0; i<CH.length; i++){
    var span = s.span[i], h = s.height ? s.height[i] : null;
    var card = document.getElementById('chart'+i);
    if (!card) continue;
    if (span) card.parentElement.style.gridColumn = 'span ' + span;
    if (h) card.style.height = h + 'px';
    if (CH[i]) CH[i].resize();
  }
}

function _rszPersist(){
  var span = [], height = [], byId = {};
  for (var i=0; i<CH.length; i++){
    var card = document.getElementById('chart'+i); if (!card) continue;
    var m = (card.parentElement.style.gridColumn || '').match(/span\s+(\d+)/);
    var sp = m ? parseInt(m[1],10) : 6;
    var h = card.offsetHeight || 320;
    span.push(sp); height.push(h); byId['chart'+i] = {span: sp, height: h};
  }
  _rszSave({span: span, height: height});
  /* 常驻看板：把尺寸写回后端配置，重新渲染（数据更新后）也还是这个大小。
     预览页/单文件没有真实 id，fetch 会失败 —— 用 catch 吞掉，不弹错。 */
  if (DASH_ID && DASH_ID !== '_preview' && DASH_ID !== '_standalone'){
    try {
      fetch('/api/dashboard/resize', {
        method:'POST', headers:{'Content-Type':'application/json'},
        body: JSON.stringify({id: DASH_ID, sizes: byId})
      }).catch(function(){});
    } catch(e){}
  }
}

function initResize(){
  document.querySelectorAll('.rz').forEach(function(handle){
    handle.addEventListener('pointerdown', function(e){
      e.preventDefault();
      var i = parseInt(handle.getAttribute('data-i'),10);
      var card = document.getElementById('chart'+i).parentElement;
      var chartDiv = document.getElementById('chart'+i);
      var isW = handle.classList.contains('rz-w');
      var grid = card.parentElement;
      var gap = parseFloat(getComputedStyle(grid).columnGap || getComputedStyle(grid).gap || '12') || 12;
      var gW = grid.clientWidth;
      var colW = (gW - gap*11) / 12;
      var startX = e.clientX, startY = e.clientY;
      var startLeft = card.getBoundingClientRect().left;
      var startH = chartDiv.offsetHeight;
      var raf = 0;
      function move(ev){
        if (isW){
          var width = ev.clientX - startLeft;
          var sp = Math.round((width + gap) / (colW + gap));
          sp = Math.max(1, Math.min(12, sp));
          card.style.gridColumn = 'span ' + sp;
        } else {
          var h = Math.max(160, Math.min(960, startH + (ev.clientY - startY)));
          chartDiv.style.height = h + 'px';
        }
        if (!raf) raf = requestAnimationFrame(function(){
          raf = 0; if (CH[i]) CH[i].resize();
        });
      }
      function up(){
        document.removeEventListener('pointermove', move);
        document.removeEventListener('pointerup', up);
        document.body.classList.remove('rsz');
        if (raf) cancelAnimationFrame(raf);
        if (CH[i]) CH[i].resize();
        _rszPersist();
      }
      document.body.classList.add('rsz');
      document.addEventListener('pointermove', move);
      document.addEventListener('pointerup', up);
    });
  });

  /* 放大面板也能拖右下角放大 */
  var zbRz = document.getElementById('zbRz');
  if (zbRz){
    zbRz.addEventListener('pointerdown', function(e){
      e.preventDefault();
      var panel = zbRz.parentElement;
      var sw = panel.offsetWidth, sh = panel.offsetHeight;
      var sx = e.clientX, sy = e.clientY;
      function mv(ev){
        panel.style.width = Math.max(420, sw + (ev.clientX - sx)) + 'px';
        panel.style.height = Math.max(320, sh + (ev.clientY - sy)) + 'px';
        if (zbInst) zbInst.resize();
      }
      function up2(){
        document.removeEventListener('pointermove', mv);
        document.removeEventListener('pointerup', up2);
        document.body.classList.remove('rsz');
      }
      document.body.classList.add('rsz');
      document.addEventListener('pointermove', mv);
      document.addEventListener('pointerup', up2);
    });
  }
}
applyDashSizes();
initResize();
"""

_JS_TAIL = r"""
function dlChart(i){
 var el=document.getElementById('chart'+i);
 var c=echarts.getInstanceByDom(el);if(!c)return;
 var url=c.getDataURL({type:'png',pixelRatio:2,backgroundColor:'#fff'});
 var a=document.createElement('a');a.href=url;
 a.download=_safe(el.previousElementSibling.firstElementChild.textContent)+'.png';
 a.click();}
"""


def _esc(s) -> str:
    """来自用户文件的文本一律转义后再拼 HTML。"""
    s = "" if s is None else str(s)
    return (s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
             .replace('"', "&quot;").replace("'", "&#39;"))


def _js_data(obj) -> str:
    """把一个值序列化成能塞进 `<script>` 块的 JS 字面量。

    "</script>" 必须转义成 "<\\/script>"：option 里带着用户文件的列名/标题，
    万一出现这段字面量，HTML 解析器会就地结束 script 块，整页脚本全废 ——
    而且报错信息完全看不出真正原因。JS 里 \\/ 就等于 /，值本身一点没变。
    """
    return json.dumps(obj, ensure_ascii=False).replace("</", "<\\/")


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


def chart_table(option: dict, x_name: str = "") -> dict:
    """把一张图的 ECharts option 还原成「图上看得见的那些数」。

    为什么反向从 option 解，而不是拿聚合结果再算一遍：
    图上显示的值经历过两道加工 —— 百分比列乘了 100（0.15 画成 15）、
    饼图遇负值会被降级成柱状图。另算一遍必然对不上，
    用户会看到「图上是 15%，表里写 0.15」，然后不知道该信哪个。
    从 option 解出来的，注定就是画在图上的那批数。

    三种图形的数据形状不一样，这里统一成 {columns, rows}：
      柱/线/面积：横轴类目 + 每个系列一列
      饼图：类目 + 数值 + 占比（占比是饼图唯一有意义的东西，顺手补上）
      散点：只有两列，一个点一行

    返回 {"columns": [...], "rows": [[...], ...]}；没数据时 rows 为空列表。
    """
    series = [s for s in (option.get("series") or []) if isinstance(s, dict)]
    if not series:
        return {"columns": [], "rows": []}

    first = series[0]
    stype = first.get("type") or ""

    if stype == "pie":
        pts = [d if isinstance(d, dict) else {} for d in (first.get("data") or [])]
        vals = [p.get("value") for p in pts]
        total = sum(v for v in vals if isinstance(v, (int, float)))
        rows = []
        for p, v in zip(pts, vals):
            share = (round(v / total * 100, 2)
                     if isinstance(v, (int, float)) and total else None)
            rows.append([p.get("name"), v, share])
        return {"columns": [x_name or "类目",
                            first.get("name") or "数值", "占比"],
                "rows": rows}

    if stype == "scatter":
        x_label = (x_name or (option.get("xAxis") or {}).get("name") or "X")
        rows = []
        for p in (first.get("data") or []):
            if isinstance(p, (list, tuple)) and len(p) >= 2:
                rows.append([p[0], p[1]])
        return {"columns": [x_label, first.get("name") or "Y"], "rows": rows}

    cats = list((option.get("xAxis") or {}).get("data") or [])
    cols = [x_name or "类目"]
    cols += [s.get("name") or f"系列{i + 1}" for i, s in enumerate(series)]
    rows = []
    for k, cat in enumerate(cats):
        row = [cat]
        for s in series:
            d = s.get("data") or []
            row.append(d[k] if k < len(d) else None)
        rows.append(row)
    return {"columns": cols, "rows": rows}


def build_dashboard_html(title: str, subtitle: str, datasets: list[Dataset],
                         chart_blocks: list[dict], metric_cards: list[dict],
                         missing: list[str], vendor_path: str,
                         dash_id: str = "") -> str:
    """拼出看板 HTML。

    chart_blocks 的每项要带 option / title / span / height；
    missing 是「看板里引用了、但当前会话里找不到」的数据表名字 ——
    这种情况必须明说，否则看板会安静地少几张图，没人知道为什么。
    dash_id 用于把「每张图的自由缩放尺寸」绑到这份看板上：常驻看板传真实 id，
    预览页传 "_preview"，拷出去的单文件传 "_standalone"。拖完之后尺寸会落回
    这份 id 对应的 localStorage（且常驻看板还会写回后端，重新渲染也记得）。
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
                f'<div class="rz rz-w" data-i="{i}" title="拖拽调整宽度"></div>'
                f'<div class="rz rz-h" data-i="{i}" title="拖拽调整高度"></div>'
                f'<div class="hd"><span>{_esc(b.get("title") or f"图表 {i + 1}")}</span>'
                f'<button class="dl zo" onclick="zoomChart({i})">放大</button>'
                f'<button class="dl zo" onclick="dlChart({i})">存 PNG</button></div>'
                f'<div class="chart" id="chart{i}" style="height:{height}px"></div></div>')
        parts.append("</div>")
    else:
        parts.append('<div class="empty">这个看板还没有配置任何图表。<br>'
                     '回到工具里配好图，再「保存到看板」。</div>')

    parts.append("<footer>由 ReportStudio 生成 · 所有数值由程序计算产出</footer>"
                 "</div>")

    # 放大层的容器。放在 .wrap 外面：它在 .wrap 里的话会被 1600px 的居中
    # 宽度和 overflow 裁掉，看着像「点了没反应」。
    parts.append(
        '<div class="zb" id="zb" hidden>'
        '<div class="zb-mask" onclick="zbClose()"></div>'
        '<div class="zb-panel"><div class="zb-hd">'
        '<b id="zbTitle"></b><span class="zb-sub" id="zbSub"></span>'
        '<span class="grow"></span>'
        '<button class="dl" id="zbTabChart" onclick="zbShow(\'chart\')">图表</button>'
        '<button class="dl" id="zbTabData" onclick="zbShow(\'data\')">数据表</button>'
        '<button class="dl" id="zbPng" onclick="zbSavePng()">存 PNG</button>'
        '<button class="dl" id="zbCsv" onclick="zbSaveCsv()">导出 CSV</button>'
        '<button class="dl" onclick="zbClose()">关闭</button>'
        '</div><div class="zb-bd">'
        '<div id="zbChart"></div><div id="zbTable" hidden></div>'
        '<div class="zb-rz" id="zbRz" title="拖拽放大面板"></div>'
        '</div></div></div>')

    parts.append(_echarts_tag(vendor_path))
    parts.append("<script>")

    # option 只序列化一次，小图和放大层共用 —— 存两份既浪费内存，
    # 又埋下「图上是 A、表里是 B」的隐患。
    titles = [_js_data(b.get("title") or f"图表 {i + 1}")
              for i, b in enumerate(chart_blocks)]
    opts = [_js_data(b.get("option") or {}) for b in chart_blocks]
    tables = [_js_data(chart_table(b.get("option") or {}, b.get("x") or ""))
              for b in chart_blocks]
    parts.append("var DASH_ID=" + _js_data(dash_id or "") + ";")
    parts.append("var TITLES=[" + ",".join(titles) + "];")
    parts.append("var OPTS=[" + ",".join(opts) + "];")
    parts.append("var TABLES=[" + ",".join(tables) + "];")
    parts.append("var CH=[];")   # 每张图的 echarts 实例，缩放后好调 resize()

    for i in range(len(chart_blocks)):
        parts.append(
            f"CH[{i}]=echarts.init(document.getElementById('chart{i}'));"
            f"CH[{i}].setOption(OPTS[{i}]);"
            f"window.addEventListener('resize',function(){{CH[{i}].resize();}});")

    parts.append(_JS_TAIL)
    parts.append(JS)
    parts.append("</script></body></html>")
    return "".join(parts)
