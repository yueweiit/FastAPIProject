// 执行两份真实页面的查询/保存函数，使用可控响应顺序；不访问服务或数据库。
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const locale = {window: {}, document: {documentElement: {lang: 'zh-CN'}}};
vm.runInNewContext(fs.readFileSync('static/page-interactions.js', 'utf8'), locale);
const localizedText = locale.window.PageInteractions.text;
let checks = 0;
const equal = (actual, expected) => { assert.equal(actual, expected); checks++; };
const flush = () => new Promise(resolve => setImmediate(resolve));
const currentPolicy = 'order_created_fx_mxn8_net_profit_v5';
const report = (month = '2026-09', store = 7, editable = true) => ({
    report_month: month, store_id: store, can_edit: editable, calculation_policy: currentPolicy, rows: [], warnings: [],
});

function setup(file, {admin = false, deferStores = false, manualInput = '123', manualKey = 'salary'} = {}) {
    const source = fs.readFileSync(file, 'utf8');
    const elements = new Map();
    const element = id => {
        if (!elements.has(id)) elements.set(id, {value: '', style: {}, disabled: false, innerHTML: '', textContent: ''});
        return elements.get(id);
    };
    element('profit-loss-month').value = '2026-09';
    element('profit-loss-store').value = '7';
    const inputs = [{dataset: {profitKey: manualKey, profitPeriod: 'current'}, value: manualInput, validity: {badInput: false}}];
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
        'profitLossPolicyReady', 'updateProfitLossSaveState', 'clearProfitLossContext', 'resetStoreProfitLossState', 'loadStoreProfitLossStores',
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

function renderManualCell(file, manualValue, inputSource = 'manual', key = 'salary', kind = 'manual', policy = 'month_end_fx_mxn8_v1', breakdown = null, inspect = false) {
    const source = fs.readFileSync(file, 'utf8');
    const elements = new Map();
    const element = id => {
        if (!elements.has(id)) elements.set(id, {style: {}, innerHTML: '', textContent: '',
            classList: {toggle() {}}, setAttribute() {}, removeAttribute() {}, getAttribute() {return null;}});
        return elements.get(id);
    };
    const context = vm.createContext({
        window: {PageInteractions: {text: key => {
            const labels={profitLossLedger:'使用明细',profitLossYtdHelp:'各月累计',profitLossReserved:'暂未接入',profitLossPolicy:'新报表口径',profitLossBackendUpdate:'后端待更新',
                profitLossNetImpairmentPolicy:localizedText('profitLossNetImpairmentPolicy'),
                profitLossLegacyImpairmentPolicy:localizedText('profitLossLegacyImpairmentPolicy'),
                profitLossManualNetProfitPolicy:localizedText('profitLossManualNetProfitPolicy'),
                profitLossLegacyNetProfitPolicy:localizedText('profitLossLegacyNetProfitPolicy'),
                profitLossPeriodCurrent:'本月',profitLossPeriodPrevious:'上月',profitLossPeriodYtd:'本年累计',profitLossPeriodSeparator:'、',
                profitLossSalaryHelp:'本月薪资合计覆盖OA',profitLossSharedAdminHelp:'本店本月管理费覆盖OA',
                profitLossManualHelp:localizedText('profitLossManualHelp'),
                profitLossOtherDirectCostHelp:localizedText('profitLossOtherDirectCostHelp'),
                profitLossSalaryBase:'工资',profitLossSalaryTax:'个税',profitLossSalaryFund:'公积金',profitLossSalaryOaTotal:'OA合计',
                profitLossSalaryYtd:'报表累计',profitLossSalaryManual:'人工覆盖',profitLossSalaryLedger:'费用台账',profitLossSalaryDefault:'OA取值'};
            equal(Object.hasOwn(labels,key),true);return labels[key];
        }}},
        document: {getElementById: element},
        isAdmin: () => false,
        formatProfitLossAmount: value => value == null ? '—' : String(value),
    });
    for (const name of ['escapeHtml', 'profitLossPolicyReady', 'compactProfitLossWarnings', 'salaryBreakdownText', 'renderStoreProfitLoss']) {
        const match = source.match(new RegExp('^function '+name+'\\([^\\n]*\\) \\{[\\s\\S]*?^\\}', 'm'));
        assert(match, `Missing ${name}`);
        vm.runInContext(match[0], context);
    }
    context.report = {
        store_name: '测试店铺', period_label: '2026年9月', can_edit: true, calculation_policy:policy, salary_oa_breakdown: breakdown,
        rows: [{key, label: '费用', kind, input_source: inputSource,
            is_auto: kind === 'auto', source: key === 'other_direct_cost' ? '销售记录中的其他费用' : undefined,
            value: {current: 100,ytd:321}, manual_value: manualValue, remark: ''}],
    };
    vm.runInContext('renderStoreProfitLoss(report)', context);
    const html = element('store-profit-loss-tbody').innerHTML;
    return inspect ? {html, policy: element('profit-loss-policy').textContent} : html;
}

async function verify(file) {
    {
        for (const policy of [currentPolicy, 'order_created_fx_mxn8_net_impairment_v4', 'order_created_fx_mxn8_income_only_v3',
            'month_end_fx_mxn8_compensation_v2', 'month_end_fx_mxn8_v1', 'unrecognized']) {
            const result = renderManualCell(file, null, 'manual', 'salary', 'manual', policy, null, true);
            const netImpairment = [currentPolicy, 'order_created_fx_mxn8_net_impairment_v4'].includes(policy);
            equal(result.policy.includes('销售转销仍参与计算'), netImpairment);
            equal(result.policy.includes('销售转销抵减营业成本'), !netImpairment && policy !== 'unrecognized');
            equal(result.policy.includes('07-存货跌价准备明细表'), policy !== 'unrecognized');
            equal(result.policy.includes('分别保留明细'), false);
            equal(result.policy.includes('净利润＝营业利润＋营业外收入－营业外支出－所得税费用'), policy === currentPolicy);
            equal(result.policy.includes('后端待更新'), policy === 'unrecognized');
            equal(result.html.includes('disabled'), policy === 'unrecognized');
        }
    }
    {
        const ui = setup(file);
        ui.element('profit-loss-updated').textContent = '旧店铺';
        ui.element('profit-loss-warnings').innerHTML = '旧警示';
        ui.element('profit-loss-warnings').hidden = false;
        ui.element('profit-loss-fx').textContent = '旧汇率';
        ui.element('profit-loss-fx-details').open = true;
        ui.element('profit-loss-fx-details').hidden = false;
        const loading = ui.load(); await flush();
        equal(ui.element('profit-loss-updated').textContent, '');
        equal(ui.element('profit-loss-warnings').innerHTML, '');
        equal(ui.element('profit-loss-warnings').hidden, true);
        equal(ui.element('profit-loss-fx').textContent, '');
        equal(ui.element('profit-loss-fx-details').open, false);
        equal(ui.element('profit-loss-fx-details').hidden, true);
        ui.queries()[0].reject(new Error('读取失败')); await loading;
        equal(ui.element('profit-loss-warnings').hidden, true);
        equal(ui.element('profit-loss-fx-details').hidden, true);
    }
    {
        const ui=setup(file);const loading=ui.load();await flush();
        const legacy=report();delete legacy.calculation_policy;
        ui.queries()[0].resolve(legacy);await loading;
        equal(ui.element('profit-loss-save').disabled,true);
        await ui.save();equal(ui.writes().length,0);
    }
    {
        const defaultCell = renderManualCell(file, null);
        const compensationCell = renderManualCell(file, null, 'manual', 'compensation_revenue', 'auto', 'month_end_fx_mxn8_compensation_v2');
        equal(compensationCell.includes('data-profit-key="compensation_revenue"'),false);
        equal(compensationCell.includes('>100</td>'),true);
        const directCostCell = renderManualCell(file, null, 'manual', 'other_direct_cost', 'auto');
        equal(directCostCell.includes('title="对账可查看销售明细「其他直接成本」列。涉及历史跨店结算关联时，请核对原始结算的店铺归属及金额。">100</td>'),true);
        equal(directCostCell.includes('title="各月累计">321</td>'),true);
        equal(/data-profit-key="salary"[^>]*value="" placeholder="100"/.test(defaultCell), true);
        const overrideCell = renderManualCell(file, {current: 120});
        equal(/data-profit-key="salary"[^>]*value="120" placeholder=""/.test(overrideCell), true);
        const ledgerCell = renderManualCell(file, {current: 120}, 'ledger');
        equal(ledgerCell.includes('title="使用明细">100</td>'), true);
        equal(ledgerCell.includes('data-profit-key="salary"'), false);
        equal(ledgerCell.includes('title="各月累计">321</td>'),true);
        const breakdown = {china_salary:{current:77,ytd:260}, individual_income_tax:{current:5,ytd:11},
            employer_contributions:{current:18,ytd:50}, total:{current:100,ytd:321}};
        for (const [manual,source,label] of [[null,'manual','OA取值'],[{current:0},'manual','人工覆盖'],[null,'ledger','费用台账']]) {
            const html = renderManualCell(file,manual,source,'salary','manual','month_end_fx_mxn8_compensation_v2',breakdown);
            for(const expected of ['工资  77','个税  5','公积金  18','OA合计  100',label,'报表累计  321','data-help-tip']) {
                equal(html.includes(expected),true);
            }
        }
        const ui = setup(file, {manualInput: ''});
        const loading = ui.load(); await flush(); ui.queries()[0].resolve(report()); await loading;
        const saving = ui.save();
        equal(ui.writes()[0].body.manual_values.salary.current, null);
        ui.writes()[0].resolve(report()); await saving;
    }
    {
        for (const [input, saved] of [['', null], ['0', 0], ['861', 861]]) {
            const cell = renderManualCell(file, saved === null ? null : {current: saved}, 'manual', 'shared_admin');
            equal(cell.includes('title="本店本月管理费覆盖OA"'), true);
            equal(cell.includes(`data-profit-key="shared_admin" data-profit-period="current" value="${input}" placeholder="${input === '' ? '100' : ''}"`), true);
            const ui = setup(file, {manualKey: 'shared_admin', manualInput: input});
            const loading = ui.load(); await flush(); ui.queries()[0].resolve(report()); await loading;
            const saving = ui.save();
            equal(ui.writes()[0].body.manual_values.shared_admin.current, saved);
            equal(ui.writes()[0].body.update_scope, 'current_visible_remarks');
            ui.writes()[0].resolve(report()); await saving;
        }
    }
    {
        for (const key of ['non_operating_income', 'non_operating_expense', 'income_tax_expense']) {
            const html = renderManualCell(file, {current: 0}, 'manual', key, 'manual', currentPolicy);
            equal(html.includes(`data-profit-key="${key}" data-profit-period="current" value="0"`), true);
            equal(html.includes('data-profit-period="ytd"'), false);
            const ui = setup(file, {manualKey: key, manualInput: '12.34'});
            const loading = ui.load(); await flush(); ui.queries()[0].resolve(report()); await loading;
            const saving = ui.save();
            equal(ui.writes()[0].body.manual_values[key].current, 12.34);
            equal(ui.writes()[0].body.update_scope, 'current_visible_remarks');
            ui.writes()[0].resolve(report()); await saving;
        }
        const derived = renderManualCell(file, null, 'manual', 'net_profit', 'formula', currentPolicy);
        equal(derived.includes('data-profit-key="net_profit"'), false);
        equal(derived.includes('>100</td>'), true);
        equal(derived.includes('>321</td>'), true);
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
        equal(ui.writes()[0].body.update_scope, 'current_visible_remarks');
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
