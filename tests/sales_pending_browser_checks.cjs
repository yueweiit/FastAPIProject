// 两份真实页面，模拟待确认API；不启动main或连接业务数据库。
const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),http=require('node:http');
const {chromium}=require(process.env.INVENTORY_PLAYWRIGHT_MODULE || 'playwright');
let checks=0;const check=(actual,expected)=>{assert.deepEqual(actual,expected);checks++;};
const deferred=()=>{let resolve;return {promise:new Promise(done=>resolve=done),resolve};};
const products=[{id:1,sku:'SKU',name:'本地商品',specification:'粉色 / 厚度1.2丝',stock_quantity:10,stock_value:100,image:null,product_type:'stable',safe_stock_quantity:0},
    {id:2,sku:'SECOND',name:'本地商品',specification:'紫色 / 厚度1.2丝',stock_quantity:3,stock_value:60,image:null,product_type:'stable',safe_stock_quantity:0}];
const seed=()=>[
    {id:1,reason_code:'stock_shortage',mapping_error:'销售日可用库存不足：SKU 本行需要6件，销售日可用2件',product_name:'本地商品*6'},
    {id:2,reason_code:'product_missing',mapping_error:'未找到本地商品映射: 产品名=<商品>',product_name:'<商品>'},
    {id:3,reason_code:'expression_invalid',mapping_error:'商品名称组合格式无效: 商品+',product_name:'商品+'},
    {id:4,reason_code:'other',mapping_error:'旧版本未知原因',product_name:'旧商品'}].map(item=>({...item,source_file:'<来源文件>.xlsx',store_name:'店铺7',
        source_row_number:10+item.id,order_id:`ORDER-${item.id}`,platform_sku_id:`000${item.id}`,transaction_type:'订单',sku_name:'来源SKU',quantity:2,
        other_expense:20,currency:'CNY',requested_local_specification:'<待核实规格>',confirmed_components:[],
        mapping_identity:{kind:'platform_sku',value:`000${item.id}`},latest_mapping_version_id:null,order_created_at:'2026-09-01'}));
