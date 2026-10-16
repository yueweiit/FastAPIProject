// 执行两份真实页面的查询/保存函数，使用可控响应顺序；不访问服务或数据库。
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
let checks = 0;
const equal = (actual, expected) => { assert.equal(actual, expected); checks++; };
const flush = () => new Promise(resolve => setImmediate(resolve));
const report = (month = '2026-09', store = 7, editable = true) => ({
    report_month: month, store_id: store, can_edit: editable, rows: [], warnings: [],
});

function setup(file, {admin = false, deferStores = false, manualInput = '123'} = {}) {
    const source = fs.readFileSync(file, 'utf8');
    const elements = new Map();
    const element = id => {
        if (!elements.has(id)) elements.set(id, {value: '', style: {}, disabled: false, innerHTML: '', textContent: ''});
        return elements.get(id);
    };
    element('profit-loss-month').value = '2026-09';
    element('profit-loss-store').value = '7';
    const inputs = [{dataset: {profitKey: 'salary', profitPeriod: 'current'}, value: manualInput}];
    const calls = [], rendered = [], notices = [];
    const context = vm.createContext({URLSearchParams, isAdmin: () => admin,
        document: {getElementById: element, querySelectorAll: selector =>
            selector.includes('[data-profit-remark]') ? [] : inputs},
        showMsg: message => notices.push(message),
        renderStoreProfitLoss: value => rendered.push(value),
        api: (method, url, body) => {
            const call = {method, url, body};
            calls.push(call);
            if (url.startsWith('/finance-masters/') && !deferStores) return Promise.resolve([{id: 7, name: 'A'}, {id: 8, name: 'B'}]);
            return new Promise((resolve, reject) => Object.assign(call, {resolve, reject}));
        },
    });
    vm.runInContext(`let storeProfitLossReport=null, storeProfitLossRequestId=0;
        let storeProfitLossLoading=false, storeProfitLossSavePromise=null;`, context);
    for (const name of ['escapeHtml', 'defaultProfitLossMonth', 'profitLossSelectionMatches',
        'updateProfitLossSaveState', 'resetStoreProfitLossState', 'loadStoreProfitLossStores',
        'loadStoreProfitLoss', 'saveStoreProfitLoss']) {
        const match = source.match(new RegExp('^(?:async )?function '+name+'\\([^\\n]*\\) \\{[\\s\\S]*?^\\}', 'm'));
        assert(match, `Missing ${name}`);
        vm.runInContext(match[0], context);
    }
    const run = code => vm.runInContext(code, context);
    const queries = () => calls.filter(call => call.method === 'GET' && call.url.startsWith('/reports/'));
    const writes = () => calls.filter(call => call.method === 'PUT');
    const load = () => run('loadStoreProfitLoss()');
    const save = () => run('saveStoreProfitLoss()');
    return {element, calls, rendered, notices, run, queries, writes, load, save};
}

function renderManualCell(file, manualValue) {
    const source = fs.readFileSync(file, 'utf8');
    const elements = new Map();
    const element = id => {
        if (!elements.has(id)) elements.set(id, {style: {}, innerHTML: '', textContent: ''});
        return elements.get(id);
    };
    const context = vm.createContext({
        document: {getElementById: element},
        formatProfitLossAmount: () => { throw new Error('unexpected non-manual row'); },
    });
    for (const name of ['escapeHtml', 'renderStoreProfitLoss']) {
        const match = source.match(new RegExp('^function '+name+'\\([^\\n]*\\) \\{[\\s\\S]*?^\\}', 'm'));
        assert(match, `Missing ${name}`);
        vm.runInContext(match[0], context);
    }
    context.report = {
        store_name: '测试店铺', period_label: '2026年9月', can_edit: true,
        rows: [{key: 'salary', label: '人员薪资', kind: 'manual',
            value: {current: 100}, manual_value: manualValue, remark: ''}],
    };
    vm.runInContext('renderStoreProfitLoss(report)', context);
    return element('store-profit-loss-tbody').innerHTML;
}

