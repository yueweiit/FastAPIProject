// 真实页面的损益保存和财务附件导出，HTTP 响应由内存夹具控制。
const assert = require('node:assert/strict');
const fs = require('node:fs');
const {chromium} = require('playwright');

const mime = 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet';
const hostile = '店铺不可用 <img src=x onerror="window.unsafe=true">';
const functions = ['api','authHeaders','formatApiError','showMsg','escapeHtml','defaultProfitLossMonth',
    'setFinancialAttachmentsExportBusy','resetFinancialAttachmentsExport','exportFinancialAttachments',
    'profitLossSelectionMatches','formatProfitLossAmount','reportPercent','loadStoreProfitLossSummary',
    'profitLossPolicyReady','compactProfitLossWarnings','salaryBreakdownText','renderStoreProfitLoss',
    'updateProfitLossSaveState','saveStoreProfitLoss'];

async function install(page, file) {
    const html = fs.readFileSync(file, 'utf8');
    assert.equal(html.includes('/profit-loss-evidence.js'), false);
    assert.equal(html.includes('include_evidence'), false);
    await page.setContent(html.replace(/<script\b[^>]*>[\s\S]*?<\/script>/gi, ''));
    await page.addScriptTag({path:'static/page-interactions.js'});
    const code = functions.map(name => {
        const match = html.match(new RegExp('^(?:async )?function '+name+'\\([^\\n]*\\) \\{[\\s\\S]*?^\\}', 'm'));
        assert(match, name);
        return match[0];
    }).join('\n');
    await page.addScriptTag({content:code});
    await page.evaluate(() => {
        document.getElementById('login-overlay').style.display = 'none';
        document.getElementById('sec-reports').classList.add('active');
        document.getElementById('report-pane-profit-loss').classList.add('active');
        document.getElementById('profit-loss-store').innerHTML = '<option value="7">测试店铺</option>';
        document.getElementById('profit-loss-month').value = '2026-10';
        window.AUTH_CSRF = '';
        window.CURRENT_USER_ID = 1;
        window.financialAttachmentsExportController = null;
        window.isAdmin = () => true;
        window.handleSessionExpired = () => {resetFinancialAttachmentsExport(); window.CURRENT_USER_ID = 0;};
        window.requests = [];
        window.fetch = (url, opts) => new Promise((resolve, reject) => {
            requests.push({url, opts, resolve, reject});
            opts.signal.addEventListener('abort', () => reject(new DOMException('Aborted', 'AbortError')), {once:true});
        });
    });
}

async function verifyAmountSave(browser, file) {
    const page = await browser.newPage();
    try {
        await install(page, file);
        const saved = {non_operating_income: 100.25, non_operating_expense: 20.10, income_tax_expense: 9.05};
        await page.evaluate(amounts => {
            window.API = '';
            window.storeProfitLossRequestId = 0;
            window.storeProfitLossLoading = false;
            window.storeProfitLossSavePromise = null;
            window.savedAmounts = amounts;
            const report = () => ({
                store_id: 7, store_name: '测试店铺', can_edit: true, report_month: '2026-10', period_label: '2026年10月',
                calculation_policy: 'order_created_fx_mxn8_net_profit_v5', warnings: [],
                rows: Object.entries(savedAmounts).map(([key, current]) => ({
                    key, label: key, kind: 'manual', input_source: 'manual',
                    manual_value: {current}, value: {current, ytd: current},
                })),
            });
            window.fetch = async (url, options) => {
                const payload = JSON.parse(options.body);
                requests.push({url, method: options.method, payload});
                for (const [key, cell] of Object.entries(payload.manual_values)) savedAmounts[key] = cell.current;
                return Response.json(report());
            };
            window.storeProfitLossReport = report();
            renderStoreProfitLoss(storeProfitLossReport);
        }, saved);

        const amountInput = key => page.locator(`[data-profit-key="${key}"]`);
        const button = page.locator('#profit-loss-save');
        for (const [key, incomplete] of [
            ['non_operating_income', '-'], ['non_operating_expense', '1e'], ['income_tax_expense', '1e309'],
        ]) {
            const input = amountInput(key);
            assert.equal(await input.inputValue(), String(saved[key]));
            await input.fill('');
            await input.pressSequentially(incomplete);
            assert.deepEqual(await input.evaluate(el => [el.value, el.validity.badInput]), ['', true]);
            await button.click();
            await page.locator('#msg .msg-err').getByText('请输入有效金额。需要清空时，请删除输入框中的内容。').waitFor();
            assert.equal(await input.evaluate(el => document.activeElement === el), true);
            assert.equal(await page.evaluate(() => requests.length), 0);
            assert.deepEqual(await page.evaluate(() => savedAmounts), saved);
            await input.fill(String(saved[key]));
        }

        await amountInput('non_operating_income').fill('');
        await amountInput('non_operating_expense').fill('0');
        await amountInput('income_tax_expense').fill('-5.25');
        await button.click();
        await page.locator('#msg .msg-ok').getByText('店铺损益表已保存').waitFor();
        assert.deepEqual(await page.evaluate(() => requests), [{
            url: '/reports/store-profit-loss', method: 'PUT',
            payload: {
                store_id: 7, report_month: '2026-10-01', update_scope: 'current', remarks: {},
                manual_values: {non_operating_income: {current: null}, non_operating_expense: {current: 0},
                    income_tax_expense: {current: -5.25}},
            },
        }]);
        assert.equal(await amountInput('non_operating_income').inputValue(), '');
        assert.equal(await amountInput('non_operating_expense').inputValue(), '0');
        assert.equal(await amountInput('income_tax_expense').inputValue(), '-5.25');
    } finally {
        await page.close();
    }
}

