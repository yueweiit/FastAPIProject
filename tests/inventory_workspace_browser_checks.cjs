// 真实无头浏览器 + 本地静态页面 + 模拟API；不启动main、不连接数据库或外部网络。
// 需现有Playwright；可用INVENTORY_PLAYWRIGHT_MODULE指定现有模块，INVENTORY_QA_DIR保存截图。
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const http = require('node:http');
const {chromium} = require(process.env.INVENTORY_PLAYWRIGHT_MODULE || 'playwright');
let checks = 0;
const check = (actual, expected) => {assert.deepEqual(actual, expected); checks++;};
const wait = () => {let resolve; const promise = new Promise(done => resolve = done); return {promise, resolve};};
const products = ['stable', 'new', 'safe_stock', 'no_impairment'].map((type, i) => ({
    id: i + 1, sku: `CW000${i + 1}`, name: ['狗狗咀嚼玩具', '猫咪自嗨转盘', '不倒翁羽毛款', '<库存安全测试>'][i],
    product_type: type, safe_stock_quantity: type === 'safe_stock' ? 100 : 0,
    stock_quantity: [7281, 1988, 80, 0][i], stock_value: [85842.99, 25495.35, 915.2, 0][i],
    image: null, last_batch_at: '2026-09-23 00:00:00', created_at: '2026-09-01T00:00:00',
    stock_status: i === 2 ? 'low' : i === 3 ? 'zero' : 'normal',
}));
const offPageProduct = {...products[1], id: 99, sku: 'CW0099', name: '猫咪测试商品（其他页）'};
const prefixProduct = {...products[0], id: 100, sku: 'CW00010', name: '相近SKU商品'};
const catalog = [...products, offPageProduct, prefixProduct];
function overview(url) {
    const keyword = url.searchParams.get('keyword') || '', filter = Number(url.searchParams.get('product_id'));
    const items = filter ? catalog.filter(p => p.id === filter) : keyword && !['slow', 'error'].includes(keyword)
        ? catalog.filter(p => `${p.sku} ${p.name}`.toLowerCase().includes(keyword.toLowerCase())) : products;
    return {items, total: items.length, page: 1, page_size: 20, scope: 'shared',
        summary: {quantity: items.reduce((sum, p) => sum + p.stock_quantity, 0), inventory_amount: items.reduce((sum, p) => sum + p.stock_value, 0),
            low_count: items.filter(p => p.stock_status === 'low').length, zero_count: items.filter(p => p.stock_status === 'zero').length}};
}
function daily(url, role) {
    const month = url.searchParams.get('month').slice(0, 7);
    const end = new Date(Number(month.slice(0, 4)), Number(month.slice(5, 7)), 0).getDate();
    const filter = Number(url.searchParams.get('product_id'));
    return {items: (filter ? catalog : products).filter(p => !filter || p.id === filter).map(product => ({
        product_id: product.id, sku: product.sku, name: product.name, image: null, warnings: [],
        days: Array.from({length: end}, (_, i) => ({date: `${month}-${String(i + 1).padStart(2, '0')}`,
            opening_quantity: role === 'admin' ? 100 + i : null, inbound: 2, outbound: 1,
            adjustment: role === 'admin' ? 0 : null, closing_quantity: role === 'admin' ? 101 + i : null})),
    })), total: filter ? 1 : 4, page: 1, page_size: 20, month: `${month}-01`, start: `${month}-01`, end: `${month}-${end}`,
    scope: role === 'admin' ? 'global' : 'store_outbound',
    warnings: ['inventory_current_documents', ...(role === 'admin' ? [] : ['inventory_scope_restricted'])]};
}

