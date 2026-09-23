/* ReportStudio 前端交互
   所有产物由后端落在 E 盘，前端只负责搬运 JSON 和展示。 */

const $ = (s) => document.querySelector(s);
const $$ = (s, root) => Array.from((root || document).querySelectorAll(s));

const CHART_TYPES = [
  ['bar', '柱状图'], ['line', '折线图'], ['pie', '饼图'],
  ['scatter', '散点图'], ['stack_bar', '堆叠柱状图'], ['combo', '柱线组合图'],
];
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

function esc(s) {
  return String(s === null || s === undefined ? '' : s)
    .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
}

const isNum = (c) => c.dtype === 'number';

/* ---------- 步骤 0：恢复上次会话 ---------- */
async function restoreSession() {
  try {
    const res = await api('/api/datasets');
    (res.items || []).forEach(ds => { STORE[ds.id] = ds; });
    if (!curId) curId = Object.keys(STORE)[0] || null;
  } catch (e) { /* 恢复失败不影响正常使用 */ }
  const n = Object.keys(STORE).length;
  if (n) toast('genMsg', `已恢复上次会话的 ${n} 张数据表，可直接继续生成报告`);
}

/* ---------- 步骤 1：导入 ---------- */
$('#drop').onclick = () => $('#file').click();
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
      <span class="rm" data-id="${ds.id}">×</span>`;
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
  if (!confirm('确定清空所有已导入的数据表？磁盘上的会话缓存也会一并删除。')) return;
  await post('/api/session/clear', {}).catch(() => {});
  Object.keys(STORE).forEach(k => delete STORE[k]);
  curId = null; lastOut = null;
  $('#charts').innerHTML = '';
  $('#frame').src = 'about:blank';
  ['openNew', 'dlHtml', 'dlMd', 'dlDocx', 'dlPdf'].forEach(b => $('#' + b).disabled = true);
  renderFiles(); renderDsSelect(); renderMerge(); renderHistory();
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
  $$('.chart-card').forEach(c => fillChartCard(c, ds));
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
    <div class="hd"><span>图表</span><span class="x">×</span></div>
    <div class="row"><label>类型</label><select class="c-type">
      ${CHART_TYPES.map(t => `<option value="${t[0]}"${t[0] === (spec.type || 'bar') ? ' selected' : ''}>${t[1]}</option>`).join('')}
    </select></div>
    <div class="row"><label>横轴</label><select class="c-x"></select></div>
    <div class="row"><label>数值</label><div class="chips c-y"></div></div>
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
  el.querySelector('.x').onclick = () => el.remove();
  el.querySelector('.c-x').onchange = () => refreshSortOptions(el);
  el.querySelector('.c-type').onchange = () => { refreshSortOptions(el); refreshDualVisibility(el); };
  // 注意：数列 chip 的点击绑定在 fillChartCard 里做 —— 那时才有 chip 节点。
  // 这里不要再绑一次，两处都绑的话后绑的会把刷新逻辑覆盖掉。
  fillChartCard(el, STORE[curId], spec);
}

function fillChartCard(el, ds, spec) {
  if (!ds) return;
  spec = spec || {};
  const all = ds.columns.map(c => c.name);
  const nums = ds.columns.filter(isNum).map(c => c.name);
  const x = el.querySelector('.c-x');
  x.innerHTML = colOptions(ds, false, spec.x || all[0]);
  const ys = spec.y && spec.y.length ? spec.y : nums.slice(0, 1);
  el.querySelector('.c-y').innerHTML = nums.map(n =>
    `<span class="chip${ys.indexOf(n) >= 0 ? ' on' : ''}" data-n="${esc(n)}">${esc(n)}</span>`).join('');
  $$('.c-y .chip', el).forEach(c => c && (c.onclick = () => {
    c.classList.toggle('on');
    // 勾掉/勾上一个数列，可选的排序项就变了；不刷新的话排序下拉是过期的，
    // 用户会觉得「按这个指标降序」根本选不出来。
    refreshSortOptions(el);
    refreshDualVisibility(el);
    refreshSplitVisibility(el);
  }));
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
  refreshDualVisibility(el);
  refreshSplitVisibility(el);
}

function refreshSplitVisibility(el) {
  /* 「拆开 / 合并」只在勾了两个以上指标时才有意义 —— 一个指标无所谓拆不拆。
     藏起来而不是灰掉：灰着的控件会让人以为哪里配错了。 */
  const wrap = el.querySelector('.c-split-wrap');
  if (!wrap) return;
  const ok = $$('.c-y .chip.on', el).length >= 2;
  wrap.hidden = !ok;
  if (!ok) el.querySelector('.c-split').value = '0';
}

function refreshDualVisibility(el) {
  /* 双轴只在「两条以上数列」且类型是组合图时才有意义，其余情况直接藏起来 */
  const wrap = el.querySelector('.c-dual-wrap');
  if (!wrap) return;
  const n = $$('.c-y .chip.on', el).length;
  const t = el.querySelector('.c-type').value;
  const ok = n >= 2 && (t === 'combo' || t === 'line' || t === 'stack_bar');
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

function collectSpec() {
  const excl = $('#exclSum') ? $('#exclSum').checked : true;
  const charts = $$('.chart-card').map(el => {
    const y = $$('.c-y .chip.on', el).map(c => c.dataset.n);
    const sb = el.querySelector('.c-sort').value;
    const lim = parseInt(el.querySelector('.c-limit').value || '0', 10);
    return {
      type: el.querySelector('.c-type').value,
      x: el.querySelector('.c-x').value,
      y,
      agg: el.querySelector('.c-agg').value,
      sort_by: sb === '__none__' ? '' : (sb === '__x__' ? '__x__' : sb),
      sort_order: el.querySelector('.c-dir').value,
      limit: lim > 0 ? lim : null,
      dual_axis: el.querySelector('.c-dual').checked,
      exclude_summary: excl,
      split_series: el.querySelector('.c-split').value === '1',
      span: parseInt(el.querySelector('.c-span').value || '6', 10) || 6,
    };
  }).filter(c => c.x && c.y.length);
  return {
    dataset_ids: Object.keys(STORE),
    merge_mode: mergeMode(),
    title: $('#rTitle').value.trim() || '数据分析报告',
    template: $('#rTpl').value,
    chart_layout: parseInt(($('#rLayout') || {}).value || '1', 10) || 1,
    charts,
    metrics: $$('#metricCols .chip.on').map(c => c.dataset.n),
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
};

/* ---------- 生成与导出 ---------- */
let curView = 'report';
let lastDashUrl = '';

$('#gen').onclick = async () => {
  const spec = collectSpec();
  if (!spec.dataset_ids.length) { toast('genMsg', '先导入数据', true); return; }
  // 没配图不再拦死：后端会降级出一份「数据摘要报告」（结论 + 指标 + 明细），
  // 保证只有纯文本 / 扫描图片 / 无数值列这类来源也能拿到报告。
  if (!spec.charts.length) { toast('genMsg', '没配图表，将生成「数据摘要报告」'); }
  $('#gen').disabled = true; $('#gen').textContent = '生成中…';
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
    $('#gen').disabled = false; $('#gen').textContent = '生成报告';
  }
};

/* ---------- 报告 / 看板：同一块地方切换，不再是两个新窗口 ---------- */
async function setView(v) {
  curView = v;
  $$('.vtab').forEach(b => b.classList.toggle('on', b.dataset.view === v));
  const isRep = v === 'report';
  $('#actsReport').hidden = !isRep;
  $('#actsDash').hidden = isRep;
  if (isRep) {
    $('#frame').src = lastOut ? lastOut.report_url : 'about:blank';
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
    $('#frame').src = 'about:blank';
    return;
  }
  if (!spec.charts.length && !spec.metrics.length) {
    $('#outInfo').textContent = '看板还是空的 —— 先在「说清需求」里配一张图，或勾一个指标列';
    $('#frame').src = 'about:blank';
    return;
  }
  $('#outInfo').textContent = '看板按当前配置重算中…';
  try {
    const res = await post('/api/dashboard/preview', dashPayload(spec));
    if (!res.ok) {
      $('#outInfo').textContent = res.error || '看板渲染失败';
      $('#frame').src = 'about:blank';
      return;
    }
    lastDashUrl = res.url;
    if (toFrame) $('#frame').src = res.url;
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

/* ---------- 历史报告 ---------- */
async function renderHistory() {
  const box = $('#hist');
  if (!box) return;
  const res = await api('/api/history').catch(() => ({ items: [] }));
  const items = res.items || [];
  if (!items.length) { box.innerHTML = '<span class="hint">还没有生成过报告</span>'; return; }
  box.innerHTML = items.map(it => {
    const chips = Object.entries(it.files).map(([k, v]) => {
      const label = { 'report.html': 'HTML', 'report.md': 'Markdown',
                      'report.docx': 'Word', 'report.pdf': 'PDF' }[k];
      return `<a class="gtag" href="${v.url}" target="_blank">${label} ${Math.round(v.size / 1024)}K</a>`;
    }).join('');
    const canExport = !it.files['report.docx'] || !it.files['report.pdf'];
    return `<div class="hitem"><span class="hnm">${esc(it.name)}</span>
      <span class="hint">${esc(it.stamp)}</span>${chips}
      ${canExport ? `<button class="btn ghost sm" data-dir="${esc(it.dir)}">补导出</button>` : ''}
      </div>`;
  }).join('');
  $$('#hist .hitem button').forEach(b => b.onclick = async () => {
    b.disabled = true; b.textContent = '导出中…';
    await post('/api/export', { dir: b.dataset.dir, kind: 'docx' });
    await post('/api/export', { dir: b.dataset.dir, kind: 'pdf' });
    renderHistory();
  });
}
if ($('#refreshHist')) $('#refreshHist').onclick = renderHistory;

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
  await restoreSession();
  renderFiles();
  renderDsSelect();
  renderMerge();
  renderHistory();
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
    el.onclick = () => dbPickTable(t);
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
    if (!confirm('删除连接「' + name + '」？只删这份配置，不动数据库里的任何东西。')) return;
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
      el.className = 'fitem';
      // metrics=0 不是「没有指标」，而是「按表里的数值列自动出」——
      // 直接写「0 指标」会让人以为看板上是空的，对不上实际看到的一排指标卡
      const mtext = it.metrics ? (it.metrics + ' 指标') : '指标按列自动';
      el.innerHTML = '<span class="nm">' + esc(it.name) + '</span>'
        + '<span class="src">' + it.charts + ' 图 · ' + mtext + '</span>'
        + '<span class="src">' + esc(it.updated || '') + '</span>';
      const open = document.createElement('button');
      open.className = 'btn ghost sm';
      open.textContent = '打开';
      open.onclick = () => dashOpen(it.id);
      const del = document.createElement('button');
      del.className = 'btn ghost sm';
      del.textContent = '删除';
      del.onclick = () => dashDelete(it.id, it.name);
      el.appendChild(open);
      el.appendChild(del);
      box.appendChild(el);
    });
  } catch (e) {
    box.innerHTML = '<span class="hint">看板列表读不出来：' + esc(String(e)) + '</span>';
  }
}

async function dashOpen(id) {
  dashMsg('正在按当前数据重新渲染…');
  try {
    const res = await post('/api/dashboard/render', { id: id });
    if (!res.ok) { dashMsg(res.error || '打开失败', true); return; }
    lastDashUrl = res.url;
    let m = '已渲染 ' + res.charts + ' 张图 · ' + res.metrics + ' 个指标';
    if ((res.missing || []).length) {
      m += '　⚠ 有 ' + res.missing.length + ' 张表不在当前会话里，对应图已跳过';
    }
    dashMsg(m, (res.missing || []).length > 0);
    window.open(res.url, '_blank');
    renderDashList();
  } catch (e) { dashMsg('出错：' + e, true); }
}

async function dashDelete(id, name) {
  if (!confirm('删除看板「' + name + '」？只删这份配置，数据集和报告都不动。')) return;
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
  try {
    const res = await post('/api/dashboard/save', dashPayload(spec));
    if (!res.ok) { dashMsg(res.error || '保存失败', true); return; }
    lastDashUrl = res.url;
    const mtext = res.metrics ? (res.metrics + ' 个指标') : '指标按列自动';
    let m = '看板「' + res.name + '」已保存（' + res.charts + ' 图 · '
      + mtext + '），已在新窗口打开';
    if ((res.missing || []).length) {
      m += '　⚠ 有 ' + res.missing.length + ' 张表不在当前会话里';
    }
    dashMsg(m, (res.missing || []).length > 0);
    if (!$('#dashName').value.trim()) $('#dashName').value = res.name;
    window.open(res.url, '_blank');
    renderDashList();
  } catch (e) { dashMsg('出错：' + e, true); }
}

if ($('#dashSave')) {
  $('#dashSave').onclick = dashSave;
  $('#dashRefreshList').onclick = () => { dashMsg('刷新列表…'); renderDashList(); };
  renderDashList();
}
