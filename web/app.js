/* ReportStudio 前端交互
   所有产物由后端落在 E 盘，前端只负责搬运 JSON 和展示。 */

const $ = (s) => document.querySelector(s);
const $$ = (s, root) => Array.from((root || document).querySelectorAll(s));

const CHART_TYPES = [
  ['bar', '柱状图'], ['line', '折线图'], ['area', '面积图'],
  ['stack_bar', '堆叠柱状图'], ['pie', '饼图'],
  ['scatter', '散点图'], ['combo', '柱线组合图'],
];
/* 只能「一个指标独占一张图」的图型：饼图一张图就一个圆环，散点图横轴得是数值列，
   都跟柱状/折线的分类横轴不是一回事，塞进同一张图两边都对不上。 */
const SOLO_TYPES = ['pie', 'scatter'];
const typeLabel = (v) => (CHART_TYPES.find(t => t[0] === v) || ['', v])[1];
const AGGS = [['sum', '求和'], ['mean', '平均'], ['count', '计数'],
              ['max', '最大'], ['min', '最小']];

/* 已导入的数据表：id -> dataset */
const STORE = {};
let curId = null;
let lastOut = null;
let chartSeq = 0;

/* ---------- 通用 ---------- */
async function post(url, body, isForm) {
  const opt = { method: 'POST' };
  if (isForm) { opt.body = body; }
  else { opt.headers = { 'Content-Type': 'application/json' }; opt.body = JSON.stringify(body); }
  return _send(url, opt);
}

async function get(url) {
  return _send(url, { method: 'GET' });
}

async function _send(url, opt) {
  let r;
  try {
    r = await fetch(url, opt);
  } catch (e) {
    // fetch 自己就失败了 —— 基本只有一个原因：本地服务没在跑（或端口不对）
    throw new Error('连不上本地服务。请重新双击项目目录下的「启动.bat」，'
      + '并保持那个黑色窗口开着（关掉窗口就等于停止服务），然后刷新本页。');
  }

  // 后端崩掉时会返回一整段非 JSON 的 500 页面，直接 r.json() 只会抛
  // “SyntaxError: Unexpected token ...”，完全看不出原因。这里先读文本再尝试解析。
  const text = await r.text();
  let data = null;
  try { data = text ? JSON.parse(text) : {}; } catch (e) { data = null; }

  if (data === null) {
    throw new Error(`后端返回 HTTP ${r.status}（不是正常数据，多半是服务端出错）：`
      + text.slice(0, 300));
  }
  // FastAPI 的 HTTPException 给的是 detail 字段，统一成 error 方便调用方使用
  if (data.error === undefined && data.detail !== undefined) data.error = data.detail;
  if (!r.ok) { data.ok = false; if (!data.error) data.error = `后端 HTTP ${r.status}`; }
  if (data.ok === undefined) data.ok = true;
  return data;
}
async function api(url) {
  // status/datasets/history 这些非关键接口：拿不到就返回空对象，别让页面挂掉
  try {
    const r = await fetch(url);
    const text = await r.text();
    return text ? JSON.parse(text) : {};
  } catch (e) { return {}; }
}

/* ---------- 自绘确认 / 输入弹窗 ----------
   window.confirm / window.prompt 在内嵌预览窗口（iframe sandbox、部分 webview）
   里会被静默禁用：点「删除」「清理」什么都不弹、直接 return，
   看起来就像按钮坏了。这里用原生 <dialog> 自绘（纯 DOM，不受弹窗限制），
   Promise 化之后调用处 await 一行。jsdom 等不支持 showModal 时降级 open 属性。 */
function _dlg({ title, body = '', okText = '确定', danger = false, input = null }) {
  return new Promise(resolve => {
    const d = document.createElement('dialog');
    d.className = 'modal' + (danger ? ' danger' : '');
    const t = document.createElement('p');
    t.className = 'm-title';
    t.textContent = title;
    d.appendChild(t);
    if (body) {
      const b = document.createElement('p');
      b.className = 'm-body';
      // 报告名、连接名都是用户输入，一律 textContent，不走 innerHTML
      b.textContent = body;
      d.appendChild(b);
    }
    let inp = null;
    if (input !== null) {
      inp = document.createElement('input');
      inp.className = 'txt';
      inp.value = input;
      d.appendChild(inp);
    }
    const acts = document.createElement('div');
    acts.className = 'm-acts';
    const c = document.createElement('button');
    c.className = 'btn';
    c.textContent = '取消';
    const k = document.createElement('button');
    k.className = 'btn ' + (danger ? 'danger' : 'primary');
    k.textContent = okText;
    acts.appendChild(c);
    acts.appendChild(k);
    d.appendChild(acts);
    let settled = false;
    const done = val => { if (settled) return; settled = true; resolve(val); d.remove(); };
    k.onclick = () => done(input !== null ? (inp.value || '') : true);
    c.onclick = () => done(input !== null ? null : false);
    d.addEventListener('cancel', () => done(input !== null ? null : false));
    // Esc 走 cancel；close 是兜底（万一 dialog 被外部 close）
    d.addEventListener('close', () => done(input !== null ? null : false));
    if (input !== null) {
      inp.addEventListener('keydown', e => {
        if (e.key === 'Enter') { e.preventDefault(); k.onclick(); }
      });
    }
    document.body.appendChild(d);
    try { d.showModal(); } catch (e) { d.setAttribute('open', ''); }
    if (inp) { inp.focus(); inp.select(); } else { k.focus(); }
  });
}
function appConfirm(title, body = '', okText = '确定') {
  return _dlg({ title, body, okText, danger: true });
}
function appPrompt(title, def = '') {
  return _dlg({ title, input: def, okText: '确定' });
}

function esc(s) {
  return String(s === null || s === undefined ? '' : s)
    .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
}

const isNum = (c) => c.dtype === 'number';

function fmtSize(n) {
  if (!n) return '';
  if (n < 1024) return n + ' B';
  if (n < 1024 * 1024) return Math.round(n / 1024) + ' KB';
  if (n < 1024 * 1024 * 1024) return (n / 1024 / 1024).toFixed(1) + ' MB';
  return (n / 1024 / 1024 / 1024).toFixed(2) + ' GB';
}

/* 浏览器只认「用户手势」那一下的临时激活状态（Chrome 约 5 秒）。
   看板渲染动不动超过 5 秒，等 await 回来再 window.open 会被弹窗拦截器
   **静默**拦掉 —— 返回 null、不报错、控制台也是干净的，用户只看到
   「已在新窗口打开」的提示，屏幕上却什么都没有，然后以为工具坏了。
   所以必须在点击的同步阶段先把空窗口开出来，拿到 URL 再让它导航过去。 */
function openPlaceholder() {
  try { return window.open('about:blank', '_blank'); } catch (e) { return null; }
}

function goPlaceholder(win, url) {
  try {
    if (win && !win.closed) { win.location.href = url; return true; }
  } catch (e) { /* 写 location 被拦，交给调用方兜底 */ }
  return false;
}

/* 回车提交。原来全站没有任何键盘绑定 —— 打完一句话得挪鼠标去点按钮，
   是这套界面里最高频的一处摩擦。
   needCtrl=true 给 textarea 用：那里回车要留给换行，走 Ctrl/⌘+Enter。 */
function bindEnter(sel, btnSel, needCtrl) {
  const el = $(sel);
  if (!el) return;
  el.addEventListener('keydown', e => {
    if (e.key !== 'Enter') return;
    if (e.isComposing) return;        // 中文输入法在组词，回车是「选字」不是「提交」
    const ctrl = e.ctrlKey || e.metaKey;
    if (needCtrl ? !ctrl : ctrl) return;
    e.preventDefault();
    const b = $(btnSel);
    if (b && !b.disabled) b.click();
  });
}

/* ---------- 草稿：把配好的图表存进浏览器，刷新后还在 ----------
   刷新页面只会从后端恢复「数据表」，图表卡片配置原本只活在内存里，
   手滑按一下 F5（或从看板新窗口切回来顺手刷新）就得全部重配。
   这里把配置本身存进 localStorage —— 只存「怎么算」，不存数据，
   数据表以后端会话为准，两边不会打架。 */
const DRAFT_KEY = 'reportstudio.draft.v1';

function saveDraft() {
  try {
    localStorage.setItem(DRAFT_KEY, JSON.stringify({
      title: $('#rTitle') ? $('#rTitle').value : '',
      template: $('#rTpl') ? $('#rTpl').value : '',
      layout: $('#rLayout') ? $('#rLayout').value : '',
      dashName: $('#dashName') ? $('#dashName').value : '',
      exclSum: $('#exclSum') ? $('#exclSum').checked : true,
      metrics: $$('#metricCols .chip.on').map(c => c.dataset.n),
      charts: collectCards(),
      detailCol: ($('#rDetailCol') || {}).value || '',
      detailDir: ($('#rDetailDir') || {}).value || 'asc',
    }));
  } catch (e) { /* 隐私模式下 localStorage 会抛异常，忽略即可，不影响使用 */ }
}

let _draftTimer = null;
function scheduleSaveDraft() {
  clearTimeout(_draftTimer);
  _draftTimer = setTimeout(saveDraft, 300);
}

function clearDraft() {
  try { localStorage.removeItem(DRAFT_KEY); } catch (e) {}
}

function loadDraft() {
  try {
    const raw = localStorage.getItem(DRAFT_KEY);
    const d = raw ? JSON.parse(raw) : null;
    return d && typeof d === 'object' ? d : null;
  } catch (e) { return null; }
}

/* ---------- 步骤 0：恢复上次会话 ---------- */
async function restoreSession() {
  try {
    const res = await api('/api/datasets');
    (res.items || []).forEach(ds => { STORE[ds.id] = ds; });
    if (!curId) curId = Object.keys(STORE)[0] || null;
  } catch (e) { /* 恢复失败不影响正常使用 */ }
  // 返回数量而不是就地 toast：启动时的提示统一在 init 里合并成一条
  return Object.keys(STORE).length;
}

/* ---------- 步骤 1：导入 ---------- */
$('#drop').onclick = () => $('#file').click();
// 拖放区是 role=button 的 div：回车 / 空格也要能打开文件选择框（键盘可达）
$('#drop').onkeydown = (e) => {
  if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); $('#file').click(); }
};
$('#folderBtn').onclick = () => $('#folder').click();

