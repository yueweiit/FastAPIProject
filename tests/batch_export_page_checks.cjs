// 执行两份首页的真实批次导出函数，验证下载反馈和异步会话边界。
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');

let checks = 0;
const equal = (actual, expected) => { assert.deepEqual(actual, expected); checks++; };
const ok = value => { assert(value); checks++; };
const deferred = () => {
  let resolve;
  return {promise: new Promise(done => { resolve = done; }), resolve};
};

function setup(file) {
  const html = fs.readFileSync(file, 'utf8');
  const elements = new Map();
  const element = id => {
    if (!elements.has(id)) elements.set(id, {value: '', textContent: '', style: {}, disabled: false});
    return elements.get(id);
  };
  const links = [], revoked = [], timers = [], requests = [];
  const context = vm.createContext({
    document: {
      getElementById: element, documentElement: {lang: 'zh-CN'},
      createElement: tag => {
        equal(tag, 'a');
        const link = {style: {}, click: () => { link.clicked = true; }, remove: () => { link.removed = true; }};
        links.push(link);
        return link;
      },
      body: {appendChild: link => { link.appended = true; }},
    },
    window: {}, URLSearchParams, URL: {createObjectURL: () => 'blob:batch', revokeObjectURL: url => revoked.push(url)},
    setTimeout: (callback, ms) => { timers.push({callback, ms}); },
    fetch: async (url, options) => { requests.push({url, options}); return {ok: true, status: 200, blob: async () => ({})}; },
    authHeaders: () => ({'X-CSRF-Token': 'test'}),
    updateBatchSelectionState: () => {},
    formatApiError: (detail, fallback) => detail || fallback,
    handleSessionExpired: () => vm.runInContext('resetBatchExportSession()', context),
  });
  vm.runInContext(fs.readFileSync('static/page-interactions.js', 'utf8'), context);
  vm.runInContext('let selectedBatchIds = new Set(), batchExportBusy = false, batchExportSessionEpoch = 0;', context);
  for (const name of ['setBatchExportStatus', 'resetBatchExportSession', 'downloadBatchExport', 'exportBatch', 'exportAllBatches']) {
    const source = html.match(new RegExp('^(?:async )?function ' + name + '\\([^\\n]*\\) \\{[\\s\\S]*?^\\}', 'm'));
    ok(source);
    vm.runInContext(source[0], context);
  }
  return {html, context, element, links, revoked, timers, requests};
}

async function verify(file) {
  const {html, context: c, element: e, links, revoked, timers, requests} = setup(file);
  ok(html.includes('id="batch-export-button"'));
  ok(html.includes('id="batch-export-status" class="msg" role="status" aria-live="polite"'));
  const expiry = html.match(/function handleSessionExpired\(\) \{([\s\S]*?)^\}/m);
  ok(expiry && expiry[1].includes('resetBatchExportSession()'));

  const pending = deferred();
  c.fetch = (url, options) => { requests.push({url, options}); return pending.promise; };
  vm.runInContext('selectedBatchIds.add(118); selectedBatchIds.add(117)', c);
  const first = c.exportAllBatches();
  equal(e('batch-export-status').textContent, '正在生成所选 2 条批次的文件，请稍候…');
  c.exportBatch(118);
  equal(requests.length, 1);
  equal(new URLSearchParams(requests[0].url.split('?')[1]).getAll('batch_ids'), ['118', '117']);
  equal(requests[0].options.credentials, 'same-origin');
  equal(e('batch-export-button').disabled, true);
  equal(e('batch-export-status').textContent, '已有批次导出正在进行，请等待当前文件生成。');
  pending.resolve({ok: false, status: 500, text: async () => 'Internal Server Error'});
  await first;
  equal(e('batch-export-button').disabled, false);
  equal(e('batch-export-status').textContent, '批次导出失败（服务器返回 500），请联系管理员查看服务器日志。');
  equal(e('batch-export-status').style.display, 'block');
  ok(e('batch-export-status').className.includes('msg-err'));

  c.fetch = async (url, options) => {
    requests.push({url, options});
    return {ok: true, status: 200, blob: async () => ({size: 100})};
  };
  await c.exportBatch(118);
  equal(requests.length, 2);
  equal(links.length, 1);
  equal(links[0].clicked, true);
  equal(links[0].appended, true);
  equal(links[0].removed, undefined);
  equal(revoked.length, 0);
  equal(timers[0].ms, 60000);
  equal(e('batch-export-status').textContent, '文件已生成，请检查浏览器下载列表。');
  timers[0].callback();
  equal(links[0].removed, true);
  equal(revoked, ['blob:batch']);

  vm.runInContext('selectedBatchIds.clear()', c);
  e('batch-keyword').value = ' CW000214 ';
  await c.exportAllBatches();
  equal(new URLSearchParams(requests[2].url.split('?')[1]).get('keyword'), 'CW000214');

  const stale = deferred();
  c.fetch = () => stale.promise;
  const previous = c.exportBatch(118);
  vm.runInContext('selectedBatchIds.add(117)', c);
  c.handleSessionExpired();
  equal(e('batch-export-button').disabled, false);
  equal(e('batch-export-status').style.display, 'none');
  equal(vm.runInContext('selectedBatchIds.size', c), 0);
  stale.resolve({ok: true, status: 200, blob: async () => ({size: 100})});
  await previous;
  equal(links.length, 2);
  equal(e('batch-export-status').style.display, 'none');
}

(async () => {
  for (const file of ['templates/index.html', 'index.html']) await verify(file);
  console.log(JSON.stringify({checks, pages: 2, scope: 'batch export feedback, failure, download cleanup and session guard'}));
})().catch(error => { console.error(error); process.exitCode = 1; });
