// 两份真实图册页面 + 本地模拟API；不启动main、不连接业务库或外部网站。
const assert = require('node:assert/strict');
const fs = require('node:fs');
const http = require('node:http');
const path = require('node:path');
const {chromium} = require(process.env.INVENTORY_PLAYWRIGHT_MODULE || 'playwright');
let checks = 0;
const check = (actual, expected) => {assert.deepEqual(actual, expected); checks++;};
const deferred = () => {let resolve; return {promise: new Promise(done => {resolve = done;}), resolve: value => resolve(value)};};
const product = (id, sku, name) => ({id, sku, name, stock_quantity: 100, stock_value: 1000,
    product_type: 'stable', safe_stock_quantity: 0, image: '/uploads/gallery-fixture.png'});
const catalog = [product(1, 'CW000207', '完整SKU商品'),
    ...Array.from({length: 25}, (_, i) => product(100 + i, `CW000207-${i + 1}`, `相似商品${i + 1}`)),
    ...Array.from({length: 30}, (_, i) => product(200 + i, `DOG-${i + 1}`, `狗狗项圈${i + 1}`)),
    product(999, 'A&B /?+%_#', '<img src=x onerror="window.galleryInjected=true">')];
function products(url) {
    const keyword = (url.searchParams.get('keyword') || '').toLowerCase();
    const items = catalog.filter(p => `${p.sku} ${p.name}`.toLowerCase().includes(keyword)).sort((a, b) => {
        if (url.searchParams.get('prioritize_exact_sku') === 'true') {
            const exact = Number(b.sku.toLowerCase() === keyword) - Number(a.sku.toLowerCase() === keyword);
            if (exact) return exact;
        }
        return b.id - a.id;
    });
    const page_size = Number(url.searchParams.get('page_size') || 20);
    const page = Math.min(Number(url.searchParams.get('page') || 1), Math.max(1, Math.ceil(items.length / page_size)));
    return {items: items.slice((page - 1) * page_size, page * page_size), page, page_size, total: items.length};
}

