// Actual pages and rendering modules with local fixtures; no application startup or business database.
const fs = require('node:fs'), path = require('node:path'), http = require('node:http');
const assert = require('node:assert/strict');
const {chromium} = require(process.env.INVENTORY_PLAYWRIGHT_MODULE || 'playwright');
let checks = 0, textSamples = 0;
const check = (actual, expected, message) => { assert.deepEqual(actual, expected, message); checks++; };
const product = {id:1, product_id:1, sku:'CW000001', name:'仿真发声小鸟', specification:'蓝色', product_type:'stable',
    safe_stock_quantity:10, image:null, quantity:100, stock_quantity:100, stock_value:500, inventory_amount:500, stock_status:'normal'};
const store = {id:7, name:'Citrijoy Mascotas', settlement_currency:'MXN', is_active:true};
const batch = {id:1, product_id:1, product_name:product.name, product_sku:product.sku, batch_no:'20260901-001',
    quantity:100, remaining_quantity:100, purchase_price:5, total_amount:500, unit_cost:5, shipping_cost:0,
    last_mile_cost:0, other_cost:0, arrived_at:'2026-09-01T00:00:00'};
const sale = {id:1, product_id:1, store_id:7, store_name:store.name, order_no:'ORDER-001', display_order_no:'ORDER-001',
    display_sku:product.sku, display_name:product.name, local_sku:product.sku, local_product_name:product.name,
    source_specification:'蓝色', local_specification:'蓝色', transaction_type:'订单', sold_at:'2026-09-10T00:00:00',
    quantity:2, source_quantity:2, currency:'MXN', exchange_rate_to_cny:'.3711', exchange_rate_date:'2026-09-30',
    exchange_rate_source:'manual', source_row_net_product_sales:'100', source_row_settlement_total:'90',
    allocated_net_product_sales:'100', allocated_settlement_total:'90', selling_price_cny:'18.555',
    sales_revenue_cny:'37.11', platform_fee_cny:'3.711', profit_cny:'19.399', total_cost:'14', order_total_cost_cny:'14',
    purchase_cost_cny:'10', head_shipping_cost_cny:'4', last_mile_cost_cny:'0', other_cost_cny:'0', impairment_cny:'0',
    unit_purchase_cost_cny:'5', unit_head_shipping_cost_cny:'2', unit_last_mile_cost_cny:'0', unit_other_cost_cny:'0', unit_total_cost_cny:'7'};
const report = {report_month:'2026-09-01', period_label:'2026年9月', store_id:7, store_name:store.name,
    can_edit:true, supports_manual_fx:true, calculation_policy:'order_created_fx_mxn8_income_only_v3',
    updated_at:'2026-11-25T11:34:50', warnings:['上月、本年累计存在历史流水异常，相关金额及利润待核对，未以0代替。'], rows:[],
    exchange_rates:[{report_month:'2026-09', currency:'MXN', rate_to_cny:'.3711', source:'manual', mode:'manual'}],
    exchange_rate_settings:[{currency:'MXN', mode:'manual', manual_rate:'.3711', revision:1,
        updated_at:'2026-11-25T03:34:50Z', updated_by:'admin', note:'月度核算汇率'}]};

async function checkBatchFieldPicker(page, label) {
    await page.locator('#batch-field-picker-button').click();
    const menu = page.locator('#batch-field-picker-menu');
    const bounds = await menu.evaluate(el => {
        const r = el.getBoundingClientRect();
        return {withinViewport:r.left >= 0 && r.right <= innerWidth, horizontalOverflow:el.scrollWidth > el.clientWidth + 1};
    });
    check(bounds, {withinViewport:true, horizontalOverflow:false}, label+' field menu stays inside viewport');
    const overflow = await menu.evaluate(el => [...el.querySelectorAll('label')].flatMap(item => {
        const box = item.getBoundingClientRect(), range = document.createRange();
        return [...item.childNodes].filter(n => n.nodeType === Node.TEXT_NODE && n.textContent.trim()).flatMap(node => {
            range.selectNodeContents(node);
            return [...range.getClientRects()].some(r => r.left < box.left - 1 || r.right > box.right + 1)
                ? [item.textContent.trim()] : [];
        });
    }));
    check(overflow, [], label+' field labels stay in their own columns');
    const target = menu.locator('[data-batch-field-toggle="last_mile_cost_no"]');
    const neighbor = menu.locator('[data-batch-field-toggle="other_cost"]');
    const targetLabel = menu.locator('label:has([data-batch-field-toggle="last_mile_cost_no"])');
    await targetLabel.scrollIntoViewIfNeeded();
    // Click the final character, where the old layout hit the adjacent checkbox.
    const tail = await targetLabel.evaluate(el => {
        const node = [...el.childNodes].find(n => n.nodeType === Node.TEXT_NODE && n.textContent.trim());
        const end = node.textContent.trimEnd().length, range = document.createRange();
        range.setStart(node,end - 1); range.setEnd(node,end);
        const r = range.getBoundingClientRect(), x = r.x + r.width / 2, y = r.y + r.height / 2;
        return {x,y,hitOwnLabel:el.contains(document.elementFromPoint(x,y))};
    });
    check(tail.hitOwnLabel, true, label+' long field label has the correct hit target');
    const originallyChecked = await target.isChecked(), neighborChecked = await neighbor.isChecked();
    await page.mouse.click(tail.x,tail.y);
    check(await target.isChecked(), !originallyChecked, label+' clicking label toggles its own field');
    check(await neighbor.isChecked(), neighborChecked, label+' adjacent field is unchanged');
    check(await page.locator('th[data-batch-column="last_mile_cost_no"]').isVisible(), !originallyChecked, label+' correct table column toggles');
    await target.focus(); await page.keyboard.press('Space');
    check(await target.isChecked(), originallyChecked, label+' keyboard restores the field');
    await page.locator('#batch-field-picker-button').click();
}