async function main() {
    const files = {'/': 'templates/index.html', '/copy': 'index.html',
        '/page-interactions.js': 'static/page-interactions.js', '/financial-control.js': 'static/financial-control.js',
        '/inventory-workspace.js': 'static/inventory-workspace.js', '/inventory-assessment.js': 'static/inventory-assessment.js', '/inventory-allocations.js': 'static/inventory-allocations.js'};
    const server = http.createServer((req, res) => {
        const file = files[new URL(req.url, 'http://unit.invalid').pathname];
        if (!file) {res.writeHead(404); res.end(); return;}
        res.setHeader('Content-Type', file.endsWith('.js') ? 'text/javascript; charset=utf-8' : 'text/html; charset=utf-8');
        res.end(fs.readFileSync(path.resolve(file)));
    });
    await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
    let browser;
    try {
        browser = await chromium.launch({headless: true, ...(process.env.INVENTORY_BROWSER_CHANNEL ? {channel: process.env.INVENTORY_BROWSER_CHANNEL} : {})});
        const page = await browser.newPage({viewport: {width: 1440, height: 1000}});
        let role = 'admin', gate = null, detailGate = null, requestCount = 0;
        let exportGate = null, exportStatus = 200, exportHtml = false;
        let productGate = null, productStatus = 200;
        const productCalls = [];
        const exportCalls = [];
        const assignmentCalls = [];
        const allocationCalls=[];let allocationGate=null,allocationStatus=200,allocationStoresStatus=200;let allocationTotal=1,allocationTotalAfterSave=null;
        const salesExportCalls = [];
        let salesExportStatus = 200;
        let salesRows = [];
        let countGate=null,countStatus=200,countNetworkError=false,countRecords=[],countCalls=[];
        const assignmentStores = [{id: 7, name: '店铺A', is_active: true}, {id: 8, name: '<店铺B>', is_active: true}];
        const batchFixture = {id: 1, product_id: 1, batch_no: 'IN-TEST-1', user_id: 10, quantity: 100, remaining_quantity: 70,
            purchase_price: '10.0000', total_amount: '1000.0000', unit_cost: '10.0000', shipping_cost: '0.0000', last_mile_cost: '0.0000',
            other_cost: '0.0000', arrived_at: '2026-09-01T00:00:00', shipping_date: null, edit_version: 'a'.repeat(64),
            impairment_store_ids: [7, 8], impairment_store_names: ['店铺A', '<店铺B>'], impairment_store_history: []};
        const pageErrors = [];
        page.on('pageerror', error => pageErrors.push(error.message));
        await page.route('**/*', async route => {
            const url = new URL(route.request().url());
            if (url.hostname !== '127.0.0.1') {await route.abort(); return;}
            const fulfill = data => route.fulfill({status: 200, contentType: 'application/json', body: JSON.stringify(data)});
            if (url.pathname === '/auth/config') return fulfill({local_enabled: true, sso_available: false});
            if (url.pathname === '/auth/me') return fulfill({id: 10, username: '库存测试', role, store_id: role === 'admin' ? null : 7, csrf_token: 'test-only'});
            if (url.pathname === '/products') {
                const keyword = url.searchParams.get('keyword') || '';
                if (!keyword) return fulfill({items: products, total: 4, page: 1, page_size: 20});
                productCalls.push(Object.fromEntries(url.searchParams));
                const status = productStatus;
                if (productGate) {const held = productGate; productGate = null; await held.promise;}
                if (status !== 200) return route.fulfill({status, contentType: 'application/json', body: JSON.stringify({detail: '商品查询失败测试'})});
                const items = catalog.filter(product => `${product.sku} ${product.name}`.toLowerCase().includes(keyword.toLowerCase()));
                return fulfill({items, total: items.length, page: 1, page_size: 20});
            }
            if (url.pathname === '/products/options') return fulfill(products);
            if (url.pathname === '/finance-masters/platform-sku-mappings') return fulfill([]);
            if(url.pathname==='/finance-masters/stores'&&allocationStoresStatus!==200)return route.fulfill({status:allocationStoresStatus,contentType:'application/json',body:JSON.stringify({detail:'store load failed'})});
            if (url.pathname === '/finance-masters/stores') return fulfill(assignmentStores);
            if (url.pathname === '/inventory/assessment/export') return route.fulfill({status:200,
                contentType:'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',body:Buffer.from('PK\x03\x04fixture')});
            if (url.pathname === '/inventory/assessment') return fulfill({items:products.map(p=>({
                product_id:p.id,sku:p.sku,name:p.name,as_of:url.searchParams.get('as_of'),system_quantity:p.stock_quantity,
                month_inbound:100,month_outbound:20,average_unit_cost:p.id===4?null:10,inventory_amount:p.id===4?null:p.stock_quantity*10,
                impairment_amount:p.id===4?null:10,monthly_total_impairment_amount:p.id===4?null:12,book_value:p.id===4?null:p.stock_quantity*10-10,
                ledger_version:'a'.repeat(64),warnings:p.id===4?['inventory_assessment_quantity_mismatch']:[],count:countRecords.filter(r=>r.product_id===p.id).at(-1)||null,count_basis_changed:false,
                batches:[{batch_no:'<BATCH>',arrived_at:'2026-09-01',age_days:29,quantity:p.id===4?null:p.stock_quantity,unit_cost:10,
                    impairment_amount:p.id===4?null:10,store_names:['<店铺A>'],store_allocations:[{store_name:'<店铺A>',impairment_amount:p.id===4?null:10}],note:'<script>unsafe</script>'}]
            })),total:4,page:1,page_size:20,can_write:role!=='viewer',scope:role==='admin'?'global':'shared_current'});
            if (url.pathname === '/inventory/stock-counts') {
                if(route.request().method()==='POST') {
                    const payload=route.request().postDataJSON();countCalls.push(payload);
                    if(countNetworkError)return route.abort('failed');
                    const status=countStatus;
                    if(countGate){const held=countGate;countGate=null;await held.promise;}
                    if(status!==200)return route.fulfill({status,contentType:'application/json',body:JSON.stringify({detail:{code:'inventory_count_conflict'}})});
                    const result={...payload,id:countRecords.length+1,revision:countRecords.length+1,created_at:'2026-10-29T10:00:00',created_by:10,
                        system_quantity:products.find(p=>p.id===payload.product_id).stock_quantity};result.difference=result.actual_quantity-result.system_quantity;
                    countRecords.push(result);return fulfill(result);
                }
                const items=countRecords.filter(r=>r.product_id===Number(url.searchParams.get('product_id'))).reverse();
                return fulfill({items,total:items.length,page:1,page_size:20});
            }
            if (url.pathname === '/sales/export') {
                salesExportCalls.push(Object.fromEntries(url.searchParams));
                if (salesExportStatus !== 200) return route.fulfill({status: salesExportStatus, contentType: 'application/json',
                    body: JSON.stringify({detail: {code: 'sales_export_empty'}})});
                return route.fulfill({status: 200, contentType: 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
                    body: Buffer.from('PK\x03\x04sales-download-transport-fixture')});
            }
            if (url.pathname === '/sales/monthly-summary' || url.pathname === '/sales/imports') return fulfill([]);
            if (url.pathname === '/sales') return fulfill({items: salesRows, total: salesRows.length, page: 1, page_size: 20});
            if (url.pathname === '/sales/pending-confirmations') return fulfill({items: [], total: 0, page: 1, page_size: 20});
            if (/^\/batches\/\d+\/impairment-stores$/.test(url.pathname)) {
                const payload=route.request().postDataJSON();allocationCalls.push(payload);
                const status=allocationStatus;
                if(allocationGate){const held=allocationGate;allocationGate=null;await held.promise;}
                if(status!==200)return route.fulfill({status,contentType:'application/json',body:JSON.stringify({detail:{code:'batch_edit_conflict'}})});
                Object.assign(batchFixture,payload,{edit_version:'b'.repeat(64),impairment_store_names:payload.impairment_store_ids.map(id=>assignmentStores.find(store=>store.id===id).name)});
                if(allocationTotalAfterSave!==null){allocationTotal=allocationTotalAfterSave;allocationTotalAfterSave=null;}
                return fulfill({...batchFixture,id:Number(url.pathname.split('/')[2])});
            }
            if (url.pathname === '/batches' || url.pathname === '/batches/1') {
                if (route.request().method() !== 'GET') {
                    const payload = route.request().postDataJSON(); assignmentCalls.push(payload);
                    return fulfill({...batchFixture,...payload,impairment_store_ids:payload.impairment_store_ids||batchFixture.impairment_store_ids,impairment_store_names:(payload.impairment_store_ids||batchFixture.impairment_store_ids).map(id=>assignmentStores.find(store=>store.id===id).name)});
                }
                const pageNumber=Math.min(Number(url.searchParams.get('page')||1),Math.max(1,Math.ceil(allocationTotal/20)));
                const start=(pageNumber-1)*20;
                const items=Array.from({length:Math.min(20,Math.max(0,allocationTotal-start))},(_,i)=>({
                    ...batchFixture,id:start+i+1,batch_no:start+i===0?batchFixture.batch_no:`IN-TEST-${start+i+1}`}));
                return fulfill({items,total:allocationTotal,page:pageNumber,page_size:20});
            }
            if (url.pathname === '/inventory/export') {
                exportCalls.push(Object.fromEntries(url.searchParams));
                const status = exportStatus, html = exportHtml;
                if (exportGate) {const held = exportGate; exportGate = null; await held.promise;}
                if (status !== 200) return route.fulfill({status, contentType: 'application/json', body: JSON.stringify({
                    detail: status === 404 ? 'Not Found' : status === 401 ? 'Login expired' : {code: 'inventory_export_too_large', message: '导出范围过大'},
                })});
                return route.fulfill({status: 200, contentType: html ? 'text/html' : 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
                    headers: {'Content-Disposition': `attachment; filename="inventory_${url.searchParams.get('view')}_test.xlsx"`},
                    body: html ? '<html>unexpected page</html>' : Buffer.from('PK\x03\x04inventory-download-transport-fixture')});
            }
            if (url.pathname === '/inventory/overview') {
                requestCount++;
                if (url.searchParams.get('keyword') === 'slow' && gate) {const held = gate; await held.promise;}
                if (url.searchParams.get('keyword') === 'error') return route.fulfill({status: 503, contentType: 'application/json', body: JSON.stringify({detail: '读取失败测试'})});
                return fulfill(overview(url));
            }
            if (url.pathname === '/inventory/daily') {requestCount++; return fulfill(daily(url, role));}
            if (url.pathname === '/inventory/movements') {
                if (detailGate) {const held = detailGate; detailGate = null; await held.promise;}
                return fulfill({items: [{occurred_at: `${url.searchParams.get('day')}T12:00:00`, kind: url.searchParams.get('kind'),
                    batch_no: '<批次测试>', reference: '<订单测试>', quantity: url.searchParams.get('kind') === 'outbound' ? -1 : 2}],
                    total: 1, page: 1, page_size: 20, sku: 'CW0001', name: '狗狗咀嚼玩具', day: url.searchParams.get('day')});
            }
            await route.continue();
        });
        const downloadExport = async () => {
            const pending = page.waitForEvent('download');
            await page.locator('#iw-export').click();
            const download = await pending;
            check(await download.failure(), null);
            await page.waitForFunction(() => document.getElementById('iw-export')?.disabled === false);
            return download;
        };
        const address = `http://127.0.0.1:${server.address().port}`;
        for (const suffix of ['/', '/copy']) {
            await page.goto(address + suffix);
            await page.waitForFunction(() => CURRENT_USER_ID === 10 && !!window.InventoryWorkspace);
            await page.locator('[data-inventory-nav]').click();
            await page.locator('#iw-tbody tr').first().waitFor();
            check(await page.locator('#iw-tbody tr').count(), 4);
            // 清空每日月份后返回总览，隐藏月份不能阻止查询；每日仍保留必填校验。
            const validMonth = await page.locator('#iw-month').inputValue();
            await page.locator('[data-iw-action="daily"]').click();
            await page.locator('#iw-month').fill('');
            await page.locator('[data-iw-action="overview"]').click();
            await page.locator('#iw-tbody tr').first().waitFor();
            check(await page.locator('#iw-month').isDisabled(), true);
            check(await page.locator('#iw-query').evaluate(form => form.checkValidity()), true);
            const overviewQuery = page.waitForResponse(response => new URL(response.url()).pathname === '/inventory/overview');
            await page.locator('#iw-query button[type="submit"]').click();
            await overviewQuery;
            await page.locator('#iw-tbody tr').first().waitFor();
            await page.locator('[data-iw-action="daily"]').click();
            check(await page.locator('#iw-month').isDisabled(), false);
            check(await page.locator('#iw-query').evaluate(form => form.checkValidity()), false);
            await page.locator('#iw-month').fill(validMonth);
            await page.locator('#iw-tbody tr').first().waitFor();
            await page.locator('[data-iw-action="overview"]').click();
            await page.locator('#iw-tbody tr').first().waitFor();
            check(await page.locator('#iw-tbody').innerText().then(value => value.includes('<库存安全测试>')), true);
            check(await page.locator('#iw-tbody img').count(), 0);
            check(await page.locator('#iw-tbody').innerText().then(value => value.includes('低于安全库存')), true);
            const overviewFile = await downloadExport();
            check(overviewFile.suggestedFilename(), 'inventory_overview_test.xlsx');
            check(exportCalls.at(-1).view, 'overview');
            check('page' in exportCalls.at(-1) || 'page_size' in exportCalls.at(-1), false);
            if (process.env.INVENTORY_QA_DIR && suffix === '/') {
                fs.mkdirSync(process.env.INVENTORY_QA_DIR, {recursive: true});
                await page.screenshot({path: path.join(process.env.INVENTORY_QA_DIR, 'inventory-overview-desktop.png'), fullPage: true});
            }
            // 顶部关键字联想复用商品候选，不输入即查询；点选按ID精确过滤并同步导出。
            const beforeKeyword = requestCount;
            await page.locator('#iw-keyword').fill('猫咪');
            await page.locator('#iw-keyword-options [role="option"]').nth(1).waitFor();
            check(requestCount, beforeKeyword);
            check(await page.locator('#iw-keyword-options').innerText().then(value => value.includes('CW0099')), true);
            check(await page.locator('#iw-export').isDisabled(), true);
            if (process.env.INVENTORY_QA_DIR && suffix === '/') await page.screenshot({path: path.join(process.env.INVENTORY_QA_DIR, 'inventory-keyword-suggestions-desktop.png'), fullPage: true});
            await page.locator('#iw-keyword-options [role="option"]').nth(1).click();
            await page.waitForFunction(() => document.getElementById('iw-page-label').textContent.includes('共 1 个商品'));
            check(await page.locator('#iw-keyword').inputValue(), 'CW0099');
            check(await page.locator('#iw-tbody tr').count(), 1);
            await downloadExport();
            check(exportCalls.at(-1).product_id, '99');
            check(exportCalls.at(-1).keyword, 'CW0099');
            // 切换日结时保留候选选择，不切换现有查看方式。
            await page.locator('[data-iw-action="daily"]').click();
            await page.locator('#iw-tbody [data-iw-move]').first().waitFor();
            check(await page.locator('#iw-layout').inputValue(), 'day');
            check(await page.locator('#iw-tbody tr').count(), 1);
            await downloadExport();
            check(exportCalls.at(-1).product_id, '99');
            await page.locator('[data-iw-action="overview"]').click();
            await page.locator('#iw-tbody [data-iw-trace]').first().waitFor();
            // 即使SKU有前缀相近商品，键盘选中后也只显示和导出目标ID。
            await page.locator('#iw-keyword').fill('CW0001');
            await page.locator('#iw-keyword-options [role="option"]').nth(1).waitFor();
            await page.locator('#iw-keyword').press('ArrowDown');
            check(await page.locator('#iw-keyword').getAttribute('aria-activedescendant'), 'iw-keyword-option-0');
            await page.locator('#iw-keyword').press('Enter');
            await page.waitForFunction(() => document.getElementById('iw-tbody').textContent.includes('狗狗咀嚼玩具'));
            check(await page.locator('#iw-tbody tr').count(), 1);
            await downloadExport();
            check(exportCalls.at(-1).product_id, '1');
            // 不选候选直接回车保留模糊搜索；Esc保留草稿，不误选第一项。
            await page.locator('#iw-keyword').fill('猫咪');
            await page.locator('#iw-keyword-options [role="option"]').nth(1).waitFor();
            await page.locator('#iw-keyword').press('Enter');
            await page.waitForFunction(() => document.getElementById('iw-page-label').textContent.includes('共 2 个商品'));
            check(await page.locator('#iw-tbody tr').count(), 2);
            await downloadExport();
            check('product_id' in exportCalls.at(-1), false);
            await page.locator('#iw-keyword').fill('missing-product');
            await page.waitForFunction(() => document.getElementById('iw-keyword-search-status').textContent.includes('没有匹配'));
            await page.locator('#iw-keyword').press('Escape');
            check(await page.locator('#iw-keyword').inputValue(), 'missing-product');
            check(await page.locator('#iw-keyword-popup').isHidden(), true);
            await page.locator('#iw-keyword').press('Enter');
            await page.waitForFunction(() => document.getElementById('iw-tbody').textContent.includes('没有匹配'));
            check(await page.locator('#iw-export').isDisabled(), true);
            productStatus = 503;
            await page.locator('#iw-keyword').fill('failure');
            await page.waitForFunction(() => document.getElementById('iw-keyword-search-status').textContent.includes('商品查询失败测试'));
            await page.locator('#iw-keyword').press('Escape');
            check(await page.locator('#iw-keyword').inputValue(), 'failure');
            productStatus = 200;
            const beforeKeywordComposition = productCalls.length;
            await page.locator('#iw-keyword').evaluate(input => {
                input.value = '羽毛'; input.dispatchEvent(new InputEvent('input', {bubbles: true, isComposing: true}));
            });
            await page.waitForTimeout(300);
            check(productCalls.length, beforeKeywordComposition);
            await page.locator('#iw-keyword').dispatchEvent('compositionend');
            await page.locator('#iw-keyword-options [role="option"]').first().waitFor();
            check(productCalls.at(-1).keyword, '羽毛');
            // 候选中的名称转义，空输入不会请求全量商品建议。
            await page.locator('#iw-keyword').fill('库存安全');
            await page.locator('#iw-keyword-options [role="option"]').first().waitFor();
            check(await page.locator('#iw-keyword-options').innerText().then(value => value.includes('<库存安全测试>')), true);
            check(await page.locator('#iw-keyword-options 库存安全测试').count(), 0);
            await page.locator('#iw-keyword').fill('');
            check(await page.locator('#iw-keyword-popup').isHidden(), true);
            await page.locator('#iw-query button[type="submit"]').click();
            await page.waitForFunction(() => document.getElementById('iw-page-label').textContent.includes('共 4 个商品'));
            await page.locator('[data-iw-action="daily"]').click();
            await page.locator('#iw-tbody [data-iw-move]').first().waitFor();
            await page.locator('#iw-month').fill('2026-09');
            await page.locator('#iw-month').dispatchEvent('change');
            await page.waitForFunction(() => document.getElementById('iw-day').value.startsWith('2026-09'));
            check(await page.locator('#iw-scope').innerText().then(value => value.includes('撤销')), true);
            const dailyFile = await downloadExport();
            check(dailyFile.suggestedFilename(), 'inventory_daily_test.xlsx');
            check(exportCalls.at(-1).month, '2026-09-01');
            await page.locator('#iw-layout').selectOption('product');
            check(await page.locator('#iw-tbody tr').count(), 30);
            await downloadExport();
            check(exportCalls.at(-1).product_id, '1');
            // 输入检索全部商品，可选择不在当前库存页的商品，导出必须跟随选择。
            await page.locator('#iw-product').fill('猫咪');
            check(await page.locator('#iw-export').isDisabled(), true);
            await page.locator('#iw-product-options [role="option"]').nth(1).waitFor();
            check(productCalls.at(-1), {keyword: '猫咪', page: '1', page_size: '20'});
            check(await page.locator('#iw-product-options').innerText().then(value => value.includes('CW0099')), true);
            if (process.env.INVENTORY_QA_DIR && suffix === '/') await page.screenshot({path: path.join(process.env.INVENTORY_QA_DIR, 'inventory-product-search-desktop.png'), fullPage: true});
            await page.locator('#iw-product-options [role="option"]').nth(1).click();
            await page.waitForFunction(() => document.getElementById('iw-product').value.startsWith('CW0099'));
            check(await page.locator('#iw-tbody tr').count(), 30);
            await downloadExport();
            check(exportCalls.at(-1).product_id, '99');
            // 连续输入只提交最后一次查询，大小写SKU与键盘选择可用。
            const productsBefore = productCalls.length;
            await page.locator('#iw-product').fill('C');
            await page.locator('#iw-product').fill('CW');
            await page.locator('#iw-product').fill('cw0002');
            await page.locator('#iw-product-options [role="option"]').first().waitFor();
            check(productCalls.length - productsBefore, 1);
            await page.locator('#iw-product').press('ArrowDown');
            check(await page.locator('#iw-product').getAttribute('aria-activedescendant'), 'iw-product-option-0');
            await page.locator('#iw-product').press('Enter');
            await page.waitForFunction(() => document.getElementById('iw-product').value.startsWith('CW0002'));
            await downloadExport();
            check(exportCalls.at(-1).product_id, '2');
            // 输入未匹配文字不会切换或导出错误商品；取消恢复已选商品。
            await page.locator('#iw-product').fill('missing-product');
            await page.waitForFunction(() => document.getElementById('iw-product-search-status').textContent.includes('没有匹配'));
            const beforeMissing = requestCount;
            await page.locator('#iw-product').press('Enter');
            check(requestCount, beforeMissing);
            check(await page.locator('#iw-export').isDisabled(), true);
            await page.locator('#iw-product').press('Escape');
            check(await page.locator('#iw-product').inputValue(), 'CW0002 · 猫咪自嗨转盘');
            check(await page.locator('#iw-product').getAttribute('aria-expanded'), 'false');
            await page.locator('#iw-product').press('ArrowUp');
            check(await page.locator('#iw-product-options [aria-selected="true"]').count(), 1);
            await page.locator('#iw-product').press('Escape');
            // 候选名称按文本展示，查询失败后保留当前商品；中文输入法完成后再检索。
            await page.locator('#iw-product').fill('库存安全');
            await page.locator('#iw-product-options [role="option"]').first().waitFor();
            check(await page.locator('#iw-product-options').innerText().then(value => value.includes('<库存安全测试>')), true);
            check(await page.locator('#iw-product-options 库存安全测试').count(), 0);
            await page.locator('#iw-product').press('Escape');
            productStatus = 503;
            await page.locator('#iw-product').fill('failure');
            await page.waitForFunction(() => document.getElementById('iw-product-search-status').textContent.includes('商品查询失败测试'));
            await page.locator('#iw-product').press('Escape');
            check(await page.locator('#iw-export').isEnabled(), true);
            productStatus = 200;
            const beforeComposition = productCalls.length;
            await page.locator('#iw-product').evaluate(input => {
                input.value = '羽毛'; input.dispatchEvent(new InputEvent('input', {bubbles: true, isComposing: true}));
            });
            await page.waitForTimeout(300);
            check(productCalls.length, beforeComposition);
            await page.locator('#iw-product').dispatchEvent('compositionend');
            await page.locator('#iw-product-options [role="option"]').first().waitFor();
            check(productCalls.at(-1).keyword, '羽毛');
            await page.locator('#iw-product').press('Escape');
            // 清除指定商品查询后，横向表继续覆盖全查询范围。
            await page.locator('#iw-query button[type="submit"]').click();
            await page.waitForFunction(() => document.getElementById('iw-product').value.startsWith('CW0001'));
            await page.locator('#iw-layout').selectOption('matrix');
            check(await page.locator('#iw-thead th').count(), 31);
            await downloadExport();
            check(exportCalls.at(-1).view, 'matrix');
            check('product_id' in exportCalls.at(-1), false);
            check(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth), true);
            if (process.env.INVENTORY_QA_DIR && suffix === '/') await page.screenshot({path: path.join(process.env.INVENTORY_QA_DIR, 'inventory-daily-matrix.png'), fullPage: true});
            await page.locator('#iw-layout').selectOption('day');
            await page.locator('[data-iw-move="outbound"]').first().click();
            await page.waitForFunction(() => document.getElementById('iw-detail-body').textContent.includes('<订单测试>'));
            check(await page.locator('#iw-detail-body').innerText().then(value => value.includes('-1')), true);
            check(await page.locator('#iw-detail-body script').count(), 0);
            await page.keyboard.press('Escape');
            check(await page.locator('#iw-detail').evaluate(node => node.open), false);
            // 连续退出/重进后，根节点点击处理器不能累积。
            for (let i = 0; i < 2; i++) await page.evaluate(async () => {
                handleSessionExpired(); CURRENT_USER_ID = 10; CURRENT_ROLE = 'admin';
                document.getElementById('login-overlay').style.display = 'none';
                await window.InventoryWorkspace.open();
            });
            const before = requestCount;
            await page.locator('[data-iw-action="refresh"]').click();
            await page.waitForFunction(() => document.getElementById('iw-status').textContent === '');
            check(requestCount - before, 1);
        }
        // 导出期间固定查询条件、拒绝重复请求；结束后保留原分页禁用状态。
        exportGate = wait();
        const heldExport = exportGate;
        const exportBefore = exportCalls.length;
        const exportStarted = page.waitForRequest(request => request.url().includes('/inventory/export'));
        const exportingDownload = page.waitForEvent('download');
        await page.locator('#iw-export').click();
        await exportStarted;
        check(await page.locator('#iw-export').isDisabled(), true);
        check(await page.locator('#iw-keyword').isDisabled(), true);
        await page.locator('#iw-export').dispatchEvent('click');
        check(exportCalls.length - exportBefore, 1);
        heldExport.resolve();
        await exportingDownload;
        await page.waitForFunction(() => document.getElementById('iw-export')?.disabled === false);
        check(await page.locator('[data-iw-action="previous"]').isDisabled(), true);
        exportStatus = 422;
        await page.locator('#iw-export').click();
        await page.waitForFunction(() => document.getElementById('iw-export-status').textContent.includes('2000'));
        check(await page.locator('#iw-export').isEnabled(), true);
        exportStatus = 404;
        await page.locator('#iw-export').click();
        await page.waitForFunction(() => document.getElementById('iw-export-status').textContent.includes('重启'));
        check(await page.locator('#iw-export').isEnabled(), true);
        exportStatus = 200; exportHtml = true;
        await page.locator('#iw-export').click();
        await page.waitForFunction(() => document.getElementById('iw-export-status').textContent.includes('文件无效'));
        check(await page.locator('#iw-export').isEnabled(), true);
        exportHtml = false;
        // 导出中的旧会话401不能清掉同账号刚恢复的新会话。
        exportGate = wait(); exportStatus = 401;
        const oldExport = exportGate;
        const oldExportStarted = page.waitForRequest(request => request.url().includes('/inventory/export'));
        await page.locator('#iw-export').click();
        await oldExportStarted;
        await page.evaluate(async () => {handleSessionExpired(); await loadCurrentUser();});
        await page.locator('[data-inventory-nav]').click();
        await page.locator('#iw-tbody tr').first().waitFor();
        exportStatus = 200;
        oldExport.resolve();
        await page.waitForTimeout(100);
        check(await page.evaluate(() => CURRENT_USER_ID), 10);
        check(await page.locator('#login-overlay').isVisible(), false);
        // 顶部联想也须丢弃慢响应，旧会话401不能清除刚恢复的会话。
        productGate = wait();
        const keywordSlow = productGate;
        const keywordSlowStarted = page.waitForRequest(request => new URL(request.url()).searchParams.get('keyword') === 'slow-keyword');
        await page.locator('#iw-keyword').fill('slow-keyword');
        await keywordSlowStarted;
        await page.locator('#iw-keyword').fill('CW0003');
        await page.locator('#iw-keyword-options [role="option"]').first().waitFor();
        keywordSlow.resolve();
        await page.waitForTimeout(100);
        check(await page.locator('#iw-keyword-options').innerText().then(value => value.includes('CW0003')), true);
        productGate = wait(); productStatus = 401;
        const keywordOld401 = productGate;
        const keyword401Started = page.waitForRequest(request => new URL(request.url()).searchParams.get('keyword') === 'old-keyword-session');
        await page.locator('#iw-keyword').fill('old-keyword-session');
        await keyword401Started;
        await page.evaluate(async () => {handleSessionExpired(); await loadCurrentUser();});
        productStatus = 200;
        await page.locator('[data-inventory-nav]').click();
        await page.locator('#iw-tbody tr').first().waitFor();
        keywordOld401.resolve();
        await page.waitForTimeout(100);
        check(await page.evaluate(() => CURRENT_USER_ID), 10);
        check(await page.locator('#login-overlay').isVisible(), false);
        // 商品搜索慢响应不能覆盖新词；退出重登后的旧401不能清除新会话。
        await page.locator('[data-iw-action="daily"]').click();
        await page.locator('#iw-tbody tr').first().waitFor();
        await page.locator('#iw-layout').selectOption('product');
        productGate = wait();
        const oldProducts = productGate;
        const oldProductsStarted = page.waitForRequest(request => new URL(request.url()).searchParams.get('keyword') === 'slow-products');
        await page.locator('#iw-product').fill('slow-products');
        await oldProductsStarted;
        await page.locator('#iw-product').fill('CW0003');
        await page.locator('#iw-product-options [role="option"]').first().waitFor();
        oldProducts.resolve();
        await page.waitForTimeout(100);
        check(await page.locator('#iw-product-options [role="option"]').count(), 1);
        check(await page.locator('#iw-product-options').innerText().then(value => value.includes('CW0003')), true);
        productGate = wait(); productStatus = 401;
        const oldProducts401 = productGate;
        const oldProducts401Started = page.waitForRequest(request => new URL(request.url()).searchParams.get('keyword') === 'old-session');
        await page.locator('#iw-product').fill('old-session');
        await oldProducts401Started;
        await page.evaluate(async () => {handleSessionExpired(); await loadCurrentUser();});
        productStatus = 200;
        await page.locator('[data-inventory-nav]').click();
        await page.locator('#iw-tbody tr').first().waitFor();
        oldProducts401.resolve();
        await page.waitForTimeout(100);
        check(await page.evaluate(() => CURRENT_USER_ID), 10);
        check(await page.locator('#login-overlay').isVisible(), false);
        // 关闭/重开来源弹窗后，前一条慢响应不能覆盖当前来源。
        await page.locator('[data-iw-action="daily"]').click();
        await page.locator('#iw-tbody [data-iw-move]').first().waitFor();
        detailGate = wait();
        const blockedDetail = detailGate;
        const detailRequest = page.waitForRequest(request => request.url().includes('/inventory/movements'));
        await page.locator('[data-iw-move="outbound"]').first().click();
        await detailRequest;
        await page.keyboard.press('Escape');
        await page.locator('[data-iw-move="inbound"]').first().click();
        await page.waitForFunction(() => document.getElementById('iw-detail-body').textContent.includes('+2'));
        blockedDetail.resolve();
        await page.waitForTimeout(100);
        check(await page.locator('#iw-detail').evaluate(node => node.open), true);
        check(await page.locator('#iw-detail-body tr').first().locator('td').last().innerText(), '+2');
        await page.keyboard.press('Escape');
        await page.locator('[data-iw-action="overview"]').click();
        await page.locator('#iw-tbody [data-iw-trace]').first().waitFor();
        // 旧请求在会话清理后完成，不得重新渲染。
        gate = wait();
        await page.locator('#iw-keyword').fill('slow');
        const slowRequest = page.waitForRequest(request => request.url().includes('keyword=slow'));
        await page.locator('#iw-query button[type="submit"]').click();
        await slowRequest;
        await page.evaluate(() => {handleSessionExpired();});
        gate.resolve();
        await page.waitForTimeout(100);
        check(await page.locator('#inventory-workspace-root').innerHTML(), '');
        // 同账号重新登录，旧会话不影响新工作台。
        await page.evaluate(() => loadCurrentUser());
        await page.locator('[data-inventory-nav]').click();
        await page.locator('#iw-tbody tr').first().waitFor();
        await page.locator('#iw-keyword').fill('error');
        await page.locator('#iw-query button[type="submit"]').click();
        await page.waitForFunction(() => document.getElementById('iw-status').textContent.includes('读取失败测试'));
        check(await page.locator('#iw-tbody tr').count(), 0);
        await page.locator('#iw-keyword').fill('');
        await page.locator('#iw-query button[type="submit"]').click();
        await page.locator('#iw-tbody tr').first().waitFor();
        await page.locator('[data-iw-trace="1"]').click();
        await page.waitForFunction(() => document.getElementById('iw-layout').value === 'product' && document.getElementById('iw-tbody').textContent.includes('2026-'));
        // 窄屏只有表格内部滚动，页面不横向溢出。
        await page.setViewportSize({width: 390, height: 844});
        check(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth), true);
        await page.locator('#iw-keyword').fill('猫咪');
        await page.locator('#iw-keyword-options [role="option"]').nth(1).waitFor();
        check(await page.evaluate(() => {
            const bounds = document.getElementById('iw-keyword-popup').getBoundingClientRect();
            return bounds.left >= 0 && bounds.right <= window.innerWidth;
        }), true);
        if (process.env.INVENTORY_QA_DIR) await page.screenshot({path: path.join(process.env.INVENTORY_QA_DIR, 'inventory-keyword-suggestions-mobile.png'), fullPage: true});
        await page.locator('#iw-keyword').fill('');
        await page.locator('#iw-keyword').press('Escape');
        await page.locator('#iw-product').fill('猫咪');
        await page.locator('#iw-product-options [role="option"]').nth(1).waitFor();
        check(await page.evaluate(() => {
            const bounds = document.getElementById('iw-product-popup').getBoundingClientRect();
            return bounds.left >= 0 && bounds.right <= window.innerWidth;
        }), true);
        if (process.env.INVENTORY_QA_DIR) await page.screenshot({path: path.join(process.env.INVENTORY_QA_DIR, 'inventory-product-search-mobile.png'), fullPage: true});
        await page.locator('#iw-product').press('Escape');
        if (process.env.INVENTORY_QA_DIR) await page.screenshot({path: path.join(process.env.INVENTORY_QA_DIR, 'inventory-mobile.png'), fullPage: true});
        // 归属移入库存：入库/批次资料编辑不再选择；独立维护只提交归属和版本。
        await page.setViewportSize({width:1440,height:1000});role='admin';
        for (const suffix of ['/', '/copy']) {
            Object.assign(batchFixture,{impairment_store_ids:[],impairment_store_names:[],edit_version:'a'.repeat(64),
                impairment_store_history:[{effective_date:'2026-09-01',store_ids:[],store_names:[],is_explicit:true}]});
            await page.goto(address+suffix);await page.waitForFunction(()=>CURRENT_USER_ID===10);
            check(await page.locator('#p-product-type option').allTextContents(),['稳健商品','新品','不跌价商品']);
            await page.getByRole('button',{name:'入库管理',exact:true}).click();
            check(await page.locator('#b-impairment-stores').count(),0);
            const beforeCreate=assignmentCalls.length;
            await page.locator('#b-pid').selectOption('1');await page.locator('#b-qty').fill('100');await page.locator('#b-total-amount').fill('1000');
            await page.getByRole('button',{name:'入库',exact:true}).click();
            await page.waitForFunction(count=>document.getElementById('batches-tbody').textContent.includes('IN-TEST-1'),beforeCreate);
            check(assignmentCalls.length,beforeCreate+1);check(assignmentCalls.at(-1).quantity,100);
            check('impairment_store_ids' in assignmentCalls.at(-1),false);
            await page.locator('#batches-tbody button').filter({hasText:'编辑'}).click();
            check(await page.locator('#eb-impairment-stores').count(),0);
            await page.locator('#eb-dingtalk-order').fill('OWNERSHIP-MOVED');await page.locator('#eb-save').click();
            await page.waitForFunction(()=>document.getElementById('edit-batch-modal').style.display==='none');
            check('impairment_store_ids' in assignmentCalls.at(-1),false);
            await page.getByRole('button',{name:'库存',exact:true}).click();
            allocationStoresStatus=503;await page.locator('[data-iw-allocation="1"]').click();
            await page.locator('#ial-status').getByText('操作失败：',{exact:false}).waitFor();
            check(await page.locator('[data-ial-edit]').count(),0);
            allocationStoresStatus=200;await page.locator('[data-ial-action="refresh"]').click();
            await page.locator('#ial-rows [data-ial-edit="1"]').waitFor();
            check(await page.locator('#ial-rows').innerText().then(text=>text.includes('待分配')),true);
            await page.locator('#ial-rows [data-ial-edit="1"]').click();
            check(await page.locator('#ial-editor input[value="from_arrival"]').isChecked(),true);
            const beforeAllocation=allocationCalls.length;
            await page.getByRole('button',{name:'保存归属',exact:true}).click();
            check(await page.locator('#ial-edit-status').innerText(),'请至少选择一家归属店铺。');check(allocationCalls.length,beforeAllocation);
            await page.locator('#ial-editor input[value="7"]').check();await page.locator('#ial-editor input[value="8"]').check();
            const held=wait();allocationGate=held;
            await page.getByRole('button',{name:'保存归属',exact:true}).click();
            await page.locator('#ial-edit-status').getByText('正在读取…',{exact:true}).waitFor();
            check(await page.locator('#ial-editor input[value="7"]').isDisabled(),true);
            check(await page.getByRole('button',{name:'保存归属',exact:true}).isDisabled(),true);
            held.resolve();await page.locator('#ial-editor').waitFor({state:'hidden'});
            check(allocationCalls.length,beforeAllocation+1);check(allocationCalls.at(-1).impairment_store_ids,[7,8]);
            check(Object.keys(allocationCalls.at(-1)).sort(),['edit_version','impairment_change_mode','impairment_store_ids']);
            check(allocationCalls.at(-1).impairment_change_mode,'from_arrival');
            check(await page.locator('#ial-rows').innerText().then(text=>text.includes('<店铺B>')),true);
            check(await page.locator('#ial-rows 店铺B').count(),0);
            // 保存期间跨页增长和缩减：刷新结果必须决定分页，不能恢复旧disabled值。
            allocationTotal=20;await page.locator('[data-ial-action="refresh"]').click();
            await page.waitForFunction(()=>document.getElementById('ial-page').textContent.includes('共20批'));
            check(await page.locator('#ial-next').isDisabled(),true);
            await page.locator('[data-ial-edit="1"]').click();allocationTotalAfterSave=21;
            await page.getByRole('button',{name:'保存归属',exact:true}).click();
            await page.waitForFunction(()=>document.getElementById('ial-page').textContent.includes('共21批'));
            check(await page.locator('#ial-next').isEnabled(),true);
            await page.locator('#ial-next').click();await page.locator('[data-ial-edit="21"]').waitFor();
            check(await page.locator('#ial-previous').isEnabled(),true);
            await page.locator('[data-ial-edit="21"]').click();allocationTotalAfterSave=20;
            await page.getByRole('button',{name:'保存归属',exact:true}).click();
            await page.waitForFunction(()=>document.getElementById('ial-page').textContent.includes('共20批')&&document.getElementById('ial-page').textContent.includes('第1页'));
            check(await page.locator('#ial-previous').isDisabled(),true);
            check(await page.locator('#ial-next').isDisabled(),true);
            check(await page.locator('#ial-rows tr').count(),20);
            allocationTotal=1;await page.locator('[data-ial-action="refresh"]').click();
            await page.waitForFunction(()=>document.getElementById('ial-page').textContent.includes('共1批'));
            if(process.env.INVENTORY_QA_DIR&&suffix==='/')await page.screenshot({path:path.join(process.env.INVENTORY_QA_DIR,'inventory-allocations-desktop.png'),fullPage:true});
            await page.locator('#ial-rows [data-ial-edit="1"]').click();
            check(await page.locator('#ial-editor input[value="from_change_date"]').isChecked(),true);
            await page.locator('#ial-editor input[value="7"]').uncheck();allocationStatus=409;
            await page.getByRole('button',{name:'保存归属',exact:true}).click();
            await page.locator('#ial-edit-status').getByText('操作失败：',{exact:false}).waitFor();
            check(await page.locator('#ial-editor input[value="8"]').isChecked(),true);
            check(await page.getByRole('button',{name:'保存归属',exact:true}).isEnabled(),true);
            allocationStatus=200;await page.getByRole('button',{name:'保存归属',exact:true}).click();await page.locator('#ial-editor').waitFor({state:'hidden'});
            check(allocationCalls.at(-1).impairment_store_ids,[8]);check(allocationCalls.at(-1).impairment_change_mode,'from_change_date');
            await page.locator('[data-ial-action="close"]').click();
            await page.locator('[data-iw-action="assessment"]').click();await page.locator('[data-ia-allocation="1"]').click();
            await page.locator('#ial-rows [data-ial-edit="1"]').waitFor();check(await page.locator('#ial-rows').innerText().then(text=>text.includes('<店铺B>')),true);
            await page.locator('#ial-rows [data-ial-edit="1"]').click();
            await page.locator('#ial-editor input[value="7"]').check();
            const stale=wait();allocationGate=stale;allocationStatus=401;
            await page.getByRole('button',{name:'保存归属',exact:true}).click();
            await page.locator('#ial-edit-status').getByText('正在读取…',{exact:true}).waitFor();
            await page.evaluate(async()=>{handleSessionExpired();await loadCurrentUser();});
            await page.getByRole('button',{name:'库存',exact:true}).click();await page.locator('[data-iw-allocation="1"]').click();
            await page.locator('#ial-rows [data-ial-edit="1"]').waitFor();stale.resolve();allocationStatus=200;
            check(await page.locator('#ial-dialog').isVisible(),true);
            check(await page.locator('#login-box').isVisible(),false);
            await page.locator('#ial-rows [data-ial-edit="1"]').click();
            check(await page.getByRole('button',{name:'保存归属',exact:true}).isEnabled(),true);
            await page.setViewportSize({width:390,height:844});
            check(await page.locator('#ial-dialog').evaluate(node=>{const r=node.getBoundingClientRect();return r.left>=0&&r.right<=innerWidth;}),true);
            if(process.env.INVENTORY_QA_DIR&&suffix==='/')await page.screenshot({path:path.join(process.env.INVENTORY_QA_DIR,'inventory-allocations-mobile.png'),fullPage:true});
            await page.setViewportSize({width:1440,height:1000});
            await page.locator('[data-ial-action="close"]').click();
        }
        role='viewer';await page.reload();await page.waitForFunction(()=>CURRENT_ROLE==='viewer');
        await page.getByRole('button',{name:'库存',exact:true}).click();await page.locator('[data-iw-allocation="1"]').click();
        await page.locator('#ial-rows tr').waitFor();check(await page.locator('[data-ial-edit]').count(),0);
        check(await page.getByText('当前账号只读，管理员和运营可维护归属。',{exact:true}).isVisible(),true);
        await page.locator('[data-ial-action="close"]').click();role='admin';await page.reload();await page.waitForFunction(()=>CURRENT_ROLE==='admin');
        salesRows = [{id: 1, product_id: 1, order_no: 'ORDER-COST', quantity: 2, sold_at: '2026-09-10T00:00:00',
            selling_price: '30', selling_price_cny: '30', sales_revenue_cny: '60', platform_fee_cny: '5',
            profit_cny: '35', total_cost: '20', order_total_cost_cny: '20', cost_status: 'unverified',
            purchase_cost_cny: '8', head_shipping_cost_cny: '4', last_mile_cost_cny: '2', other_cost_cny: '6', impairment_cny: '0.9'},
            {id: 2, product_id: 1, order_no: 'UNKNOWN-COST', quantity: 1, sold_at: '2026-09-11T00:00:00',
             selling_price: '30', selling_price_cny: '30', sales_revenue_cny: '30', platform_fee_cny: '0', profit_cny: '20', total_cost: '10',
             cost_breakdown_review_reason: '<script>unsafe</script>', impairment_review_reason: '<img onerror="unsafe">'},
            {id: -3, record_type: 'settlement_adjustment', quantity: 0, sold_at: '2026-09-12T00:00:00',
             selling_price_cny: '-3', sales_revenue_cny: '-3', platform_fee_cny: '0', profit_cny: '-3', total_cost: '0',
             order_total_cost_cny: '0', purchase_cost_cny: '0', head_shipping_cost_cny: '0', last_mile_cost_cny: '0',
             other_cost_cny: '0', impairment_cny: '0'}];
        for (const suffix of ['/', '/copy']) {
            await page.goto(address + suffix);
            await page.waitForFunction(() => CURRENT_USER_ID === 10);
            await page.getByRole('button', {name: '销售记录', exact: true}).click();
            await page.waitForFunction(() => document.querySelectorAll('#sales-tbody tr').length === 3);
            check(await page.locator('.sales-ledger-table thead th').count(), 16);
            check(await page.locator('#sales-tbody tr').first().locator('td').count(), 16);
            check(await page.locator('#sales-tbody tr').first().locator('td').allTextContents().then(v => v.slice(11)),
                ['¥8.00', '¥4.00', '¥2.00', '¥6.00', '¥0.90']);
            check(await page.locator('#sales-tbody tr').first().locator('td').nth(8).innerText(), '¥20.00');
            check(await page.locator('#sales-tbody tr').first().locator('td').nth(10).innerText(), '¥35.00');
            check(await page.locator('#sales-tbody tr').nth(1).locator('td').allTextContents().then(v => v.slice(11)),
                Array(5).fill('待核对'));
            check(await page.locator('#sales-total-summary-body td').allTextContents().then(v => v.slice(-5)), Array(5).fill('待核对'));
            check(await page.locator('#sales-tbody script, #sales-tbody img').count(), 0);
            check(await page.locator('.sales-cost-help').innerText().then(v => v.includes('不代表销售店铺承担额')), true);
            check(await page.locator('[data-sales-cost-text="salesCostTotal"]').innerText(), '订单总成本（元，本行）');
            check(await page.locator('.sales-ledger-scroll').evaluate(e => e.scrollWidth > e.clientWidth), true);
            await page.evaluate(() => {document.querySelector('.sales-ledger-scroll').scrollLeft = 9999;});
            await page.evaluate(() => {document.querySelector('.sales-ledger-scroll').scrollLeft = 0;});
            await page.locator('#sales-month').fill('2026-09');
            await page.locator('#sales-product-keyword').fill(' CW0001 ');
            await page.evaluate(() => {salesPage = 5;});
            const download = page.waitForEvent('download');
            await page.locator('#sales-export-button').click();
            check((await download).suggestedFilename(), 'sales_202609.xlsx');
            check(salesExportCalls.at(-1), {month: '2026-09', keyword: 'CW0001'});
            await page.waitForFunction(() => !salesExportBusy);
            check(await page.locator('#sales-month').isEnabled(), true);
            salesExportStatus = 404;
            await page.locator('#sales-export-button').click();
            await page.waitForFunction(() => document.getElementById('sales-export-status').textContent.includes('没有符合条件'));
            check(await page.locator('#sales-export-button').isEnabled(), true);
            salesExportStatus = 200;
        }
        for(const suffix of ['/', '/copy']) {
            countRecords=[];countCalls=[];
            await page.goto(address+suffix);await page.waitForFunction(()=>CURRENT_USER_ID===10);
            await page.locator('[data-inventory-nav]').click();
            await page.locator('[data-iw-action="assessment"]').click();
            await page.locator('[data-ia-edit="1"]').waitFor();
            check(await page.locator('#inventory-workspace-root').isVisible(),false);
            check(await page.locator('#ia-body > tr').count(),4);
            check(await page.locator('#ia-body > tr').first().locator(':scope > td').count(),14);
            check(await page.locator('#ia-body .tag-orange').getAttribute('title').then(value=>value.includes('逐批数量')),true);
            check(await page.locator('#ia-total td').nth(5).innerText(),'待核对');
            const countButton=await page.locator('[data-ia-edit="1"]').boundingBox();
            check(countButton.x+countButton.width<=1440,true);
            if(process.env.INVENTORY_QA_DIR&&suffix==='/')await page.screenshot({path:path.join(process.env.INVENTORY_QA_DIR,'inventory-assessment-desktop.png'),fullPage:true});
            await page.locator('[data-ia-batch="1"]').click();
            check(await page.locator('#ia-batch-body').innerText().then(v=>v.includes('<BATCH>')),true);
            if(process.env.INVENTORY_QA_DIR&&suffix==='/')await page.screenshot({path:path.join(process.env.INVENTORY_QA_DIR,'inventory-assessment-batches.png'),fullPage:true});
            await page.locator('[data-ia-action="close-batches"]').click();
            check(await page.locator('#ia-body script').count(),0);
            await page.locator('#ia-date').fill('2026-09-30');
            await page.locator('[data-ia-edit="1"]').click();
            check(await page.locator('#ia-edit').isVisible(),false);
            check(await page.locator('#ia-status').innerText().then(v=>v.includes('请先查询')),true);
            await page.locator('#ia-query button.btn-primary').click();
            await page.waitForFunction(()=>document.getElementById('ia-status').textContent==='');
            await page.locator('[data-ia-edit="1"]').click();
            await page.locator('#ia-save').click();
            if(process.env.INVENTORY_QA_DIR&&suffix==='/')await page.screenshot({path:path.join(process.env.INVENTORY_QA_DIR,'inventory-assessment-editor.png'),fullPage:true});
            check(countCalls.length,0);
            check(await page.locator('#ia-edit-status').innerText().then(v=>v.includes('整数')),true);
            await page.locator('#ia-actual').fill('0');await page.locator('#ia-note').fill('<script>unsafe</script>');
            await page.evaluate(()=>Object.defineProperty(globalThis.crypto,'randomUUID',{value:undefined,configurable:true}));
            const held=wait();countGate=held;
            await page.locator('#ia-save').click();await page.waitForFunction(()=>document.getElementById('ia-save').disabled);
            check(await page.locator('#ia-actual').isEditable(),false);
            check(await page.locator('#ia-note').isEditable(),false);
            check(await page.locator('[data-ia-action="cancel"]').isDisabled(),true);
            await page.evaluate(()=>document.getElementById('ia-save').click());
            check(countCalls.length,1);held.resolve();
            await page.locator('#ia-edit').waitFor({state:'hidden'});
            check(countRecords[0].actual_quantity,0);check(countRecords[0].difference,-7281);
            check(/^[A-Za-z0-9_-]{16,64}$/.test(countCalls[0].request_key),true);
            check(await page.locator('#ia-body > tr').first().locator(':scope > td').nth(10).innerText(),'0');
            await page.locator('[data-ia-history="1"]').click();
            await page.locator('#ia-history-body tr').waitFor();
            check(await page.locator('#ia-history-body').innerText().then(v=>v.includes('<script>unsafe</script>')),true);
            check(await page.locator('#ia-history-body script').count(),0);
            await page.locator('[data-ia-action="close-history"]').click();
            await page.locator('[data-ia-edit="1"]').click();await page.locator('#ia-actual').fill('5');countStatus=409;
            await page.locator('#ia-save').click();await page.waitForFunction(()=>document.getElementById('ia-edit-status').textContent.includes('已变化'));
            check(await page.locator('#ia-edit').isVisible(),true);check(await page.locator('#ia-actual').inputValue(),'5');
            check(await page.locator('#ia-actual').isEnabled(),true);
            check(await page.locator('#ia-note').isEnabled(),true);
            check(await page.locator('#ia-save').isEnabled(),true);
            await page.locator('[data-ia-action="cancel"]').click();countStatus=200;
            await page.locator('[data-ia-edit="1"]').click();
            await page.locator('#ia-actual').fill('6');await page.locator('#ia-note').fill('retry-note');countNetworkError=true;
            const recordsBeforeFailure=countRecords.length;
            await page.locator('#ia-save').click();
            await page.waitForFunction(()=>!document.getElementById('ia-save').disabled&&document.getElementById('ia-edit-status').textContent.includes('操作失败'));
            check(await page.locator('#ia-actual').inputValue(),'6');check(await page.locator('#ia-note').inputValue(),'retry-note');
            check(await page.locator('#ia-actual').isEnabled(),true);check(await page.locator('#ia-note').isEnabled(),true);
            check(countRecords.length,recordsBeforeFailure);
            const retryKey=countCalls.at(-1).request_key;countNetworkError=false;
            await page.locator('#ia-save').click();await page.locator('#ia-edit').waitFor({state:'hidden'});
            check(countCalls.at(-1).request_key,retryKey);
            check(countRecords.length,recordsBeforeFailure+1);check(countRecords.at(-1).actual_quantity,6);
            const downloaded=page.waitForEvent('download');await page.locator('[data-ia-action="export"]').click();
            check((await downloaded).suggestedFilename().startsWith('inventory_assessment_'),true);
            await page.waitForFunction(()=>document.getElementById('ia-status').textContent.includes('导出已生成'));
            // 同账号重登也须忽略旧盘点POST的401，不能退出新会话。
            await page.locator('[data-ia-edit="1"]').click();
            const old=wait();countGate=old;countStatus=401;
            const oldRequest=page.waitForRequest(r=>new URL(r.url()).pathname==='/inventory/stock-counts'&&r.method()==='POST');
            await page.locator('#ia-save').click();await oldRequest;
            const oldCancelled=page.waitForEvent('requestfailed',{predicate:r=>new URL(r.url()).pathname==='/inventory/stock-counts'});
            await page.evaluate(async()=>{handleSessionExpired();await loadCurrentUser();});
            await oldCancelled;old.resolve();countStatus=200;
            await page.waitForFunction(()=>CURRENT_USER_ID===10);
            check(await page.locator('#inventory-assessment-root').innerHTML(),'');
            check(await page.locator('#login-overlay').isVisible(),false);
            await page.locator('[data-inventory-nav]').click();await page.locator('[data-iw-action="assessment"]').click();
            await page.locator('[data-ia-edit="1"]').waitFor();
            check(await page.locator('#ia-actual').isEnabled(),true);
            check(await page.locator('#ia-note').isEnabled(),true);
            await page.locator('[data-ia-action="overview"]').click();
            check(await page.locator('#inventory-assessment-root').isVisible(),false);
            check(await page.locator('#inventory-workspace-root').isVisible(),true);
        }
        role = 'viewer';
        await page.evaluate(async () => {handleSessionExpired(); await loadCurrentUser();});
        await page.locator('[data-inventory-nav]').click();
        await page.locator('[data-iw-action="daily"]').click();
        await page.locator('#iw-tbody tr').first().waitFor();
        check(await page.locator('#iw-scope').innerText().then(value => value.includes('仅含绑定店铺')), true);
        check(await page.locator('#iw-tbody tr').first().locator('td').nth(1).innerText(), '—');
        check(await page.locator('#iw-tbody [data-iw-move="adjustment"]').count(), 0);
        await downloadExport();
        exportStatus = 401;
        await page.locator('#iw-export').click();
        await page.waitForFunction(() => CURRENT_USER_ID === 0);
        check(await page.locator('#inventory-workspace-root').innerHTML(), '');
        check(await page.locator('#login-overlay').isVisible(), true);
        check(pageErrors, []);
        console.log(JSON.stringify({page_copies: 2, checks, browser: 'headless Chromium', scope: 'real DOM with local fixture APIs; no business database'}));
    } finally {
        if (browser) await browser.close();
        await new Promise(resolve => server.close(resolve));
    }
}
main().catch(error => {console.error(error); process.exitCode = 1;});
