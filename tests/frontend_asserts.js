/* 前端逻辑断言 —— 由 tests/frontend_logic.js 拼在 web/app.js 后面、
   在同一个 jsdom 作用域里执行，所以能直接用到 app.js 里的 $ / STORE / addChartCard 等。

   为什么需要它：check_frontend.py 只能静态查「引用的 id 存不存在」，
   抓不到 `if (type !== 'pie')` 这种「变量写错名」的逻辑 bug ——
   那种错误语法上完全合法，只有真跑一遍才暴露。 */

window.__RESULTS = [];

function ok(cond, msg) {
  window.__RESULTS.push({ pass: !!cond, msg: msg });
}

function optValues(sel) {
  return Array.from(sel.options).map(o => o.value);
}

window.__runAsserts = (async function () {
  // 等 init() 里的异步调用（status / datasets / db kinds / db connections）跑完
  await new Promise(r => setTimeout(r, 60));

  /* ---------- 1. 造假数据表，供图表卡片使用 ---------- */
  STORE['t1'] = {
    id: 't1', name: '测试表', source_type: 'paste', total_rows: 5, warnings: [],
    columns: [
      { name: '省份', dtype: 'text' }, { name: '销售额', dtype: 'number' },
      { name: '毛利率', dtype: 'number', unit: '%' }, { name: '订单量', dtype: 'number' },
    ],
    rows: [{ 省份: '广东', 销售额: 100, 毛利率: 0.4, 订单量: 10 }],
  };
  curId = 't1';

  /* ---------- 2. 排序下拉必须生成「按某一列」的选项 ----------
     回归点：refreshSortOptions 里曾写成 `if (type !== 'pie')`，
     type 未定义 → 抛 ReferenceError → 下拉永远只有两个占位项，
     用户没法「按销售额排序」。 */
  $('#charts').innerHTML = '';
  addChartCard({ type: 'bar', x: '省份', y: ['销售额'], agg: 'sum',
                 sort_by: '__x__', sort_order: 'desc' });

  const cards = $$('.chart-card');
  ok(cards.length === 1, '加一张图后出现 1 个图表卡片');
  const card = cards[0];
  const sortVals = optValues(card.querySelector('.c-sort'));
  ok(sortVals.indexOf('__x__') >= 0, '排序下拉含「按横轴」');
  ok(sortVals.indexOf('销售额') >= 0, '排序下拉含「按 销售额」（回归：修好前这里是空的）');
  // 排序候选只来自「已勾选显示的数列」——这样下拉不会出现一堆根本没画出来的列
  ok(sortVals.indexOf('毛利率') < 0, '未勾选的数列不出现在排序候选里');
  const gchip = $$('.c-y .chip', card).find(c => c.dataset.n === '毛利率');
  if (gchip) {
    gchip.classList.add('on');
    refreshSortOptions(card);          // 等价于用户点了那个 chip
    ok(optValues(card.querySelector('.c-sort')).indexOf('毛利率') >= 0,
       '勾选毛利率后它进入排序候选');
  } else {
    ok(false, '找不到毛利率的数列 chip');
  }

  /* ---------- 2b. 点一下 chip 必须自己刷新排序下拉 ----------
     回归点：卡片建好后 fillChartCard 重新绑定的 onclick 只 toggle 样式、
     没调 refreshSortOptions，于是「再勾一个指标，想在排序里选它」根本选不出来。
     这就是用户说的「按什么指标降序也不能做到」的手动操作版本 ——
     上一段是手动调 refreshSortOptions 验的，绕过了这个 bug，所以必须真点一下。 */
  $('#charts').innerHTML = '';
  addChartCard({ type: 'bar', x: '省份', y: ['销售额'] });
  const cc = $$('.chart-card')[0];
  ok(optValues(cc.querySelector('.c-sort')).indexOf('订单量') < 0,
     '2b 起点：订单量未勾选，不在排序候选里');
  const ochip = $$('.c-y .chip', cc).find(c => c.dataset.n === '订单量');
  if (ochip) {
    ochip.onclick();                       // 真点，不手动刷新
    ok(ochip.classList.contains('on'), '2b 点一下 chip 它被选中');
    ok(optValues(cc.querySelector('.c-sort')).indexOf('订单量') >= 0,
       '2b 点完 chip 排序下拉立刻出现「按 订单量」（回归：修好前不刷新）');
    ochip.onclick();
    ok(optValues(cc.querySelector('.c-sort')).indexOf('订单量') < 0,
       '2b 再点一下取消勾选，它从排序候选里收回');
  } else {
    ok(false, '2b 找不到订单量的数列 chip');
  }

  /* ---------- 3. 预填的排序设置不能被吞掉 ----------
     回归点：原来 `if (spec.sort_by) 设为「不排序」`，传进来的排序反而被清掉。 */
  ok(card.querySelector('.c-sort').value === '__x__',
     '预填的 sort_by=__x__ 被保留（实际 ' + card.querySelector('.c-sort').value + '）');
  ok(card.querySelector('.c-dir').value === 'desc',
     '预填的降序被保留（实际 ' + card.querySelector('.c-dir').value + '）');

  /* ---------- 4. 饼图不该出现「按横轴」 ---------- */
  $('#charts').innerHTML = '';
  addChartCard({ type: 'pie', x: '省份', y: ['销售额'] });
  const pieVals = optValues($$('.chart-card')[0].querySelector('.c-sort'));
  ok(pieVals.indexOf('__x__') < 0, '饼图的排序里没有「按横轴」');
  ok(pieVals.indexOf('销售额') >= 0, '饼图仍可按数值列排序');

  /* ---------- 5. 按数值列排序要能传进 spec ---------- */
  $('#charts').innerHTML = '';
  addChartCard({ type: 'bar', x: '省份', y: ['销售额'] });
  const c2 = $$('.chart-card')[0];
  c2.querySelector('.c-sort').value = '销售额';
  c2.querySelector('.c-dir').value = 'desc';
  const spec = collectSpec();
  ok(spec.charts.length === 1, 'collectSpec 收到 1 张图');
  ok(spec.charts[0].sort_by === '销售额',
     '按数值列排序被正确收集（实际 ' + spec.charts[0].sort_by + '）');
  ok(spec.charts[0].sort_order === 'desc', '降序被正确收集');

  /* ---------- 6. 数据库面板：字段随类型切换 ---------- */
  const kindSel = $('#dbKind');
  ok(kindSel && kindSel.options.length >= 5,
     '数据库类型下拉有 5 种（实际 ' + (kindSel ? kindSel.options.length : 0) + '）');

  kindSel.value = 'mysql';
  renderDbFields();
  const myFields = $$('#dbFields input').map(i => i.dataset.f);
  ok(myFields.join(',') === 'host,port,user,password,database,charset',
     'MySQL 字段齐全：' + myFields.join(','));
  const pw = $('#dbFields input[data-f="password"]');
  ok(pw && pw.type === 'password', '密码框是 password 类型（不明文显示）');

  kindSel.value = 'sqlite';
  renderDbFields();
  const sqFields = $$('#dbFields input').map(i => i.dataset.f);
  ok(sqFields.join(',') === 'path', 'SQLite 只要一个「库文件」路径：' + sqFields.join(','));

  kindSel.value = 'oracle';
  renderDbFields();
  const oraFields = $$('#dbFields input').map(i => i.dataset.f);
  ok(oraFields.indexOf('service') >= 0, 'Oracle 用的是 service 服务名');

  /* ---------- 7. dbPayload 组装要正确 ---------- */
  kindSel.value = 'mysql';
  $$('#dbFields input').forEach(i => {
    if (i.dataset.f === 'host') i.value = '10.0.0.5';
    if (i.dataset.f === 'port') i.value = '3307';
    if (i.dataset.f === 'user') i.value = 'readonly';
  });
  $('#dbConnName').value = '';
  $('#dbSaved').value = '';
  const pay = dbPayload({ sql: 'SELECT 1', import: false });
  ok(pay.connection && pay.connection.kind === 'mysql',
     'payload 带上了连接类型');
  ok(pay.connection.host === '10.0.0.5' && pay.connection.port === '3307',
     'payload 带上了主机与端口');
  ok(pay.sql === 'SELECT 1' && pay.import === false, 'payload 带上了 sql 与 import 开关');
  ok(pay.connection_name === undefined, '没选已存连接时不带 connection_name（避免误用旧密码）');

  /* ---------- 8. 结果预览：必须转义，不许把列名里的标签直接塞进 DOM ---------- */
  $('#dbResult').innerHTML = '';
  renderDbResult({
    ok: true, row_count: 1, elapsed_ms: 3, truncated: false, readonly_enforced: true,
    source: '测试库<script>bad</script>',
    columns_detail: [{ name: '省份<x>', dtype: 'text' }, { name: '销售额', dtype: 'number' }],
    preview_rows: [{ '省份<x>': '广东<b>', 销售额: 1234.5 }],
    warnings: ['来源：测试<script>'],
  });
  const box = $('#dbResult');
  ok(box.querySelectorAll('table').length === 1, '结果预览渲染出表格');
  ok(box.innerHTML.indexOf('<script>') < 0, '结果预览里的脚本标签被转义（无 XSS）');
  ok(box.querySelector('td') && box.querySelector('td').textContent.indexOf('广东<b>') >= 0,
     '单元值按文本输出而不是当成标签');

  /* ---------- 9. 表清单渲染也要转义 ---------- */
  $('#dbTables').innerHTML = '';
  renderDbTables([{ schema: '', name: 'a<script>alert(1)</script>', type: 'table' },
                  { schema: 'public', name: 'v', type: 'view' }]);
  const tb = $('#dbTables');
  ok(tb.querySelectorAll('.fitem').length === 2, '表清单渲染出 2 项');
  ok(tb.innerHTML.indexOf('<script>alert') < 0, '表名里的脚本被转义');
  ok(tb.textContent.indexOf('public.v') >= 0, '带 schema 的表显示为 schema.表名');

  /* ---------- 10. 只读提示必须在界面上出现 ---------- */
  const note = document.querySelector('.db-note');
  ok(note && note.textContent.indexOf('只读') >= 0, '面板上写明了只读约束');
  ok(note && note.textContent.indexOf('SELECT') >= 0, '面板上写明了只允许 SELECT');

  /* ---------- 11. 数据看板：宽度选择要被收进 spec ---------- */
  $('#charts').innerHTML = '';
  addChartCard({ type: 'bar', x: '省份', y: ['销售额'], span: 12 });
  const dcard = $$('.chart-card')[0];
  ok(dcard.querySelector('.c-span') !== null, '图表卡上有「看板宽度」选择');
  ok(dcard.querySelector('.c-span').value === '12', '预填的宽度 12 被保留');
  ok(collectSpec().charts[0].span === 12,
     '宽度被收进 spec（实际 ' + collectSpec().charts[0].span + '）');

  $('#charts').innerHTML = '';
  addChartCard({ type: 'bar', x: '省份', y: ['销售额'] });
  ok($$('.chart-card')[0].querySelector('.c-span').value === '6', '没指定宽度时默认半宽(6)');

  /* ---------- 12. 看板列表渲染 + 转义 ---------- */
  const dbox = $('#dashList');
  ok(dbox.querySelectorAll('.fitem').length === 2, '看板列表渲染出 2 条');
  ok(dbox.innerHTML.indexOf('<script>bad') < 0, '看板名里的脚本标签被转义');
  ok(dbox.textContent.indexOf('<script>bad</script>') >= 0, '看板名按文本原样显示');
  ok(dbox.textContent.indexOf('3 图') >= 0, '列表里列出了图数与指标数');
  ok(dbox.textContent.indexOf('指标按列自动') >= 0,
     'metrics=0 显示「指标按列自动」而不是「0 指标」（后者会让人以为看板是空的）');
  ok(dbox.querySelectorAll('button').length === 4, '每条看板都有「打开 / 删除」两个按钮');

  /* ---------- 13. 空看板要拦住并说清原因 ---------- */
  $('#charts').innerHTML = '';
  $$('#metricCols .chip').forEach(c => c.classList.remove('on'));
  await dashSave();
  ok($('#dashMsg').textContent.indexOf('至少配一张图') >= 0,
     '空看板被拦下并说明了原因（实际：' + $('#dashMsg').textContent + '）');

  /* ---------- 14. 多指标「合并 / 拆开」 ---------- */
  $('#charts').innerHTML = '';
  addChartCard({ type: 'bar', x: '省份', y: ['销售额', '订单量'], agg: 'sum' });
  const sc = $$('.chart-card')[0];
  ok(sc.querySelector('.c-split-wrap').hidden === false,
     '勾了两个指标时「多指标」选择可见');
  ok(sc.querySelector('.c-split').value === '0', '默认是合并成一张图');

  $$('.c-y .chip', sc).forEach(c => {
    if (c.dataset.n === '订单量') c.classList.remove('on');
  });
  refreshSplitVisibility(sc);
  ok(sc.querySelector('.c-split-wrap').hidden === true,
     '只剩一个指标时「多指标」选择被隐藏（拆不拆没意义）');
  ok(sc.querySelector('.c-split').value === '0',
     '隐藏时自动回到「合并」，不会残留上次的拆开设置');

  $$('.c-y .chip', sc).forEach(c => c.classList.add('on'));
  refreshSplitVisibility(sc);
  sc.querySelector('.c-split').value = '1';
  ok(collectSpec().charts[0].split_series === true, '「拆开」被收进 spec');

  /* ---------- 15. 报告图表版面进 spec ---------- */
  $('#rLayout').value = '3';
  ok(collectSpec().chart_layout === 3, '图表版面（每行 3 张）被收进 spec');
  $('#rLayout').value = '1';

  /* ---------- 16. 报告 / 看板切换（同一块地方，不再是两个窗口） ---------- */
  ok($('#tabReport').classList.contains('on'), '默认停在「报告」视图');
  await setView('dash');
  ok($('#tabDash').classList.contains('on') && !$('#tabReport').classList.contains('on'),
     '切到看板后页签状态跟着变');
  ok($('#actsDash').hidden === false && $('#actsReport').hidden === true,
     '切到看板后按钮组同步换成看板那组');
  const pv = window.__CALLS.filter(c => c.url.indexOf('/api/dashboard/preview') >= 0);
  ok(pv.length === 1, '切到看板时按当前配置请求了一次即时预览（实际 ' + pv.length + ' 次）');
  ok($('#frame').src.indexOf('_preview.html') >= 0,
     'iframe 指向即时预览页（实际 ' + $('#frame').src + '）');
  ok($('#outInfo').textContent.indexOf('实时重算') >= 0,
     '写明看板是按当前数据实时重算的（实际：' + $('#outInfo').textContent + '）');

  // 再切一次：每次都用最新配置重算，所以看板不会停留在旧数据上
  sc.querySelector('.c-split').value = '0';
  await setView('dash');
  const pv2 = window.__CALLS.filter(c => c.url.indexOf('/api/dashboard/preview') >= 0);
  ok(pv2.length === 2, '每次切到看板都重算一次（不是缓存旧结果）');

  // 切回报告：不该再去请求看板
  await setView('report');
  const pv3 = window.__CALLS.filter(c => c.url.indexOf('/api/dashboard/preview') >= 0);
  ok(pv3.length === 2, '切回报告不会多余地重算看板');
  ok($('#actsReport').hidden === false, '切回报告后按钮组换回来');

  /* ---------- 17. 每个指标各自的图型（不再是所有指标共用一个） ---------- */
  $('#charts').innerHTML = '';
  addChartCard({ type: 'bar', x: '省份', y: ['销售额', '订单量'], agg: 'sum' });
  const tc = $$('.chart-card')[0];
  ok(tc.querySelectorAll('.yt-row').length === 2,
     '两个已选指标各有一行图型设置（实际 '
     + tc.querySelectorAll('.yt-row').length + ' 行）');

  const pick = (n) => $$('.yt-type', tc).find(s => s.dataset.n === n);
  ok($$('.yt-type', tc).every(s => s.value === ''), '默认都是「跟随默认」');
  ok(pick('销售额').options[0].textContent.indexOf('柱状图') >= 0,
     '「跟随默认」写明当前默认图型是什么（实际：'
     + pick('销售额').options[0].textContent + '）');

  // 只给其中一个指标单独指定折线
  pick('订单量').value = 'line';
  pick('订单量').onchange();
  ok(collectSpec().charts[0].y_types['订单量'] === 'line',
     '单独指定的图型被收进 spec（实际 '
     + JSON.stringify(collectSpec().charts[0].y_types) + '）');
  ok(collectSpec().charts[0].y_types['销售额'] === undefined,
     '跟着默认的那个不写进 y_types —— 全写进去的话改默认图型就对它不生效了');

  // 改成饼图：得说清楚它会单独成图，不然用户以为界面没反应
  pick('订单量').value = 'pie';
  pick('订单量').onchange();
  ok(tc.textContent.indexOf('单独成图') >= 0, '选了饼图会写明它会单独成图');
  ok(tc.querySelector('.c-split-wrap').hidden === true,
     '只剩一个指标进得了同一张图时，「拆/合」选择被隐藏（没有可做的选择）');

  // 改默认图型：跟随默认的指标要跟着变
  pick('订单量').value = 'line';
  pick('订单量').onchange();
  tc.querySelector('.c-type').value = 'stack_bar';
  tc.querySelector('.c-type').onchange();
  ok(pick('销售额').options[0].textContent.indexOf('堆叠柱状图') >= 0,
     '改默认图型后「跟随默认」的标签同步更新（实际：'
     + pick('销售额').options[0].textContent + '）');
  ok(pick('销售额').value === '', '它自己仍然是跟随默认，没被写死');

  // 取消一个指标再勾回来，别的指标已经选好的图型不能跟着丢
  $$('.c-y .chip', tc).forEach(c => {
    if (c.dataset.n === '销售额') c.classList.remove('on');
  });
  renderYTypes(tc);
  ok($$('.yt-type', tc).length === 1, '取消一个指标后只剩一行图型设置');
  $$('.c-y .chip', tc).forEach(c => c.classList.add('on'));
  renderYTypes(tc);
  ok(pick('订单量') && pick('订单量').value === 'line',
     '勾回来之后，另一个指标选好的图型还在（实际 '
     + (pick('订单量') || {}).value + '）');

  /* ---------- 18. 预览区放大（看板里「放大某张图」要地方铺开） ---------- */
  setFrame('/dashboards/_preview.html');
  ok($('#frameZoom').disabled === false, '有内容之后「放大预览」可用');
  ok($('#frame').classList.contains('zoomed') === false, '默认不是放大态');

  $('#frameZoom').onclick();
  ok($('#frame').classList.contains('zoomed'), '点一下就铺满窗口');
  ok($('#frameZoom').textContent === '退出放大',
     '按钮文字跟着变（实际 ' + $('#frameZoom').textContent + '）');

  const esc = new window.KeyboardEvent('keydown', {key: 'Escape', bubbles: true});
  document.dispatchEvent(esc);
  ok($('#frame').classList.contains('zoomed') === false, 'ESC 能退出放大');
  ok($('#frameZoom').textContent === '放大预览', '按钮文字也复位了');

  // 清空预览时不能留在放大态 —— 否则会看到一块盖住整屏的空白
  $('#frameZoom').onclick();
  setFrame('');
  ok($('#frame').classList.contains('zoomed') === false, '清空预览会退出放大');
  ok($('#frameZoom').disabled === true, '没内容时「放大预览」是禁用的');
  setFrame('');

  /* ---------- 19. 横纵轴切换：角色互换 ---------- */
  // 场景A：横轴=省份(text)，纵轴=[销售额, 毛利率] → 切换后横轴=销售额，
  // 省份(text非数值)不进纵轴，毛利率仍在。
  $('#charts').innerHTML = '';
  addChartCard({ type: 'bar', x: '省份', y: ['销售额', '毛利率'], agg: 'sum' });
  const sw = $$('.chart-card')[0];
  swapAxis(sw);
  ok(sw.querySelector('.c-x').value === '销售额',
     '切换后横轴变成原第一个纵轴指标（销售额）');
  ok(!$$('.c-y .chip', sw).find(c => c.dataset.n === '销售额').classList.contains('on'),
     '原横轴指标已移出纵轴');
  ok($$('.c-y .chip', sw).find(c => c.dataset.n === '毛利率').classList.contains('on'),
     '另一个指标仍在纵轴（非空不撤销）');

  // 场景B：横轴=省份(text)，纵轴=[销售额] 单指标 → 切换会让纵轴空，应撤销
  $('#charts').innerHTML = '';
  addChartCard({ type: 'bar', x: '省份', y: ['销售额'] });
  const sw2 = $$('.chart-card')[0];
  swapAxis(sw2);
  ok(sw2.querySelector('.c-x').value === '省份',
     '单指标且原横轴非数值：切换被撤销，横轴不变（实际 '
     + sw2.querySelector('.c-x').value + '）');

  // 场景C：数值型维度（年份）也能当横轴并切换 —— 验证「横轴纵轴都识别」
  STORE['t2'] = {
    id: 't2', name: '带年份', source_type: 'paste', total_rows: 4, warnings: [],
    columns: [
      { name: '年份', dtype: 'number' }, { name: '地区', dtype: 'text' },
      { name: '销售额', dtype: 'number' },
    ],
    rows: [{ 年份: 2020, 地区: '华东', 销售额: 100 }],
  };
  curId = 't2';
  $('#charts').innerHTML = '';
  addChartCard({ type: 'bar', x: '地区', y: ['销售额'] });
  const sw3 = $$('.chart-card')[0];
  sw3.querySelector('.c-x').value = '年份';   // 数值型维度作横轴
  swapAxis(sw3);
  ok(sw3.querySelector('.c-x').value === '销售额',
     '数值型横轴(年份)切换：原指标销售额变横轴');
  ok($$('.c-y .chip', sw3).find(c => c.dataset.n === '年份').classList.contains('on'),
     '原数值型横轴年份被收进纵轴（只有数值才能当纵轴）');

  return true;
})();