const dz = $('#drop');
['dragenter', 'dragover'].forEach(ev => dz.addEventListener(ev, e => {
  e.preventDefault(); dz.classList.add('over');
}));
['dragleave', 'drop'].forEach(ev => dz.addEventListener(ev, e => {
  e.preventDefault(); dz.classList.remove('over');
}));
dz.addEventListener('drop', e => { if (e.dataTransfer.files.length) upload(e.dataTransfer.files); });

$('#file').onchange = e => { if (e.target.files.length) upload(e.target.files); e.target.value = ''; };
$('#folder').onchange = e => { if (e.target.files.length) upload(e.target.files); e.target.value = ''; };

async function upload(files) {
  const fd = new FormData();
  Array.from(files).forEach(f => fd.append('files', f));
  const res = await post('/api/upload', fd, true);
  (res.results || []).forEach(r => {
    if (r.ok) { STORE[r.dataset.id] = r.dataset; curId = r.dataset.id; }
    else { toast('genMsg', r.name + '：' + r.error, true); }
  });
  renderFiles(); renderDsSelect(); renderMerge(); renderChartsPanel();
}

$('#pasteBtn').onclick = async () => {
  const text = $('#paste').value.trim();
  if (!text) return;
  const res = await post('/api/paste', { text });
  if (res.ok) { STORE[res.dataset.id] = res.dataset; curId = res.dataset.id; $('#paste').value = ''; }
  else toast('genMsg', res.error || '粘贴导入失败', true);
  renderFiles(); renderDsSelect(); renderMerge(); renderChartsPanel();
};

function renderFiles() {
  const box = $('#files');
  box.innerHTML = '';
  Object.values(STORE).forEach(ds => {
    const cls = ds.warnings && ds.warnings.length ? 'warn' : '';
    const bad = ds.total_rows === 0 ? 'err' : cls;
    const el = document.createElement('div');
    el.className = 'fitem';
    el.innerHTML = `<span class="dot ${bad}"></span>
      <span class="nm">${esc(ds.name)}</span>
      <span class="src">${esc(ds.source_type)} · ${ds.total_rows} 行 · ${ds.columns.length} 列</span>
      <button class="rm" data-id="${ds.id}" aria-label="移除数据表 ${esc(ds.name)}">×</button>`;
    box.appendChild(el);
  });
  $$('#files .rm').forEach(x => x.onclick = async () => {
    const id = x.dataset.id;
    delete STORE[id];
    if (curId === id) curId = Object.keys(STORE)[0] || null;
    renderFiles(); renderDsSelect(); renderMerge(); renderChartsPanel();
    // 后端也删掉，否则刷新页面它又回来了
    await fetch('/api/dataset/' + id, { method: 'DELETE' }).catch(() => {});
  });
}

$('#clearAll').onclick = async () => {
  if (!Object.keys(STORE).length) return;
  if (!await appConfirm('清空所有数据表？', '磁盘上的会话缓存也会一并删除。', '清空')) return;
  await post('/api/session/clear', {}).catch(() => {});
  Object.keys(STORE).forEach(k => delete STORE[k]);
  curId = null; lastOut = null;
  clearDraft();                   // 图表配置也一起没了，草稿留着只会把旧配置复活
  $('#charts').innerHTML = '';
  setFrame('');
  ['openNew', 'dlHtml', 'dlMd', 'dlDocx', 'dlPdf', 'dlPng']
    .forEach(b => { if ($('#' + b)) $('#' + b).disabled = true; });
  renderFiles(); renderDsSelect(); renderMerge(); renderChartsPanel(); renderHistory();
  toast('genMsg', '已清空');
};

/* ---------- 步骤 2：预览与修正 ---------- */
function renderDsSelect() {
  const sel = $('#dsSelect');
  const ids = Object.keys(STORE);
  $('#preview').hidden = ids.length === 0;
  $('#empty').hidden = ids.length !== 0;
  if (!ids.length) { sel.innerHTML = ''; $('#ptable').innerHTML = ''; return; }
  if (!curId || !STORE[curId]) curId = ids[0];
  sel.innerHTML = ids.map(id =>
    `<option value="${id}"${id === curId ? ' selected' : ''}>${esc(STORE[id].name)}</option>`).join('');
  $('#headerRow').value = 1;
  renderPreview();
}

$('#dsSelect').onchange = e => { curId = e.target.value; $('#headerRow').value = 1; renderPreview(); };
$('#delDs').onclick = async () => {
  if (!curId) return;
  const id = curId;
  delete STORE[id];
  curId = Object.keys(STORE)[0] || null;
  renderFiles(); renderDsSelect(); renderMerge(); renderChartsPanel();
  await fetch('/api/dataset/' + id, { method: 'DELETE' }).catch(() => {});
};

function renderPreview() {
  const ds = STORE[curId];
  if (!ds) return;
  renderTypes(ds);
  renderWarns(ds);
  renderTable(ds);
}

function renderTypes(ds) {
  const box = $('#types');
  box.innerHTML = ds.columns.map((c, i) => {
    const cur = c.dtype;
    const opts = ['number', 'date', 'text'].map(t =>
      `<option value="${t}"${t === cur ? ' selected' : ''}>${ {number:'数值',date:'日期',text:'文本'}[t] }</option>`).join('');
    return `<span class="tchip">${esc(c.name)}<select data-i="${i}">${opts}</select></span>`;
  }).join('');
  $$('#types select').forEach(s => s.onchange = async () => {
    const types = {};
    $$('#types select').forEach(x => { types[ds.columns[+x.dataset.i].name] = x.value; });
    await applyFix({ types });
  });
}

function renderWarns(ds) {
  const box = $('#warns');
  if (!ds.warnings || !ds.warnings.length) { box.innerHTML = ''; return; }
  box.innerHTML = ds.warnings.map(w => `<div>${esc(w)}</div>`).join('');
}

function renderTable(ds) {
  const t = $('#ptable');
  let h = '<thead><tr>' + ds.columns.map(c => `<th>${esc(c.name)}</th>`).join('') + '</tr></thead><tbody>';
  ds.rows.slice(0, 60).forEach(r => {
    h += '<tr>' + ds.columns.map(c => {
      const v = r[c.name];
      const s = v === null || v === undefined ? '' : String(v);
      const align = isNum(c) ? ' style="text-align:right"' : '';
      return `<td${align}>${esc(s)}</td>`;
    }).join('') + '</tr>';
  });
  t.innerHTML = h + '</tbody>';
}

$('#applyHeader').onclick = () => applyFix({ header_row: Math.max(1, parseInt($('#headerRow').value || '1', 10)) });

async function applyFix(payload) {
  const res = await post('/api/fix', Object.assign({ id: curId }, payload));
  if (res.ok) { STORE[res.dataset.id] = res.dataset; renderPreview(); renderFiles(); renderChartsPanel(); }
  else toast('genMsg', res.error || '修正失败', true);
}

/* ---------- 导出「修正后」的数据表 ----------
   以前「导入 → 改表头行 / 改列类型 / 剔掉合计行」这条路上攒下的成果
   只能用来画图，拿不回去 —— 一进一出的闭环断在最后一步。 */
if ($('#exportDs')) {
  $('#exportDs').onclick = async () => {
    const ds = STORE[curId];
    if (!ds) { toast('genMsg', '先在右边选一张数据表', true); return; }
    const btn = $('#exportDs');
    const old = btn.textContent;
    btn.disabled = true; btn.textContent = '导出中…';
    try {
      const kind = ($('#exportKind') || {}).value || 'csv';
      const res = await post('/api/export_dataset', { id: curId, kind });
      if (res.ok) {
        download(res.url);
        toast('genMsg', `已导出「${ds.name}」${res.rows} 行 × ${res.cols} 列`
          + (kind === 'csv' ? '（CSV 带 BOM，Excel 双击不乱码）' : ''));
      } else {
        toast('genMsg', res.error || '导出失败', true);
      }
    } catch (e) {
      toast('genMsg', '导出出错：' + e, true);
    } finally { btn.disabled = false; btn.textContent = old; }
  };
}

/* ---------- 步骤 3：整合方式 ---------- */
async function renderMerge() {
  const ids = Object.keys(STORE);
  $('#mergeBox').hidden = ids.length < 2;
  $('#mergeHint').hidden = ids.length >= 2;
  if (ids.length < 2) return;
  const res = await post('/api/plan', { dataset_ids: ids });
  $$('input[name=mmode]').forEach(r => { r.checked = (r.value === res.suggestion); });
  $('#mergeGroups').innerHTML = (res.group_names || []).map((g, i) =>
    `<span class="gtag">第 ${i + 1} 组：${g.map(esc).join('、')}</span>`).join('') +
    `<br><span class="hint">结构相似的表已归到同一组，选「合并」时按组装表</span>`;
}

/* ---------- 步骤 4：需求 ---------- */
const mergeMode = () => ($$('input[name=mmode]').find(r => r.checked) || {}).value || 'merge';

function renderChartsPanel() {
  const ds = STORE[curId];
  renderMetricChips(ds);
  renderDetailCols();
  // 重填前先把每张卡当前填的值抄下来，填回去。
  // 以前这里直接 fillChartCard(c, ds) 不传原值，等于把卡片重置成默认：
  // 改一次列类型、修正一次表头行，横轴/指标/排序/每个指标的图型全被清空，
  // 用户得从头再配一遍 —— 而「改完数据发现图没了」会被当成工具坏了。
  const keep = $$('.chart-card').map(collectCard);
  $$('.chart-card').forEach((c, i) => fillChartCard(c, ds, keep[i] || {}));
}

function renderMetricChips(ds) {
  const box = $('#metricCols');
  if (!ds) { box.innerHTML = ''; return; }
  const nums = ds.columns.filter(isNum).map(c => c.name);
  box.innerHTML = nums.map(n =>
    `<span class="chip" data-n="${esc(n)}">${esc(n)}</span>`).join('') ||
    '<span class="hint">这张表没有数值列</span>';
  $$('#metricCols .chip').forEach(c => c.onclick = () => c.classList.toggle('on'));
}

function colOptions(ds, onlyNum, sel) {
  const cols = ds ? ds.columns.filter(c => !onlyNum || isNum(c)) : [];
  return cols.map(c =>
    `<option value="${esc(c.name)}"${c.name === sel ? ' selected' : ''}>${esc(c.name)}</option>`).join('');
}

