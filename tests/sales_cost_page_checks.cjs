// 执行两份真实销售页面：分项/总成本、独立跌价、未知传播、空态和翻译键。
const fs = require('node:fs'), vm = require('node:vm'), assert = require('node:assert/strict');
let checks = 0;
const check = (actual, expected) => {assert.deepEqual(actual, expected); checks++;};
const cells = html => [...html.matchAll(/<td\b[^>]*>([\s\S]*?)<\/td>/g)].map(match => match[1]);
async function main() {
    for (const file of ['templates/index.html', 'index.html']) {
        const html = fs.readFileSync(file, 'utf8'), elements = new Map();
        const element = id => {
            if (!elements.has(id)) elements.set(id, {style: {}, innerHTML: ''});
            return elements.get(id);
        };
        const translated = [...html.matchAll(/data-sales-cost-text="([^"]+)"/g)].map(match => ({dataset: {salesCostText: match[1]}}));
        const c = vm.createContext({window: {}, URLSearchParams,
            document: {documentElement: {lang: 'zh-CN'}, getElementById: element,
                querySelectorAll: selector => selector === '[data-sales-cost-text]' ? translated : []},
            escapeHtml: value => String(value).replaceAll('&','&amp;').replaceAll('<','&lt;').replaceAll('>','&gt;').replaceAll('"','&quot;'),
            isOperator: () => false, loadProductOptions: async () => {}, renderMonthlySalesSummary: () => {},
            renderPagination: () => {}, loadPendingConfirmations: async () => {},
        });
        vm.runInContext(fs.readFileSync('static/page-interactions.js','utf8'),c);
        check(translated.length,7);
        check(translated.every(node => typeof node.textContent === 'string' && node.textContent.length > 0),true);
        vm.runInContext('let salesPage=1,salesPageSize=20,salesMonth="2026-09",salesProductKeyword="SKU",salesTotal=0,allProducts=[];',c);
        const source = html.match(/^async function loadSales\(\) \{[\s\S]*?^\}/m);
        assert(source); vm.runInContext(source[0],c);
        const sale = {id:1,product_id:1,quantity:2,total_cost:'20.004',order_total_cost_cny:'20.00',
            selling_price_cny:'30', sales_revenue_cny:'60',platform_fee_cny:'5',profit_cny:'34.996',
            purchase_cost_cny:'8',head_shipping_cost_cny:'4',last_mile_cost_cny:'2',other_cost_cny:'6',impairment_cny:'.9'};
        let rows = [sale];
        c.api = async (_method,url) => url.startsWith('/sales/monthly-summary') ? [] : {items:rows,total:rows.length,page:1,page_size:20};
        await c.loadSales();
        let body=cells(element('sales-tbody').innerHTML), footer=cells(element('sales-total-summary-body').innerHTML);
        check(body.length,16);check(body.slice(11),['¥8.00','¥4.00','¥2.00','¥6.00','¥0.90']);
        check(body[8],'¥20.00');check(body[10],'¥35.00'); // 跌价不再次扣减毛利
        check(footer.slice(-5),body.slice(11));
        check(footer[5],'¥20.00');
        check(body[6].includes('¥10.00'),true); // 原成本精度仍用于FIFO单价
        rows=[sale,{...sale,id:2,quantity:0,total_cost:0,order_total_cost_cny:0,purchase_cost_cny:0,
            head_shipping_cost_cny:0,last_mile_cost_cny:0,other_cost_cny:0,impairment_cny:0}];
        await c.loadSales();
        check(cells(element('sales-total-summary-body').innerHTML).slice(-5),['¥8.00','¥4.00','¥2.00','¥6.00','¥0.90']);
        rows=[sale,{...sale,id:2,purchase_cost_cny:null,impairment_cny:null,
            cost_breakdown_review_reason:'<script>unsafe</script>',impairment_review_reason:'" onmouseover="unsafe'}];
        await c.loadSales();
        body=cells(element('sales-tbody').innerHTML);footer=cells(element('sales-total-summary-body').innerHTML);
        check(body.length,32);check(body[27],'待核对');check(body[31],'待核对');
        check(footer.slice(-5),['待核对','¥8.00','¥4.00','¥12.00','待核对']);
        check(element('sales-tbody').innerHTML.includes('<script>'),false);
        check(element('sales-tbody').innerHTML.includes('title="&quot; onmouseover=&quot;unsafe"'),true);
        // 旧后端没有新字段时必须待核对，不能显示0。
        rows=[{id:3,quantity:1,total_cost:10}];await c.loadSales();
        check(cells(element('sales-tbody').innerHTML).slice(-5),Array(5).fill('待核对'));
        rows=[];await c.loadSales();
        check(element('sales-tbody').innerHTML.includes('colspan="16"'),true);
        check(cells(element('sales-total-summary-body').innerHTML).slice(-5),Array(5).fill('¥0.00'));
        check(cells(element('sales-total-summary-body').innerHTML).length,13); // 4列合并为1格
    }
    console.log(JSON.stringify({page_copies:2,checks,scope:'sales cost rendering and localized labels'}));
}
main().catch(error => {console.error(error);process.exitCode=1;});
