// 两份真实页面加载函数 + 共享依据抽屉，Chromium只使用内存HTTP替身。
const fs = require('node:fs'), path = require('node:path'), os = require('node:os');
const assert = require('node:assert/strict');
const {chromium} = require('playwright');
let checks = 0;
const check = (a, b) => {assert.deepEqual(a, b); checks++;};
const hostile = '$& <img src=x onerror="window.unsafe=1">';
async function install(page, file) {
    const html = fs.readFileSync(file, 'utf8');
    await page.setContent(html.replace(/<script\b[^>]*>[\s\S]*?<\/script>/gi, ''));
    await page.addScriptTag({path:'static/page-interactions.js'});
    await page.addScriptTag({path:'static/profit-loss-evidence.js'});
    await page.evaluate(hostile => {
        document.getElementById('login-overlay').style.display = 'none';
        document.querySelectorAll('.page').forEach(n => n.style.display='none');
        for (const id of ['report-pane-inventory', 'report-pane-product-line']) {
            const node = document.getElementById(id); document.body.append(node); node.style.display='block';
        }
        window.CURRENT_USER_ID = 1; window.inventoryImpairmentRequestId = 0; window.productLineProfitRequestId = 0;
        window.isAdmin = () => true;
        document.getElementById('profit-loss-month').value='2026-09';
        document.getElementById('profit-loss-store').innerHTML='<option value="7">店铺7</option><option value="8">店铺8</option>';
        document.getElementById('inventory-impairment-date').value='2026-09-30';
        document.getElementById('inventory-report-view').value='batch';
        window.fixture = {name:'P10 · '+hostile, group_by:'product', store_name:'店铺7', sku:'P10', product_name:hostile,
            batch_no:'B1', arrived_at:'2026-09-01', age_days:29, unit_cost:10, original_quantity:100,
            original_inventory_amount:1000, sold_quantity:40, sold_cost_amount:400, quantity:60, provision_quantity:60,
            inventory_amount:600, impairment_rate:.15, impairment_amount:90, monthly_impairment_amount:90,
            opening_impairment_amount:0, monthly_total_impairment_amount:125, monthly_writeoff_amount:35,
            monthly_transfer_in_amount:0, monthly_transfer_out_amount:0, book_value:510, book_value_difference:-490,
            current_quantity:40, current_revenue:100, current_cost:60, current_gross:40, current_margin:.4,
            previous_margin:null, margin_change:null, ytd_revenue:100, ytd_gross:40, ytd_margin:.4,
            evidence:{schema_version:'report_row_evidence_v1', token:'token', row_key:'1', calculation_id:'same', total_calculation_id:'total'}};
        const field = (label, value, format='money', inputs=[]) => ({label, value, format, inputs, status:'complete',
            note:'保存的核算口径 '+hostile, formula:inputs.length?'本月毛利 ÷ 本月收入':'来源参与值相加', source_total:value,source_count:51});
        window.evidenceData = {schema_version:'report_row_evidence_v1', row_key:'1', calculation_id:'same', title:fixture.name,
            context:'2026-09-01', warning:'', status_labels:{complete:'依据金额一致'}, rounding:'先计算后显示，保留精度',
            fields:{current_quantity:field('本月销售数量','40','number'), current_revenue:field('本月收入','100'),
                current_margin:field('本月毛利率','.4','percent',[
                    {key:'current_gross',label:'本月毛利',value:'40',operator:'+',format:'money'},
                    {key:'current_revenue',label:'本月收入',value:'100',operator:'÷',format:'money'}]),
                current_gross:field('本月毛利','40'), previous_margin:field('上月毛利率',null,'percent')}};
        window.calls=[];window.guards=[];
        window.api = async (_method, url, _body, options) => {
            calls.push(url); guards.push(options);
            if (!url.includes('/row-evidence/')) {
                if (window.deferReport) return new Promise(resolve => window.finishReport=resolve);
                return window.legacy ? [{...fixture,evidence:null}] : [fixture];
            }
            if (window.deferSources) return new Promise(resolve => window.finishSource=resolve);
            if (window.sourceError) throw new Error('查询依据已过期 <img>');
            const p = new URL(url,'http://fixture').searchParams;
            const field = p.get('field') || 'current_quantity';
            const sourceItems = window.sourceItemsByField?.[field];
            return {...evidenceData, calculation_id:window.badId?'bad':p.get('row_key')==='__total__'?'total':'same',
                row_key:p.get('row_key'), field, page:Number(p.get('page')), total:sourceItems?.length ?? 51,
                items:sourceItems ?? Array.from({length:p.get('page')==='1'?50:1},(_,i)=>({day:'2026-09-01',amount:'2',source:'来源',
                    reference:p.get('page')==='1'?String(i+1):'51', details:{原始文件:hostile}}))};
        };
    }, hostile);
    const names = ['escapeHtml', 'reportMoney', 'reportPercent', 'defaultProfitLossMonth', 'inventoryComparisonCell',
        'inventoryReportTotals', 'loadInventoryImpairment', 'loadProductLineProfit'];
    const code = names.map(name => {
        const match=html.match(new RegExp('^(?:async )?function '+name+'\\([^\\n]*\\) \\{[\\s\\S]*?^\\}', 'm'));
        assert(match,name);return match[0];
    }).join('\n');
    await page.addScriptTag({content:code});
}
async function run(browser, file) {
    const page = await browser.newPage({viewport:{width:1440,height:960}});
    try {
        await install(page,file);
        for (const [fn, body, kind] of [['loadInventoryImpairment','inventory-impairment-tbody','inventory'],
                                      ['loadProductLineProfit','product-line-profit-tbody','product']]) {
            await page.evaluate(fn => window[fn](), fn);
            check(await page.locator(`#${body} [data-ple-row-added]`).count(),1);
            check(await page.evaluate(()=>calls.at(-1).includes('include_evidence=true')),true);
            await page.locator(`#${body} [data-ple-row-added]`).click();
            await page.waitForSelector('.ple-record');
            check(await page.locator('.ple-value').innerText(),'40');
            check(await page.locator('.ple-record').count(),50);
            check(await page.locator('.ple-dialog img').count(),0);
            check(await page.evaluate(()=>window.unsafe),undefined);
            check((await page.locator('.ple-note').innerText()).includes(hostile),true);
            check(await page.evaluate(()=>guards.at(-1).cache),'no-store');
            await page.getByRole('button',{name:'下一页',exact:true}).click();
            await page.waitForFunction(()=>document.querySelectorAll('.ple-record').length===1);
            check((await page.locator('.ple-record strong').innerText()).includes('51'),true);
            await page.locator('[data-ple-row-field]').selectOption('current_margin');
            await page.waitForFunction(()=>document.querySelector('.ple-value')?.textContent==='40.00%');
            check((await page.locator('.ple-calculation').innerText()).includes('本月收入 (100)'),true);
            await page.locator('[data-ple-row-input="current_revenue"]').click();
            await page.waitForFunction(()=>document.querySelector('.ple-value')?.textContent.includes('100.00'));
            await page.locator('[data-ple-row-field]').selectOption('previous_margin');
            await page.waitForFunction(()=>document.querySelector('.ple-value')?.textContent==='—');
            check(await page.locator('.ple-value').innerText(),'—');
            await page.evaluate(kind=>ProfitLossEvidence.clearRows(kind),kind);
            check(await page.locator('.ple-dialog').evaluate(n=>n.open),false);
            await page.evaluate(fn => window[fn](),fn);
            await page.evaluate(()=>window.badId=true);
            await page.locator(`#${body} [data-ple-row-added]`).click();
            await page.waitForSelector('.ple-error');
            check((await page.locator('.ple-error').innerText()).includes('不一致'),true);
            await page.evaluate(()=>{window.badId=false;window.sourceError=true;});
            await page.getByRole('button',{name:'关闭',exact:true}).click();
            await page.locator(`#${body} [data-ple-row-added]`).click();
            await page.waitForSelector('.ple-error');
            check((await page.locator('.ple-error').innerText()).includes('已过期'),true);
            check(await page.locator('.ple-error img').count(),0);
            await page.evaluate(()=>{window.sourceError=false;window.deferSources=true;});
            await page.getByRole('button',{name:'关闭',exact:true}).click();
            await page.locator(`#${body} [data-ple-row-added]`).click();
            await page.waitForFunction(()=>typeof finishSource==='function');
            await page.evaluate(fn=>window[fn](),fn);
            check(await page.evaluate(()=>guards.at(-2).isCurrent()),false);
            await page.evaluate(()=>{finishSource({...evidenceData,field:'current_revenue',total:0,items:[]});window.deferSources=false;delete window.finishSource;});
            check(await page.locator('.ple-dialog').evaluate(n=>n.open),false);
            await page.evaluate(()=>window.legacy=true);
            await page.evaluate(fn=>window[fn](),fn);
            await page.locator(`#${body} [data-ple-row-added]`).click();
            check((await page.locator('.ple-note').innerText()).includes('未保存完整'),true);
            await page.getByRole('button',{name:'关闭',exact:true}).click();
            await page.evaluate(()=>window.legacy=false);
        }
        await page.evaluate(()=>{window.deferReport=true;window.loadProductLineProfit();});
        await page.waitForFunction(()=>typeof finishReport==='function');
        await page.evaluate(()=>{window.CURRENT_USER_ID=2;finishReport([fixture]);window.deferReport=false;});
        check(await page.locator('#product-line-profit-tbody [data-ple-row-added]').count(),0);
        await page.evaluate(()=>window.loadProductLineProfit());
        await page.locator('#product-line-profit-tbody [data-ple-row-added]').click();
        await page.waitForSelector('.ple-record');
        await page.locator('[data-ple-row-field]').selectOption('current_margin');
        await page.waitForFunction(()=>document.querySelector('.ple-value')?.textContent==='40.00%');
        if (file.startsWith('templates')) await page.screenshot({path:path.join(os.tmpdir(),'fifo-07-12-evidence.png')});
        await page.locator('#profit-loss-store').selectOption('8', {force:true});
        check(await page.locator('.ple-dialog').evaluate(n=>n.open),false);
        check(await page.locator('#product-line-profit-tbody [data-ple-row-added]').count(),0);
        await page.evaluate(async () => {
            fixture.book_value = null; fixture.impairment_amount = null;
            evidenceData.status_labels.unknown = '待核对';
            evidenceData.fields.book_value = {label:'期末账面价值', value:null, format:'money', status:'unknown',
                inputs:[], note:'历史流水待核对', formula:'期末库存成本减期末准备', source_total:null, source_count:0};
            window.sourceItemsByField = {book_value:[]};
            await loadInventoryImpairment();
        });
        check(await page.locator('#inventory-impairment-tbody tr').first().locator('td').nth(20).innerText(),'-');
        await page.locator('#inventory-impairment-tbody [data-ple-row-added]').click();
        await page.locator('[data-ple-row-field]').selectOption('book_value');
        await page.waitForFunction(()=>document.querySelector('.ple-value')?.textContent==='—');
        check(await page.locator('.ple-record').count(),0);
        check((await page.locator('.ple-dialog').innerText()).includes('来源未舍入合计：待核对/未提供'),true);
        await page.getByRole('button',{name:'关闭',exact:true}).click();
        await page.evaluate(async () => {
            fixture.current_cost = 55.69411764705882;
            evidenceData.fields.current_cost = {label:'本月成本',value:'55.69411764705882',format:'money',status:'complete',
                inputs:[],note:'成本包含生效的正负费用',formula:'来源参与值相加',source_total:'55.69411764705882',source_count:1};
            sourceItemsByField.current_cost = [{day:'',amount:'0',source:'店铺人工直接收入/费用分摊',reference:'7',
                details:{参与科目:{customs_import_tax:'100',local_logistics_cost:'-100'},待分摊合计:'0',
                    生效凭据:[{reference:'TAX-POSTED',amount:'100'},{reference:'TAX-REVERSAL',amount:'-100'}]}}];
            await loadProductLineProfit();
        });
        const cost = await page.locator('#product-line-profit-tbody tr').first().locator('td').nth(3).innerText();
        await page.locator('#product-line-profit-tbody [data-ple-row-added]').click();
        await page.locator('[data-ple-row-field]').selectOption('current_cost');
        await page.waitForFunction(()=>document.querySelectorAll('.ple-record').length===1);
        check((await page.locator('.ple-value').innerText()).replace(/[^\d.-]/g,''),cost.replace(/[^\d.-]/g,''));
        const offset = await page.locator('.ple-record').innerText();
        for (const text of ['参与值：0','TAX-POSTED','TAX-REVERSAL','100','-100']) check(offset.includes(text),true);
    } finally {await page.close();}
}
(async()=>{const browser=await chromium.launch({headless:true});try {
    for(const file of ['templates/index.html','index.html']) await run(browser,file);
    console.log(JSON.stringify({pages:2,checks,screenshot:path.join(os.tmpdir(),'fifo-07-12-evidence.png')}));
} finally {await browser.close();}})().catch(error=>{console.error(error);process.exitCode=1;});