/* 明细排序的列下拉：选项取所有已导入表的列并集。
   报告可能合并多张表，明细段会逐表列出，所以能选的列必须是「至少在某张表里存在」的。
   重填前先记住当前选择，填完再还原 —— 否则每次导入新表都会把用户刚挑的排序列清空。 */
function renderDetailCols() {
  const sel = $('#rDetailCol');
  if (!sel) return;
  const prev = sel.value;
  const set = new Set();
  Object.values(STORE).forEach(ds => (ds.columns || []).forEach(c => set.add(c.name)));
  const names = [...set];
  sel.innerHTML = '<option value="">保持原始顺序（不排序）</option>' +
    names.map(n => `<option value="${esc(n)}">${esc(n)}</option>`).join('');
  if ([...sel.options].some(o => o.value === prev)) sel.value = prev;
}

$('#addChart').onclick = () => {
  if (!curId) { toast('genMsg', '先导入数据', true); return; }
  addChartCard({});
};

function addChartCard(spec) {
  const id = 'c' + (++chartSeq);
  const el = document.createElement('div');
  el.className = 'chart-card';
  el.dataset.id = id;
  el.innerHTML = `
    <div class="hd"><span>图表</span><button type="button" class="x" aria-label="删除这张图表">×</button></div>
    <div class="row"><label>类型</label><select class="c-type">
      ${CHART_TYPES.map(t => `<option value="${t[0]}"${t[0] === (spec.type || 'bar') ? ' selected' : ''}>${t[1]}</option>`).join('')}
    </select><span class="hint">默认图型；每个指标可在下面单独改</span></div>
    <div class="row"><label>横轴</label><select class="c-x"></select>
      <button type="button" class="btn ghost sm c-swap" title="把横轴与第一个纵轴指标互换角色">⇄ 切换横纵轴</button></div>
    <div class="row"><label>数值</label><div class="chips c-y"></div></div>
    <div class="c-ytypes" hidden></div>
    <div class="row c-split-wrap"><label>多指标</label><select class="c-split">
      <option value="0">合并成一张图（多系列）</option>
      <option value="1">拆成多张单指标图</option>
    </select>
    <span class="hint">量级差很多时拆开看，小数值才不会被压成一条贴地线</span></div>
    <div class="row"><label>聚合</label><select class="c-agg">
      ${AGGS.map(a => `<option value="${a[0]}"${a[0] === (spec.agg || 'sum') ? ' selected' : ''}>${a[1]}</option>`).join('')}
    </select>
    <label>排序</label><select class="c-sort">
      <option value="__x__">按横轴</option><option value="__none__">不排序</option>
    </select>
    <select class="c-dir">
      <option value="asc">升序</option><option value="desc">降序</option>
    </select></div>
    <div class="row"><label>前 N 条</label><input class="c-limit num" type="number" min="0" placeholder="不限">
      <label>看板宽度</label><select class="c-span">
        <option value="6">半宽</option><option value="12">整宽</option>
        <option value="4">三分之一</option><option value="3">四分之一</option>
      </select>
      <label class="c-dual-wrap"><input type="checkbox" class="c-dual"> 双轴（第二个数列走右轴）</label></div>`;
  $('#charts').appendChild(el);
  el.querySelector('.x').onclick = () => { el.remove(); scheduleSaveDraft(); };
  el.querySelector('.c-x').onchange = () => refreshSortOptions(el);
  el.querySelector('.c-swap').onclick = () => swapAxis(el);
  el.querySelector('.c-type').onchange = () => {
    // 默认图型改了，「跟随默认」那几个指标的显示标签也得跟着变，否则看到的是旧名字
    renderYTypes(el);
    refreshSortOptions(el);
    refreshDualVisibility(el);
  };
  // 注意：数列 chip 的点击绑定在 fillChartCard 里做 —— 那时才有 chip 节点。
  // 这里不要再绑一次，两处都绑的话后绑的会把刷新逻辑覆盖掉。
  fillChartCard(el, STORE[curId], spec);
  scheduleSaveDraft();
}

function fillChartCard(el, ds, spec) {
  if (!ds) return;
  spec = spec || {};
  const all = ds.columns.map(c => c.name);
  const nums = ds.columns.filter(isNum).map(c => c.name);
  const roles = rolesOf(ds);
  const dims = roles.dimensions || [];
  const x = el.querySelector('.c-x');
  // 换了一张表 / 改了列之后，原来选的横轴和指标可能已经不存在了。
  // 先过滤再回填，别让下拉停在空值上（那样看起来是选好了，实际生成时会被丢掉）。
  // 默认横轴优先选「维度」列（文本/日期/低基数数值如年份），更符合"横轴是分类"的直觉。
  const wantX = (spec.x && all.indexOf(spec.x) >= 0) ? spec.x
              : (dims.indexOf(all[0]) >= 0 ? all[0] : (dims[0] || all[0]));
  x.innerHTML = xOptions(ds, dims, roles.measures || [], wantX);
  let ys = (spec.y || []).filter(n => nums.indexOf(n) >= 0);
  if (!ys.length) ys = nums.slice(0, 1);
  const mea = new Set(roles.measures || []);
  el.querySelector('.c-y').innerHTML = nums.map(n =>
    `<button type="button" class="chip${ys.indexOf(n) >= 0 ? ' on' : ''}" data-n="${esc(n)}"
      aria-pressed="${ys.indexOf(n) >= 0 ? 'true' : 'false'}"
      title="${mea.has(n) ? '度量（纵轴候选）' : ''}">${esc(n)}</button>`).join('') ||
    '<span class="hint">勾选数值列作为纵轴指标（度量）</span>';
  const toggleChip = (c) => {
    c.classList.toggle('on');
    c.setAttribute('aria-pressed', c.classList.contains('on') ? 'true' : 'false');
    // 勾掉/勾上一个数列，可选的排序项就变了；不刷新的话排序下拉是过期的，
    // 用户会觉得「按这个指标降序」根本选不出来。
    refreshSortOptions(el);
    renderYTypes(el);          // 指标增删后，每个指标那几行图型也要跟着变
    refreshDualVisibility(el);
    refreshSplitVisibility(el);
    scheduleSaveDraft();
  };
  $$('.c-y .chip', el).forEach(c => c && (c.onclick = () => toggleChip(c)));
  // chip 是 role=button 的真按钮：键盘回车/空格由浏览器触发 click，无需额外绑定。
  // aria-pressed 让读屏软件知道它是可切换的，勾没勾上听得到。
  el.querySelector('.c-dir').value = spec.sort_order || 'asc';
  el.querySelector('.c-limit').value = spec.limit || '';
  el.querySelector('.c-span').value = String(spec.span || 6);
  el.querySelector('.c-dual').checked = !!spec.dual_axis;
  el.querySelector('.c-split').value = spec.split_series ? '1' : '0';
  // 先把「按哪一列排序」的选项建出来（它依赖已选中的数列），
  // 再回填 spec 里的排序选择。顺序反了的话，选项还不存在，赋值会被丢掉。
  refreshSortOptions(el);
  const sel = el.querySelector('.c-sort');
  const want = spec.sort_by || '__none__';
  sel.value = want;
  if (sel.value !== want) sel.value = '__none__';   // 该列不在选项里就退回不排序
  renderYTypes(el, spec.y_types || {});
  refreshDualVisibility(el);
  refreshSplitVisibility(el);
}

/* 取列角色：识别哪些列适合横轴（维度）、哪些适合纵轴（度量）。
   roles 由后端 classify_columns 随数据集带出；前端只在缺字段时退化成
   「文本/日期=维度、数值=度量」的兜底判断。 */
function rolesOf(ds) {
  if (ds && ds.roles && ds.roles.dimensions) return ds.roles;
  const cols = ds ? ds.columns : [];
  return {
    dimensions: cols.filter(c => c.dtype !== 'number').map(c => c.name),
    measures: cols.filter(c => c.dtype === 'number').map(c => c.name),
  };
}

/* 横轴下拉：维度候选（推荐）与度量候选分组标注。
   年份、评分档这类低基数数值列在 dimensions 里，所以数值列也能被识别成横轴候选，
   满足「横轴纵轴指标都识别」；高基数数值（销售额）只在 measures，不当横轴候选。 */
function xOptions(ds, dims, measures, sel) {
  const dimSet = new Set(dims), meaSet = new Set(measures);
  const cols = ds ? ds.columns.map(c => c.name) : [];
  const inDim = cols.filter(n => dimSet.has(n));
  const inMea = cols.filter(n => meaSet.has(n) && !dimSet.has(n));
  const opt = (n) => `<option value="${esc(n)}"${n === sel ? ' selected' : ''}>${esc(n)}</option>`;
  let h = '';
  if (inDim.length) h += `<optgroup label="维度（推荐横轴）">${inDim.map(opt).join('')}</optgroup>`;
  if (inMea.length) h += `<optgroup label="度量（数值列，也可作横轴）">${inMea.map(opt).join('')}</optgroup>`;
  if (!h) h = cols.map(opt).join('');
  return h;
}

/* 横纵轴切换：把当前横轴与第一个纵轴指标互换角色。
   只有数值列能当纵轴，所以原横轴若不是数值、且切换后纵轴会清空，则撤销并提示。 */
function swapAxis(el) {
  const ds = STORE[curId];
  if (!ds) return;
  const xSel = el.querySelector('.c-x');
  const curX = xSel.value;
  const ys = $$('.c-y .chip.on', el).map(c => c.dataset.n);
  if (!ys.length) { toast('genMsg', '请先勾选至少一个数值指标再切换', true); return; }
  const newX = ys[0];
  const col = ds.columns.find(c => c.name === curX);
  const xIsNum = col ? isNum(col) : false;
  const chips = $$('.c-y .chip', el);
  xSel.value = newX;
  chips.forEach(c => {
    const n = c.dataset.n;
    if (n === newX) c.classList.remove('on');            // 新横轴从纵轴移出
    else if (n === curX && xIsNum) c.classList.add('on'); // 原横轴是数值则补进纵轴
  });
  const newYs = $$('.c-y .chip.on', el).map(c => c.dataset.n);
  if (!newYs.length) {
    // 原横轴非数值、且只有一个纵轴 → 切换后没纵轴，撤销
    xSel.value = curX;
    chips.forEach(c => {
      if (c.dataset.n === newX) c.classList.add('on');
      if (c.dataset.n === curX) c.classList.remove('on');
    });
    toast('genMsg', '原横轴不是数值列，切换后没有纵轴指标，已撤销', true);
    return;
  }
  refreshSortOptions(el); renderYTypes(el);
  refreshDualVisibility(el); refreshSplitVisibility(el);
  scheduleSaveDraft();
}