async function main(){
    const files={'/':'templates/index.html','/copy':'index.html'};
    for(const name of ['page-interactions','product-search','sales-ledger','sales-pending','sales-deduction-preview','sales-import-progress','inventory-workspace','inventory-assessment','inventory-allocations','financial-control'])files[`/${name}.js`]=`static/${name}.js`;
    const server=http.createServer((req,res)=>{
        const file=files[new URL(req.url,'http://unit.invalid').pathname];if(!file){res.writeHead(404);res.end();return;}
        res.setHeader('Content-Type',file.endsWith('.js')?'text/javascript; charset=utf-8':'text/html; charset=utf-8');res.end(fs.readFileSync(file));
    });await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));let browser;
    try{
        browser=await chromium.launch({headless:true,...(process.env.INVENTORY_BROWSER_CHANNEL?{channel:process.env.INVENTORY_BROWSER_CHANNEL}:{})});
        const page=await browser.newPage({viewport:{width:1440,height:1000}});let entries=seed(),role='admin',stockFixed=false,failRetry=false,gate=null,pendingStatus=200;
        const retryCalls=[],exportCalls=[],mappingCalls=[],errors=[];page.on('pageerror',error=>{if(!errors.includes(error.message))process.stderr.write(error.stack+'\n');errors.push(error.message);});
        await page.route('**/*',async route=>{
            const url=new URL(route.request().url());if(url.hostname!=='127.0.0.1')return route.abort();
            const json=(data,status=200)=>route.fulfill({status,contentType:'application/json',body:JSON.stringify(data)});
            if(url.pathname==='/auth/config')return json({local_enabled:true,sso_available:false});
            if(url.pathname==='/auth/me')return json({id:1,username:'待确认测试',role,store_id:7,csrf_token:'test-only'});
            if(url.pathname==='/finance-masters/stores')return json([{id:7,name:'店铺7',is_active:true}]);
            if(url.pathname==='/sales/product-mappings'){
                if(route.request().method()==='POST'){mappingCalls.push(route.request().postDataJSON());return json({id:13},201);}
                check(url.searchParams.get('store_id'),'7');
                return json([11,12].map((id,index)=>({id,latest_version_id:12,store_id:7,key_kind:'product_name',key_value:index?'BAG':'bag',
                    source_unit:'包',effective_from:index?'2026-09-01':'2026-01-01',is_active:true,
                    components:[{...products[0],product_id:1,multiplier:index?3:2}]})));
            }
            if(url.pathname==='/products/options')return json(products);
            if(url.pathname==='/products'){
                const word=(url.searchParams.get('keyword') || '').toLowerCase(),items=products.filter(p=>`${p.sku} ${p.name} ${url.searchParams.get('search_specification')==='true'?p.specification:''}`.toLowerCase().includes(word));
                return json({items,total:items.length,page:1,page_size:20});
            }
            if(['/sales-fields.json','/sales-fields.zh-CN.json'].includes(url.pathname))return route.fulfill({contentType:'application/json',body:fs.readFileSync('static/sales-fields.zh-CN.json','utf8')});
            if(url.pathname==='/sales/pending-confirmations'){
                if(pendingStatus!==200)return json({detail:'模拟待确认查询失败'},pendingStatus);
                const keyword=url.searchParams.get('keyword') || '',reason=url.searchParams.get('reason');
                const matching=entries.filter(item=>`${item.order_id} ${item.product_name}`.includes(keyword)),counts={};
                matching.forEach(item=>counts[item.reason_code]=(counts[item.reason_code] || 0)+1);
                const items=matching.filter(item=>!reason || item.reason_code===reason);
                return json({items,total:items.length,page:1,page_size:20,reason_counts:counts});
            }
            if(url.pathname==='/sales/pending-confirmations/preview'){
                const body=route.request().postDataJSON();
                const items=entries.filter(item=>body.entry_ids?body.entry_ids.includes(item.id):(!body.reason || item.reason_code===body.reason)&&(!body.keyword || `${item.order_id} ${item.product_name}`.includes(body.keyword)));
                return json({preview_token:'browser-preview',rows:items.map(item=>({row:item.source_row_number,order_no:item.order_id,source_sku:item.platform_sku_id,
                    product_name:item.product_name,source_quantity:item.quantity,status:body.components?'deduct':'pending',reason:null,resolution:{method:'manual'},
                    components:(body.components || []).map(c=>({...products.find(p=>p.id===c.product_id),...c,required_quantity:item.quantity*c.multiplier,deduct_quantity:item.quantity*c.multiplier,available_quantity:10}))}))});
            }
            if(url.pathname==='/sales/pending-confirmations/retry'){
                const body=route.request().postDataJSON();retryCalls.push(body);
                check(body.preview_token,'browser-preview');
                if(gate){const pending=gate;pending.started.resolve();await pending.promise;return json({detail:'old session'},401);}
                if(failRetry){failRetry=false;return json({detail:'<b>模拟失败</b>'},500);}
                const ids=body.entry_ids || entries.filter(item=>(!body.reason || item.reason_code===body.reason) &&
                    (!body.keyword || `${item.order_id} ${item.product_name}`.includes(body.keyword))).map(item=>item.id);
                const confirmed=ids.filter(id=>body.components || id===1 && stockFixed),pending=ids.length-confirmed.length;
                entries=entries.filter(item=>!confirmed.includes(item.id));
                return json({confirmed_rows:confirmed.length,pending_rows:pending,skipped_rows:0,sales_created:confirmed.length*(body.components?.length || 1),results:[]});
            }
            if(url.pathname==='/sales/pending-confirmations/export'){
                exportCalls.push(Object.fromEntries(url.searchParams));return route.fulfill({contentType:'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',body:Buffer.from('fixture-excel')});
            }
            if(url.pathname==='/sales')return json({items:[],total:0,page:1,page_size:20});
            if(url.pathname==='/batches')return json({items:[],total:0,page:1,page_size:20});
            if(['/sales/imports','/sales/monthly-summary','/finance-masters/stores'].includes(url.pathname))return json([]);
            if(files[url.pathname])return route.continue();return json([]);
        });
        for(const suffix of ['/','/copy']){
            entries=seed();role='admin';stockFixed=false;failRetry=false;
            await page.goto(`http://127.0.0.1:${server.address().port}${suffix}`);
            await page.waitForFunction(()=>CURRENT_USER_ID===1);await page.getByRole('button',{name:'销售记录',exact:true}).click();
            const ready=()=>page.waitForFunction(()=>document.getElementById('pending-confirmations-summary').textContent.includes('当前筛选共'));
            await ready();
            // 当前页保留商品缓存；刷新后的待确认组件必须尊重接口明确返回的空规格。
            check(await page.evaluate(()=>allProducts.find(product=>product.id===1).specification),products[0].specification);
            const pendingEntry=entries.find(item=>item.id===2),retryBeforeSpecs=retryCalls.length,mappingBeforeSpecs=mappingCalls.length;
            for(const [specification,expected] of [[null,''],['',''],[undefined,products[0].specification],['<最新规格>','<最新规格>']]){
                pendingEntry.confirmed_components=[{product_id:1,sku:products[0].sku,name:products[0].name,multiplier:2,
                    ...(specification===undefined?{}:{specification})}];
                await page.locator('#pending-refresh').click();await ready();
                await page.locator('[data-pending-row-action="resolve"][data-pending-id="2"]').click();
                const shownSpecs=(await page.locator('#pending-components p').allTextContents()).map(text=>text.replace(/^本地商品规格\/型号：/,'')).filter(Boolean);
                check(shownSpecs,expected?[expected]:[]);
                check(await page.locator('#pending-components').textContent().then(text=>text.includes(products[0].sku)&&text.includes(products[0].name)),true);
                check(await page.locator('[data-part-index="0"]').inputValue(),'2');
                await page.locator('#pending-cancel').click();
            }
            check(retryCalls.length,retryBeforeSpecs);check(mappingCalls.length,mappingBeforeSpecs);
            pendingEntry.confirmed_components=[];
            await page.locator('#pending-refresh').click();await ready();
            await page.evaluate(()=>{
                window.previewChoice=SalesDeductionPreview.confirm({preview_token:'recognition-preview',rows:
                    ['local_sku','product_name'].map((method,index)=>({row:index+2,order_no:`MATCH-${index}`,source_sku:index?'':'SKU*2',
                        product_name:'本地商品',source_quantity:1,status:'deduct',resolution:{method},
                        components:[{sku:'SKU',name:'本地商品',specification:'粉色',multiplier:2,required_quantity:2,available_quantity:10,deduct_quantity:2}]}))});
            });
            check(await page.locator('#sales-deduction-rows tr').evaluateAll(rows=>rows.map(row=>row.cells[4].textContent)),['本地 SKU 编码','产品名唯一匹配']);
            await page.locator('#sales-deduction-cancel').click();
            check(await page.evaluate(()=>window.previewChoice),null);
            check(await page.locator('#pending-confirmations-tbody tr').count(),4);
            check(await page.locator('#pending-reason option').count(),9);
            check(await page.locator('#pending-confirmations-tbody 商品').count(),0);
            await page.locator('#sales-import-store').selectOption('7');
            const retryBeforeMapping=retryCalls.length;
            await page.locator('#pending-mappings').click();
            await page.locator('[data-mapping-version="11"]').click();
            check(await page.locator('#pending-name-unique-label').isVisible(),true);
            check(await page.locator('#pending-save-mapping').isDisabled(),true);
            check(await page.locator('#pending-mapping-date').inputValue(),'');
            await page.locator('#pending-mapping-date').fill('2026-10-01');
            await page.locator('#pending-name-unique').check();
            await page.locator('[data-part-index="0"]').fill('4');
            await page.locator('#pending-submit').click();await page.locator('#pending-result').filter({hasText:'新版本已保存'}).waitFor();
            check(mappingCalls.at(-1),{store_id:7,key_kind:'product_name',key_value:'bag',effective_from:'2026-10-01',expected_version_id:12,
                source_unit:'包',confirm_name_unique:true,components:[{product_id:1,multiplier:4}]});
            check(retryCalls.length,retryBeforeMapping);
            await ready();
            await page.locator('[data-pending-id="2"][type="checkbox"]').check();pendingStatus=500;
            await page.locator('#pending-refresh').click();await page.locator('#pending-confirmations-tbody [role="alert"]').waitFor();
            check(await page.locator('#pending-selected').isDisabled(),true);
            check(await page.locator('[data-pending-action="filtered"]').isDisabled(),true);
            pendingStatus=200;await page.locator('#pending-refresh').click();await ready();
            await page.locator('#pending-reason').selectOption('stock_shortage');await ready();
            check(await page.locator('#pending-confirmations-tbody tr').count(),1);
            const download=page.waitForEvent('download');await page.locator('#pending-export').click();await download;
            check(exportCalls.at(-1).reason,'stock_shortage');
            const beforeCancel=retryCalls.length;
            await page.locator('[data-pending-row-action="retry"]').click();await page.locator('#pending-cancel').click();
            check(retryCalls.length,beforeCancel);
            await page.locator('[data-pending-row-action="retry"]').click();await page.locator('#pending-submit').click();
            await page.locator('#sales-deduction-cancel').click();
            check(retryCalls.length,beforeCancel);check(await page.locator('#pending-submit').isEnabled(),true);
            await page.locator('#pending-cancel').click();
            await page.locator('[data-pending-row-action="retry"]').click();await page.locator('#pending-submit').click();await page.locator('#sales-deduction-confirm').click();
            await page.locator('#pending-result').filter({hasText:'仍待确认 1 条'}).waitFor();
            check(await page.locator('#pending-confirmations-tbody tr').count(),1);
            stockFixed=true;
            await page.locator('[data-pending-id="1"][type="checkbox"]').check();
            await page.locator('#pending-selected').click();await page.locator('#pending-submit').click();await page.locator('#sales-deduction-confirm').click();
            await page.locator('#pending-result').filter({hasText:'已确认 1 条'}).waitFor();
            check(retryCalls.at(-1).entry_ids,[1]);
            await page.locator('#pending-tools [data-pending-action="clear"]').click();await ready();
            await page.locator('[data-pending-row-action="resolve"][data-pending-id="2"]').click();
            check(await page.locator('#pending-dialog-info').textContent().then(value=>value.includes('来源规格（SKU 名称）：来源SKU')),true);
            check(await page.locator('#pending-dialog-info').textContent().then(value=>value.includes('导入指定规格/型号：<待核实规格>')),true);
            check(await page.locator('#pending-dialog-info 待核实规格').count(),0);
            await page.locator('#pending-save-mapping').check();
            const input=page.locator('#pending-product');await input.fill('SKU');
            await page.locator('#pending-product-options [role="option"]').first().waitFor();
            check(await page.locator('#pending-product-options').textContent().then(value=>value.includes('粉色 / 厚度1.2丝')),true);
            await input.press('ArrowDown');await input.press('Enter');
            await page.locator('[data-part-index="0"]').fill('2');
            check(await page.locator('[data-part-units="0"]').textContent().then(value=>value.includes('所需库存数量 4')),true);
            await page.locator('[data-part-index="0"]').press('Tab');
            await input.fill('紫色');await page.locator('#pending-product-options [role="option"]').first().waitFor();await page.locator('#pending-product-options [role="option"]').first().click();
            check(await page.locator('#pending-components').textContent().then(value=>value.includes('紫色 / 厚度1.2丝')&&value.includes('粉色 / 厚度1.2丝')),true);
            check(await page.locator('#pending-components [data-remove-part]').count(),2);
            check(await page.locator('#pending-components').textContent().then(value=>value.includes('所需库存数量 4')),true);
            if(process.env.INVENTORY_QA_DIR){fs.mkdirSync(process.env.INVENTORY_QA_DIR,{recursive:true});await page.locator('#pending-retry-dialog').screenshot({path:path.join(process.env.INVENTORY_QA_DIR,`pending-${suffix==='/'?'template':'copy'}-components.png`)});}
            failRetry=true;await page.locator('#pending-submit').click();await page.locator('#sales-deduction-confirm').waitFor();
            if(process.env.INVENTORY_QA_DIR)await page.locator('#sales-deduction-preview').screenshot({path:path.join(process.env.INVENTORY_QA_DIR,`deduction-${suffix==='/'?'template':'copy'}-desktop.png`)});
            await page.setViewportSize({width:390,height:844});
            check(await page.locator('#sales-deduction-preview').evaluate(node=>{const box=node.getBoundingClientRect();return box.left>=0&&box.right<=innerWidth;}),true);
            if(process.env.INVENTORY_QA_DIR)await page.screenshot({path:path.join(process.env.INVENTORY_QA_DIR,`deduction-${suffix==='/'?'template':'copy'}-mobile.png`)});
            await page.setViewportSize({width:1440,height:1000});await page.locator('#sales-deduction-confirm').click();
            await page.locator('#pending-dialog-error').filter({hasText:'模拟失败'}).waitFor();
            check(await page.locator('#pending-dialog-error b').count(),0);
            check(await page.locator('#pending-components [data-remove-part]').count(),2);
            await page.locator('#pending-submit').click();await page.locator('#sales-deduction-confirm').click();await page.locator('#pending-result').filter({hasText:'新增销售明细 2 条'}).waitFor();
            check(retryCalls.at(-1).components,[{product_id:1,multiplier:2},{product_id:2,multiplier:1}]);
            check(retryCalls.at(-1).save_mapping.effective_from,'2026-09-01');
            check(retryCalls.at(-1).save_mapping.expected_version_id,null);
            await page.locator('#pending-keyword').fill('ORDER-3');await page.locator('#pending-keyword').press('Enter');await ready();
            check(await page.locator('#pending-confirmations-tbody tr').count(),1);
            check(await page.locator('#pending-reason option[value="expression_invalid"]').textContent().then(value=>value.includes('(1)')),true);
            await page.locator('[data-pending-action="filtered"]').click();await page.locator('#pending-submit').click();await page.locator('#sales-deduction-confirm').click();
            await page.locator('#pending-result').filter({hasText:'仍待确认 1 条'}).waitFor();
            check(retryCalls.at(-1).keyword,'ORDER-3');
            check(retryCalls.at(-1).entry_ids,undefined);
            gate={...deferred(),started:deferred()};await page.locator('[data-pending-row-action="retry"]').click();
            const before=retryCalls.length;await page.locator('#pending-submit').click();await page.locator('#sales-deduction-confirm').click();await gate.started.promise;
            await page.evaluate(()=>document.getElementById('pending-submit').click());check(retryCalls.length,before+1);
            await page.evaluate(async()=>{handleSessionExpired();await loadCurrentUser();});
            await page.getByRole('button',{name:'销售记录',exact:true}).click();await ready();
            await page.locator('[data-pending-row-action="resolve"][data-pending-id="3"]').click();
            check(await page.locator('#pending-submit').isEnabled(),true);
            gate.resolve();await page.waitForLoadState('networkidle');gate=null;
            check(await page.evaluate(()=>CURRENT_USER_ID),1);check(await page.locator('#pending-submit').isEnabled(),true);
            await page.locator('#pending-cancel').click();
            await page.setViewportSize({width:390,height:844});await page.locator('#pending-confirmations-card').scrollIntoViewIfNeeded();
            check(await page.locator('#pending-keyword').evaluate(node=>{const box=node.getBoundingClientRect();return box.left>=0&&box.right<=innerWidth;}),true);
            if(process.env.INVENTORY_QA_DIR){fs.mkdirSync(process.env.INVENTORY_QA_DIR,{recursive:true});await page.screenshot({path:path.join(process.env.INVENTORY_QA_DIR,`pending-${suffix==='/'?'template':'copy'}-mobile.png`)});}
            await page.setViewportSize({width:1440,height:1000});
            if(process.env.INVENTORY_QA_DIR)await page.locator('#pending-confirmations-card').screenshot({path:path.join(process.env.INVENTORY_QA_DIR,`pending-${suffix==='/'?'template':'copy'}-desktop.png`)});
            role='viewer';await page.evaluate(async()=>{handleSessionExpired();await loadCurrentUser();});
            await page.getByRole('button',{name:'销售记录',exact:true}).click();await ready();
            check(await page.locator('[data-pending-row-action]').count(),0);
            check(await page.locator('#pending-selected').isVisible(),false);
            check(await page.locator('#pending-mappings').isVisible(),false);
        }
        check(errors,[]);console.log(JSON.stringify({page_copies:2,checks,scope:'real pending UI; filters, manual components, batch retry, export, old401, duplicate guard, viewer/mobile; fixture APIs'}));
    }finally{if(browser)await browser.close();await new Promise(resolve=>server.close(resolve));}
}
main().catch(error=>{console.error(error);process.exitCode=1;});