async function verify(file) {
    {
        const defaultCell = renderManualCell(file, null);
        equal(/data-profit-key="salary"[^>]*value="" placeholder="100"/.test(defaultCell), true);
        const overrideCell = renderManualCell(file, {current: 120});
        equal(/data-profit-key="salary"[^>]*value="120" placeholder=""/.test(overrideCell), true);
        const ui = setup(file, {manualInput: ''});
        const loading = ui.load(); await flush(); ui.queries()[0].resolve(report()); await loading;
        const saving = ui.save();
        equal(ui.writes()[0].body.manual_values.salary.current, null);
        ui.writes()[0].resolve(report()); await saving;
    }
    {
        const ui = setup(file);
        const loading = ui.load(); await flush();
        equal(ui.element('profit-loss-save').disabled, true);
        await ui.save(); equal(ui.writes().length, 0);
        ui.queries()[0].resolve(report()); await loading;
        equal(ui.element('profit-loss-save').disabled, false);
        ui.element('profit-loss-month').value = '2026-10';
        await ui.save(); equal(ui.writes().length, 0);
        const next = ui.load(); await flush();
        await ui.save(); equal(ui.writes().length, 0);
        ui.queries()[1].resolve(report('2026-10')); await next;
        const saving = ui.save();
        equal(ui.writes()[0].body.report_month, '2026-10-01');
        equal(ui.writes()[0].body.update_scope, 'current');
        equal(ui.writes()[0].body.manual_values.salary.current, 123);
        await ui.save(); equal(ui.writes().length, 1);
        equal(ui.element('profit-loss-save').disabled, true);
        ui.writes()[0].resolve(report('2026-10')); await saving;
        equal(ui.element('profit-loss-save').disabled, false);
    }
    {
        const ui = setup(file);
        const old = ui.load(); await flush();
        ui.element('profit-loss-month').value = '2026-10';
        const latest = ui.load(); await flush();
        ui.queries()[1].resolve(report('2026-10')); await latest;
        ui.queries()[0].resolve(report()); await old;
        equal(ui.rendered.length, 1);
        equal(ui.run('storeProfitLossReport.report_month'), '2026-10');
        equal(ui.element('profit-loss-save').disabled, false);
    }
    {
        const ui = setup(file);
        const old = ui.load(); await flush();
        ui.element('profit-loss-month').value = '2026-10';
        const latest = ui.load(); await flush();
        ui.queries()[1].resolve(report('2026-10')); await latest;
        ui.queries()[0].reject(new Error('stale failure')); await old;
        equal(ui.run('storeProfitLossReport.report_month'), '2026-10');
        equal(ui.element('profit-loss-save').disabled, false);
    }
    {
        const ui = setup(file, {admin: true});
        const initial = ui.load(); await flush(); ui.queries()[0].resolve(report()); await initial;
        const saving = ui.save();
        ui.element('profit-loss-month').value = '2026-10';
        ui.element('profit-loss-store').value = '8';
        const next = ui.load(); await flush();
        equal(ui.queries().length, 1);
        equal(ui.writes()[0].body.report_month, '2026-09-01');
        equal(ui.writes()[0].body.store_id, 7);
        ui.writes()[0].resolve(report()); await saving; await flush();
        equal(ui.rendered.length, 1);
        equal(ui.queries().length, 2);
        equal(new URLSearchParams(ui.queries()[1].url.split('?')[1]).get('store_id'), '8');
        ui.queries()[1].resolve(report('2026-10', 8)); await next;
        equal(ui.run('storeProfitLossReport.store_id'), 8);
        equal(ui.run('storeProfitLossReport.report_month'), '2026-10');
    }
    {
        const ui = setup(file);
        const initial = ui.load(); await flush(); ui.queries()[0].resolve(report()); await initial;
        const saving = ui.save();
        ui.element('profit-loss-month').value = '2026-10';
        const next = ui.load(); await flush();
        ui.writes()[0].reject(new Error('模拟保存失败')); await saving; await flush();
        equal(ui.notices.length, 1);
        equal(ui.notices[0].includes('2026-09 店铺损益表保存失败'), true);
        ui.queries()[1].resolve(report('2026-10')); await next;
        equal(ui.run('storeProfitLossReport.report_month'), '2026-10');
    }
    {
        const ui = setup(file);
        const old = ui.load(); await flush();
        ui.run('resetStoreProfitLossState()');
        ui.queries()[0].resolve(report()); await old;
        equal(ui.rendered.length, 0);
        equal(ui.run('storeProfitLossReport'), null);
        equal(ui.element('profit-loss-save').disabled, true);
    }
    {
        const ui = setup(file, {admin: true, deferStores: true});
        const old = ui.load(); await flush();
        ui.element('profit-loss-store').value = '8';
        const latest = ui.load(); await flush();
        ui.calls[1].resolve([{id: 7, name: 'A'}, {id: 8, name: 'B'}]); await flush();
        ui.calls[0].resolve([{id: 7, name: 'obsolete'}]); await old;
        equal(ui.element('profit-loss-store').value, '8');
        equal(ui.queries().length, 1);
        ui.queries()[0].resolve(report('2026-09', 8)); await latest;
        equal(ui.run('storeProfitLossReport.store_id'), 8);
    }
    {
        const ui = setup(file);
        const load = ui.load(); await flush();
        ui.queries()[0].reject(new Error('current failure')); await load;
        await ui.save(); equal(ui.writes().length, 0);
        equal(ui.element('profit-loss-save').disabled, true);
    }
    {
        const ui = setup(file);
        const load = ui.load(); await flush(); ui.queries()[0].resolve(report('2026-09',7,false)); await load;
        await ui.save(); equal(ui.writes().length, 0);
        equal(ui.element('profit-loss-save').disabled, true);
    }
}

(async () => {
    for (const file of ['templates/index.html', 'index.html']) await verify(file);
    console.log(JSON.stringify({pages: 2, checks, scope: 'in-memory request-order and save-context checks'}));
})().catch(error => { console.error(error); process.exitCode = 1; });
