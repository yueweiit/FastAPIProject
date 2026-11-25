// 两份实际页面函数：默认成功、切换/刷新、撤销后重查及过期响应隔离。
const fs = require('node:fs'), vm = require('node:vm'), assert = require('node:assert/strict');
let checks = 0;
const equal = (a, b) => {assert.deepEqual(a, b); checks++;};
const deferred = () => {let resolve; const promise = new Promise(r => {resolve = r;}); return {promise, resolve};};
const response = (rows, status = 200) => ({ok: status === 200, status, text: async () => JSON.stringify(rows)});

function setup(file) {
    const html = fs.readFileSync(file, 'utf8'), elements = new Map(), calls = [];
    const el = id => {
        if (!elements.has(id)) elements.set(id, {value: '', innerHTML: '', textContent: '', style: {}, dataset: {}});
        return elements.get(id);
    };
    const labels = [...html.matchAll(/data-sales-history-text="([^"]+)"/g)].map(m => ({dataset: {salesHistoryText: m[1]}}));
    const select = html.match(/<select id="sales-import-status"[^>]*>([\s\S]*?)<\/select>/);
    assert(select, file);
    equal(select[0].includes('onchange="loadSalesImports()"'), true);
    equal(html.includes('label for="sales-import-status"'), true);
    el('sales-import-status').value = select[1].match(/value="([^"]*)" selected/)[1];
    let session = 1, expired = 0;
    const c = vm.createContext({window: {}, URLSearchParams,
        document: {documentElement: {lang: 'zh-CN'}, getElementById: el,
            querySelectorAll: query => query === '[data-sales-history-text]' ? labels : []},
        authHeaders: () => ({}), updateSalesImportControls: () => {}, showMsg: () => {},
        loadSales: () => {}, loadBatches: () => {},
    });
    vm.runInContext(fs.readFileSync('static/page-interactions.js', 'utf8'), c);
    vm.runInContext("const API='';let CURRENT_USER_ID=1,salesStoreRequestId=0,salesImportSessionId=0,salesImportBusy=false,salesImportStoresReady=false,salesExportBusy=false,salesRollbackBusy=false,salesPage=1,pendingConfirmationsPage=1;", c);
    vm.runInContext(html.match(/^let salesImportsRequestId = .*$/m)[0], c);
    for (const name of ['api', 'formatApiError', 'escapeHtml', 'loadSalesImports', 'resetSalesImportState', 'rollbackSalesImport', 'refreshSalesWorkspace']) {
        const match = html.match(new RegExp('^(?:async )?function ' + name + '\\([^\\n]*\\) \\{[\\s\\S]*?^\\}', 'm'));
        assert(match, name); vm.runInContext(match[0], c);
    }
    c.window.SalesLedger = {requestOptions: () => {const captured = session; return {isCurrent: () => captured === session};}};
    c.handleSessionExpired = () => {expired++; session++; c.resetSalesImportState();};
    c.rows = [
        {id: 1, status: 'completed', file_name: 'success $& {store} <file>.csv', pending_confirmation: 4, can_rollback: true},
        {id: 2, status: 'rolled_back', file_name: 'rolled-back.csv', pending_confirmation: 0, can_rollback: false},
    ];
    c.fetch = async (url, options) => {
        calls.push({url, options});
        if (options.method === 'POST') {
            c.rows[0].status = 'rolled_back'; c.rows[0].can_rollback = false;
            return response({restored_quantity: 1, sales_removed: 1});
        }
        const status = new URLSearchParams(url.split('?')[1]).get('status');
        return response(c.rows.filter(row => !status || row.status === status));
    };
    return {c, el, calls, labels, expired: () => expired, newSession: () => {session++; c.resetSalesImportState();}};
}

