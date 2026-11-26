// 浏览器执行两份页面的店铺筛选和对账下载函数，数据使用隔离响应。
const assert = require('node:assert/strict');
const fs = require('node:fs');
const {chromium} = require('playwright');

const functions = [
    'authHeaders', 'formatApiError', 'escapeHtml', 'loadSales', 'loadSalesRecordStores',
    'salesLedgerFilterParams', 'searchSales', 'clearSalesSearch', 'setSalesExportStatus',
    'setSalesExportBusy', 'resetSalesExportSession', 'exportSalesRecords',
];

async function verify(browser, file) {
    const page = await browser.newPage({acceptDownloads: true});
    try {
        const html = fs.readFileSync(file, 'utf8');
        await page.setContent(html.replace(/<script\b[^>]*>[\s\S]*?<\/script>/gi, ''));
        await page.addScriptTag({path: 'static/page-interactions.js'});
        const code = functions.map(name => {
            const match = html.match(new RegExp('^(?:async )?function ' + name + '\\([^\\n]*\\) \\{[\\s\\S]*?^\\}', 'm'));
            assert(match, name);
            return match[0];
        }).join('\n');
        await page.addScriptTag({content: code});
        await page.evaluate(() => {
            document.getElementById('login-overlay').style.display = 'none';
            document.getElementById('sec-sales').classList.add('active');
            document.getElementById('sales-month').value = '2026-10';
            Object.assign(window, {ADMIN: true, AUTH_CSRF: '', CURRENT_USER_ID: 1, allProducts: [],
                salesLedgerStoreRequestId: 0, salesExportBusy: false, salesExportSessionEpoch: 0,
                salesExportController: null, salesMonth: '2026-10', salesPage: 3, salesPageSize: 20,
                salesProductKeyword: '', salesTotal: 0, reads: [], downloads: [], exportStoreAck: '7', blobReads: 0});
            window.isAdmin = () => ADMIN;
            window.isOperator = () => false;
            window.renderMonthlySalesSummary = () => {};
            window.renderPagination = () => {};
            window.loadProductOptions = async () => {};
            window.loadPendingConfirmations = async () => {};
            window.SalesLedger = {beginLoad: () => 1, currentLoad: () => true, ready: async () => {},
                render: () => {}, fail: error => {throw error;}};
            window.api = async (_method, url) => {
                reads.push(url);
                if (url === '/finance-masters/stores') {
                    return [{id: 7, name: 'Lemos'}, {id: 8, name: '旧店 <img src=x>', is_active: false}];
                }
                if (url.startsWith('/sales/monthly-summary?')) return [];
                return {items: [], total: 0, page: salesPage, page_size: 20};
            };
            window.fetch = async (url, options) => {
                downloads.push(url);
                window.lastDownloadSignal = options.signal;
                const headers = {
                    'Content-Type': 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
                    'X-Sales-Impairment-Policy': 'sale_month_elapsed_v1',
                };
                if (url.startsWith('/sales/export?') && exportStoreAck !== null) {
                    headers['X-Sales-Export-Store-Id'] = exportStoreAck;
                }
                const response = new Response(new Blob(['fixture']), {headers});
                const readBlob = response.blob.bind(response);
                response.blob = () => {blobReads++; return readBlob();};
                return response;
            };
        });

        const filter = page.locator('#sales-record-store');
        assert.equal(await filter.isVisible(), false);
        await page.evaluate(() => loadSalesRecordStores());
        assert.equal(await filter.isVisible(), true);
        assert.deepEqual(await filter.locator('option').allTextContents(), ['全部店铺', 'Lemos', '旧店 <img src=x>']);
        assert.equal(await page.locator('#sales-record-store img').count(), 0);
        await filter.selectOption('7');
        await page.waitForFunction(() => reads.some(url => url.startsWith('/sales/monthly-summary?')));
        const reads = await page.evaluate(() => window.reads);
        const currentQuery = path => Object.fromEntries(new URL(reads.find(url => url.startsWith(path)), 'http://fixture').searchParams);
        assert.deepEqual(currentQuery('/sales?'), {store_id: '7', month: '2026-10', page: '1', page_size: '20'});
        assert.deepEqual(currentQuery('/sales/monthly-summary?'), {store_id: '7', month: '2026-10'});

        const waiting = page.waitForEvent('download');
        await page.locator('#sales-export-button').click();
        assert.equal((await waiting).suggestedFilename(), 'sales_store_7_202610.xlsx');
        const url = await page.evaluate(() => downloads[0]);
        assert.deepEqual(Object.fromEntries(new URL(url, 'http://fixture').searchParams), {store_id: '7', month: '2026-10'});

        const batchDownload = page.waitForEvent('download');
        await page.evaluate(() => exportSalesRecords(42));
        assert.equal((await batchDownload).suggestedFilename(), 'sales_import_42.xlsx');
        assert.equal(await page.evaluate(() => downloads[1]), '/sales/imports/42/export');

        for (const acknowledgedStore of [null, '8']) {
            const before = await page.evaluate(() => blobReads);
            await page.evaluate(async store => {
                window.exportStoreAck = store;
                await exportSalesRecords();
            }, acknowledgedStore);
            assert.equal(await page.evaluate(() => blobReads), before);
            assert.equal(await page.evaluate(() => lastDownloadSignal.aborted), true);
            assert.equal(await page.locator('#sales-export-status').innerText(),
                await page.evaluate(() => window.PageInteractions.text('salesExportStoreMismatch')));
            assert.equal(await page.locator('#sales-export-button').isEnabled(), true);
            assert.equal(await filter.isEnabled(), true);
        }

        await page.locator('#sales-clear-button').click();
        assert.equal(await filter.inputValue(), '');
        await page.evaluate(async () => {
            // 模拟管理员请求未返回，随后会话切换至绑定店铺的账号。
            window.api = () => new Promise(resolve => {window.finishStoreRequest = resolve;});
            const pending = loadSalesRecordStores();
            resetSalesExportSession();
            window.ADMIN = false;
            window.CURRENT_USER_ID = 2;
            finishStoreRequest([{id: 8, name: '过期数据'}]);
            await pending;
            await loadSalesRecordStores();
        });
        assert.equal(await filter.isVisible(), false);
        assert.equal(await filter.locator('option').count(), 1);
        assert.equal(await page.evaluate(() => salesLedgerFilterParams('2026-10', '').has('store_id')), false);
    } finally {
        await page.close();
    }
}

(async () => {
    const browser = await chromium.launch();
    try {
        for (const file of ['templates/index.html', 'index.html']) await verify(browser, file);
        console.log('Sales store filter and scoped downloads passed for both home pages');
    } finally {
        await browser.close();
    }
})().catch(error => {console.error(error); process.exitCode = 1;});
