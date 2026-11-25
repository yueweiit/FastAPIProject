// Real shared editor in Chromium plus both page copies' draft-preserving refresh.
const fs = require('node:fs'), vm = require('node:vm'), assert = require('node:assert/strict');
const {chromium} = require('playwright');
let checks = 0;
const check = (actual, expected) => { assert.deepEqual(actual, expected); checks++; };
const report = {store_id:7, report_month:'2026-09', can_edit:true, supports_manual_fx:true,
    exchange_rate_settings:[{currency:'USD',mode:'auto',revision:0},{currency:'MXN',mode:'auto',revision:0}],
    exchange_rates:[{currency:'USD',report_month:'2026-09',rate_to_cny:'.5',rate_date:'2026-09-30',source:'Frankfurter/ECB'}]};
async function draftChecks(file) {
    const source=fs.readFileSync(file,'utf8');
    check(source.includes('/report-exchange-rates.js'),true);
    check(source.includes('id="report-fx-editor"'),true);
    for (const scenario of ['success','failure','switched','editing']) {
        let inputs=[{dataset:{profitKey:'salary'},value:'223'},{dataset:{profitKey:'advertising'},value:'100'},
                    {dataset:{profitRemark:'salary'},value:'保留备注'}], resolve, reject;
        const warnings={innerHTML:'',hidden:true};
        const data={store_id:7,report_month:'2026-09',warnings:['existing warning'],rows:[{key:'salary',manual_value:{current:'123'},remark:''},
                    {key:'advertising',manual_value:{current:'100'},remark:''}]};
        const context=vm.createContext({URLSearchParams,document:{querySelectorAll:()=>inputs,getElementById:()=>warnings},
            window:{PageInteractions:{text:()=> 'saved but refresh failed'}},escapeHtml:s=>s,updateProfitLossSaveState:()=>{},
            compactProfitLossWarnings:items=>[...(items||[])],
            api:()=>new Promise((yes,no)=>{resolve=yes;reject=no;}),profitLossSelectionMatches:()=>true,
            renderStoreProfitLoss:()=>{inputs=[{dataset:{profitKey:'salary'},value:'123'},
                {dataset:{profitKey:'advertising'},value:'200'},{dataset:{profitRemark:'salary'},value:''}];},data});
        vm.runInContext('let storeProfitLossReport=data,storeProfitLossRequestId=1,storeProfitLossLoading=false;',context);
        const fn=source.match(/^async function reloadProfitLossPreservingDraft\([^\n]*\) \{[\s\S]*?^\}/m);
        assert(fn);vm.runInContext(fn[0],context);
        const pending=vm.runInContext('reloadProfitLossPreservingDraft()',context);
        if(scenario==='failure') {
            reject(new Error('offline'));await assert.rejects(pending,/saved but refresh failed/);
            check(inputs[0].value,'223');check(warnings.hidden,false);
            check(warnings.innerHTML.includes('existing warning'),true);
            check(vm.runInContext('storeProfitLossLoading',context),false);
        } else {
            if(scenario==='switched') vm.runInContext('storeProfitLossRequestId++; storeProfitLossReport=null;',context);
            if(scenario==='editing') inputs[0].value='333';
            resolve({...data,rows:[]}); await pending;
            if(scenario==='switched') check(vm.runInContext('storeProfitLossReport',context),null);
            else {check(inputs.map(i=>i.value),[scenario==='editing'?'333':'223','200','保留备注']);check(vm.runInContext('storeProfitLossLoading',context),false);}
        }
    }
}
async function reloadDuringSaveChecks(browser, file) {
    const source = fs.readFileSync(file, 'utf8');
    const names = ['api', 'defaultProfitLossMonth', 'profitLossSelectionMatches', 'profitLossPolicyReady',
        'updateProfitLossSaveState', 'resetStoreProfitLossState', 'clearProfitLossContext', 'compactProfitLossWarnings',
        'formatProfitLossAmount', 'salaryBreakdownText', 'renderStoreProfitLoss', 'reloadProfitLossPreservingDraft',
        'loadStoreProfitLossStores', 'loadStoreProfitLoss', 'openReportView'];
    const code = names.map(name => {
        const found = source.match(new RegExp('^(?:async )?function ' + name + '\\([^\\n]*\\) \\{[\\s\\S]*?^\\}', 'm'));
        assert(found, `${file}: ${name}`); return found[0];
    }).join('\n');
    for (const scenario of ['manual', 'restore', 'failure', 'logout', 'canonical']) {
        const page = await browser.newPage();
        try {
            // Actual DOM, API wrapper and page functions, with only HTTP/auth isolated.
            await page.setContent(source.replace(/<script\b[^>]*>[\s\S]*?<\/script>/gi, ''));
            await page.addScriptTag({path:'static/page-interactions.js'});
            await page.addScriptTag({path:'static/report-exchange-rates.js'});
            await page.addScriptTag({content:`
                let storeProfitLossReport=null,storeProfitLossRequestId=1,storeProfitLossLoading=false,storeProfitLossSavePromise=null;
                const API='',isAdmin=()=>false,authHeaders=()=>({}),escapeHtml=x=>String(x??''),
                    formatApiError=detail=>String(detail),showMsg=()=>{},handleSessionExpired=()=>{},
                    loadInventoryImpairment=()=>{},loadProductLineProfit=()=>{};
                ${code}
            `});
            await page.evaluate(({scenario, fixture}) => {
                const initialManual = scenario === 'restore' || scenario === 'canonical';
                const rate = scenario === 'canonical' ? '0.3711' : initialManual ? '0.5' : '0.4';
                window.serverReport = {...fixture, store_name:'Test', period_label:'2026-09',
                    calculation_policy:'order_created_fx_mxn8_income_only_v3', rows:[], warnings:[],
                    exchange_rate_settings:[{currency:'USD', mode:initialManual?'manual':'auto',
                        manual_rate:initialManual?rate:null, revision:initialManual?1:0}],
                    exchange_rates:[{currency:'USD', report_month:'2026-09', rate_to_cny:rate, source:'fixture'}]};
                window.requests=[];
                window.fetch = async (url, options) => {
                    const body=options.body ? JSON.parse(options.body) : null;
                    requests.push({method:options.method, url, body});
                    if(options.method==='PUT') return new Promise(resolve => {
                        window.finishWrite = () => {
                            if(scenario==='failure') {
                                resolve(new Response(JSON.stringify({detail:'fixture failure'}),{status:500}));return;
                            }
                            const setting={currency:body.currency, mode:body.mode, manual_rate:body.manual_rate,
                                revision:body.expected_revision+1, note:body.note};
                            serverReport.exchange_rate_settings=[setting];
                            serverReport.exchange_rates=[{currency:'USD',report_month:'2026-09',mode:body.mode,
                                rate_to_cny:body.mode==='manual'?body.manual_rate:'0.4',source:body.mode==='manual'?'manual':'fixture'}];
                            resolve(new Response(JSON.stringify({setting}),{status:200}));
                        };
                    });
                    return new Response(JSON.stringify(serverReport),{status:200});
                };
                document.getElementById('profit-loss-month').value='2026-09';
            }, {scenario, fixture:report});
            await page.addScriptTag({content:'storeProfitLossReport=structuredClone(serverReport);renderStoreProfitLoss(storeProfitLossReport);'});
            await page.evaluate(scenario => {
                if(scenario==='canonical') document.querySelector('[data-fx-note]').value='只修改说明';
                else document.querySelector('[data-fx-rate]').value='0.5';
                document.querySelector(scenario==='restore'?'[data-fx-restore]':'[data-fx-save]').click();
            },scenario);
            await page.waitForFunction(()=>Boolean(window.finishWrite));
            if(scenario==='canonical') check(await page.evaluate(()=>requests[0].body.manual_rate),'0.3711');
            // The sidebar remains usable while the ordinary query controls are disabled.
            await page.evaluate(()=>{
                const tab=document.querySelector('[data-report-view="profit-loss"]');tab.click();tab.click();
            });
            check(await page.evaluate(()=>requests.filter(r=>r.method==='GET').length),0);
            if(scenario==='logout') await page.evaluate(()=>resetStoreProfitLossState());
            await page.evaluate(()=>finishWrite());
            await page.evaluate(()=>window.ReportExchangeRates.waitForPendingSaves());
            await page.waitForFunction(()=>!storeProfitLossLoading);
            const result = await page.evaluate(()=>({
                reads:requests.filter(r=>r.method==='GET').length,
                setting:storeProfitLossReport?.exchange_rate_settings[0]||null,
                rate:storeProfitLossReport?.exchange_rates[0].rate_to_cny||null,
                currentText:document.querySelector('[data-fx-effective]')?.textContent||'',
                busy:window.ReportExchangeRates.isBusy(),
            }));
            check(result.busy,false);
            if(scenario==='logout') {check(result.reads,0);check(result.setting,null);}
            else {
                check(result.reads,1);
                check(result.setting.mode,scenario==='restore'||scenario==='failure'?'auto':'manual');
                check(result.setting.revision,scenario==='failure'?0:scenario==='restore'||scenario==='canonical'?2:1);
                const expected=scenario==='canonical'?'0.3711':scenario==='manual'?'0.5':'0.4';
                check(result.rate,expected);check(result.currentText.includes(expected),true);
                if(scenario==='canonical') check(result.setting.note,'只修改说明');
            }
        } finally {await page.close();}
    }
}
async function main() {
    for(const file of ['templates/index.html','index.html']) await draftChecks(file);
    const browser=await chromium.launch({headless:true});
    try {
        for(const file of ['templates/index.html','index.html']) await reloadDuringSaveChecks(browser,file);
        const page=await browser.newPage({viewport:{width:1100,height:850}});
        const errors=[];page.on('pageerror',e=>errors.push(e.message));
        const css=fs.readFileSync('templates/index.html','utf8').match(/<style>([\s\S]*?)<\/style>/)[1];
        await page.setContent(`<html lang="zh-CN"><head><style>${css}</style></head><body><main style="max-width:1000px;margin:16px"><div id="report-fx-editor"></div></main></body></html>`);
        await page.addScriptTag({path:'static/page-interactions.js'});
        await page.addScriptTag({path:'static/report-exchange-rates.js'});
        await page.evaluate(data=>{
            window.requests=[];window.refreshes=0;window.notices=[];window.canStart=true;
            window.renderFx=(value=data)=>window.ReportExchangeRates.render(value,{
                api:(method,url,body,options)=>new Promise((resolve,reject)=>window.requests.push({method,url,body,options,resolve,reject})),
                onBusy:()=>{},isCurrent:()=>true,canStart:()=>window.canStart,
                notify:msg=>window.notices.push(msg),onSaved:async()=>{window.refreshes++;},
            });
            window.renderFx();
        },report);
        await page.locator('summary').click();
        for(const invalid of ['0','-1','NaN','0.123456789']) {
            await page.locator('[data-fx-rate]').fill(invalid);
            await page.locator('[data-fx-save]').click();
            check(await page.evaluate(()=>requests.length),0);
        }
        await page.locator('[data-fx-rate]').fill('0.37110000');
        await page.locator('[data-fx-note]').fill('<img src=x onerror="window.pwned=1">');
        await page.evaluate(()=>{window.canStart=false;});
        await page.locator('[data-fx-save]').click();check(await page.evaluate(()=>requests.length),0);
        await page.evaluate(()=>{window.canStart=true;});
        await page.locator('[data-fx-save]').click();
        check(await page.locator('[data-fx-save]').isDisabled(),true);
        check(await page.evaluate(()=>requests.length),1);
        check(await page.evaluate(()=>requests[0].options.isCurrent()),true);
        const body=await page.evaluate(()=>requests[0].body);
        check(body.manual_rate,'0.37110000');check(body.expected_revision,0);check(body.store_id,7);check(body.report_month,'2026-09-01');
        await page.evaluate(()=>requests[0].resolve({setting:{currency:'USD',mode:'manual',manual_rate:'.3711',revision:1}}));
        await page.waitForFunction(()=>refreshes===1);
        await page.locator('[data-fx-restore]').click();
        check(await page.evaluate(()=>requests[1].body.mode),'auto');
        check(await page.evaluate(()=>requests[1].body.manual_rate),null);
        check(await page.evaluate(()=>requests[1].body.expected_revision),1);
        await page.evaluate(()=>requests[1].resolve({setting:{currency:'USD',mode:'auto',manual_rate:null,revision:2}}));
        await page.waitForFunction(()=>refreshes===2);
        await page.locator('[data-fx-reference]').click();
        await page.locator('[data-fx-currency]').selectOption('MXN');
        check(await page.evaluate(()=>requests[2].options.isCurrent()),false);
        await page.evaluate(()=>requests[2].resolve({currency:'USD',rate_to_cny:'.99',rate_date:'2026-09-30',source:'wrong old currency'}));
        await page.waitForTimeout(10);
        check((await page.locator('[data-fx-reference-value]').textContent()).includes('wrong old currency'),false);
        await page.locator('[data-fx-rate]').fill('.4');
        await page.locator('[data-fx-save]').click();
        await page.evaluate(()=>requests[3].reject(new Error('conflict: refresh first')));
        await page.waitForFunction(()=>!window.ReportExchangeRates.isBusy());
        check(await page.locator('[data-fx-rate]').inputValue(),'.4');
        check((await page.locator('[data-fx-message]').textContent()).includes('conflict'),true);
        await page.locator('[data-fx-save]').click();
        await page.evaluate(data=>{window.ReportExchangeRates.clear();window.renderFx(data);requests[4].resolve({setting:{currency:'MXN',mode:'manual',revision:1}});},report);
        check(await page.evaluate(()=>requests[4].options.isCurrent()),false);
        await page.waitForTimeout(10);check(await page.evaluate(()=>refreshes),2);check(await page.evaluate(()=>notices.length),2);
        await page.evaluate(data=>window.renderFx({...data,can_edit:false}),report);
        check(await page.locator('#report-fx-editor').isHidden(),true);
        await page.evaluate(data=>window.renderFx({...data,supports_manual_fx:false}),report);
        check(await page.locator('#report-fx-editor').isHidden(),true);
        await page.evaluate(data=>window.renderFx({...data,exchange_rate_settings:[{currency:'USD',mode:'manual',manual_rate:'.4',revision:1}],
            exchange_rates:[{currency:'USD',report_month:'2026-09',rate_to_cny:'.4',mode:'manual',is_provisional:true}]}),report);
        await page.locator('summary').click();
        check((await page.locator('[data-fx-effective]').textContent()).includes('暂估'),true);
        await page.setViewportSize({width:375,height:800});
        check(await page.evaluate(()=>document.documentElement.scrollWidth<=window.innerWidth),true);
        check(await page.evaluate(()=>Boolean(window.pwned)),false);check(errors,[]);
        console.log(JSON.stringify({page_copies:2,checks,scope:'Chromium editor, validation, stale requests, permissions and expense drafts'}));
    } finally {await browser.close();}
}
main().catch(error=>{console.error(error);process.exitCode=1;});
