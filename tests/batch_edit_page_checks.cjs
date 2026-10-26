// 执行两份首页的真实批次函数；DOM模拟，视觉检查另行执行。
const fs = require('node:fs');
const vm = require('node:vm');
const assert = require('node:assert/strict');
let checks = 0;
const ok = value => { assert(value); checks++; };
const equal = (a, b) => { assert.deepEqual(a, b); checks++; };
const fixture = {id: 12, product_id: 42, user_id: 7, batch_no: 'HISTORY-01', quantity: 13,
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
    documentElement: {lang: 'zh-CN'}}, isAdmin: () => false, isOperator: () => true,
    escapeHtml: value => String(value).replaceAll('"', '&quot;'), showMsg: (...args) => messages.push(args),
    applyBatchColumnSelection: () => {}, renderPagination: () => {}, updateBatchSelectionState: () => {},
    loadBatches: async () => {}, calcEditUnitCost: () => {}});
  vm.runInContext(fs.readFileSync('static/page-interactions.js', 'utf8'), c);
  vm.runInContext('let editingBatchId=null,batchEditSaving=false,currentBatchList=[],CURRENT_USER_ID=7;' +
    'let batchTotal=1,batchPage=1,batchPageSize=20,visibleBatchColumns=new Set(),selectedBatchIds=new Set();', c);
  for (const name of ['batchEditBlockedReason', 'renderBatchPage', 'openEditBatch', 'closeEditBatch', 'saveEditBatch']) {
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
  setBatch(fixture); c.renderBatchPage();
  ok(e('batches-tbody').innerHTML.includes('openEditBatch(12)'));
  ok(!e('batches-tbody').innerHTML.includes('disabled'));
  ok(e('batches-tbody').innerHTML.includes('deleteBatch(12)'));
  for (const [batch, reason] of [[{...fixture, user_id: 9}, 'batchEditOwner'],
    [{...fixture, remaining_quantity: 0}, 'batchEditConsumed'],
    [{...fixture, remaining_quantity: 12}, 'batchEditConsumed']]) {
    setBatch(batch); c.renderBatchPage();
    const body = e('batches-tbody').innerHTML;
    ok(body.includes('disabled') && body.includes(c.window.PageInteractions.text(reason)));
    ok(!body.includes('deleteBatch(12)'));
    await c.openEditBatch(12); equal(vm.runInContext('editingBatchId', c), null);
  }
  setBatch({...fixture, user_id: 9}); c.isAdmin = () => true; await c.openEditBatch(12);
  equal(vm.runInContext('editingBatchId', c), 12); c.closeEditBatch();
  c.isOperator = () => false; c.renderBatchPage();
  ok(!e('batches-tbody').innerHTML.includes('openEditBatch'));
  ok(e('batches-tbody').innerHTML.includes('exportBatch'));
  await c.openEditBatch(12); equal(vm.runInContext('editingBatchId', c), null);
  c.isOperator = () => true; c.isAdmin = () => false; setBatch(fixture);
  await c.openEditBatch(12);
  equal(e('eb-total-amount').value, '34.0054');
  equal(e('eb-ship').value, '161.8555');
  equal(e('eb-date').value, '2026-08-30T00:15');
  let calls = 0, payload, target;
  c.api = async (method, url, body) => { calls++; payload = body; target = [method, url]; return fixture; };
  e('edit-batch-form').reportValidity = () => false;
  await c.saveEditBatch(); equal(calls, 0);
  e('edit-batch-form').reportValidity = () => true;
  for (const id of ['eb-dingtalk-order', 'eb-ship-no', 'eb-lastmile-no', 'eb-other-no']) e(id).value = '  ';
  await c.saveEditBatch(); equal(calls, 1); equal(target, ['PUT', '/batches/12']);
  equal(payload.product_id, 42); equal(payload.total_amount, '34.0054'); equal(payload.shipping_cost, '161.8555');
  for (const key of ['dingtalk_order_no', 'shipping_cost_no', 'last_mile_cost_no', 'other_cost_no']) equal(payload[key], null);
  equal(payload.arrived_at, fixture.arrived_at); equal(payload.shipping_date, fixture.shipping_date);
  equal(e('edit-batch-modal').style.display, 'none'); equal(e('eb-save').disabled, false);
  // 保存中不能重复提交、切换编辑目标或取消；请求使用点击时的批次。
  await c.openEditBatch(12);
  let finish; c.api = async () => { calls++; return new Promise(resolve => { finish = resolve; }); };
  const saving = c.saveEditBatch(); await c.saveEditBatch(); c.closeEditBatch(); await c.openEditBatch(99);
  equal(calls, 2); equal(e('eb-save').disabled, true); equal(vm.runInContext('editingBatchId', c), 12);
  finish(fixture); await saving; equal(e('eb-save').disabled, false);
  // 服务端409/并发出库拦截保留表单，便于核对；刷新失败明确表示已经保存。
  await c.openEditBatch(12); e('eb-total-amount').value = '99.9999';
  c.api = async () => { throw Error('batch_has_cost_adjustments'); };
  await c.saveEditBatch(); equal(e('eb-total-amount').value, '99.9999');
  equal(e('edit-batch-modal').style.display, ''); equal(messages.at(-1)[0], 'batch_has_cost_adjustments');
  equal(e('eb-save').disabled, false);
  c.api = async () => fixture; c.loadBatches = async () => { throw Error('offline'); };
  await c.saveEditBatch(); equal(messages.at(-1)[0], c.window.PageInteractions.text('batchEditRefreshFailed'));
  equal(vm.runInContext('editingBatchId', c), null);
}
(async () => {
  for (const file of ['templates/index.html', 'index.html']) await verify(file);
  console.log(JSON.stringify({checks, page_copies: 2, scope: 'actual batch page functions; simulated DOM and API'}));
})().catch(error => { console.error(error); process.exitCode = 1; });