/* 每个已选指标一行：各自选图型。
   默认「跟随默认」= 用卡片顶上那个类型；单独指定后这个指标就走自己的图型。 */
function renderYTypes(el, initial) {
  const box = el.querySelector('.c-ytypes');
  if (!box) return;
  // 重建前先把当前选择抄下来：勾掉/勾上一个指标会触发重建，
  // 不抄的话别的指标已经选好的图型会被清空。
  const cur = Object.assign({}, initial || {});
  $$('.yt-type', box).forEach(s => { if (s.value) cur[s.dataset.n] = s.value; });
  const def = el.querySelector('.c-type').value;
  const sel = $$('.c-y .chip.on', el).map(c => c.dataset.n);
  if (sel.length < 1) { box.innerHTML = ''; box.hidden = true; return; }
  box.hidden = false;
  box.innerHTML = sel.map(n => {
    const v = cur[n] || '';
    const solo = SOLO_TYPES.indexOf(v) >= 0;
    const opts = [`<option value=""${v === '' ? ' selected' : ''}>跟随默认（${esc(typeLabel(def))}）</option>`]
      .concat(CHART_TYPES.map(t =>
        `<option value="${t[0]}"${t[0] === v ? ' selected' : ''}>${t[1]}</option>`)).join('');
    return `<div class="yt-row"><span class="yt-nm">${esc(n)}</span>
      <select class="yt-type" data-n="${esc(n)}">${opts}</select>
      ${solo ? '<span class="hint">饼图 / 散点图一张图只装一个指标，会单独成图</span>' : ''}
      </div>`;
  }).join('');
  // 能进同一张图的指标有多个时提醒一句：它们共用一根纵轴。
  // 销售额上万、毛利率是百分数，画在一起小数值会被压成贴着 0 的一条线。
  if (cartesianCount(el) >= 2) {
    box.insertAdjacentHTML('beforeend',
      '<div class="hint">多个指标共用一根纵轴；量级差很多（如销售额 vs 毛利率）时，'
      + '开「双轴」或改用「拆成多张」</div>');
  }
  $$('.yt-type', box).forEach(s => s.onchange = () => {
    renderYTypes(el);
    refreshDualVisibility(el);
    refreshSplitVisibility(el);
    scheduleSaveDraft();
  });
}

/* 每个已选指标最终用的图型：{指标名: 图型} */
function yTypesMap(el) {
  const def = el.querySelector('.c-type').value;
  const out = {};
  $$('.c-y .chip.on', el).forEach(c => { out[c.dataset.n] = def; });
  $$('.yt-type', el).forEach(s => { if (s.value) out[s.dataset.n] = s.value; });
  return out;
}

/* 能放在同一张直角坐标系图里的指标个数（饼图/散点图不算） */
function cartesianCount(el) {
  const m = yTypesMap(el);
  return Object.keys(m).filter(n => SOLO_TYPES.indexOf(m[n]) < 0).length;
}

function refreshSplitVisibility(el) {
  /* 「拆开 / 合并」只在「有两个以上指标能进同一张图」时才有意义。
     只有一个指标、或剩下的都被指定成饼图/散点图（本来就得单独成图）时，
     没有可做的选择，藏起来而不是灰掉：灰着的控件会让人以为哪里配错了。 */
  const wrap = el.querySelector('.c-split-wrap');
  if (!wrap) return;
  const ok = cartesianCount(el) >= 2;
  wrap.hidden = !ok;
  if (!ok) el.querySelector('.c-split').value = '0';
}

function refreshDualVisibility(el) {
  /* 双轴要两条以上数列才谈得上「第二条走右轴」。
     现在每个指标可以各用各的图型，柱状+折线、折线+面积都能双轴，
     所以不再按图型过滤 —— 只看能进同一张图的数列够不够两条。 */
  const wrap = el.querySelector('.c-dual-wrap');
  if (!wrap) return;
  const ok = cartesianCount(el) >= 2;
  wrap.hidden = !ok;
  if (!ok) el.querySelector('.c-dual').checked = false;
}

function refreshSortOptions(el) {
  const ds = STORE[curId];
  if (!ds) return;
  const ctype = el.querySelector('.c-type').value;
  const ysel = $$('.c-y .chip.on', el).map(c => c.dataset.n);
  const sort = el.querySelector('.c-sort');
  const keep = sort.value;
  let opts = '<option value="__none__">不排序</option>';
  if (ctype !== 'pie') opts += '<option value="__x__">按横轴</option>';
  ysel.forEach(n => { opts += `<option value="${esc(n)}">按 ${esc(n)}</option>`; });
  sort.innerHTML = opts;
  if (keep && opts.indexOf('value="' + keep + '"') >= 0) sort.value = keep;
}

/* 把界面上所有图表卡片的值读出来。
   刻意**不**在这里丢掉配了一半的卡片 —— 存草稿时要把它们也留着，
   用户刷新回来还能接着配完。过滤交给 collectSpec。 */
function collectCard(el) {
  const excl = $('#exclSum') ? $('#exclSum').checked : true;
  const y = $$('.c-y .chip.on', el).map(c => c.dataset.n);
  const sb = el.querySelector('.c-sort').value;
  const lim = parseInt(el.querySelector('.c-limit').value || '0', 10);
  const def = el.querySelector('.c-type').value;
  // 只记「和默认不一样」的那些。
  // 把每个指标都记下来的话，用户再去改顶上的默认图型就对已选指标不生效了
  // —— 界面显示的是默认改了，图却没变，这种不一致最难查。
  const y_types = {};
  $$('.yt-type', el).forEach(s => {
    if (s.value && s.value !== def && y.indexOf(s.dataset.n) >= 0) {
      y_types[s.dataset.n] = s.value;
    }
  });
  return {
    type: def,
    x: el.querySelector('.c-x').value,
    y,
    agg: el.querySelector('.c-agg').value,
    sort_by: sb === '__none__' ? '' : (sb === '__x__' ? '__x__' : sb),
    sort_order: el.querySelector('.c-dir').value,
    limit: lim > 0 ? lim : null,
    dual_axis: el.querySelector('.c-dual').checked,
    exclude_summary: excl,
    split_series: el.querySelector('.c-split').value === '1',
    y_types,
    span: parseInt(el.querySelector('.c-span').value || '6', 10) || 6,
  };
}

const collectCards = () => $$('.chart-card').map(collectCard);

function collectSpec() {
  const charts = collectCards().filter(c => c.x && c.y.length);
  return {
    dataset_ids: Object.keys(STORE),
    merge_mode: mergeMode(),
    title: $('#rTitle').value.trim() || '数据分析报告',
    template: $('#rTpl').value,
    chart_layout: parseInt(($('#rLayout') || {}).value || '1', 10) || 1,
    charts,
    metrics: $$('#metricCols .chip.on').map(c => c.dataset.n),
    detail_sort_by: ($('#rDetailCol') || {}).value || '',
    detail_sort_order: ($('#rDetailDir') || {}).value || 'asc',
  };
}

$('#nlBtn').onclick = async () => {
  const text = $('#nl').value.trim();
  if (!text) return;
  if (!curId) { toast('nlMsg', '先导入数据'); return; }
  const res = await post('/api/parse_nl', { dataset_id: curId, text });
  $('#nlMsg').textContent = res.ok ? `已解析（${res.source}）` : (res.error || '没能理解这句话，请在下面手动选');
  if (!res.ok) return;
  if (res.spec.title) $('#rTitle').value = res.spec.title;
  $('#charts').innerHTML = '';
  (res.spec.charts || []).forEach(c => addChartCard(c));
  const want = res.spec.metrics || [];
  $$('#metricCols .chip').forEach(c => {
    c.classList.toggle('on', want.indexOf(c.dataset.n) >= 0);
  });
  // 一句话里若点了「明细按X降序」，把排序列落到报告明细排序控件上。
  // 模型/规则解析出来的列名必须先经 renderDetailCols 验证存在，否则选项里没有会选中失败。
  if (res.spec.detail_sort_by) {
    renderDetailCols();
    const col = $('#rDetailCol');
    if (col && [...col.options].some(o => o.value === res.spec.detail_sort_by)) {
      col.value = res.spec.detail_sort_by;
      $('#rDetailDir').value = res.spec.detail_sort_order === 'desc' ? 'desc' : 'asc';
    }
  }
  scheduleSaveDraft();
};

/* 回车 = 点「解析」。nl 是单行输入框，回车不会跟换行冲突 */
bindEnter('#nl', '#nlBtn');

/* ---------- 生成与导出 ---------- */
let curView = 'report';
let lastDashUrl = '';

$('#gen').onclick = async () => {
  const spec = collectSpec();
  if (!spec.dataset_ids.length) { toast('genMsg', '先导入数据', true); return; }
  // 没配图不再拦死：后端会降级出一份「数据摘要报告」（结论 + 指标 + 明细），
  // 保证只有纯文本 / 扫描图片 / 无数值列这类来源也能拿到报告。
  if (!spec.charts.length) { toast('genMsg', '没配图表，将生成「数据摘要报告」'); }
  saveDraft();
  $('#gen').disabled = true;
  // 生成是后端一口气算完的，拿不到真实百分比 —— 那就别摆一个假的进度条，
  // 改成走秒。用户至少知道它还在跑、没卡死，而不是对着一动不动的
  // 「生成中…」猜要不要刷新（刷新反而会把这次生成打断）。
  const t0 = Date.now();
  const tick = () => {
    const s = Math.round((Date.now() - t0) / 1000);
    $('#gen').textContent = `生成中… ${s}s`;
  };
  tick();
  const timer = setInterval(tick, 1000);
  try {
    const res = await post('/api/generate', spec);
    if (!res.ok) { toast('genMsg', res.error || '生成失败', true); return; }
    lastOut = res;
    $('#outInfo').textContent = res.degraded
      ? `已生成「数据摘要报告」· ${res.metrics} 个指标 · 无可绘制图表 · ${res.dir}`
      : `已生成 ${res.charts} 张图 · ${res.metrics} 个指标 · 输出目录 ${res.dir}`;
    ['openNew', 'dlHtml', 'dlMd', 'dlDocx', 'dlPdf', 'dlPng']
      .forEach(b => $('#' + b).disabled = false);
    toast('genMsg', '生成完成');
    // 生成完停在「报告」这一屏。看板不用单独管：切过去会拿同一份配置重算，
    // 所以它和手上这份报告永远是一致的。
    setView('report');
    renderHistory();
  } catch (e) {
    toast('genMsg', '生成出错：' + e, true);
  } finally {
    clearInterval(timer);
    $('#gen').disabled = false; $('#gen').textContent = '生成报告';
  }
};