async function checkSalesHeaders(page, label) {
    await page.locator('#sales-tbody [data-sales-value="selling_price_cny"]').waitFor();
    await page.locator('#sales-visible-toggle').click();
    await page.locator('#sales-visible-all').click();
    await page.locator('#sales-visible-toggle').click();
    await page.locator('#sales-pin-toggle').click();
    await page.locator('#sales-pin-clear').click();
    // A long heading must also fit when the mobile pinned-column budget narrows it.
    await page.locator('[data-sales-pin-key="selling_price_cny"]').check();
    await page.locator('#sales-pin-toggle').click();
    for (const offset of [0,1800]) {
        await page.locator('#sales-ledger-scroll').evaluate(async (el,offset) => {
            el.scrollLeft = offset;
            await new Promise(requestAnimationFrame);
        },offset);
        const layout = await page.locator('#sales-ledger-table').evaluate(table => {
            const outside = [], misaligned = [];
            const rectsIntersect = (a,b) => Math.min(a.right,b.right) - Math.max(a.left,b.left) > 1 && Math.min(a.bottom,b.bottom) - Math.max(a.top,b.top) > 1;
            for (const th of table.querySelectorAll('th[data-sales-field]')) {
                const key = th.dataset.salesField, box = th.getBoundingClientRect(), anchor = th.querySelector('span');
                const button = anchor.querySelector('.help-tip-trigger'), trigger = button.getBoundingClientRect();
                const fits = r => r.left >= box.left - 1 && r.right <= box.right + 1 && r.top >= box.top - 1 && r.bottom <= box.bottom + 1;
                const range = document.createRange();
                const textRects = [...anchor.childNodes].filter(n => n.nodeType === Node.TEXT_NODE && n.textContent.trim()).flatMap(node => {
                    range.selectNodeContents(node); return [...range.getClientRects()];
                });
                if (!fits(trigger) || textRects.some(r => !fits(r) || rectsIntersect(r,trigger))) outside.push(key);
                for (const selector of [`#sales-tbody [data-sales-value="${key}"]`,`[data-sales-total="${key}"]`]) {
                    const r = document.querySelector(selector).getBoundingClientRect();
                    if (Math.abs(r.left - box.left) > 1 || Math.abs(r.width - box.width) > 1) misaligned.push(key);
                }
            }
            return {outside,misaligned};
        });
        check(layout.outside, [], label+' sales headings and help buttons stay in their own cells at '+offset);
        check(layout.misaligned, [], label+' sales headers, rows and totals align at '+offset);
    }
    const help = page.locator('[data-sales-field="selling_price_cny"] .help-tip-trigger');
    await help.click();
    const helpId = await help.getAttribute('aria-describedby');
    check(await page.locator('#'+helpId).isVisible(), true, label+' wrapped heading help still opens');
    await page.keyboard.press('Escape');
    check(await page.locator('#'+helpId).isVisible(), false, label+' heading help closes with Escape');
}