async function main() {
    for (const file of ['templates/index.html', 'index.html']) {
        const h = setup(file), {c, el, calls} = h;
        equal(el('sales-import-status').value, 'completed');
        await c.loadSalesImports();
        equal(calls.at(-1).url, '/sales/imports?limit=30&status=completed');
        equal(h.labels.every(node => node.textContent && node.textContent !== node.dataset.salesHistoryText), true);
        equal(el('sales-imports-tbody').innerHTML.includes('viewSalesImportResult(1)'), true);
        equal(el('sales-imports-tbody').innerHTML.includes('rolled-back.csv'), false);
        equal(el('sales-imports-tbody').innerHTML.includes('success $&amp; {store} &lt;file&gt;.csv'), true);
        equal(el('sales-imports-tbody').innerHTML.includes('<td>4</td>'), true);
        equal(el('sales-imports-tbody').innerHTML.includes('status-completed">成功'), true);
        for (const status of ['', 'rolled_back']) {
            el('sales-import-status').value = status;
            await c.loadSalesImports();
            equal(new URLSearchParams(calls.at(-1).url.split('?')[1]).get('status'), status || null);
            equal(el('sales-imports-tbody').innerHTML.includes('viewSalesImportResult(2)'), true);
            equal(el('sales-imports-tbody').innerHTML.includes('exportSalesRecords(2)'), false);
            equal(el('sales-imports-tbody').innerHTML.includes('viewSalesImportResult(1)'), !status);
            await c.loadSalesImports();
            equal(el('sales-import-status').value, status);
        }
        // 默认成功列表中的记录撤销后移出，切换已撤销仍可查看原始结果。
        el('sales-import-status').value = 'completed';
        c.window.PageInteractions.confirmSalesRollback = async () => true;
        const loadHistory = c.loadSalesImports;
        let refreshed;
        c.loadSalesImports = () => {refreshed = loadHistory(); return refreshed;};
        await c.rollbackSalesImport(1); await refreshed;
        equal(Boolean(refreshed), true);
        c.loadSalesImports = loadHistory;
        equal(el('sales-imports-tbody').innerHTML.includes(c.window.PageInteractions.text('salesHistoryEmpty')), true);
        el('sales-import-status').value = 'rolled_back'; await c.loadSalesImports();
        equal(el('sales-imports-tbody').innerHTML.includes('viewSalesImportResult(1)'), true);
        equal(calls.filter(call => call.options.method === 'POST').length, 1);
        // A→B→A、同状态刷新和会话退出；成功、错误、旧401均不能覆盖最新结果。
        for (const action of ['switch', 'refresh', 'logout']) {
            for (const phase of ['headers', 'body']) {
                for (const statusCode of [200, 401, 500]) {
                    if (phase === 'body' && statusCode === 401) continue;
                    const f = setup(file), gate = deferred(), started = deferred();
                    f.c.fetch = async () => {
                        if (phase === 'headers') {started.resolve(); await gate.promise;}
                        return {ok: statusCode === 200, status: statusCode, text: async () => {
                            if (phase === 'body') {started.resolve(); await gate.promise;}
                            return JSON.stringify(statusCode === 200 ? [{id: 99, status: 'completed', file_name: 'STALE'}] : {detail: 'STALE-ERROR'});
                        }};
                    };
                    const old = f.c.loadSalesImports(); await started.promise;
                    f.c.fetch = async () => response([{id: 3, status: 'completed', file_name: 'LATEST'}]);
                    if (action === 'switch') {
                        f.el('sales-import-status').value = 'rolled_back'; await f.c.loadSalesImports();
                        f.el('sales-import-status').value = 'completed';
                    }
                    if (action === 'logout') {
                        f.el('sales-import-status').value = 'rolled_back'; f.newSession();
                        equal(f.el('sales-import-status').value, 'completed');
                        equal(f.el('sales-imports-tbody').innerHTML, '');
                    }
                    await f.c.loadSalesImports();
                    const latest = f.el('sales-imports-tbody').innerHTML;
                    gate.resolve(); await old;
                    equal(f.el('sales-imports-tbody').innerHTML, latest); equal(f.expired(), 0);
                    equal(latest.includes('LATEST'), true);
                }
            }
        }
        c.fetch = async () => response({detail: '<current error>'}, 500);
        equal(await c.loadSalesImports(), false);
        equal(el('sales-imports-tbody').innerHTML.includes('&lt;current error&gt;'), true);
        c.fetch = async () => response(null, 401); await c.loadSalesImports();
        equal(h.expired(), 1); equal(el('sales-import-status').value, 'completed');
    }
    console.log(JSON.stringify({checks, pages: 2, scope: 'actual history functions/API wrapper; mocked network, no database'}));
}
main().catch(error => {console.error(error); process.exitCode = 1;});