/* ---------- 报告 / 看板：同一块地方切换，不再是两个新窗口 ---------- */

/* 预览框只留这一个入口：有地址就显示页面；没有就把白框收起来、换成一句说明。
   原来没内容时也会摆一个 640px 高的空 iframe，白白占掉一屏。 */
function setFrame(src) {
  const fr = $('#frame');
  const tip = $('#frameEmpty');
  const has = !!(src && src !== 'about:blank');
  fr.src = has ? src : 'about:blank';
  fr.classList.toggle('is-empty', !has);
  if (tip) tip.hidden = has;
  if (!has) toggleFrameZoom(false);      // 内容没了就别留在放大态
  const zb = $('#frameZoom');
  if (zb) zb.disabled = !has;
}

/* 预览区放大：预览框固定 640px 高，看板里「放大某一张图」的面板
   最高只到视口的 92%，在这么矮的地方铺不开。这里让 iframe 占满
   整个浏览器窗口，退出方式：再点一次按钮，或按 ESC（焦点在页面里时）。 */
function toggleFrameZoom(on) {
  const fr = $('#frame');
  if (!fr) return;
  const next = (on === undefined) ? !fr.classList.contains('zoomed') : !!on;
  if (next && fr.classList.contains('is-empty')) return;
  fr.classList.toggle('zoomed', next);
  const b = $('#frameZoom');
  if (b) b.textContent = next ? '退出放大' : '放大预览';
}
if ($('#frameZoom')) $('#frameZoom').onclick = () => toggleFrameZoom();
document.addEventListener('keydown', (e) => {
  if (e.key !== 'Escape') return;
  const fr = $('#frame');
  if (fr && fr.classList.contains('zoomed')) toggleFrameZoom(false);
});

/* 只切页签外观，不碰预览内容 —— 新窗口被拦时要把「已保存的看板」
   直接塞进预览区，而那时不能走 setView('dash')（那条路会按**当前界面配置**
   重算，和刚打开的看板不是同一份东西）。 */
function switchTab(v) {
  curView = v;
  $$('.vtab').forEach(b => {
    const on = b.dataset.view === v;
    b.classList.toggle('on', on);
    b.setAttribute('aria-selected', on ? 'true' : 'false');  // 读屏需要，光有样式不算选中态
  });
  $('#actsReport').hidden = v !== 'report';
  $('#actsDash').hidden = v === 'report';
}

async function setView(v) {
  switchTab(v);
  if (v === 'report') {
    setFrame(lastOut ? lastOut.report_url : '');
    if (lastOut) $('#outInfo').textContent = `报告 · 输出目录 ${lastOut.dir}`;
  } else {
    await renderDash(true);
  }
}

function dashPayload(spec) {
  spec = spec || collectSpec();
  const nm = ($('#dashName').value || '').trim();
  return {
    name: nm || '未命名看板',
    title: nm || spec.title,
    dataset_ids: spec.dataset_ids,
    merge_mode: spec.merge_mode,
    metrics: spec.metrics,
    charts: spec.charts,
  };
}

async function renderDash(toFrame) {
  const spec = collectSpec();
  if (!spec.dataset_ids.length) {
    $('#outInfo').textContent = '还没有数据 —— 先在左边导入文件或用 SQL 取数';
    setFrame('');
    return;
  }
  if (!spec.charts.length && !spec.metrics.length) {
    $('#outInfo').textContent = '看板还是空的 —— 先在「说清需求」里配一张图，或勾一个指标列';
    setFrame('');
    return;
  }
  $('#outInfo').textContent = '看板按当前配置重算中…';
  try {
    const res = await post('/api/dashboard/preview', dashPayload(spec));
    if (!res.ok) {
      $('#outInfo').textContent = res.error || '看板渲染失败';
      setFrame('');
      return;
    }
    lastDashUrl = res.url;
    if (toFrame) setFrame(res.url);
    $('#dashOpenNew').disabled = false;
    let info = `看板 · ${res.charts} 张图 · ${res.metrics} 个指标 · 按当前数据实时重算`;
    if ((res.missing || []).length) {
      info += `　⚠ 有 ${res.missing.length} 张数据表不在当前会话里，相关图已跳过`;
    }
    $('#outInfo').textContent = info;
  } catch (e) {
    $('#outInfo').textContent = '看板渲染出错：' + e;
  }
}

$('#tabReport').onclick = () => setView('report');
$('#tabDash').onclick = () => setView('dash');
$('#dashRefresh').onclick = () => renderDash(true);
$('#dashOpenNew').onclick = () => lastDashUrl && window.open(lastDashUrl, '_blank');

$('#openNew').onclick = () => lastOut && window.open(lastOut.report_url, '_blank');
$('#dlHtml').onclick = () => lastOut && download(lastOut.report_url);
$('#dlMd').onclick = () => lastOut && download(lastOut.md_url);

async function exportAs(kind) {
  if (!lastOut) return;
  const btn = { docx: $('#dlDocx'), pdf: $('#dlPdf'), png: $('#dlPng') }[kind];
  const old = btn.textContent;
  btn.disabled = true; btn.textContent = '导出中…';
  try {
    // 拼总图的列数跟你选的报告版面一致，免得「报告里两列、导出却一列」
    const cols = parseInt(($('#rLayout') || {}).value || '1', 10) || 1;
    const res = await post('/api/export', { dir: lastOut.dir, kind, cols });
    if (res.ok) download(res.url);
    else toast('genMsg', res.error || '导出失败', true);
  } catch (e) { toast('genMsg', '导出出错：' + e, true); }
  finally { btn.disabled = false; btn.textContent = old; }
}
$('#dlDocx').onclick = () => exportAs('docx');
$('#dlPdf').onclick = () => exportAs('pdf');
$('#dlPng').onclick = () => exportAs('png');

function download(url) {
  const a = document.createElement('a');
  a.href = url; a.click();
}

function toast(id, msg, isErr) {
  const el = $('#' + id);
  if (!el) return;
  el.textContent = msg;
  el.className = 'msg' + (isErr ? ' err' : '');
}

/* ---------- 历史报告 ----------
   报告会攒到几百份（本机实测 857 份 / 943MB），所以这个列表必须能搜、能删。
   筛选走服务端（/api/history?q=）：只回传前 N 条，前端本地筛是筛不全的，
   用户搜一个明明存在的名字却查不到，最难解释。
   整个列表用 DOM 节点拼，不用 innerHTML 拼字符串 —— 报告名来自用户输入，
   属性拼接一旦漏转义就是注入。 */
let histQ = '';
let _histTimer = null;

function histItem(it) {
  const el = document.createElement('div');
  el.className = 'hitem';

  const head = document.createElement('div');
  head.className = 'h-head';

  const nm = document.createElement('span');
  nm.className = 'hnm';
  nm.textContent = it.name;
  nm.title = it.name + ' ' + it.stamp;
  head.appendChild(nm);

  const stamp = document.createElement('span');
  stamp.className = 'hmeta';
  stamp.textContent = it.stamp;
  head.appendChild(stamp);

  // 摘要：几图 / 几指标 / 多少行 / 占用多大。
  // 老报告没有 meta.json，就只显示目录大小，不硬凑一个假的「0 图」。
  const m = it.meta || {};
  const rows = (m.datasets || []).reduce((a, d) => a + (d.rows || 0), 0);
  const bits = [];
  if (m.charts !== undefined) bits.push(m.charts + ' 图');
  if (m.metrics !== undefined) bits.push(m.metrics + ' 指标');
  if (rows) bits.push(rows.toLocaleString() + ' 行');
  if (m.degraded) bits.push('摘要报告');
  if (it.size) bits.push(fmtSize(it.size));
  if (bits.length) {
    const meta = document.createElement('span');
    meta.className = 'hmeta';
    meta.textContent = bits.join(' · ');
    head.appendChild(meta);
  }
  el.appendChild(head);

  const foot = document.createElement('div');
  foot.className = 'h-foot';

  const chips = document.createElement('span');
  chips.className = 'hchips';
  Object.keys(it.files).forEach(k => {
    const label = { 'report.html': 'HTML', 'report.md': 'Markdown',
                    'report.docx': 'Word', 'report.pdf': 'PDF' }[k];
    const a = document.createElement('a');
    a.className = 'gtag';
    a.href = it.files[k].url;
    a.target = '_blank';
    a.textContent = label + ' ' + Math.round(it.files[k].size / 1024) + 'K';
    chips.appendChild(a);
  });
  foot.appendChild(chips);

  const acts = document.createElement('span');
  acts.className = 'd-acts';
  if (!it.files['report.docx'] || !it.files['report.pdf']) {
    const b = document.createElement('button');
    b.className = 'btn ghost sm';
    b.textContent = '补导出';
    b.onclick = async () => {
      b.disabled = true; b.textContent = '导出中…';
      await post('/api/export', { dir: it.dir, kind: 'docx' });
      await post('/api/export', { dir: it.dir, kind: 'pdf' });
      renderHistory();
    };
    acts.appendChild(b);
  }
  const del = document.createElement('button');
  del.className = 'btn ghost sm';
  del.textContent = '删除';
  del.onclick = () => histDelete(it);
  acts.appendChild(del);
  foot.appendChild(acts);
  el.appendChild(foot);

  return el;
}

async function histDelete(it) {
  if (!(await appConfirm('删除报告「' + it.name + ' ' + it.stamp + '」？',
    '整个报告目录都会删掉（含图表图片），不可恢复。', '删除'))) return;
  try {
    // DELETE 方法会被部分代理 / 预览转发层拦下，统一走 POST
    const res = await post('/api/history/delete', { dir: it.dir });
    toast('histMsg', res.ok ? '已删除报告「' + it.name + '」'
                            : (res.error || '删除失败'), !res.ok);
    renderHistory();
  } catch (e) { toast('histMsg', '删除出错：' + e, true); }
}