async function verify(browser, file) {
    const page = await browser.newPage({acceptDownloads:true});
    try {
        await install(page, file);
        const button = page.locator('#financial-attachments-export');
        const message = page.locator('#msg');
        assert.equal(await page.locator('#financial-attachments-format').count(), 0);
        const idle = async () => {
            await button.filter({hasText:/^导出财务附件$/}).waitFor();
            assert.equal(await button.isEnabled(), true);
            assert.equal(await button.getAttribute('aria-busy'), 'false');
        };
        await button.click();
        assert.match(await message.innerText(), /请选择报告截止日/);
        assert.equal(await page.evaluate(()=>requests.length), 0);

        await page.locator('#inventory-impairment-date').fill('2026-11-26');
        await button.click();
        assert.equal(await button.innerText(), '正在生成…');
        assert.equal(await button.isDisabled(), true);
        assert.equal(await button.getAttribute('aria-busy'), 'true');
        await page.evaluate(() => {exportFinancialAttachments(); exportFinancialAttachments();});
        assert.equal(await page.evaluate(()=>requests.length), 1);
        const query = new URL(await page.evaluate(()=>requests[0].url), 'http://fixture').searchParams;
        assert.deepEqual(Object.fromEntries(query), {report_date:'2026-11-26',report_month:'2026-10-01',store_id:'7'});

        await page.evaluate(mime => requests[0].resolve({status:200, ok:true, headers:new Headers({'content-type':mime}),
            blob:()=>new Promise(resolve=>window.resolveBody=resolve)}), mime);
        await page.waitForFunction(()=>typeof resolveBody==='function');
        assert.equal(await button.innerText(), '正在生成…');
        assert.equal(await button.isDisabled(), true);
        const downloading = page.waitForEvent('download');
        await page.evaluate(mime => resolveBody(new Blob(['fixture'],{type:mime})), mime);
        const download = await downloading;
        assert.equal(download.suggestedFilename(), '财务附件报表_2026-11-26.xlsx');
        assert.equal(await download.failure(), null);
        await message.getByText('财务附件已生成，已开始下载。').waitFor();
        await idle();

        await page.evaluate(() => {
            window.storeProfitLossReport = {store_id:7, report_month:'2026-10-01'};
            window.storeProfitLossSummaryRequestId = 0;
            window.api = async (_method, url) => {
                if (url !== '/reports/store-profit-loss-summary?report_month=2026-10-01') throw Error(url);
                return {stores:[{name:'甲店', warnings:[]}, {name:'乙店', warnings:[]}],
                    rows:[{key:'operating_revenue', label:'营业收入', values:['80', '20'], total:'100', ratio:'1'}]};
            };
            document.getElementById('profit-loss-summary-details').hidden = false;
        });
        await page.getByText('查看各店铺损益汇总', {exact:true}).click();
        const summary = page.locator('#profit-loss-summary');
        await summary.getByText('¥100.00', {exact:true}).waitFor();
        assert.equal(await summary.getByText('依据').count(), 0);

        await button.click();
        await page.evaluate(hostile => requests[1].resolve(new Response(JSON.stringify({detail:hostile}), {status:403})), hostile);
        await message.getByText(hostile,{exact:true}).waitFor();
        assert.equal(await message.locator('img').count(), 0);
        assert.equal(await page.evaluate(()=>window.unsafe), undefined);
        await idle();

        await button.click();
        await page.evaluate(()=>requests[2].resolve(new Response('invalid', {headers:{'content-type':'text/html'}})));
        await message.getByText('服务器未返回有效的财务附件，请稍后重试。').waitFor();
        await idle();

        await button.click();
        await page.evaluate(() => {
            // 只返回 5xx 状态，响应流不关闭。界面须在正文结束之前恢复。
            const response = new Response(new ReadableStream(), {
                status:503, headers:{'content-type':'application/json'},
            });
            const readJson = response.json.bind(response);
            window.errorBodyReads = 0;
            response.json = () => {window.errorBodyReads++; return readJson();};
            requests[3].resolve(response);
        });
        await message.getByText('财务附件导出失败，请稍后重试。').waitFor({timeout:3000});
        await idle();
        assert.equal(await page.evaluate(()=>errorBodyReads), 0);
        assert.equal(await page.evaluate(()=>requests[3].opts.signal.aborted), true);

        await button.click();
        await page.evaluate(()=>requests[4].resolve(new Response('', {status:401})));
        await message.getByText('登录已过期，请重新登录后导出。').waitFor();
        assert.equal(await page.evaluate(()=>requests[4].opts.signal.aborted), true);
        await idle();

        await page.evaluate(() => {window.CURRENT_USER_ID = 2; window.isAdmin = () => false;});
        await button.click();
        assert.equal(await page.evaluate(()=>requests[5].url.includes('store_id')), false);
        await page.evaluate(()=>resetFinancialAttachmentsExport());
        assert.equal(await page.evaluate(()=>requests[5].opts.signal.aborted), true);
        await idle();
    } finally {
        await page.close();
    }
}

(async () => {
    const browser = await chromium.launch();
    try {
        for (const file of ['templates/index.html', 'index.html']) {
            await verifyAmountSave(browser, file);
            await verify(browser, file);
        }
        console.log('Profit/loss amount save and financial attachments export checks passed for both home pages');
    } finally {
        await browser.close();
    }
})().catch(error=>{console.error(error); process.exitCode=1;});
