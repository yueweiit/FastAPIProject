// Actual shared drawer + both runtime page renderers in Chromium, isolated HTTP fixtures.
const fs = require('node:fs'), os = require('node:os'), path = require('node:path');
const assert = require('node:assert/strict');
const {chromium} = require('playwright');
let checks = 0;
function check(actual, expected) { assert.deepEqual(actual, expected); checks++; }
const hostile = '$& <img src=x onerror="window.unsafe=1">';
function fixture() {
    const cells = (value, inputs = [], count = 0) => Object.fromEntries(['current','previous','ytd'].map(p => [p,
        {value:p === 'ytd' ? String(Number(value) * 2) : value, inputs, source_count:count, source_total:value, status:'complete'}]));
    return {store_id:7, store_name:'S7 ' + hostile, store_platform:'test', report_month:'2026-09', period_label:'2026年9月',
        can_edit:true, calculation_policy:'order_created_fx_mxn8_net_impairment_v4', warnings:[], exchange_rates:[],
        rows:[{key:'product_sales_revenue',label:'商品销售收入',kind:'auto',is_auto:true,value:{current:'100',ytd:'200'}},
              {key:'advertising',label:'广告推广费',kind:'manual',manual_value:{current:'10'},value:{current:'10',ytd:'20'},remark:hostile},
              {key:'gross_profit',label:'毛利',kind:'formula',is_formula:true,value:{current:'90',ytd:'180'}}],
        evidence:{schema_version:'profit_loss_evidence_v1', calculation_id:'FIXTURE', token:'token',
            fields:{product_sales_revenue:{label:'商品销售收入',kind:'auto',formula:'参与金额合计',note:'原始结算与分摊 ' + hostile, cells:cells('100',[],51)},
                advertising:{label:'广告推广费',kind:'manual',formula:'人工填写',note:'空白按0；显式0覆盖',cells:cells('10',[],1)},
                gross_profit:{label:'毛利',kind:'formula',formula:'收入 − 费用',note:'示例计算依赖',cells:cells('90',[
                    {key:'product_sales_revenue',label:'商品销售收入',operator:'+',value:'100'},
                    {key:'advertising',label:'广告推广费',operator:'−',value:'10'}])}},
            source_labels:{sale:'销售记录'},status_labels:{complete:'依据金额一致',partial:'依据不完整'},notes:{rounding:'先计算后显示，保留精度'}}};
}
async function install(page, file, admin=true) {
    const source = fs.readFileSync(file, 'utf8');
    check(source.includes('/profit-loss-evidence.js'), true);
    const funcs = ['formatProfitLossAmount','compactProfitLossWarnings','salaryBreakdownText','renderStoreProfitLoss','clearProfitLossContext'];
    const code = funcs.map(name => {
        const match = source.match(new RegExp('^function ' + name + '\\([^\\n]*\\) \\{[\\s\\S]*?^\\}', 'm'));
        assert(match, name); return match[0];
    }).join('\n');
    await page.setContent(source.replace(/<script\b[^>]*>[\s\S]*?<\/script>/gi,''));
    await page.addScriptTag({path:'static/page-interactions.js'});
    await page.addScriptTag({path:'static/profit-loss-evidence.js'});
    await page.evaluate(({report, admin}) => {
        document.getElementById('login-overlay').style.display='none';
        document.querySelectorAll('.page').forEach(n => n.style.display='none');
        const pane = document.getElementById('report-pane-profit-loss');
        document.body.append(pane); pane.style.display='block';
        window.fixtureReport = report; window.isAdmin = () => admin;
        window.profitLossPolicyReady = () => true;
        window.profitLossSelectionMatches = () => true;
        window.escapeHtml = s => String(s ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
        window.storeProfitLossRequestId = 1;
        window.calls = []; window.requestGuards = [];
        window.summaryFixture = null; window.scopedSources = false;
        window.api = async (_method, url, _body, options) => {
            window.requestGuards.push(options);
            window.calls.push(url);
            if (window.deferSources && url.includes('/evidence/')) return new Promise(resolve => window.finishSource = resolve);
            if (url.includes('store-profit-loss-summary')) return window.summaryFixture || {
                report_month:'2026-09', stores:[{id:7,name:'S7 ' + report.store_name,evidence:report.evidence,warnings:['本月待核对']}],
                rows:[{key:'product_sales_revenue',label:'收入',values:['100'],total:'100',ratio:'1'},
                      {key:'operating_revenue',label:'营业收入',values:['100'],total:'100',ratio:'1'},
                      {key:'gross_profit',label:'毛利',values:['90'],total:'90',ratio:'.9'},
                      {key:'gross_margin',label:'毛利率',values:['.9'],total:'.9',ratio:null}]
            };
            const query = new URL(url,'http://fixture').searchParams, page=Number(query.get('page'));
            if (window.scopedSources) return {calculation_id:report.evidence.calculation_id,field:query.get('field'),period:query.get('period'),total:1,
                items:[{source:'sale',reference:'S1',day:'2026-09-01',amount:'100',scope_status:'restricted',details:{原始结算行:[],核对提示:'来源归属不一致，已隐藏相关明细'}}]};
            return {calculation_id:window.badId ? 'OTHER' : report.evidence.calculation_id, field:query.get('field'),period:query.get('period'),total:51,
                items:Array.from({length:page===1?50:1},(_,i)=>({source:'sale',reference:`E${(page-1)*50+i+1}`,day:'2026-09-01',amount:'2',details:{文件:report.store_name}}))};
        };
    }, {report:fixture(), admin});
    await page.addScriptTag({content:code});
    await page.evaluate(() => renderStoreProfitLoss(window.fixtureReport));
}
async function run(browser, file) {
    const page = await browser.newPage({viewport:{width:1280,height:900}});
    try {
        await install(page,file);
        check(await page.locator('#store-profit-loss-tbody .ple-action').count(),9);
        await page.locator('#store-profit-loss-tbody tr').first().getByRole('button',{name:'口径',exact:true}).click();
        await page.waitForSelector('.ple-record');
        check(await page.locator('.ple-record').count(),50);
        check(await page.locator('.ple-dialog img').count(),0);
        check(await page.evaluate(()=>window.unsafe),undefined);
        check((await page.locator('.ple-note').innerText()).includes(hostile),true);
        await page.getByRole('button',{name:'下一页',exact:true}).click();
        await page.waitForFunction(()=>document.querySelectorAll('.ple-record').length===1);
        check((await page.locator('.ple-record').innerText()).includes('E51'),true);
        await page.locator('[data-ple-period]').selectOption('previous');
        await page.waitForSelector('.ple-record');
        check(await page.evaluate(()=>window.calls.at(-1).includes('period=previous')),true);
        await page.keyboard.press('Escape');
        check(await page.locator('.ple-dialog').evaluate(d=>d.open),false);
        await page.locator('#store-profit-loss-tbody tr').nth(2).getByRole('button',{name:'口径',exact:true}).click();
        check((await page.locator('.ple-calculation').innerText()).includes('¥90.00'),true);
        await page.locator('[data-ple-key="advertising"]').click();
        check(await page.locator('.ple-dialog h3').innerText(),'广告推广费');
        check((await page.locator('.ple-context').first().innerText()).includes('未保存的输入不参与'),true);
        await page.getByRole('button',{name:'关闭',exact:true}).click();
        await page.getByRole('button',{name:'查看各店铺损益汇总',exact:true}).click();
        await page.waitForSelector('#profit-loss-summary table');
        check((await page.locator('#profit-loss-summary').innerText()).includes('90.00%'),true);
        check((await page.locator('#profit-loss-summary .ple-error').innerText()).includes('待核对'),true);
        await page.locator('[data-ple-total="total"][data-ple-key="gross_margin"]').click();
        check(await page.locator('.ple-dialog .ple-note').innerText(),'合计毛利 ÷ 合计营业收入');
        check(await page.locator('.ple-dialog .ple-value').innerText(),'90.00%');
        await page.getByRole('button',{name:'关闭',exact:true}).click();
        // 与后端/Excel回归使用同组分子分母，避免平均或相加各店毛利率。
        await page.evaluate(() => { window.summaryFixture = {
            report_month:'2026-09',stores:[{id:7,name:'S7'},{id:8,name:'S8'}],rows:[
                {key:'operating_revenue',label:'营业收入',values:['85','100'],total:'185',ratio:'1'},
                {key:'gross_profit',label:'毛利',values:['25','86'],total:'111',ratio:'.6'},
                {key:'gross_margin',label:'毛利率',values:['.2941176470588235','.86'],total:'.6',ratio:null},
                {key:'product_purchase_cost',label:'商品采购成本',values:['40','0'],total:'40',ratio:'.2162162162162162'}]}; });
        await page.getByRole('button',{name:'查看各店铺损益汇总',exact:true}).click();
        await page.locator('[data-ple-total="total"][data-ple-key="gross_margin"]').click();
        check(await page.locator('.ple-calculation').innerText(),'毛利(¥111.00) ÷ 营业收入(¥185.00) = 60.00%');
        check(await page.locator('.ple-dialog [data-ple-store]').count(),4);
        await page.getByRole('button',{name:'关闭',exact:true}).click();
        await page.locator('[data-ple-total="ratio"][data-ple-key="product_purchase_cost"]').click();
        check(await page.locator('.ple-calculation').innerText(),'商品采购成本(¥40.00) ÷ 营业收入(¥185.00) = 21.62%');
        await page.getByRole('button',{name:'关闭',exact:true}).click();
        for (const [amount, ratio, formatted] of [['3.25','.01625','1.63%'],['-3.25','-.01625','-1.63%']]) {
            await page.evaluate(({amount, ratio}) => {
                window.summaryFixture.rows.find(r=>r.key==='operating_revenue').total='200';
                Object.assign(window.summaryFixture.rows.find(r=>r.key==='product_purchase_cost'),{total:amount,ratio});
            }, {amount, ratio});
            await page.getByRole('button',{name:'查看各店铺损益汇总',exact:true}).click();
            await page.locator('[data-ple-total="ratio"][data-ple-key="product_purchase_cost"]').click();
            check((await page.locator('.ple-calculation').innerText()).endsWith(' = ' + formatted),true);
            check(await page.locator('.ple-dialog .ple-value').innerText(),formatted);
            await page.getByRole('button',{name:'关闭',exact:true}).click();
        }
        await page.evaluate(()=>{window.summaryFixture.rows.find(r=>r.key==='product_purchase_cost').total='40';});
        await page.locator('[data-ple-total="ratio"][data-ple-key="gross_margin"]').click();
        check(await page.locator('.ple-dialog .ple-note').innerText(),'毛利率不再计算占比');
        check(await page.locator('.ple-dialog [data-ple-store]').count(),0);
        check(await page.locator('.ple-calculation').count(),0);
        await page.getByRole('button',{name:'关闭',exact:true}).click();
        for (const denominator of ['0', null]) {
            await page.evaluate(value => {
                window.summaryFixture.rows.find(r=>r.key==='operating_revenue').total=value;
                window.summaryFixture.rows.find(r=>r.key==='product_purchase_cost').ratio=null;
            }, denominator);
            await page.getByRole('button',{name:'查看各店铺损益汇总',exact:true}).click();
            await page.locator('[data-ple-total="ratio"][data-ple-key="product_purchase_cost"]').click();
            check(await page.locator('.ple-dialog .ple-value').innerText(),'—');
            check(await page.locator('.ple-calculation').innerText(),`商品采购成本(¥40.00) ÷ 营业收入(${denominator === null ? '待核对/未提供' : '¥0.00'}) = 待核对/未提供`);
            await page.getByRole('button',{name:'关闭',exact:true}).click();
        }
        // 来源受限时仍展示原报表金额与明确提示，不伪装为没有来源的手工销售。
        await page.evaluate(() => {
            window.scopedSources=true;
            window.fixtureReport.warnings=['来源归属不一致，已隐藏相关明细'];
            window.fixtureReport.evidence.warnings=window.fixtureReport.warnings;
            window.fixtureReport.evidence.fields.product_sales_revenue.cells.current.status='partial';
            renderStoreProfitLoss(window.fixtureReport);
        });
        await page.locator('#store-profit-loss-tbody tr').first().getByRole('button',{name:'口径',exact:true}).click();
        await page.waitForSelector('.ple-record');
        check((await page.locator('.ple-dialog').innerText()).includes('依据不完整'),true);
        check((await page.locator('.ple-record').innerText()).includes('来源归属不一致'),true);
        check(await page.locator('.ple-dialog .ple-value').innerText(),'¥100.00');
        check((await page.locator('#profit-loss-warnings').innerText()).includes('来源归属不一致'),true);
        await page.getByRole('button',{name:'关闭',exact:true}).click();
        await page.evaluate(()=>{ window.fixtureReport.evidence=undefined; renderStoreProfitLoss(window.fixtureReport); });
        await page.locator('#store-profit-loss-tbody tr').first().getByRole('button',{name:'口径',exact:true}).click();
        check((await page.locator('.ple-dialog').innerText()).includes('该版本未保存完整计算依据'),true);
        check(await page.locator('[data-ple-period]').count(),0);
        await page.evaluate(()=>clearProfitLossContext());
        check(await page.locator('.ple-dialog').evaluate(d=>d.open),false);
        check(await page.locator('#profit-loss-summary').innerText(),'');
        // Old responses cannot repopulate the drawer after changing report context.
        await install(page,file);
        await page.evaluate(()=>{window.deferSources=true;});
        await page.locator('#store-profit-loss-tbody tr').first().getByRole('button',{name:'口径',exact:true}).click();
        await page.waitForFunction(()=>!!window.finishSource);
        check(await page.evaluate(()=>window.requestGuards.at(-1).isCurrent()),true);
        await page.evaluate(()=>{clearProfitLossContext();window.finishSource({calculation_id:'FIXTURE',items:[]});});
        check(await page.evaluate(()=>window.requestGuards.at(-1).isCurrent()),false);
        check(await page.locator('.ple-dialog').evaluate(d=>d.open),false);
        check(await page.locator('#store-profit-loss-tbody .ple-action').count(),0);
        // Mismatched snapshots stay visible as an error, never as matching source rows.
        await page.evaluate(()=>{window.deferSources=false;window.badId=true;renderStoreProfitLoss(window.fixtureReport);});
        await page.locator('#store-profit-loss-tbody tr').first().getByRole('button',{name:'口径',exact:true}).click();
        await page.waitForSelector('.ple-dialog .ple-error');
        check((await page.locator('.ple-dialog .ple-error').innerText()).includes('不一致'),true);
        await page.evaluate(()=>{clearProfitLossContext();window.badId=false;renderStoreProfitLoss(window.fixtureReport);});
        await page.setViewportSize({width:390,height:844});
        await page.locator('#store-profit-loss-tbody tr').first().getByRole('button',{name:'口径',exact:true}).click();
        await page.waitForSelector('.ple-record');
        check(await page.locator('.ple-dialog').evaluate(d=>d.getBoundingClientRect().right<=innerWidth+1),true);
        check(await page.locator('.ple-dialog').evaluate(d=>d.scrollWidth<=d.clientWidth+1),true);
        await page.setViewportSize({width:1280,height:900});
        if(file.startsWith('templates')) await page.screenshot({path:path.join(os.tmpdir(),'fifo-profit-loss-evidence.png')});
        await page.evaluate(()=>{clearProfitLossContext();window.isAdmin=()=>false;renderStoreProfitLoss(window.fixtureReport);});
        check(await page.locator('#profit-loss-evidence-tools button').count(),0);
    } finally { await page.close(); }
}
(async()=>{
    const browser=await chromium.launch({headless:true});
    try {for(const file of ['templates/index.html','index.html']) await run(browser,file);}
    finally {await browser.close();}
    console.log(JSON.stringify({pages:2,checks,screenshot:path.join(os.tmpdir(),'fifo-profit-loss-evidence.png')}));
})().catch(e=>{console.error(e);process.exitCode=1;});