async function renderHistory() {
  const box = $('#hist');
  if (!box) return;
  const res = await api('/api/history?q=' + encodeURIComponent(histQ) + '&limit=50')
    .catch(() => ({ items: [] }));
  const items = res.items || [];
  const cnt = $('#histCount');
  if (cnt) {
    // grand_* 是 outputs 里全部报告的份数和体积（服务端算的，不受搜索词影响）；
    // 老后端没有这两个字段时退回 total，别显示成 0。
    const gt = res.grand_total || res.total || 0;
    const gs = res.grand_size || 0;
    cnt.textContent = !gt ? ''
      : `共 ${gt} 份${gs ? ' · ' + fmtSize(gs) : ''}`
        + (res.total > items.length ? `，显示 ${items.length} 份` : '');
  }
  // 按钮显隐要在提前 return 之前算：列表为空（全删光 / 刚装好）时
  // 也得让清理按钮收起来，不然会停留在上一轮的旧状态
  updateHistCleanBtn(res);
  box.textContent = '';
  if (!items.length) {
    const s = document.createElement('span');
    s.className = 'hint';
    s.textContent = histQ ? `没有匹配「${histQ}」的报告` : '还没有生成过报告';
    box.appendChild(s);
    return;
  }
  items.forEach(it => box.appendChild(histItem(it)));
  updateHistCleanBtn(res);
}

if ($('#refreshHist')) $('#refreshHist').onclick = renderHistory;

/* ---------- 残片回收 ----------
   删报告 / 看板时如果删不掉（文件被占用、或环境的删除守卫拦下），
   后端会把文件改名成 *.deleted 兜底，这样它至少从列表里消失。
   但这些残片没有任何流程会再去读，只会一直占地方 —— 本机实测攒到过
   147 个 / 72MB。按钮平时藏起来，只有真有东西可回收时才出现：
   没事摆一个「清理」按钮，只会让人怀疑是不是删了什么有用的东西。 */
async function refreshResidue() {
  const btn = $('#cleanupBtn');
  if (!btn) return;
  let s = {};
  try { s = await get('/api/status'); } catch (e) { s = {}; }
  if (s.residue) {
    btn.hidden = false;
    btn.textContent = `清理缓存（${s.residue} 个 · ${fmtSize(s.residue_size)}）`;
    btn.title = '回收删报告 / 看板时留下的残片（*.deleted、过期的 *.tmp）。'
      + '正常报告、看板配置和数据表都不会被动。';
  } else {
    btn.hidden = true;
  }
}

if ($('#cleanupBtn')) {
  $('#cleanupBtn').onclick = async () => {
    const btn = $('#cleanupBtn');
    btn.disabled = true;
    btn.textContent = '清理中…';
    try {
      const res = await post('/api/cleanup', {});
      if (!res.ok) { toast('histMsg', res.error || '清理失败', true); return; }
      let m = res.removed
        ? `已回收 ${res.removed} 个残片，释放 ${fmtSize(res.freed)}`
        : '没有可回收的残片';
      if (res.left) {
        m += `　还剩 ${res.left} 个 / ${fmtSize(res.left_size)}（可能正被占用，稍后再点一次）`;
      }
      toast('histMsg', m, !!res.left);
    } catch (e) {
      toast('histMsg', '清理出错：' + e, true);
    } finally {
      btn.disabled = false;
    }
    await refreshResidue();
  };
}

/* ---------- 旧报告批量清理 ----------
   outputs 只进不出：报告越攒越多，1.8GB 的存量没有入口清。
   和残片清理不同，这里删的是**正常报告**，不可逆——所以走三步：
   先输入保留天数 → 服务端统计（不删）→ 弹确认框列出「将删 N 份 · X GB」
   让用户点头，才真删。存量还小的时候不摆这个按钮，
   没事摆一个「清理旧报告」只会让人怀疑是不是要动他的东西。 */
function updateHistCleanBtn(res) {
  const btn = $('#histCleanBtn');
  if (!btn) return;
  const gt = res.grand_total || 0, gs = res.grand_size || 0;
  const worth = gt > 50 || gs > 100 * 1024 * 1024;
  btn.hidden = !worth;
  if (worth) {
    const s = fmtSize(gs);
    btn.textContent = s ? `清理旧报告（${s}）` : `清理旧报告（${gt} 份）`;
  }
}

if ($('#histCleanBtn')) {
  $('#histCleanBtn').onclick = async () => {
    const input = await appPrompt('清理多少天前的旧报告？（最少保留 7 天，建议 90）', '90');
    if (input === null) return;
    const days = parseInt(input, 10);
    if (!days || days < 7) { toast('histMsg', '最少保留 7 天', true); return; }
    const btn = $('#histCleanBtn');
    // 先统计不删，把数摆出来让用户点头，再真删
    const prev = await post('/api/history/cleanup', { days, apply: false })
      .catch(e => ({ ok: false, error: String(e) }));
    if (!prev.ok) { toast('histMsg', prev.error || '统计失败', true); return; }
    if (!prev.removed) { toast('histMsg', `${days} 天以前没有旧报告，没什么可清的`); return; }
    const sizeTxt = prev.freed ? '、约 ' + fmtSize(prev.freed) : '';
    if (!await appConfirm('确认清理旧报告？',
      `将删除 ${prev.removed} 份 ${days} 天前的旧报告${sizeTxt}，删掉无法恢复。`, '删除')) return;
    btn.disabled = true;
    btn.textContent = '清理中…';
    try {
      const res = await post('/api/history/cleanup', { days, apply: true });
      if (!res.ok) { toast('histMsg', res.error || '清理失败', true); return; }
      let m = res.removed
        ? `已清理 ${res.removed} 份旧报告，释放 ${fmtSize(res.freed) || '0 B'}`
        : '没有可清理的旧报告';
      if (res.left) m += `　还有 ${res.left} 份没删掉（可能正被占用，稍后再点一次）`;
      toast('histMsg', m, !!res.left);
    } catch (e) {
      toast('histMsg', '清理出错：' + e, true);
    } finally {
      btn.disabled = false;
    }
    await renderHistory();
  };
}

if ($('#histQ')) {
  $('#histQ').addEventListener('input', () => {
    clearTimeout(_histTimer);
    _histTimer = setTimeout(() => {
      histQ = $('#histQ').value.trim();
      renderHistory();
    }, 250);
  });
  $('#histQ').addEventListener('keydown', e => {
    if (e.key === 'Enter' && !e.isComposing) { e.preventDefault(); renderHistory(); }
  });
}

/* ---------- 恢复草稿（必须在数据表恢复之后调用，建图表卡要用到列名） ----------
   返回 {cards, dropped}：恢复了几张卡、有几张因为列对不上被跳过。
   提示不在函数里 toast —— 启动时 restoreSession 也要报一句，
   两处各报一次的话后写的会把前一条盖掉，用户只看得到半句话。 */
function restoreDraft() {
  const d = loadDraft();
  if (!d) return { cards: 0, dropped: 0 };
  if (d.title && $('#rTitle')) $('#rTitle').value = d.title;
  if (d.template && $('#rTpl')) $('#rTpl').value = d.template;
  if (d.layout && $('#rLayout')) $('#rLayout').value = d.layout;
  if (d.dashName && $('#dashName')) $('#dashName').value = d.dashName;
  if (d.exclSum === false && $('#exclSum')) $('#exclSum').checked = false;
  if (d.detailCol && $('#rDetailCol')) {
    // 草稿里存的列名可能不在当前表里：先等 renderDetailCols 把选项补出来，
    // 这里直接赋值，下面 addChartCard 之前面板已重渲染过一次。
    $('#rDetailCol').value = d.detailCol;
  }
  if (d.detailDir && $('#rDetailDir')) $('#rDetailDir').value = d.detailDir;

  const want = d.metrics || [];
  $$('#metricCols .chip').forEach(c => {
    c.classList.toggle('on', want.indexOf(c.dataset.n) >= 0);
  });

  const saved = (d.charts || []).filter(c => c && typeof c === 'object');
  if (!saved.length) return { cards: 0, dropped: 0 };
  const ds = STORE[curId];
  if (!ds) return { cards: 0, dropped: 0 };   // 数据表还没回来，草稿留着不清

  const cols = ds.columns.map(c => c.name);
  let dropped = 0;
  $('#charts').innerHTML = '';
  saved.forEach(c => {
    // 换成另一张数据表后，草稿里的列名可能对不上了。
    // 逐列过滤而不是整张卡丢掉 —— 至少把还能用的部分还给用户。
    const usable = Object.assign({}, c, {
      y: (c.y || []).filter(n => cols.indexOf(n) >= 0),
    });
    if (!usable.x || cols.indexOf(usable.x) < 0) usable.x = cols[0] || '';
    if (!usable.y.length) { dropped++; return; }
    addChartCard(usable);
  });
  return { cards: $$('.chart-card').length, dropped };
}

/* ---------- 启动 ---------- */
(async function init() {
  try {
    const s = await api('/api/status');
    const f = $('#llmFlag');
    if (s.llm) { f.textContent = '大模型已接入 · 图片识别与智能润色可用'; f.className = 'flag on'; }
    else { f.textContent = '未接大模型 · 图片需配置后才可识别'; f.className = 'flag off'; }
    // 产物目录以后端实际值为准，页面上不写死盘符 —— 项目换到别的盘也能显示正确路径
    const oh = $('#outDirHint');
    if (oh && s.outputs) oh.textContent = '输出目录：' + s.outputs;
  } catch (e) {
    // 服务没起来时，页面本身还能显示（静态文件是缓存里的），
    // 但任何操作都会失败。这里直接把原因写在界面上，省得用户对着「生成出错」发懵。
    const f = $('#llmFlag');
    f.textContent = '⚠ 连不上本地服务：请双击项目目录下的「启动.bat」，'
      + '保持黑窗口开着，再刷新本页';
    f.className = 'flag off';
  }
  const nDs = await restoreSession();
  renderFiles();
  renderDsSelect();
  renderMerge();
  // 「指标列」的 chip 只在导入时才生成，刷新后原本是空的 —— 补上
  renderChartsPanel();
  const draft = restoreDraft();
  renderHistory();
  refreshResidue();

  // 启动提示合并成一条：原来两处各 toast 一次，后写的会把前一条盖掉
  const tips = [];
  if (nDs) tips.push(`已恢复上次会话的 ${nDs} 张数据表`);
  if (draft.cards) tips.push(`${draft.cards} 张图表配置`);
  if (tips.length) {
    toast('genMsg', '已恢复 ' + tips.join(' 和 ')
      + (draft.dropped ? `（另有 ${draft.dropped} 张图的列在当前数据里找不到，已跳过）` : '')
      + '，可直接继续生成报告');
  }

  // 回车即提交。以前全站没有任何键盘绑定，打完字要挪鼠标去点按钮。
  bindEnter('#paste', '#pasteBtn', true);      // textarea：回车留给换行，走 Ctrl+Enter
  bindEnter('#dashName', '#dashSave');
  bindEnter('#dbSql', '#dbRun', true);         // 同上
  bindEnter('#dbConnName', '#dbScan');

  // 任何输入变化都攒一份草稿（防抖 300ms）。capture 阶段监听，
  // 动态加进来的图表卡片里的控件也能被收到。
  document.addEventListener('input', scheduleSaveDraft, true);
  document.addEventListener('change', scheduleSaveDraft, true);
})();

