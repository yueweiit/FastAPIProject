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
        '/inventory-workspace.js': 'static/inventory-workspace.js'};
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
        await page.setViewportSize({width: 1440, height: 1000});
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
