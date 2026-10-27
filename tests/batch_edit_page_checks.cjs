// 执行两份首页的真实批次函数；DOM模拟，视觉检查另行执行。
const fs = require('node:fs');
const vm = require('node:vm');
const assert = require('node:assert/strict');
let checks = 0;
const ok = value => { assert(value); checks++; };
const equal = (a, b) => { assert.deepEqual(a, b); checks++; };
const fixture = {id: 12, product_id: 42, user_id: 7, batch_no: 'HISTORY-01', quantity: 13, edit_version: 'a'.repeat(64),
  remaining_quantity: 13, total_amount: '34.0054', purchase_price: '2.6158', shipping_cost: '161.8555',
  last_mile_cost: '0', other_cost: '0', unit_cost: '15.0662', dingtalk_order_no: 'OLD',
  shipping_cost_no: 'HEAD', last_mile_cost_no: 'LAST', other_cost_no: 'OTHER',
  arrived_at: '2026-08-30T00:15:37.123456', shipping_date: '2026-08-29T23:59:41'};
function setup(file) {
  const html = fs.readFileSync(file, 'utf8'), elements = new Map();
  const element = id => {
    if (!elements.has(id)) elements.set(id, {value: '', dataset: {}, style: {}, disabled: false,
      textContent: '', innerHTML: '', reportValidity: () => true});
    return elements.get(id);
  };
  const messages = [];
  const c = vm.createContext({window: {}, document: {getElementById: element,
    querySelectorAll: () => [], documentElement: {lang: 'zh-CN'}}, isAdmin: () => false, isOperator: () => true,
    escapeHtml: value => String(value).replaceAll('"', '&quot;'), showMsg: (...args) => messages.push(args),
    applyBatchColumnSelection: () => {}, renderPagination: () => {}, updateBatchSelectionState: () => {},
    loadBatches: async () => {}, loadProducts: async () => {}, calcEditUnitCost: () => {}, resetSalesImportState: () => {},
    resetBatchExportSession: () => {}, batchVoidBusy: false,
    resetProductImportPreview: () => {}, resetStoreProfitLossState: () => {},
    clearLegacyAuth: () => {}, showLogin: () => {}});
  vm.runInContext(fs.readFileSync('static/page-interactions.js', 'utf8'), c);
  vm.runInContext('let editingBatchId=null,editingBatchVersion=null,batchEditSaving=false,batchEditSessionEpoch=0,currentBatchList=[],CURRENT_USER_ID=7;' +
    'let batchTotal=1,batchPage=1,batchPageSize=20,batchListRequestId=0,visibleBatchColumns=new Set(),selectedBatchIds=new Set();', c);
  for (const name of ['formatApiError', 'handleSessionExpired', 'batchEditBlockedReason', 'renderBatchPage',
    'setBatchEditStatus', 'resetBatchEditSession', 'openEditBatch', 'closeEditBatch', 'saveEditBatch', 'voidBatch']) {
    const source = html.match(new RegExp('^(?:async )?function ' + name + '\\([^\\n]*\\) \\{[\\s\\S]*?^\\}', 'm'));
    assert(source, name); vm.runInContext(source[0], c);
  }
  const setBatch = batch => vm.runInContext(`currentBatchList=[${JSON.stringify(batch)}]`, c);
  return {html, c, e: element, messages, setBatch};
}
async function verify(file) {
  const {html, c, e, messages, setBatch} = setup(file);
  ok(/\[data-batch-column="actions"\][^\n]*position:sticky; right:0/.test(html));
  ok(!html.includes('data-batch-column="actions" style="display:flex'));
  ok(html.includes('id="eb-total-amount" type="number" min="0" step="0.0001" required'));
  ok(html.includes('id="edit-batch-form" novalidate'));
  ok(html.includes('id="batch-edit-status"'));
  ok(html.indexOf('id="batch-edit-status"') > html.indexOf('id="eb-save"'));
  setBatch(fixture); c.renderBatchPage();
  ok(e('batches-tbody').innerHTML.includes('openEditBatch(12)'));
  ok(!e('batches-tbody').innerHTML.includes('disabled'));
  ok(!e('batches-tbody').innerHTML.includes('deleteBatch(12)'));
  ok(e('batches-tbody').innerHTML.includes('voidBatch(12)'));
  ok(!html.includes('async function deleteBatch('));
  equal(c.formatApiError({code: 'batch_delete_retired'}, ''),
    c.window.PageInteractions.text('batchDeleteRetired'));
  setBatch({...fixture, user_id:9});c.renderBatchPage();
  ok(!e('batches-tbody').innerHTML.includes('disabled'));
  ok(!e('batches-tbody').innerHTML.includes('deleteBatch(12)'));
  ok(e('batches-tbody').innerHTML.includes('voidBatch(12)'));
  ok(e('batches-tbody').innerHTML.includes('openBatchLogs(12)'));
  await c.openEditBatch(12);equal(vm.runInContext('editingBatchId',c),12);c.closeEditBatch();
  for (const batch of [{...fixture, remaining_quantity: 0}, {...fixture, remaining_quantity: 12}]) {
    setBatch(batch); c.renderBatchPage();
    const body = e('batches-tbody').innerHTML;
    ok(!body.includes('disabled') && body.includes('openEditBatch(12)'));
    ok(!body.includes('deleteBatch(12)'));
    await c.openEditBatch(12); equal(vm.runInContext('editingBatchId', c), 12);
    equal(e('eb-qty').min, Math.max(1, fixture.quantity - batch.remaining_quantity));
    equal(e('batch-edit-notice').textContent, c.window.PageInteractions.text('batchEditRestatement'));
    c.closeEditBatch();
  }
  setBatch({...fixture, user_id: 9}); c.isAdmin = () => true; await c.openEditBatch(12);
  equal(vm.runInContext('editingBatchId', c), 12); c.closeEditBatch();
  c.isOperator = () => false; c.renderBatchPage();
  ok(!e('batches-tbody').innerHTML.includes('openEditBatch'));
  ok(!e('batches-tbody').innerHTML.includes('openBatchLogs'));
  ok(!e('batches-tbody').innerHTML.includes('voidBatch'));
  ok(e('batches-tbody').innerHTML.includes('exportBatch'));
  await c.openEditBatch(12); equal(vm.runInContext('editingBatchId', c), null);
  c.isOperator = () => true; c.isAdmin = () => false; setBatch(fixture);
  setBatch({...fixture, shipping_cost: '100.0000', edit_values: {shipping_cost:'200.0000',
    total_amount:'34.0054', purchase_price:'2.6158', last_mile_cost:'0', other_cost:'0'}});
  c.renderBatchPage(); ok(e('batches-tbody').innerHTML.includes('¥200.0000'));
  await c.openEditBatch(12); equal(e('eb-ship').value, '200.0000'); c.closeEditBatch();
  setBatch({...fixture, edit_version: undefined});
  await c.openEditBatch(12); equal(vm.runInContext('editingBatchId', c), null);
  equal(messages.at(-1)[0], c.window.PageInteractions.text('batchEditVersionRequired'));
  setBatch(fixture);
  await c.openEditBatch(12);
  equal(e('eb-total-amount').value, '34.0054');
  equal(e('eb-ship').value, '161.8555');
  equal(e('eb-date').value, '2026-08-30T00:15');
  let calls = 0, payload, target;
  c.api = async (method, url, body) => { calls++; payload = body; target = [method, url]; return fixture; };
  e('edit-batch-form').reportValidity = () => false;
  e('edit-batch-form').querySelector = () => ({validationMessage: '请输入入库时间',
    parentElement: {querySelector: () => ({textContent: '入库时间'})}});
  await c.saveEditBatch(); equal(calls, 0);
  equal(e('batch-edit-status').textContent, '请检查“入库时间”：请输入入库时间');
  equal(e('batch-edit-status').style.display, 'block');
  e('edit-batch-form').reportValidity = () => true;
  for (const id of ['eb-dingtalk-order', 'eb-lastmile-no', 'eb-other-no']) e(id).value = '  ';
  e('eb-ship-no').value = ' HEAD-NEW ';
  await c.saveEditBatch(); equal(calls, 1); equal(target, ['PUT', '/batches/12']);
  equal(payload.product_id, 42); equal(payload.total_amount, '34.0054'); equal(payload.shipping_cost, '161.8555');
  equal(payload.edit_version, fixture.edit_version);
  equal(payload.cost_edit_mode, 'restate');
  for (const key of ['dingtalk_order_no', 'last_mile_cost_no', 'other_cost_no']) equal(payload[key], null);
  equal(payload.shipping_cost_no, 'HEAD-NEW');
  equal(payload.arrived_at, fixture.arrived_at); equal(payload.shipping_date, fixture.shipping_date);
  equal(e('edit-batch-modal').style.display, 'none'); equal(e('eb-save').disabled, false);
  equal(e('batch-edit-status').style.display, 'none');
  equal(vm.runInContext('editingBatchVersion', c), null);
  // 保存中不能重复提交、切换编辑目标或取消；请求使用点击时的批次。
  await c.openEditBatch(12);
  let finish; c.api = async () => { calls++; return new Promise(resolve => { finish = resolve; }); };
  const saving = c.saveEditBatch(); await c.saveEditBatch(); c.closeEditBatch(); await c.openEditBatch(99);
  equal(calls, 2); equal(e('eb-save').disabled, true); equal(vm.runInContext('editingBatchId', c), 12);
  equal(e('batch-edit-status').textContent, c.window.PageInteractions.text('batchEditSaving'));
  finish(fixture); await saving; equal(e('eb-save').disabled, false);
  // 服务端409/并发出库拦截保留表单，便于核对；刷新失败明确表示已经保存。
  await c.openEditBatch(12); e('eb-total-amount').value = '99.9999';
  c.api = async () => { throw Error('batch_has_cost_adjustments'); };
  await c.saveEditBatch(); equal(e('eb-total-amount').value, '99.9999');
  equal(e('edit-batch-modal').style.display, ''); equal(e('batch-edit-status').textContent, 'batch_has_cost_adjustments');
  equal(e('batch-edit-status').style.display, 'block');
  equal(e('eb-save').disabled, false);
  // 打开时捕获版本；列表对象被更新也不能把旧表单伪装成新版本重试。
  setBatch({...fixture, edit_version: 'b'.repeat(64), shipping_cost: '200.0000'});
  let conflictCalls = 0;
  c.api = async (_method, _url, body) => {
    conflictCalls++; payload = body;
    throw Error(c.formatApiError({code: 'batch_edit_conflict', message: 'fallback'}, 'error'));
  };
  await c.saveEditBatch(); await c.saveEditBatch();
  equal(conflictCalls, 2); equal(payload.edit_version, fixture.edit_version);
  equal(payload.shipping_cost, '161.8555'); equal(e('eb-total-amount').value, '99.9999');
  equal(vm.runInContext('editingBatchVersion', c), fixture.edit_version);
  equal(e('batch-edit-status').textContent, c.window.PageInteractions.text('batchEditConflict'));
  equal(c.formatApiError({code: 'batch_edit_version_required'}, 'error'), c.window.PageInteractions.text('batchEditVersionRequired'));
  c.closeEditBatch(); await c.openEditBatch(12);
  equal(vm.runInContext('editingBatchVersion', c), 'b'.repeat(64));
  equal(e('eb-ship').value, '200.0000');
  c.api = async () => fixture; c.loadBatches = async () => { throw Error('offline'); };
  await c.saveEditBatch(); equal(messages.at(-1)[0], c.window.PageInteractions.text('batchEditRefreshFailed'));
  equal(vm.runInContext('editingBatchId', c), null);

  // 401 时必须关闭高于登录层的编辑弹窗，旧请求不能影响重新登录后的同一账号。
  c.loadBatches = async () => {}; setBatch(fixture);
  await c.openEditBatch(12);
  let rejectExpired;
  c.api = () => new Promise((_resolve, reject) => { rejectExpired = reject; });
  const expiredSave = c.saveEditBatch();
  const messagesBeforeExpiry = messages.length;
  c.handleSessionExpired();
  equal(e('login-overlay').style.display, 'flex');
  equal(e('edit-batch-modal').style.display, 'none');
  equal(e('batch-edit-status').style.display, 'none');
  equal(e('eb-save').disabled, false);
  equal(vm.runInContext('editingBatchId', c), null);
  rejectExpired(Error('登录已过期')); await expiredSave;
  equal(messages.length, messagesBeforeExpiry);

  vm.runInContext('CURRENT_USER_ID=7', c);
  await c.openEditBatch(12);
  let resolveStale;
  c.api = () => new Promise(resolve => { resolveStale = resolve; });
  const staleSave = c.saveEditBatch();
  c.handleSessionExpired();
  vm.runInContext('CURRENT_USER_ID=7', c);
  await c.openEditBatch(12);
  let resolveCurrent;
  c.api = () => new Promise(resolve => { resolveCurrent = resolve; });
  const currentSave = c.saveEditBatch();
  resolveStale(fixture); await staleSave;
  equal(e('edit-batch-modal').style.display, '');
  equal(e('eb-save').disabled, true);
  equal(e('batch-edit-status').textContent, c.window.PageInteractions.text('batchEditSaving'));
  resolveCurrent(fixture); await currentSave;
  equal(e('edit-batch-modal').style.display, 'none');
  equal(messages.at(-1)[0], c.window.PageInteractions.text('batchEditSaved'));

  c.isAdmin = () => false; c.isOperator = () => true;
  setBatch({...fixture, remaining_quantity: 10}); c.renderBatchPage();
  ok(e('batches-tbody').innerHTML.includes('voidBatch(12)'));
  ok(!e('batches-tbody').innerHTML.includes('deleteBatch(12)'));
  setBatch({...fixture, voided_at: '2026-10-27T01:00:00'}); c.renderBatchPage();
  ok(e('batches-tbody').innerHTML.includes('已作废'));
  ok(!e('batches-tbody').innerHTML.includes('voidBatch(12)'));
  ok(!e('batches-tbody').innerHTML.includes('openEditBatch(12)'));
  setBatch({...fixture, remaining_quantity: 10});
  let voidRequests = 0;
  c.api = async (method, url, body) => { voidRequests++; equal([method, url], ['POST', '/batches/12/void']);
    equal(body.edit_version, fixture.edit_version); equal(body.reason, '盘点重复');
    return {...fixture, voided_at: '2026-10-27T01:00:00+00:00'}; };
  c.window.PageInteractions.reason = async () => null;
  await c.voidBatch(12); equal(voidRequests, 0);
  c.window.PageInteractions.reason = async () => '盘点重复';
  await c.voidBatch(12); equal(voidRequests, 1);
  equal(messages.at(-1)[0], c.window.PageInteractions.text('batchVoidSaved'));
  ok(!e('batches-tbody').innerHTML.includes('voidBatch(12)'));
  setBatch({...fixture, remaining_quantity: 10});
  c.loadBatches = async () => { throw new Error('refresh failed'); };
  await c.voidBatch(12); equal(voidRequests, 2);
  equal(messages.at(-1)[0], c.window.PageInteractions.text('batchVoidRefreshFailed'));
  ok(!e('batches-tbody').innerHTML.includes('voidBatch(12)'));

  // 作废已经成功，退出或同账号重新登录后，旧刷新失败不得污染新会话。
  for (const relogin of [false, true]) {
    setBatch({...fixture, remaining_quantity: 10});
    let rejectRefresh;
    c.loadBatches = () => new Promise((_resolve, reject) => { rejectRefresh = reject; });
    const staleVoid = c.voidBatch(12);
    while (!rejectRefresh) await Promise.resolve();
    c.handleSessionExpired();
    if (relogin) vm.runInContext('CURRENT_USER_ID=7', c);
    const before = messages.length;
    rejectRefresh(new Error('old refresh failed'));
    await staleVoid;
    equal(messages.length, before);
  }
}
(async () => {
  for (const file of ['templates/index.html', 'index.html']) await verify(file);
  console.log(JSON.stringify({checks, page_copies: 2, scope: 'actual batch page functions; simulated DOM and API'}));
})().catch(error => { console.error(error); process.exitCode = 1; });