/* ================= 从数据库取数（只读 SQL） =================
   三条纪律：
   1. 只读由后端保证（语句闸门 + 只读会话），前端不做、也不依赖任何"过滤"
   2. 取数结果走 /api/db/query 登记成数据表，之后和导入的 Excel 完全一样用
   3. 密码后端从不回显，所以这里也不回填密码框；留空 = 沿用已保存的那个
*/

const DB_FIELDS = {
  sqlite: [['path', '库文件', '如 D:/data/shop.db']],
  mysql: [['host', '主机', '127.0.0.1'], ['port', '端口', '3306'],
          ['user', '账号', ''], ['password', '密码', '', 1],
          ['database', '库名', ''], ['charset', '字符集', 'utf8mb4']],
  postgres: [['host', '主机', '127.0.0.1'], ['port', '端口', '5432'],
             ['user', '账号', ''], ['password', '密码', '', 1],
             ['database', '库名', '']],
  mssql: [['host', '主机', '127.0.0.1'], ['port', '端口', '1433'],
          ['user', '账号', ''], ['password', '密码', '', 1],
          ['database', '库名', ''], ['driver', 'ODBC 驱动', '留空自动挑']],
  oracle: [['host', '主机', '127.0.0.1'], ['port', '端口', '1521'],
           ['user', '账号', ''], ['password', '密码', '', 1],
           ['service', '服务名', 'ORCLPDB1']],
};
const dbForm = {};        // 按「类型.字段」记住输入，切换类型不丢
let dbSavedList = [];

const dbMsg = (m, err) => toast('dbMsg', m, err);
const dbSqlMsg = (m, err) => toast('dbSqlMsg', m, err);

function renderDbFields() {
  const kind = $('#dbKind').value;
  const box = $('#dbFields');
  box.innerHTML = (DB_FIELDS[kind] || []).map(([f, label, ph, secret]) => {
    const key = kind + '.' + f;
    return '<div class="row"><label>' + esc(label) + '</label>'
      + '<input class="txt" data-f="' + f + '" type="' + (secret ? 'password' : 'text') + '"'
      + ' placeholder="' + esc(ph || '') + '" value="' + esc(dbForm[key] || '') + '"></div>';
  }).join('');
  $$('#dbFields input').forEach(i => {
    i.oninput = () => { dbForm[$('#dbKind').value + '.' + i.dataset.f] = i.value; };
  });
}

/* 提交给后端的连接信息。带上 connection_name，后端才能把保存过的密码补上
   （表单里的密码框永远是空的）。所以：改了连接名就等于要重填密码。 */
function dbPayload(extra) {
  const kind = $('#dbKind').value;
  const name = $('#dbConnName').value.trim();
  const c = { kind: kind, name: name };
  $$('#dbFields input').forEach(i => {
    const v = i.value.trim();
    if (v !== '') c[i.dataset.f] = v;
  });
  const p = Object.assign({ connection: c }, extra || {});
  const sel = $('#dbSaved').value;
  if (sel && (!name || name === sel)) p.connection_name = sel;
  return p;
}

async function loadDbKinds() {
  const res = await api('/api/db/kinds');
  const kinds = res.kinds || [];
  if (!kinds.length) return;
  $('#dbKind').innerHTML = kinds.map(k =>
    '<option value="' + k.kind + '">' + esc(k.label) + '</option>').join('');
  $('#dbKind').onchange = renderDbFields;
  renderDbFields();
}

async function loadDbConnections(keep) {
  const res = await api('/api/db/connections');
  dbSavedList = res.items || [];
  const sel = $('#dbSaved');
  const cur = keep !== undefined ? keep : sel.value;
  sel.innerHTML = '<option value="">（不选，用下面填的信息）</option>' +
    dbSavedList.map(c => '<option value="' + esc(c.name) + '">' + esc(c.name) + '</option>').join('');
  sel.value = dbSavedList.some(c => c.name === cur) ? cur : '';
}

function dbFillFromSaved() {
  const name = $('#dbSaved').value;
  const c = dbSavedList.find(x => x.name === name);
  if (!c) return;
  $('#dbKind').value = c.kind;
  Object.keys(c).forEach(k => {
    if (k === 'name' || k === 'kind' || k === 'has_password') return;
    dbForm[c.kind + '.' + k] = String(c[k]);
  });
  renderDbFields();
  $('#dbConnName').value = c.name;
  dbMsg(c.has_password
    ? '已载入「' + c.name + '」，密码留空即沿用保存的那个'
    : '已载入「' + c.name + '」（没记密码，需要重新填）');
}