async function main() {
    const staticFiles = Object.fromEntries(['page-interactions', 'product-search', 'sales-ledger', 'inventory-workspace',
        'inventory-assessment', 'inventory-allocations', 'financial-control'].map(name => [`/${name}.js`, `static/${name}.js`]));
    const files = {'/': 'templates/index.html', '/copy': 'index.html', ...staticFiles};
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
        const calls = [], errors = [];
        let gate = null;
        page.on('pageerror', error => errors.push(error.message));
        await page.route('**/*', async route => {
            const url = new URL(route.request().url());
            if (url.hostname !== '127.0.0.1') return route.abort();
            const json = (data, status = 200) => route.fulfill({status, contentType: 'application/json', body: JSON.stringify(data)});
            if (url.pathname === '/auth/config') return json({local_enabled: true, sso_available: false});
            if (url.pathname === '/auth/me') return json({id: 10, username: '图册测试', role: 'admin', store_id: null, csrf_token: 'test-only'});
            if (url.pathname === '/products') {
                calls.push(Object.fromEntries(url.searchParams));
                const keyword = url.searchParams.get('keyword');
                if (keyword === 'slow' && gate) {
                    const pending = gate; pending.started.resolve(); await pending.promise;
                    return pending.expired ? json({detail: 'old session'}, 401) : json(products(new URL('http://unit.invalid/products?keyword=狗狗')));
                }
                if (keyword === 'error') return json({detail: '<b>模拟加载失败</b>'}, 500);
                if (keyword === 'expired') return json({detail: 'expired'}, 401);
                return json(products(url));
            }
            if (url.pathname === '/products/options') return json(catalog);
            if (url.pathname === '/sales-fields.zh-CN.json') return route.fulfill({contentType: 'application/json', body: fs.readFileSync('static/sales-fields.zh-CN.json', 'utf8')});
            if (url.pathname === '/uploads/gallery-fixture.png') return route.fulfill({contentType: 'image/png', body: Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jQ1sAAAAASUVORK5CYII=', 'base64')});
            if (files[url.pathname]) return route.continue();
            return json([]);
        });
        const address = `http://127.0.0.1:${server.address().port}`;
        for (const suffix of ['/', '/copy']) {
            await page.goto(address + suffix);
            await page.waitForFunction(() => CURRENT_USER_ID === 10);
            await page.getByRole('button', {name: '产品图册', exact: true}).click();
            const grid = page.locator('#gallery-grid'), input = page.locator('#gallery-keyword');
            const ready = () => page.waitForFunction(() => {
                const status = document.getElementById('gallery-status').textContent;
                return status.includes('图册加载失败') || status === '' && document.getElementById('gallery-pagination').textContent.includes('共');
            });
            const query = async value => {await input.fill(value); await input.press('Enter'); await ready();};
            await ready();
            check(await grid.locator(':scope > div').count(), 20);
            check(await page.locator('#gallery-query').getByRole('button').allTextContents(), ['查询', '刷新', '清空']);
            check(await page.locator('#gallery-grid [onerror]').count(), 0);
            await query('  狗狗  ');
            check(await input.inputValue(), '狗狗');
            check(await grid.locator(':scope > div').count(), 20);
            check(await page.locator('#gallery-pagination').textContent().then(value => value.includes('共 30 条')), true);
            await page.locator('#gallery-pagination').getByRole('button', {name: '下一页', exact: true}).click(); await ready();
            check([calls.at(-1).keyword, calls.at(-1).page], ['狗狗', '2']);
            check(await grid.locator(':scope > div').count(), 10);
            await page.locator('#gallery-pagination select').selectOption('10'); await ready();
            check([calls.at(-1).keyword, calls.at(-1).page, calls.at(-1).page_size], ['狗狗', '1', '10']);
            await page.locator('#gallery-pagination').getByRole('button', {name: '下一页', exact: true}).click(); await ready();
            await page.locator('#gallery-query').getByRole('button', {name: '刷新', exact: true}).click(); await ready();
            check([calls.at(-1).keyword, calls.at(-1).page], ['狗狗', '2']);
            await input.fill('CW000207');
            await page.locator('#gallery-options [role="option"]').first().waitFor();
            check(await page.locator('#gallery-options strong').first().textContent(), 'CW000207');
            await input.press('ArrowDown'); await input.press('Enter'); await ready();
            check(await grid.locator(':scope > div').first().textContent().then(value => value.includes('完整SKU商品')), true);
            check(calls.at(-1).prioritize_exact_sku, 'true');
            check(await input.getAttribute('aria-expanded'), 'false');
            await input.fill('CW000207-3');
            await page.locator('#gallery-options [role="option"]').first().waitFor();
            await page.locator('#gallery-options [role="option"]').first().click(); await ready();
            check(await input.inputValue(), 'CW000207-3');
            check(await grid.locator(':scope > div').count(), 1);
            check(await grid.locator('a[download]').getAttribute('href'), '/uploads/gallery-fixture.png');
            await query('A&B /?+%_#');
            check(calls.at(-1).keyword, 'A&B /?+%_#');
            check(await grid.locator('[onerror]').count(), 0);
            check(await page.evaluate(() => !!window.galleryInjected), false);
            await query('不存在的产品');
            check(await grid.textContent().then(value => value.includes('没有匹配的产品')), true);
            await query('error');
            await page.locator('#gallery-status').filter({hasText: '图册加载失败'}).waitFor();
            check(await page.locator('#gallery-status b').count(), 0);
            await page.locator('#gallery-query').getByRole('button', {name: '清空', exact: true}).click(); await ready();
            check(await input.inputValue(), '');
            check(calls.at(-1).keyword, undefined);
            check(calls.at(-1).page, '1');
            gate = {...deferred(), started: deferred()};
            await input.fill('slow'); await input.press('Enter'); await gate.started.promise;
            await query('CW000207-3');
            gate.resolve(); await page.waitForLoadState('networkidle'); gate = null;
            check(await grid.locator(':scope > div').count(), 1);
            check(await grid.textContent().then(value => value.includes('CW000207-3')), true);
            gate = {...deferred(), started: deferred(), expired: true};
            await input.fill('slow'); await input.press('Enter'); await gate.started.promise;
            await page.evaluate(async () => {handleSessionExpired(); await loadCurrentUser();});
            check(await input.inputValue(), '');
            gate.resolve(); await page.waitForLoadState('networkidle'); gate = null;
            check(await page.evaluate(() => CURRENT_USER_ID), 10);
            check(await grid.textContent(), '');
            await page.getByRole('button', {name: '产品图册', exact: true}).click(); await ready();
            await page.setViewportSize({width: 390, height: 844});
            await input.fill('狗狗'); await page.locator('#gallery-options [role="option"]').first().waitFor();
            check(await page.locator('#gallery-popup').evaluate(node => {const box = node.getBoundingClientRect(); return box.left >= 0 && box.right <= innerWidth;}), true);
            check(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true);
            if (process.env.INVENTORY_QA_DIR) {fs.mkdirSync(process.env.INVENTORY_QA_DIR, {recursive: true}); await page.screenshot({path: path.join(process.env.INVENTORY_QA_DIR, `gallery-search-${suffix === '/' ? 'template' : 'copy'}-mobile.png`)});}
            await input.press('Escape'); await page.setViewportSize({width: 1440, height: 1000});
            await query('CW000207-3');
            if (process.env.INVENTORY_QA_DIR) await page.screenshot({path: path.join(process.env.INVENTORY_QA_DIR, `gallery-search-${suffix === '/' ? 'template' : 'copy'}-desktop.png`), fullPage: true});
            await input.fill('expired'); await input.press('Enter');
            await page.waitForFunction(() => CURRENT_USER_ID === 0);
            check(await grid.textContent(), '');
            check(await page.locator('#gallery-status').textContent(), '');
        }
        check(errors, []);
        console.log(JSON.stringify({page_copies: 2, checks, scope: 'real gallery DOM, fixture APIs, search/pagination/races/session reset/mobile; no business database'}));
    } finally {if (browser) await browser.close(); await new Promise(resolve => server.close(resolve));}
}
main().catch(error => {console.error(error); process.exitCode = 1;});
