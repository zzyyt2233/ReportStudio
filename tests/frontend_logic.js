/* 前端逻辑回归：把 web/app.js 放进 jsdom 里真跑一遍，验行为而不是验字符串。

   为什么不用浏览器：这里要验的是「点一下按钮之后 DOM 对不对」，
   不需要渲染引擎，jsdom 就够，而且几秒跑完、进 CI 也不用装 Chromium。

   为什么不能用 check_frontend.py 代替：那个只做静态检查（引用的 id 存不存在）。
   `if (type !== 'pie')` 这种变量写错名的 bug 语法完全合法、id 也都对，
   静态检查永远发现不了 —— 只有真执行一遍才会暴露。

   依赖：jsdom 装在 .devtools（E 盘）。没装就跳过并给出提示，不让整个套件失败。
   用法：node tests/frontend_logic.js
*/

const fs = require('fs');
const path = require('path');

const ROOT = path.dirname(__dirname);
const DEVTOOLS = path.join(ROOT, '.devtools', 'node_modules');

let JSDOM;
try {
  ({ JSDOM } = require(path.join(DEVTOOLS, 'jsdom')));
} catch (e) {
  try {
    ({ JSDOM } = require('jsdom'));
  } catch (e2) {
    console.log('跳过前端逻辑测试：未安装 jsdom。');
    console.log('安装方式（约 10 MB，落在 E 盘）：');
    console.log('  cd ' + path.join(ROOT, '.devtools') + ' && npm install');
    process.exit(0);
  }
}

const html = fs.readFileSync(path.join(ROOT, 'web', 'index.html'), 'utf8');
const appJs = fs.readFileSync(path.join(ROOT, 'web', 'app.js'), 'utf8');
const asserts = fs.readFileSync(path.join(__dirname, 'frontend_asserts.js'), 'utf8');

const dom = new JSDOM(html, {
  url: 'http://127.0.0.1:8765/',
  runScripts: 'outside-only',
  pretendToBeVisual: true,
});
const { window } = dom;

/* ---------- 桩：把后端接口全部拦在本地，前端逻辑不需要真服务 ---------- */
function jsonResponse(obj, status) {
  const body = JSON.stringify(obj);
  return {
    ok: (status || 200) < 400,
    status: status || 200,
    text: async () => body,
    json: async () => obj,
  };
}

const CALLS = [];
/* 断言代码是在 window 的全局作用域里 eval 的，看不到这个模块作用域的 CALLS，
   所以要显式挂上去。 */
window.__CALLS = CALLS;
window.fetch = async (url, opt) => {
  const u = String(url);
  CALLS.push({ url: u, method: (opt && opt.method) || 'GET' });

  if (u.indexOf('/api/status') >= 0) {
    return jsonResponse({ ok: true, llm: false, datasets: 0, persist: true });
  }
  if (u.indexOf('/api/db/kinds') >= 0) {
    return jsonResponse({
      ok: true, readonly: true,
      kinds: [
        { kind: 'sqlite', label: 'SQLite（本地文件库）' },
        { kind: 'mysql', label: 'MySQL / MariaDB' },
        { kind: 'postgres', label: 'PostgreSQL' },
        { kind: 'mssql', label: 'SQL Server' },
        { kind: 'oracle', label: 'Oracle' },
      ],
    });
  }
  if (u.indexOf('/api/db/connections') >= 0) return jsonResponse({ ok: true, items: [] });
  /* 看板：清单里故意放一条名字带脚本的记录，验前端有没有转义。
     注意顺序 —— '/api/dashboards' 要放在 '/api/dashboard/' 之前判断。 */
  if (u.indexOf('/api/dashboards') >= 0) {
    return jsonResponse({
      ok: true,
      items: [
        { id: 'd1', name: '销售看板', title: '销售看板', charts: 3, metrics: 2,
          updated: '2026-09-23 12:00' },
        { id: 'd2', name: '<script>bad</script>', title: '', charts: 1, metrics: 0,
          updated: '' },
      ],
    });
  }
  if (u.indexOf('/api/dashboard/render') >= 0) {
    return jsonResponse({ ok: true, id: 'd1', name: '销售看板', charts: 3,
                          metrics: 2, missing: [], url: '/dashboards/d1.html' });
  }
  if (u.indexOf('/api/dashboard/save') >= 0) {
    return jsonResponse({ ok: true, id: 'd9', name: '测试看板', charts: 2,
                          metrics: 1, missing: [], url: '/dashboards/d9.html' });
  }
  if (u.indexOf('/api/dashboard/preview') >= 0) {
    return jsonResponse({ ok: true, name: '即时预览', charts: 3, metrics: 2,
                          missing: [], url: '/dashboards/_preview.html?_t=1' });
  }
  if (u.indexOf('/api/datasets') >= 0) return jsonResponse({ ok: true, items: [] });
  /* 注意顺序：'/api/history/cleanup' 要放在 '/api/history' 之前判断。
     返回值可用 window.__CLEANUP_STUB / window.__HIST_STUB 注入，供存量管理断言用。 */
  if (u.indexOf('/api/history/cleanup') >= 0) {
    return jsonResponse(window.__CLEANUP_STUB ||
      { ok: true, removed: 0, freed: 0, left: 0 });
  }
  if (u.indexOf('/api/history') >= 0) {
    return jsonResponse(window.__HIST_STUB ||
      { ok: true, items: [], total: 0, grand_total: 0, grand_size: 0 });
  }
  return jsonResponse({ ok: true });
};
window.confirm = () => true;
window.prompt = () => '90';
window.alert = () => {};
window.open = () => null;

/* ---------- 把 app.js 和断言放在同一次 eval 里 ----------
   必须是同一次：app.js 顶层的 const/let 不会挂到 window 上，
   分两次 eval 的话断言代码就看不到 $ / STORE 这些名字了。 */
let loadError = null;
try {
  window.eval(appJs + '\n;\n' + asserts);
} catch (e) {
  loadError = e;
}

(async function main() {
  const results = [];
  const push = (pass, msg) => results.push({ pass: !!pass, msg: msg });

  if (loadError) {
    push(false, 'app.js 加载/执行抛异常：' + (loadError && loadError.message));
  } else {
    push(true, 'app.js 在 jsdom 里加载执行无异常');
    try {
      await window.__runAsserts;
      (window.__RESULTS || []).forEach(r => results.push(r));
    } catch (e) {
      push(false, '断言执行抛异常：' + (e && e.message));
    }
  }

  console.log('='.repeat(66));
  console.log('前端逻辑回归（jsdom 真跑一遍 web/app.js）');
  console.log('='.repeat(66));
  results.forEach(r => console.log((r.pass ? '  ✓ ' : '  ✗ 失败! ') + r.msg));

  const failed = results.filter(r => !r.pass);
  console.log('\n' + '='.repeat(66));
  if (failed.length) {
    console.log(`前端逻辑测试失败 ${failed.length} 项 ❌`);
    process.exit(1);
  }
  console.log(`前端逻辑测试全部通过 ✅（${results.length} 项，含排序与转义回归）`);
  process.exit(0);
})();