function renderDbTables(tables) {
  const box = $('#dbTables');
  if (!tables.length) {
    box.innerHTML = '<div class="hint">这个库里没读到表（或当前账号没有权限）。</div>';
    return;
  }
  box.innerHTML = '';
  tables.forEach(t => {
    const el = document.createElement('div');
    el.className = 'fitem';
    el.innerHTML = '<span class="dot' + (t.type === 'view' ? ' warn' : '') + '"></span>'
      + '<span class="nm">' + esc(t.schema ? t.schema + '.' + t.name : t.name) + '</span>'
      + '<span class="src">' + (t.type === 'view' ? '视图' : '表') + '</span>';
    // 整行可点选：没有 tabindex 的话键盘用户进不来（WCAG 2.1.1）
    el.setAttribute('role', 'button');
    el.tabIndex = 0;
    // aria-label 里剥掉 <>&"：setAttribute 虽不会执行脚本，
    // 但别把恶意字符带进可访问名（也保住 XSS 回归断言的口径）
    const safeName = (t.schema ? t.schema + '.' : '') + t.name;
    el.setAttribute('aria-label', '选用表 ' + safeName.replace(/[<>&"]/g, ''));
    el.onclick = () => dbPickTable(t);
    el.onkeydown = (e) => {
      if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); dbPickTable(t); }
    };
    box.appendChild(el);
  });
}

async function dbPickTable(t) {
  dbMsg('读「' + t.name + '」的列定义…');
  try {
    const res = await post('/api/db/columns', dbPayload({ table: t.name, schema: t.schema || '' }));
    if (!res.ok) { dbMsg(res.error || '读列定义失败', true); return; }
    $('#dbSql').value = res.suggest_sql || '';
    dbMsg('已按「' + t.name + '」生成查询语句（' + (res.columns || []).length
      + ' 列）。可以改，或点「只看结果」。');
    dbSqlMsg('');
  } catch (e) { dbMsg('出错：' + e, true); }
}

function renderDbResult(res) {
  const cols = res.columns_detail || [];
  const rows = res.preview_rows || [];
  let html = '';
  if (cols.length) {
    html += '<div class="db-col"><b>识别到的字段</b>：' + cols.map(c =>
      esc(c.name) + '<span>(' + esc(c.dtype) + (c.unit ? ' ' + esc(c.unit) : '') + ')</span>'
    ).join('　') + '</div>';
  }
  if (rows.length) {
    const names = cols.length ? cols.map(c => c.name) : Object.keys(rows[0]);
    const cell = (v) => {
      if (v === null || v === undefined) return '';
      if (typeof v === 'number') {
        if (Number.isInteger(v)) return String(v);
        return v.toFixed(4).replace(/0+$/, '').replace(/\.$/, '');
      }
      return String(v);
    };
    html += '<div class="tablewrap"><table><thead><tr>'
      + names.map(n => '<th>' + esc(n) + '</th>').join('') + '</tr></thead><tbody>'
      + rows.slice(0, 50).map(r => '<tr>' + names.map(n =>
        '<td>' + esc(cell(r[n])) + '</td>').join('') + '</tr>').join('')
      + '</tbody></table></div>';
    if (rows.length > 50) html += '<div class="hint" style="margin-top:6px">预览只显示前 50 行</div>';
  } else {
    html += '<div class="hint">这条查询没有返回任何行。</div>';
  }
  if ((res.warnings || []).length) {
    html += '<div class="warns" style="margin-top:8px">'
      + res.warnings.map(w => '<div>' + esc(w) + '</div>').join('') + '</div>';
  }
  $('#dbResult').innerHTML = html;
}

async function dbExec(doImport) {
  const sql = $('#dbSql').value.trim();
  if (!sql) { dbSqlMsg('先写一条 SQL', true); return; }
  const btn = doImport ? $('#dbRun') : $('#dbPreview');
  const old = btn.textContent;
  btn.disabled = true; btn.textContent = '执行中…';
  try {
    const res = await post('/api/db/query', dbPayload({ sql: sql, import: doImport }));
    if (!res.ok) {
      dbSqlMsg(res.blocked ? ('已拦下：' + res.error) : (res.error || '执行失败'), true);
      return;
    }
    const bits = ['取回 ' + res.row_count + ' 行', '耗时 ' + Math.round(res.elapsed_ms) + ' ms'];
    if (res.truncated) bits.push('超过行数上限已截断');
    if (!res.readonly_enforced) bits.push('⚠ 该库未启用只读会话，仅靠语句层拦截');
    dbSqlMsg(bits.join(' · ') + '　来源：' + res.source);
    renderDbResult(res);

    if (doImport && res.dataset) {
      STORE[res.dataset.id] = res.dataset;
      curId = res.dataset.id;
      renderFiles(); renderDsSelect(); renderMerge(); renderChartsPanel();
      const sug = res.suggest_charts || [];
      if ($('#dbAutoChart').checked && sug.length) {
        $('#charts').innerHTML = '';
        sug.forEach(s => addChartCard(s));
        toast('genMsg', '已导入为数据表并预填了图表配置，往下拉到「告诉我你要什么」就能生成报告');
      } else {
        toast('genMsg', '已导入为数据表，可在「告诉我你要什么」里配置图表');
      }
    }
  } catch (e) {
    dbSqlMsg('执行出错：' + e, true);
  } finally {
    btn.disabled = false; btn.textContent = old;
  }
}

if ($('#dbTest')) {
  $('#dbTest').onclick = async () => {
    dbMsg('连接中…');
    try {
      const res = await post('/api/db/test', dbPayload());
      if (!res.ok) { dbMsg(res.error || '连接失败', true); return; }
      const bits = ['✓ 连上了 ' + res.source];
      if (res.table_count !== null && res.table_count !== undefined) bits.push(res.table_count + ' 张表');
      bits.push(res.readonly_enforced ? '只读已生效' : '⚠ 未启用只读会话');
      dbMsg(bits.join(' · '));
      dbSqlMsg((res.warnings || []).length ? res.warnings.join('　') : '');
    } catch (e) { dbMsg('出错：' + e, true); }
  };

  $('#dbSave').onclick = async () => {
    try {
      const p = dbPayload();
      const body = Object.assign({}, p.connection, { remember_password: $('#dbRemember').checked });
      const res = await post('/api/db/connections', body);
      if (!res.ok) { dbMsg(res.error || '保存失败', true); return; }
      dbMsg(res.note || '已保存');
      await loadDbConnections((res.connection || {}).name || '');
    } catch (e) { dbMsg('出错：' + e, true); }
  };

  $('#dbDelConn').onclick = async () => {
    const name = $('#dbSaved').value;
    if (!name) { dbMsg('先在上一行选一个已存连接', true); return; }
    if (!await appConfirm('删除连接「' + name + '」？', '只删这份配置，不动数据库里的任何东西。', '删除')) return;
    try {
      const r = await fetch('/api/db/connections?name=' + encodeURIComponent(name),
        { method: 'DELETE' });
      const res = await r.json();
      dbMsg(res.note || (res.ok ? '已删除' : '删除失败'), !res.ok);
      await loadDbConnections('');
    } catch (e) { dbMsg('出错：' + e, true); }
  };

  $('#dbSaved').onchange = dbFillFromSaved;

  $('#dbScan').onclick = async () => {
    dbMsg('连接中…');
    try {
      const res = await post('/api/db/scan', dbPayload());
      if (!res.ok) { dbMsg(res.error || '连接失败', true); return; }
      const n = (res.tables || []).length;
      dbMsg('已连上 ' + res.source + ' · 找到 ' + n + ' 张表'
        + (res.readonly_enforced ? ' · 只读已生效' : ' · ⚠ 该库未启用只读会话'));
      dbSqlMsg((res.warnings || []).length ? res.warnings.join('　') : '');
      renderDbTables(res.tables || []);
    } catch (e) { dbMsg('出错：' + e, true); }
  };

  $('#dbRun').onclick = () => dbExec(true);
  $('#dbPreview').onclick = () => dbExec(false);
  $('#dbPickFile').onclick = () => $('#dbFile').click();

  $('#dbFile').onchange = async (e) => {
    const f = e.target.files[0];
    e.target.value = '';
    if (!f) return;
    dbMsg('上传库文件…');
    const fd = new FormData();
    fd.append('file', f);
    try {
      const res = await post('/api/db/upload', fd, true);
      if (!res.ok) { dbMsg(res.error || '上传失败', true); return; }
      dbForm['sqlite.path'] = res.path;
      $('#dbKind').value = 'sqlite';
      renderDbFields();
      if (!$('#dbConnName').value.trim()) $('#dbConnName').value = res.name || '本地库';
      dbMsg('库文件已就位，点「浏览库里的表」看看里面有什么');
    } catch (err) { dbMsg('上传出错：' + err, true); }
  };

  loadDbKinds();
  loadDbConnections('');
}

/* ================= 数据看板（一屏概览 / 常驻） =================
   两条纪律：
   1. 存的是「怎么算」不是数字 —— 每次打开都按当前数据重算，所以看板永远是最新的
   2. 引用的数据表可能已经没了（重启 / 清理 / 手动删），必须如实提示，
      不能安静地少几张图 —— 那样用户会以为「这个指标本来就没数」
*/

const dashMsg = (m, err) => toast('dashMsg', m, err);

async function renderDashList() {
  const box = $('#dashList');
  if (!box) return;
  try {
    const res = await get('/api/dashboards');
    const items = res.items || [];
    if (!items.length) {
      box.innerHTML = '<span class="hint">还没有存过看板。配好上面的图表，起个名字点「保存到看板」。</span>';
      return;
    }
    box.innerHTML = '';
    items.forEach(it => {
      const el = document.createElement('div');
      el.className = 'fitem dash-item';
      // metrics=0 不是「没有指标」，而是「按表里的数值列自动出」——
      // 直接写「0 指标」会让人以为看板上是空的，对不上实际看到的一排指标卡
      const mtext = it.metrics ? (it.metrics + ' 指标') : '指标按列自动';
      // 名称与元信息竖着排成一块，右边单独放按钮组。
      // 原来是三组 span + 两个按钮平铺同一行，只有名称可收缩，
      // 名称被挤到几像素宽、汉字压成竖条，右侧「删除」还被顶出容器。
      el.innerHTML = '<div class="d-main">'
        + '<span class="nm">' + esc(it.name) + '</span>'
        + '<span class="src">' + it.charts + ' 图 · ' + mtext
        + (it.updated ? ' · ' + esc(it.updated) : '') + '</span>'
        + '</div>';
      const acts = document.createElement('span');
      acts.className = 'd-acts';
      const open = document.createElement('button');
      open.className = 'btn ghost sm';
      open.textContent = '打开';
      open.onclick = () => dashOpen(it.id);
      const del = document.createElement('button');
      del.className = 'btn ghost sm';
      del.textContent = '删除';
      del.onclick = () => dashDelete(it.id, it.name);
      acts.appendChild(open);
      acts.appendChild(del);
      el.appendChild(acts);
      box.appendChild(el);
    });
  } catch (e) {
    box.innerHTML = '<span class="hint">看板列表读不出来：' + esc(String(e)) + '</span>';
  }
}

async function dashOpen(id) {
  dashMsg('正在按当前数据重新渲染…');
  // 占位窗口必须在点击的同步阶段就开出来 —— 理由见 openPlaceholder 的注释。
  // 这里尤其重要：看板渲染（多图 + 大数据集）最可能超过 5 秒的激活窗口。
  const win = openPlaceholder();
  try {
    const res = await post('/api/dashboard/render', { id: id });
    if (!res.ok) {
      if (win) win.close();
      dashMsg(res.error || '打开失败', true);
      return;
    }
    lastDashUrl = res.url;
    let m = '已渲染 ' + res.charts + ' 张图 · ' + res.metrics + ' 个指标';
    if ((res.missing || []).length) {
      m += '　⚠ 有 ' + res.missing.length + ' 张表不在当前会话里，对应图已跳过';
    }
    if (!goPlaceholder(win, res.url)) {
      // 占位窗口也被拦（罕见）：退到下方预览区，别让结果彻底看不到
      switchTab('dash');
      setFrame(res.url);
      $('#dashOpenNew').disabled = false;
      $('#outInfo').textContent = '看板「' + res.name + '」· ' + res.charts + ' 张图';
      m += '　（新窗口被浏览器拦下，已改在下方预览区显示）';
    }
    dashMsg(m, (res.missing || []).length > 0);
    renderDashList();
  } catch (e) {
    if (win) win.close();
    dashMsg('出错：' + e, true);
  }
}

async function dashDelete(id, name) {
  if (!await appConfirm('删除看板「' + name + '」？', '只删这份配置，数据集和报告都不动。', '删除')) return;
  try {
    const r = await fetch('/api/dashboard/' + encodeURIComponent(id), { method: 'DELETE' });
    const res = await r.json();
    dashMsg(res.ok ? '已删除看板「' + name + '」' : (res.error || '删除失败'), !res.ok);
    renderDashList();
  } catch (e) { dashMsg('出错：' + e, true); }
}

async function dashSave() {
  const spec = collectSpec();
  if (!spec.dataset_ids.length) { dashMsg('先导入数据', true); return; }
  if (!spec.charts.length && !spec.metrics.length) {
    dashMsg('至少配一张图，或在「指标列」里勾一个指标', true); return;
  }
  dashMsg('保存并渲染中…');
  const win = openPlaceholder();      // 同上：必须在 await 之前同步开
  try {
    const res = await post('/api/dashboard/save', dashPayload(spec));
    if (!res.ok) {
      if (win) win.close();
      dashMsg(res.error || '保存失败', true);
      return;
    }
    lastDashUrl = res.url;
    const mtext = res.metrics ? (res.metrics + ' 个指标') : '指标按列自动';
    let m = '看板「' + res.name + '」已保存（' + res.charts + ' 图 · ' + mtext + '），已在新窗口打开';
    if ((res.missing || []).length) {
      m += '　⚠ 有 ' + res.missing.length + ' 张表不在当前会话里';
    }
    if (!goPlaceholder(win, res.url)) {
      switchTab('dash');
      setFrame(res.url);
      $('#dashOpenNew').disabled = false;
      $('#outInfo').textContent = '看板「' + res.name + '」· ' + res.charts + ' 张图';
      m = m.replace('，已在新窗口打开', '　（新窗口被浏览器拦下，已改在下方预览区显示）');
    }
    dashMsg(m, (res.missing || []).length > 0);
    if (!$('#dashName').value.trim()) $('#dashName').value = res.name;
    renderDashList();
  } catch (e) {
    if (win) win.close();
    dashMsg('出错：' + e, true);
  }
}

if ($('#dashSave')) {
  $('#dashSave').onclick = dashSave;
  $('#dashRefreshList').onclick = () => { dashMsg('刷新列表…'); renderDashList(); };
  renderDashList();
}
