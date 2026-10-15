// Node执行真实页面函数；DOM模拟，不是浏览器视觉验收。
const fs = require('fs');
const vm = require('vm');
const assert = require('assert/strict');
const fixture = {store_name: '<测试店铺>', sku: 'SKU-1', product_name: '财务示例', batch_no: 'A',
  arrived_at: '2026-08-21', age_days: 40, unit_cost: 10, original_quantity: 100,
  original_inventory_amount: 1000, sold_quantity: 40, sold_cost_amount: 400,
  quantity: 60, provision_quantity: 60, inventory_amount: 600, impairment_rate: .2,
  impairment_amount: 120, monthly_impairment_amount: 90, opening_impairment_amount: 30, monthly_total_impairment_amount: 90, monthly_writeoff_amount: 0, book_value: 480,
  book_value_difference: -90, comparison_note: '', status: '正常计提', batch_count: 1};
let checks = 0;

async function verify(path) {
  const html = fs.readFileSync(path, 'utf8');
  const scripts = [...html.matchAll(/<script\b[^>]*>([\s\S]*?)<\/script>/gi)].map(m => m[1]);
  scripts.forEach((script, index) => new vm.Script(script, {filename: `${path}:${index}`}));
  const script = scripts.join('\n');
  const names = ['escapeHtml', 'reportMoney', 'reportPercent', 'inventoryComparisonCell',
    'inventoryReportTotals', 'loadInventoryImpairment'];
  const functions = names.map(name => {
    const start = script.search(new RegExp(`^(?:async )?function ${name}\\(`, 'm'));
    assert(start >= 0);
    const remaining = script.slice(start);
    const next = remaining.slice(1).search(/\n(?:async )?function /);
    return next < 0 ? remaining : remaining.slice(0, next + 1);
  });
  for (const view of ['batch', 'sku']) {
    const elements = new Map();
    const element = id => {
      if (!elements.has(id)) elements.set(id, {value: '', innerHTML: '', textContent: '', hidden: false});
      return elements.get(id);
    };
    element('inventory-report-view').value = view;
    element('inventory-impairment-date').value = '2026-09-30';
    const context = vm.createContext({document: {getElementById: element},
      api: async () => [fixture], console});
    vm.runInContext(functions.join('\n') + '\n inventoryImpairmentRequestId = 0;', context);
    await vm.runInContext('loadInventoryImpairment()', context);
    const prefix = view === 'batch' ? 'inventory-impairment' : 'inventory-summary';
    const body = element(`${prefix}-tbody`).innerHTML;
    const footer = element(`${prefix}-tfoot`).innerHTML;
    assert.equal((body.match(/<td\b/g) || []).length, view === 'batch' ? 23 : 18);
    assert(body.includes('120.00') && body.includes('480.00') && body.includes('90.00'));
    assert(footer.includes('480.00') && footer.includes('120.00'));
    assert(body.includes('&lt;测试店铺&gt;') && !body.includes('undefined'));
    assert(element('inventory-report-scope').textContent.includes('全部已到货批次'));
    checks += 5;
    const unknown = {...fixture, provision_quantity: null, impairment_rate: null,
      impairment_amount: null, monthly_impairment_amount: null, opening_impairment_amount: null,
      monthly_total_impairment_amount: null, monthly_writeoff_amount: null, book_value: null,
      book_value_difference: null, comparison_note: '历史流水待核对', status: '历史流水待核对'};
    context.api = async () => [unknown];
    await vm.runInContext('loadInventoryImpairment()', context);
    const unknownBody = element(`${prefix}-tbody`).innerHTML;
    const unknownFooter = element(`${prefix}-tfoot`).innerHTML;
    assert(unknownBody.includes('历史流水待核对') && !unknownBody.includes('undefined') && !unknownBody.includes('null'));
    assert(!unknownFooter.includes('480.00') && !unknownFooter.includes('120.00'));
    assert(unknownFooter.includes('合计留空') && unknownFooter.includes('1,000.00'));
    checks += 3;
  }
  assert(html.includes('账面价值(元)') && html.includes('现存库存成本金额 − 期末累计跌价准备'));
  checks += 1;
}

(async () => {
  await verify('templates/index.html');
  await verify('index.html');
  console.log(JSON.stringify({page_copies: 2, syntax: 'passed', dom_checks: checks,
    scope: 'in-memory DOM simulation, not browser visual verification'}));
})().catch(err => { console.error(err); process.exitCode = 1; });