async function main() {
    const files = {'/':'templates/index.html', '/copy':'index.html', '/sales-fields.json':'static/sales-fields.zh-CN.json'};
    for (const file of fs.readdirSync('static')) if (/\.(js|json)$/.test(file)) files['/'+file] = 'static/'+file;
    const server = http.createServer((req, res) => {
        const file = files[new URL(req.url, 'http://fixture').pathname];
        if (!file) {res.writeHead(404); res.end(); return;}
        res.setHeader('Content-Type', file.endsWith('.js') ? 'text/javascript; charset=utf-8' : file.endsWith('.json') ? 'application/json' : 'text/html; charset=utf-8');
        res.end(fs.readFileSync(file));
    });
    await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
    let browser;
    try {
        const launch = {headless:true};
        if (process.env.INVENTORY_BROWSER_CHANNEL) launch.channel = process.env.INVENTORY_BROWSER_CHANNEL;
        else if (process.env.FIFO_TEST_CHROME) launch.executablePath = process.env.FIFO_TEST_CHROME;
        else if (fs.existsSync('C:/Program Files/Google/Chrome/Application/chrome.exe')) launch.executablePath = 'C:/Program Files/Google/Chrome/Application/chrome.exe';
        browser = await chromium.launch(launch);
        const page = await browser.newPage(), errors = [], writes = [];
        page.on('pageerror', error => errors.push(error.message));
        await page.route('**/*', async route => {
            const req = route.request(), url = new URL(req.url()), p = url.pathname;
            if (req.method() !== 'GET') writes.push(req.method()+' '+p);
            if (files[p]) return route.continue();
            const ok = data => route.fulfill({status:200, contentType:'application/json', body:JSON.stringify(data)});
            const paged = items => ({items, total:items.length, page:1, page_size:20});
            if (p === '/auth/config') return ok({local_enabled:true, local_login_enabled:true, sso_available:false});
            if (p === '/auth/me') return ok({id:10, username:'admin', role:'admin', csrf_token:'fixture-only'});
            if (p === '/products/options') return ok([product]);
            if (p === '/products') return ok(paged([product]));
            if (p === '/batches') return ok(paged([batch]));
            if (p === '/batches/1') return ok(batch);
            if (p === '/finance-masters/stores') return ok([store]);
            if (p === '/users' || p === '/auth/users') return ok([
                {id:10, username:'admin', role:'admin', is_active:true},
                {id:11, username:'运营账号', role:'operator', store_id:7, is_active:true}]);
            if (p === '/inventory/overview') return ok({...paged([product]), scope:'shared',
                summary:{quantity:100, inventory_amount:500, low_count:0, zero_count:0}, warnings:[]});
            if (p === '/sales') return ok(paged([sale]));
            if (p === '/sales/pending-confirmations') return ok(paged([]));
            if (p === '/sales/monthly-summary') return ok({month:'2026-09', items:[], total_quantity:0});
            if (p === '/reports/store-profit-loss') return ok(report);
            if (p === '/operation-logs/options') return ok({sources:['batch'], stores:[store], audit_enabled:true, audit_available:true});
            if (p === '/operation-logs') return ok(paged([{id:1, source:'batch', action:'edit', recorded_at:'2026-11-25T11:34:50',
                time_basis:'original', actor_id:10, actor_name:'admin', reference:batch.batch_no, related_id:1,
                store_id:7, store_name:store.name, summary:{changed_fields:['quantity']}}]));
            return ok([]);
        });
        const inspect = async (selector, label) => {
            const result = await page.locator(selector).evaluate(root => {
                const small = [], buttons = [], seen = new Set();
                const visible = el => el.getClientRects().length && getComputedStyle(el).visibility !== 'hidden';
                const inspectElement = el => {
                    if (!visible(el) || seen.has(el)) return;
                    seen.add(el);
                    const size = parseFloat(getComputedStyle(el).fontSize);
                    if (size < 14) small.push({tag:el.tagName, id:el.id, size, text:el.textContent.trim().slice(0,45)});
                };
                const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT);
                while (walker.nextNode()) if (walker.currentNode.textContent.trim()) inspectElement(walker.currentNode.parentElement);
                root.querySelectorAll('input,select,textarea,button').forEach(inspectElement);
                root.querySelectorAll('button').forEach(el => {
                    if (visible(el) && el.textContent.trim() && el.scrollWidth > el.clientWidth + 2)
                        buttons.push({text:el.textContent, width:el.clientWidth, content:el.scrollWidth});
                });
                return {small, buttons, samples:seen.size, pageOverflow:document.documentElement.scrollWidth > innerWidth + 1};
            });
            check(result.small, [], label+' readable font size');
            check(result.buttons, [], label+' unclipped button labels');
            check(result.pageOverflow, false, label+' viewport overflow');
            assert(result.samples > 0, label+' must contain rendered text'); textSamples += result.samples;
        };
        const screenshot = async name => {
            if (!process.env.INVENTORY_QA_DIR) return;
            fs.mkdirSync(process.env.INVENTORY_QA_DIR, {recursive:true});
            await page.screenshot({path:path.join(process.env.INVENTORY_QA_DIR,name+'.png'), fullPage:true});
        };
        for (const [suffix, copy] of [['/','template'],['/copy','copy']]) {
            for (const width of [1440,390]) {
                await page.setViewportSize({width,height:1000});
                await page.goto(`http://127.0.0.1:${server.address().port}${suffix}`);
                await page.waitForFunction(() => CURRENT_USER_ID === 10 && currentProductList.length === 1);
                await inspect('.header', copy+' header '+width);
                for (const section of ['products','inventory','batches','sales','reports','operation-logs','users']) {
                    await page.locator(`[data-main-tab="${section}"]`).click();
                    if (section === 'inventory') await page.locator('#iw-tbody tr').first().waitFor();
                    if (section === 'batches') {
                        const productCell = page.locator('#batches-tbody [data-batch-column="product_name"]').first();
                        await productCell.scrollIntoViewIfNeeded();
                        check(await productCell.evaluate(el => {
                            const r = el.getBoundingClientRect(), box = el.closest('.table-scroll').getBoundingClientRect();
                            const x = (Math.max(r.left, box.left) + Math.min(r.right, box.right)) / 2;
                            return el.contains(document.elementFromPoint(x, r.top + r.height / 2));
                        }), true, 'batch product remains readable beside action column');
                    }
                    if (section === 'operation-logs') await page.locator('#ol-body tr').first().waitFor();
                    if (section === 'users') await page.locator('#users-tbody tr').first().waitFor();
                    if (section === 'reports') {
                        await page.evaluate(() => {document.getElementById('profit-loss-month').value='2026-09';});
                        await page.locator('[data-report-view="profit-loss"]').click();
                        await page.locator('.report-fx-panel summary').waitFor();
                        await page.locator('.report-fx-panel summary').click();
                        check(await page.locator('#profit-loss-warnings').isVisible(), true, 'financial warning stays inline');
                        const hint = await page.locator('.report-fx-panel p').first().evaluate(el => ({size:parseFloat(getComputedStyle(el).fontSize), line:parseFloat(getComputedStyle(el).lineHeight), color:getComputedStyle(el).color}));
                        check(hint.line >= hint.size * 1.5, true, 'multiline report help spacing');
                        check(hint.color, 'rgb(82, 96, 116)', 'secondary report text contrast');
                    }
                    await inspect('#sec-'+section, copy+' '+section+' '+width);
                    if (copy === 'template') await screenshot(`typography-${section}-${width}`);
                }
                await page.locator('[data-main-tab="products"]').click();
                await page.evaluate(() => openNewProduct());
                await inspect('#product-editor', copy+' editor '+width);
                await page.locator('#product-editor .help-tip-trigger').first().click();
                await inspect('.help-tip-source:popover-open', copy+' help '+width);
                await page.keyboard.press('Escape');
                await page.evaluate(() => closeEditProduct());
                await page.evaluate(() => {
                    handleSessionExpired();
                    document.getElementById('eims-login-hint').textContent='请使用授权账号登录；如无法登录，请联系管理员。';
                });
                await inspect('#login-overlay', copy+' login '+width);
                if (copy === 'template') await screenshot(`typography-login-${width}`);
            }
        }
        for (const [suffix, copy] of [['/','template'],['/copy','copy']]) {
            for (const width of [1440,600,601,390,320]) {
                await page.setViewportSize({width,height:1000});
                await page.goto(`http://127.0.0.1:${server.address().port}${suffix}`);
                await page.waitForFunction(() => CURRENT_USER_ID === 10 && currentProductList.length === 1);
                await page.locator('[data-main-tab="batches"]').click();
                await page.locator('#batches-tbody tr').first().waitFor();
                await checkBatchFieldPicker(page, copy+' '+width);
                if (copy === 'template') {
                    await page.locator('#batch-field-picker-button').click();
                    await screenshot('typography-fields-'+width);
                    await page.locator('#batch-field-picker-button').click();
                }
                await page.locator('[data-main-tab="sales"]').click();
                await checkSalesHeaders(page, copy+' '+width);
                if (copy === 'template') await screenshot('typography-sales-headers-'+width);
            }
        }
        check(errors, [], 'browser errors'); check(writes, [], 'read-only fixture interactions');
        console.log(JSON.stringify({pages:2, widths:[1440,390], checks, textSamples,
            layoutWidths:[1440,600,601,390,320], scope:'seven workspaces, dialogs, field-menu hit targets, sales header containment/alignment and help; local fixtures only'}));
    } finally {
        if (browser) await browser.close();
        await new Promise(resolve => server.close(resolve));
    }
}
main().catch(error => {console.error(error); process.exitCode=1;});
