// 两份真实页面函数：全量筛选导出、错误恢复、下载及旧会话隔离。
const fs = require('node:fs'), vm = require('node:vm'), assert = require('node:assert/strict');
let checks = 0;
const equal = (a, b) => {assert.deepEqual(a, b); checks++;};
const deferred = () => {let resolve; return {promise: new Promise(done => {resolve = done;}), resolve};};
function setup(file) {
    const html = fs.readFileSync(file, 'utf8'), elements = new Map(), links = [], revoked = [], timers = [], requests = [];
    const element = id => {
        if (!elements.has(id)) elements.set(id, {value: '', disabled: false, textContent: '', style: {}});
        return elements.get(id);
    };
    const c = vm.createContext({window: {}, URLSearchParams, AbortController,
        document: {documentElement: {lang: 'zh-CN'}, getElementById: element,
            querySelectorAll: selector => selector === '[data-sales-import-export]' ? [element('import-export-button')] : [],
            createElement: () => {const link = {style: {}, click() {this.clicked = true;}, remove() {this.removed = true;}}; links.push(link); return link;},
            body: {appendChild: link => {link.appended = true;}}},
        URL: {createObjectURL: () => 'blob:sales', revokeObjectURL: url => revoked.push(url)},
        setTimeout: (callback, ms) => timers.push({callback, ms}),
        authHeaders: () => ({'X-CSRF-Token': 'fixture'}),
        formatApiError: (detail, fallback) => detail?.message || detail || fallback,
    });
    vm.runInContext(fs.readFileSync('static/page-interactions.js', 'utf8'), c);
    vm.runInContext('let salesExportBusy=false,salesExportSessionEpoch=0,salesExportController=null,salesImportsRequestId=0;', c);
    for (const name of ['setSalesExportStatus', 'setSalesExportBusy', 'resetSalesExportSession', 'exportSalesRecords', 'loadSalesImports', 'escapeHtml']) {
        const source = html.match(new RegExp('^(?:async )?function '+name+'\\([^\\n]*\\) \\{[\\s\\S]*?^\\}', 'm'));
        assert(source, name); vm.runInContext(source[0], c);
    }
    c.handleSessionExpired = () => {c.expired = (c.expired || 0) + 1; c.resetSalesExportSession();};
    c.fetch = async (url, options) => {requests.push({url, options}); return success();};
    return {c, element, links, revoked, timers, requests, html};
}
const success = (policy = 'sale_month_elapsed_v1') => ({ok: true, status: 200,
    headers: {get: name => ({'content-type': 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
        'x-sales-impairment-policy': policy})[name.toLowerCase()] ?? null}, blob: async () => ({size: 100})});
async function main() {
    for (const file of ['templates/index.html', 'index.html']) {
        const {c, element: e, links, revoked, timers, requests, html} = setup(file);
        equal(html.includes('data-sales-export-text="salesExport"'), true);
        equal(html.includes('data-sales-export-text="salesSearch"'), true);
        equal(html.includes('data-sales-search-placeholder="salesSearchPlaceholder"'), true);
        equal(html.includes('window.SalesExport?.reset()'), true);
        e('sales-month').value = '2026-09'; e('sales-product-keyword').value = '  586332269815105453  ';
        const held = deferred();
        c.fetch = (url, options) => {requests.push({url, options}); return held.promise;};
        const first = c.exportSalesRecords(); await c.exportSalesRecords();
        equal(requests.length, 1);
        equal(Object.fromEntries(new URLSearchParams(requests[0].url.split('?')[1])), {month: '2026-09', keyword: '586332269815105453'});
        equal(requests[0].options.credentials, 'same-origin');
        equal(e('sales-export-button').disabled, true); equal(e('sales-month').disabled, true);
        held.resolve(success()); await first;
        equal(links[0].download, 'sales_202609.xlsx'); equal(links[0].clicked, true);
        equal(e('sales-export-button').disabled, false); equal(e('sales-month').disabled, false);
        equal(timers[0].ms, 60000); timers[0].callback(); equal(links[0].removed, true); equal(revoked, ['blob:sales']);
        e('sales-month').value = ''; e('sales-product-keyword').value = '';
        c.fetch = async (url, options) => {requests.push({url, options}); return success();};
        await c.exportSalesRecords(); equal(links[1].download, 'sales_all.xlsx');
        equal(requests.at(-1).url, '/sales/export?');
        const failureCases = [
            [{ok: false, status: 404, text: async () => JSON.stringify({detail: {code: 'sales_export_empty'}})}, 'salesExportEmpty'],
            [{ok: false, status: 404, text: async () => '{"detail":"Not Found"}'}, 'salesExportUnavailable'],
            [{ok: false, status: 500, text: async () => 'server error'}, 'salesExportFailed'],
            [{ok: true, status: 200, headers: {get: () => 'text/html'}}, 'salesExportInvalidFile'],
            [{...success(), blob: async () => ({size: 0})}, 'salesExportInvalidFile'],
        ];
        for (const [response, key] of failureCases) {
            c.fetch = async () => response; await c.exportSalesRecords();
            equal(e('sales-export-status').textContent, c.window.PageInteractions.text(key));
            equal(e('sales-export-button').disabled, false); equal(links.length, 2);
        }
        c.fetch = async () => ({ok: false, status: 422, text: async () => JSON.stringify({detail: {code: 'sales_export_too_large', max_rows: 20000}})});
        await c.exportSalesRecords(); equal(e('sales-export-status').textContent.includes('20000'), true);
        // 旧会话401不能使新会话失效，也不能恢复新请求的按钮。
        const old = deferred(); let oldSignal;
        c.fetch = (_url, options) => {oldSignal = options.signal; return old.promise;};
        const oldTask = c.exportSalesRecords(); c.resetSalesExportSession(); equal(oldSignal.aborted, true);
        const current = deferred(); c.fetch = () => current.promise; const currentTask = c.exportSalesRecords();
        old.resolve({ok: false, status: 401}); await oldTask;
        equal(c.expired, undefined); equal(e('sales-export-button').disabled, true);
        current.resolve(success()); await currentTask; equal(links.length, 3);
        // 正文或blob读取中退出也不得下载、提示旧错误。
        for (const phase of ['text', 'blob']) {
            const body = deferred();
            c.fetch = async () => phase === 'text' ? {ok: false, status: 403, text: () => body.promise}
                : {...success(), blob: () => body.promise};
            const task = c.exportSalesRecords(); await Promise.resolve(); await Promise.resolve();
            c.resetSalesExportSession(); body.resolve(phase === 'text' ? '{"detail":"old failure"}' : {size: 100}); await task;
            equal(links.length, 3); equal(e('sales-export-status').textContent, '');
        }
        c.fetch = async () => ({ok: false, status: 401}); await c.exportSalesRecords();
        equal(c.expired, 1); equal(e('sales-export-button').disabled, false);
        // 批次导出不继承页面月份/商品筛选，与普通导出共用防重和会话隔离。
        e('sales-month').value = '1900-01'; e('sales-product-keyword').value = 'not-this-import';
        const batchRequest = deferred();
        c.fetch = (url, options) => {requests.push({url, options}); return batchRequest.promise;};
        const batchTask = c.exportSalesRecords(17); await c.exportSalesRecords(18);
        equal(requests.at(-1).url, '/sales/imports/17/export');
        equal(e('import-export-button').disabled, true);
        batchRequest.resolve(success()); await batchTask;
        equal(links.at(-1).download, 'sales_import_17.xlsx');
        equal(e('import-export-button').disabled, false);
        equal(e('sales-import-export-status').textContent, c.window.PageInteractions.text('salesExportReady'));
        for (const [code, key, status] of [
            ['sales_export_batch_not_found', 'salesImportExportNotFound', 404],
            ['sales_export_batch_rolled_back', 'salesImportExportRolledBack', 409],
        ]) {
            c.fetch = async () => ({ok: false, status, text: async () => JSON.stringify({detail: {code}})});
            await c.exportSalesRecords(17);
            equal(e('sales-import-export-status').textContent, c.window.PageInteractions.text(key));
            equal(e('import-export-button').disabled, false);
        }
        c.api = async () => [
            {id: 17, status: 'completed', file_name: '<source>.xlsx', can_rollback: false},
            {id: 18, status: 'rolled_back', file_name: 'removed.xlsx', can_rollback: false},
        ];
        await c.loadSalesImports();
        equal(e('sales-imports-tbody').innerHTML.includes('exportSalesRecords(17)'), true);
        equal(e('sales-imports-tbody').innerHTML.includes('exportSalesRecords(18)'), false);
        equal(e('sales-imports-tbody').innerHTML.includes('viewSalesImportResult(17)'), true);
        equal(e('sales-imports-tbody').innerHTML.includes('viewSalesImportResult(18)'), true);
        equal(e('sales-imports-tbody').innerHTML.includes('&lt;source&gt;'), true);
        equal(e('sales-imports-tbody').innerHTML.includes('导出本次对账'), true);
        const late = deferred(); const downloadCount = links.length;
        c.fetch = async () => ({...success(), blob: () => late.promise});
        const lateTask = c.exportSalesRecords(17); await Promise.resolve(); await Promise.resolve();
        c.resetSalesExportSession(); late.resolve({size: 100}); await lateTask;
        equal(links.length, downloadCount); equal(e('sales-import-export-status').textContent, '');
        const waitingFx = setup(file), fxSave = deferred();
        waitingFx.c.window.ReportExchangeRates = {waitForPendingSaves: () => fxSave.promise};
        const exportAfterSave = waitingFx.c.exportSalesRecords(17);
        await Promise.resolve(); equal(waitingFx.requests.length, 0);
        fxSave.resolve(); await exportAfterSave; equal(waitingFx.requests.length, 1);
        const staleFx = deferred();
        waitingFx.c.window.ReportExchangeRates.waitForPendingSaves = () => staleFx.promise;
        const staleExport = waitingFx.c.exportSalesRecords();
        waitingFx.c.resetSalesExportSession(); staleFx.resolve(); await staleExport;
        equal(waitingFx.requests.length, 1); equal(waitingFx.links.length, 1);

        // 直接从任一入口导出，不依赖销售列表是否加载；旧接口、新旧进程混合均按本次响应校验。
        for (const batchId of [null, 17]) {
            for (const policy of [null, '', 'holding_days_v0', 'sale_month_elapsed_v2']) {
                const blocked = setup(file);
                let blobReads = 0, objectUrls = 0, exportSignal;
                blocked.c.URL.createObjectURL = () => {objectUrls++; return 'blob:sales';};
                blocked.c.fetch = async (_url, options) => {
                    exportSignal = options.signal;
                    return {...success(policy), blob: async () => {blobReads++; return {size: 100};}};
                };
                await blocked.c.exportSalesRecords(batchId);
                equal(blobReads, 0); equal(objectUrls, 0); equal(blocked.links.length, 0);
                equal(exportSignal.aborted, true);
                const message = blocked.c.window.PageInteractions.text('salesExportPolicyMismatch');
                equal(message.includes('无法确认'), true);
                equal(message.includes('无需重新导入'), true);
                for (const id of ['sales-export-status', 'sales-import-export-status']) {
                    equal(blocked.element(id).textContent, message);
                    equal(blocked.element(id).className, 'msg msg-err');
                }
                equal(blocked.element('sales-export-button').disabled, false);
                equal(blocked.element('import-export-button').disabled, false);
                // 后端更新后立即可重试，不永久禁用；随后命中旧进程仍须拒绝。
                blocked.c.fetch = async () => success();
                await blocked.c.exportSalesRecords(batchId);
                equal(blocked.links.length, 1); equal(blocked.links[0].clicked, true);
                equal(blocked.links[0].download, batchId === null ? 'sales_all.xlsx' : 'sales_import_17.xlsx');
                blocked.c.fetch = async () => success(policy);
                await blocked.c.exportSalesRecords(batchId);
                equal(blocked.links.length, 1); equal(objectUrls, 1);
                // 退出后晚到的旧口径响应不能覆盖新会话。
                const latePolicy = deferred();
                blocked.c.fetch = () => latePolicy.promise;
                const obsolete = blocked.c.exportSalesRecords(batchId);
                blocked.c.resetSalesExportSession();
                latePolicy.resolve(success(policy)); await obsolete;
                equal(blocked.links.length, 1);
                equal(blocked.element('sales-import-export-status').textContent, '');
            }
        }

    }
    console.log(JSON.stringify({checks, pages: 2, scope: 'actual sales export functions in VM'}));
}
main().catch(error => {console.error(error); process.exitCode = 1;});
