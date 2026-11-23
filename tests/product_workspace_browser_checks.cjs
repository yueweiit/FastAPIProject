// 真实Chrome、实际两份首页和模拟API。仅用Node内置模块，不启动main或连接业务数据库。
// FIFO_TEST_CHROME可指定已安装浏览器；INVENTORY_QA_DIR可指定截图目录。
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const http = require('node:http');
const {spawn} = require('node:child_process');
const out = process.env.INVENTORY_QA_DIR || path.resolve('output/product-workspace');
fs.mkdirSync(out, {recursive:true});
const png = Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jQ1sAAAAASUVORK5CYII=', 'base64');
const product = (id, sku, name, specification = '黑色 / XL') => ({id,sku,name,specification,
    product_type:'stable',safe_stock_quantity:0,image:'/uploads/product-fixture.png',stock_quantity:100,
    stock_value:1000,last_batch_at:'2026-11-20 10:00:00'});
const initial = () => [
    {...product(1,'EXACT','精确SKU商品',null),product_type:'safe_stock',safe_stock_quantity:40},
    ...Array.from({length:25},(_,i)=>product(100+i,`EXACT-${i+1}`,`相似商品${i+1}`)),
    ...Array.from({length:30},(_,i)=>({...product(200+i,`DOG-${i+1}`,`狗狗项圈${i+1}`,i%2 ? '蓝色 / M' : null),image:i%3 ? '/uploads/product-fixture.png' : null})),
    product(999,'A&B /?+%_#','<img src=x onerror="window.catalogInjected=true">','<b>特殊规格</b>')
];
let catalog = initial(), calls = [], mutations = [], exportRequests = [], inventoryCalls = [], importCalls = 0;
let listGate, saveGate, inventoryGate, failNextList = false;
const deferred = () => {let resolve;return {promise:new Promise(done=>{resolve=done;}),resolve:()=>resolve(),started:false};};
function pageData(url) {
    const keyword=(url.searchParams.get('keyword')||'').toLowerCase();
    const completeness=url.searchParams.get('completeness')||'all';
    let items=catalog.filter(p=>`${p.sku} ${p.name} ${url.searchParams.get('search_specification')==='true' ? p.specification||'' : ''}`.toLowerCase().includes(keyword));
    if(completeness==='missing_specification')items=items.filter(p=>!p.specification?.trim());
    if(completeness==='missing_image')items=items.filter(p=>!p.image);
    items.sort((a,b)=>(url.searchParams.get('prioritize_exact_sku')==='true' ? Number(b.sku.toLowerCase()===keyword)-Number(a.sku.toLowerCase()===keyword) : 0)||b.id-a.id);
    const page_size=Number(url.searchParams.get('page_size')||20), page=Math.min(Number(url.searchParams.get('page')||1),Math.max(1,Math.ceil(items.length/page_size)));
    return {items:items.slice((page-1)*page_size,page*page_size),page,page_size,total:items.length};
}
const server=http.createServer(async(req,res)=>{
    const url=new URL(req.url,'http://unit.invalid');
    const json=(value,status=200)=>{res.writeHead(status,{'Content-Type':'application/json'});res.end(JSON.stringify(value));};
    const excel=()=>{res.writeHead(200,{'Content-Type':'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet','Content-Disposition':'attachment; filename="fixture.xlsx"'});res.end('PK fixture');};
    try {
        if(url.pathname==='/auth/config')return json({local_enabled:true,sso_available:false});
        if(url.pathname==='/auth/me')return json({id:10,username:'商品测试',role:'viewer',csrf_token:'fixture-csrf'});
        if(url.pathname==='/products/options')return json(catalog);
        if(url.pathname==='/products/export'){exportRequests.push(url);return excel();}
        if(url.pathname==='/products/import-template')return excel();
        if(url.pathname==='/products/import/preview' || url.pathname==='/products/import') {
            importCalls++;
            if(url.pathname==='/products/import')catalog.push(product(1500,'IMPORTED','导入商品','白色 / L'));
            return json({total_rows:1,new_count:1,skipped_count:0,error_count:0,created_count:url.pathname.endsWith('/import')?1:0,
                can_import:true,preview_token:'fixture-token',message:'导入完成',rows:[{row_number:2,sku:'IMPORTED',name:'导入商品',specification:'白色 / L',product_type:'stable',safe_stock_quantity:0,status:'new',message:'待新增',errors:[]}]});
        }
        if(url.pathname==='/products/import/result')return excel();
        if(url.pathname==='/products' && req.method==='GET') {
            calls.push(url);
            if(failNextList){failNextList=false;return json({detail:'模拟刷新失败'},503);}
            const keyword=url.searchParams.get('keyword');
            if(keyword==='slow' && listGate){const gate=listGate;gate.started=true;await gate.promise;return gate.expired?json({detail:'old session'},401):json(pageData(new URL('http://unit.invalid/products?keyword=狗狗')));}
            if(keyword==='error')return json({detail:'<b>模拟加载失败</b>'},500);
            if(keyword==='expired')return json({detail:'expired'},401);
            return json(pageData(url));
        }
        if((url.pathname==='/products' && req.method==='POST') || /^\/products\/\d+$/.test(url.pathname)) {
            if(req.method==='DELETE') {catalog=catalog.filter(p=>p.id!==Number(url.pathname.split('/').pop()));return json({ok:true});}
            const chunks=[];for await(const chunk of req)chunks.push(chunk);
            const form=await new Response(Buffer.concat(chunks),{headers:{'Content-Type':req.headers['content-type']}}).formData();
            const data=Object.fromEntries(form);mutations.push({url:url.pathname,method:req.method,data,csrf:req.headers['x-csrf-token']});
            if(data.sku==='ERROR')return json({detail:'模拟保存失败'},422);
            if(data.sku==='SLOW' && saveGate){const gate=saveGate;gate.started=true;await gate.promise;}
            if(data.sku==='REFRESH-FAIL')failNextList=true;
            const id=req.method==='POST'?Math.max(...catalog.map(p=>p.id))+1:Number(url.pathname.split('/').pop());
            const previous=catalog.find(p=>p.id===id)||product(id,data.sku,data.name);
            const saved={...previous,...data,id,safe_stock_quantity:Number(data.safe_stock_quantity),image:data.image?'/uploads/new-fixture.png':previous.image};
            catalog=catalog.filter(p=>p.id!==id);catalog.push(saved);return json(saved);
        }
        if(['/inventory/overview','/inventory/daily'].includes(url.pathname)) {
            if(inventoryGate && !inventoryGate.started){const gate=inventoryGate;gate.started=true;await gate.promise;if(gate.expired!==false)return json({detail:'old inventory session'},401);}
            inventoryCalls.push(url);const id=Number(url.searchParams.get('product_id'));
            const keyword=(url.searchParams.get('keyword')||'').toLowerCase();
            const matches=catalog.filter(p=>(!id||p.id===id)&&`${p.sku} ${p.name}`.toLowerCase().includes(keyword));
            const page_size=Number(url.searchParams.get('page_size')||20),page=Math.min(Number(url.searchParams.get('page')||1),Math.max(1,Math.ceil(matches.length/page_size)));
            const items=matches.slice((page-1)*page_size,page*page_size).map(p=>({...p,stock_status:'normal'}));
            if(url.pathname==='/inventory/daily') {
                const month=url.searchParams.get('month').slice(0,7);
                return json({items:items.map(p=>({...p,product_id:p.id,days:[1,2].map(day=>({date:`${month}-0${day}`,
                    opening_quantity:null,inbound:0,outbound:0,adjustment:null,closing_quantity:null}))})),
                    total:matches.length,page,page_size,start:`${month}-01`,end:`${month}-02`,scope:'restricted',warnings:[]});
            }
            return json({items,total:matches.length,page,page_size,scope:'shared',summary:{quantity:100,inventory_amount:1000,low_count:0,zero_count:0}});
        }
        if(url.pathname==='/inventory/export'){exportRequests.push(url);return excel();}
        if(url.pathname.startsWith('/uploads/')){res.writeHead(200,{'Content-Type':'image/png'});res.end(png);return;}
        const file=url.pathname==='/'?'templates/index.html':url.pathname==='/copy'?'index.html':/^\/[a-zA-Z0-9_.-]+\.(js|json)$/.test(url.pathname)?'static'+url.pathname:null;
        if(file && fs.existsSync(file)){res.writeHead(200,{'Content-Type':file.endsWith('.js')?'text/javascript; charset=utf-8':file.endsWith('.json')?'application/json':'text/html; charset=utf-8'});res.end(fs.readFileSync(file));return;}
        return json([]);
    } catch(error) {json({detail:error.message},500);}
});
let checks=0;
function check(actual, expected, message) {assert.deepEqual(actual,expected,message);checks++;}
async function until(fn,message='browser readiness') {for(let i=0;i<200;i++){if(await fn())return;await new Promise(resolve=>setTimeout(resolve,25));}throw new Error('Timed out: '+message);}
async function main() {
    await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));
    const profile=fs.mkdtempSync(path.join(out,'catalog-chrome-'));
    const browser=spawn(process.env.FIFO_TEST_CHROME || 'C:/Program Files/Google/Chrome/Application/chrome.exe',['--headless=new','--no-first-run','--no-default-browser-check','--disable-background-networking',`--user-data-dir=${profile}`,'--remote-debugging-port=0','about:blank'],{windowsHide:true});
    let socket,send;
    try {
        const address=await new Promise((resolve,reject)=>{let output='';const timer=setTimeout(()=>reject(new Error('Chrome startup timeout')),15000);browser.on('error',reject);browser.stderr.on('data',data=>{output+=data;const match=output.match(/DevTools listening on (ws:\/\/[^\s]+)/);if(match){clearTimeout(timer);resolve(match[1]);}});});
        socket=new WebSocket(address);const pending=new Map(),errors=[];let sequence=0;
        socket.addEventListener('message',event=>{const data=JSON.parse(event.data);if(data.method==='Runtime.exceptionThrown')errors.push(data.params.exceptionDetails.exception?.description||data.params.exceptionDetails.text);const callback=pending.get(data.id);if(callback){pending.delete(data.id);data.error?callback.reject(new Error(JSON.stringify(data.error))):callback.resolve(data.result);}});
        await new Promise((resolve,reject)=>{socket.addEventListener('open',resolve,{once:true});socket.addEventListener('error',reject,{once:true});});
        send=(method,params={},sessionId)=>new Promise((resolve,reject)=>{const id=++sequence;pending.set(id,{resolve,reject});socket.send(JSON.stringify({id,method,params,sessionId}));});
        const {targetId}=await send('Target.createTarget',{url:'about:blank'});
        const {sessionId}=await send('Target.attachToTarget',{targetId,flatten:true});
        await send('Runtime.enable',{},sessionId);await send('Page.enable',{},sessionId);
        await send('Browser.setDownloadBehavior',{behavior:'deny'});
        const evaluate=async expression=>{const result=await send('Runtime.evaluate',{expression,returnByValue:true,awaitPromise:true},sessionId);if(result.exceptionDetails)throw new Error(result.exceptionDetails.exception?.description||result.exceptionDetails.text);return result.result.value;};
        const click=selector=>evaluate(`document.querySelector(${JSON.stringify(selector)}).click()`);
        const fill=(selector,value)=>evaluate(`(() => {const node=document.querySelector(${JSON.stringify(selector)});node.value=${JSON.stringify(value)};node.dispatchEvent(new Event('input',{bubbles:true}));})()`);
        const press=async key=>{const code={Enter:13,ArrowDown:40,Escape:27}[key];await send('Input.dispatchKeyEvent',{type:'keyDown',key,code:key,windowsVirtualKeyCode:code,...(key==='Enter'?{text:'\r',unmodifiedText:'\r'}:{})},sessionId);await send('Input.dispatchKeyEvent',{type:'keyUp',key,code:key,windowsVirtualKeyCode:code},sessionId);};
        const ready=()=>until(()=>evaluate(`document.getElementById('product-list-status').textContent==='' && document.getElementById('products-pagination').textContent.includes('共')`),'product response');
        const query=async keyword=>{await fill('#product-keyword',keyword);await evaluate('document.querySelector("#product-keyword").focus()');await press('Enter');await ready();};
        const inventoryReady=()=>until(()=>evaluate(`document.querySelector('#iw-status').textContent==='' && document.querySelector('#iw-page-label').textContent.includes('共')`),'inventory response');
        const checkInventoryScope=(mode,id,keyword)=>{
            const url=inventoryCalls.at(-1);
            check(url.pathname,`/inventory/${mode}`);check(url.searchParams.get('product_id'),id);check(url.searchParams.get('keyword'),keyword);
        };
        const exportInventoryScope=async(view,id,keyword)=>{
            const previous=exportRequests.length;
            await click('#iw-export');await until(()=>exportRequests.length>previous,'inventory export request');
            const url=exportRequests.at(-1);
            check(url.pathname,'/inventory/export');check(url.searchParams.get('view'),view);
            check(url.searchParams.get('product_id'),id);check(url.searchParams.get('keyword'),keyword);
            await until(()=>evaluate('!document.querySelector("#iw-export").disabled'),'inventory export completion');
        };
        const screenshot=async name=>{const data=await send('Page.captureScreenshot',{format:'png'},sessionId);fs.writeFileSync(path.join(out,name+'.png'),Buffer.from(data.data,'base64'));};
        for(const [route,label] of [['/','template'],['/copy','copy']]) {
            catalog=initial();calls=[];mutations=[];exportRequests=[];inventoryCalls=[];importCalls=0;listGate=null;saveGate=null;
            await send('Emulation.setDeviceMetricsOverride',{width:1440,height:1000,deviceScaleFactor:1,mobile:false},sessionId);
            await send('Page.navigate',{url:`http://127.0.0.1:${server.address().port}${route}`},sessionId);
            await until(()=>evaluate(`typeof CURRENT_USER_ID!=='undefined' && CURRENT_USER_ID===10`),'login');await ready();
            check(await evaluate('document.querySelectorAll(".nav [data-main-tab=gallery]").length'),0);
            check(await evaluate('document.querySelector(".product-table thead").textContent'), '产品图片SKU产品名称规格/型号操作');
            check(await evaluate('document.querySelectorAll("#products-tbody tr").length'),20);
            check(await evaluate('document.querySelector("#product-editor").open'),false);
            check(await evaluate('!!window.catalogInjected'),false);
            await query('  狗狗  ');check(await evaluate('document.querySelector("#product-keyword").value'),'狗狗');
            await evaluate('goProductPage(2)');await ready();check(calls.at(-1).searchParams.get('page'),'2');
            await evaluate('refreshProducts()');await ready();check(calls.at(-1).searchParams.get('page'),'2');
            await click('#products-tbody [data-product-select]');
            const selection=await evaluate('[...selectedProductIds]'), requestCount=calls.length;
            await click('#product-view-gallery');check(calls.length,requestCount,'view switching reuses loaded page');
            check(await evaluate('productPage'),2);check(await evaluate('[...selectedProductIds]'),selection);
            check(await evaluate('document.querySelectorAll("#gallery-grid article").length'),10);
            check(await evaluate('document.querySelector("#gallery-grid [data-product-select]").checked'),true);
            await evaluate('changeProductPageSize(10)');await ready();check(calls.at(-1).searchParams.get('page_size'),'10');
            await query('EXACT');check(await evaluate('currentProductList[0].id'),1);
            check(calls.at(-1).searchParams.get('prioritize_exact_sku'),'true');
            await fill('#product-keyword','EXACT');await until(()=>evaluate('document.querySelectorAll("#product-options [role=option]").length>0'),'keyboard suggestions');
            await press('ArrowDown');await press('Enter');await ready();check(await evaluate('currentProductList[0].id'),1);check(await evaluate('document.querySelector("#product-keyword").getAttribute("aria-expanded")'),'false');
            await fill('#product-keyword','EXACT-3');await evaluate('document.getElementById("product-keyword").focus()');
            await until(()=>evaluate('document.querySelectorAll("#product-options [role=option]").length>0'),'suggestions');
            await click('#product-options [role=option]');await ready();
            check(await evaluate('currentProductList.length'),1);check(await evaluate('document.querySelector("#gallery-grid a[download]").getAttribute("href")'),'/uploads/product-fixture.png');
            await query('XL');check(calls.at(-1).searchParams.get('search_specification'),'true');check(await evaluate('productTotal'),25);
            await evaluate('document.querySelector("#product-completeness").value="missing_specification";searchProducts()');await ready();check(await evaluate('productTotal'),0);
            await fill('#product-keyword','');await evaluate('searchProducts()');await ready();check(await evaluate('productTotal'),16);
            await evaluate('document.querySelector("#product-completeness").value="missing_image";searchProducts()');await ready();check(await evaluate('productTotal'),10);
            await evaluate('clearProductSearch()');await ready();await query('A&B /?+%_#');
            check(calls.at(-1).searchParams.get('keyword'),'A&B /?+%_#');check(await evaluate('document.querySelectorAll("#gallery-grid [onerror], #gallery-grid b").length'),0);
            await query('不存在');check(await evaluate('document.querySelector("#gallery-grid").textContent.includes("没有匹配")'),true);
            await fill('#product-keyword','error');await evaluate('searchProducts()');check(await evaluate('document.querySelector("#product-list-status b")===null'),true);
            check(await evaluate('document.querySelector("#product-list-status").textContent.includes("模拟加载失败")'),true);
            await query('EXACT');await evaluate('openEditProduct(1)');
            check(await evaluate('document.querySelector("#p-specification-help").hidden'),false);
            check(await evaluate('[document.querySelector("#p-product-type").value,document.querySelector("#p-safe-stock-quantity").value]'),['stable','40']);
            await evaluate('saveProduct()');check(mutations.length,0,'legacy empty specification blocks writes');
            await fill('#p-specification','黑色\n标准版');await evaluate('saveProduct()');await ready();
            check(mutations.at(-1).data.specification,'黑色 标准版');check(mutations.at(-1).data.safe_stock_quantity,'40');check(mutations.at(-1).data.image,undefined);check(mutations.at(-1).csrf,'fixture-csrf');
            await evaluate('openNewProduct()');check(await evaluate('document.querySelector("#product-accounting").open'),true);
            for(const [field,value] of [['sku','NEW'],['name','新商品'],['specification','蓝色 / L']])await fill('#p-'+field,value);
            await evaluate('document.querySelector("#p-product-type").value="new";toggleSafeStockField("p")');check(await evaluate('document.querySelector("#p-safe-stock-quantity").disabled'),true);
            await fill('#p-specification','黑色\u000b标准版');const before=mutations.length;await evaluate('saveProduct()');check(mutations.length,before);
            await fill('#p-specification','蓝色 / L');await evaluate('saveProduct()');await ready();check(mutations.at(-1).method,'POST');check(mutations.at(-1).data.safe_stock_quantity,'0');
            await evaluate('openNewProduct()');for(const [field,value] of [['sku','ERROR'],['name','保留录入'],['specification','500 mL']])await fill('#p-'+field,value);
            await evaluate('saveProduct()');check(await evaluate('document.querySelector("#product-editor").open'),true);check(await evaluate('document.querySelector("#p-specification").value'),'500 mL');
            check(await evaluate('document.querySelector("#product-editor-status").textContent'),'模拟保存失败');
            await fill('#p-sku','REFRESH-FAIL');await evaluate('saveProduct()');check(await evaluate('document.querySelector("#product-editor").open'),false);
            check(await evaluate('document.querySelector("#msg").textContent.includes("已保存")'),true);await evaluate('refreshProducts()');await ready();
            await evaluate('exportSelectedProducts()');check(exportRequests.at(-1).searchParams.getAll('product_ids'),selection.map(String));
            await evaluate('exportAllProducts()');check(exportRequests.at(-1).searchParams.getAll('product_ids'),[],'all export ignores selection explicitly');
            const exported=exportRequests.length;await evaluate('clearProductSelection();exportSelectedProducts()');check(await evaluate('document.querySelector("#product-export-selected").disabled'),true);check(exportRequests.length,exported);
            await query('EXACT');await evaluate('openProductInventory(1)');await until(()=>inventoryCalls.length>0,'inventory link');
            check(inventoryCalls.at(-1).searchParams.get('product_id'),'1');check(await evaluate('document.querySelector("#sec-inventory").classList.contains("active")'),true);
            check(await evaluate('document.querySelectorAll("#iw-tbody tr").length'),1);
            await click('#iw-export');await until(()=>exportRequests.at(-1).pathname==='/inventory/export');check(exportRequests.at(-1).searchParams.get('product_id'),'1');
            await until(()=>evaluate('!document.querySelector("#iw-export").disabled'),'inventory export completion');
            // 商品页直达后，各库存视图、刷新、分页和Excel沿用同一精确商品范围。
            await click('[data-iw-action="daily"]');await inventoryReady();checkInventoryScope('daily','1','EXACT');
            check(await evaluate('document.querySelector("#iw-keyword").value'),'EXACT');
            await exportInventoryScope('daily','1','EXACT');
            for(const layout of ['product','matrix','day']) {
                await evaluate(`document.querySelector('#iw-layout').value='${layout}';document.querySelector('#iw-layout').dispatchEvent(new Event('change'))`);
                await exportInventoryScope(layout==='matrix'?'matrix':'daily','1','EXACT');
            }
            await click('[data-iw-action="overview"]');await inventoryReady();checkInventoryScope('overview','1','EXACT');
            await click('[data-iw-action="refresh"]');await inventoryReady();checkInventoryScope('overview','1','EXACT');
            await click('#iw-query button[type="submit"]');await inventoryReady();checkInventoryScope('overview','1','EXACT');
            check(await evaluate('document.querySelectorAll("#iw-tbody tr").length'),1,'resubmitting the shown SKU keeps exact selection among similar SKUs');
            await evaluate(`document.querySelector('#iw-page-size').value='10';document.querySelector('#iw-page-size').dispatchEvent(new Event('change'))`);
            await inventoryReady();checkInventoryScope('overview','1','EXACT');await exportInventoryScope('overview','1','EXACT');
            // 主动更改或清空查询能解除精确商品，不能把旧ID带入新的查询/导出。
            await fill('#iw-keyword','DOG');check(await evaluate('document.querySelector("#iw-export").disabled'),true);
            await click('[data-iw-action="daily"]');await inventoryReady();checkInventoryScope('daily',null,'DOG');
            await exportInventoryScope('daily',null,'DOG');
            await fill('#iw-keyword','');await click('#iw-query button[type="submit"]');await inventoryReady();
            checkInventoryScope('daily',null,'');await exportInventoryScope('daily',null,'');
            // 直达请求的SKU回显不能覆盖用户等待期间输入的新查询。
            inventoryGate={...deferred(),expired:false};await evaluate('void openProductInventory(1)');await until(()=>inventoryGate.started,'inventory draft race');
            await fill('#iw-keyword','DOG');inventoryGate.resolve();await inventoryReady();inventoryGate=null;
            check(await evaluate('document.querySelector("#iw-keyword").value'),'DOG');
            check(await evaluate('document.querySelector("#iw-export").disabled'),true);
            await click('#iw-query button[type="submit"]');await inventoryReady();checkInventoryScope('overview',null,'DOG');
            await exportInventoryScope('overview',null,'DOG');
            // 请求尚未完成时切换视图也保留ID，晚到的旧401不能覆盖新视图/会话。
            inventoryGate=deferred();await evaluate('void openProductInventory(1)');await until(()=>inventoryGate.started,'pending inventory link');
            await click('[data-iw-action="daily"]');await inventoryReady();
            check(inventoryCalls.at(-1).searchParams.get('product_id'),'1');
            check(await evaluate('document.querySelector("#iw-keyword").value'),'EXACT');
            inventoryGate.resolve();await new Promise(resolve=>setTimeout(resolve,100));inventoryGate=null;
            check(await evaluate('CURRENT_USER_ID'),10);await exportInventoryScope('daily','1','EXACT');
            await click('[data-iw-action="overview"]');await inventoryReady();
            inventoryGate=deferred();await evaluate('void switchTab("inventory")');await until(()=>inventoryGate.started,'old inventory query');
            await evaluate('openProductInventory(100)');inventoryGate.resolve();await new Promise(resolve=>setTimeout(resolve,100));check(await evaluate('CURRENT_USER_ID'),10);check(await evaluate('document.querySelector("#iw-tbody").textContent.includes("EXACT-1")'),true);inventoryGate=null;
            await evaluate('switchTab("products")');await ready();check(await evaluate('productKeyword'),'EXACT');
            await evaluate('openProductImport()');check(await evaluate('document.querySelector("#product-import-dialog").open'),true);
            await evaluate(`(() => {const transfer=new DataTransfer();transfer.items.add(new File(['fixture'],'products.xlsx',{type:'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'}));document.getElementById('product-import-file').files=transfer.files;resetProductImportPreview();})()`);
            await evaluate('previewProductImport()');check(await evaluate('document.querySelector("#product-import-tbody").textContent.includes("白色 / L")'),true);
            await evaluate('confirmProductImport()');check(importCalls,2);await evaluate('closeProductImport()');await ready();
            listGate=deferred();await fill('#product-keyword','slow');await evaluate('void searchProducts()');await until(()=>listGate.started,'slow list');
            await query('EXACT-3');listGate.resolve();await new Promise(resolve=>setTimeout(resolve,100));check(await evaluate('currentProductList[0].sku'),'EXACT-3');
            listGate={...deferred(),expired:true};await fill('#product-keyword','slow');await evaluate('void searchProducts()');await until(()=>listGate.started,'old session list');
            await evaluate('handleSessionExpired()');check(await evaluate('document.querySelector("#gallery-grid").textContent'),'');
            await evaluate('loadCurrentUser()');await ready();listGate.resolve();await new Promise(resolve=>setTimeout(resolve,100));check(await evaluate('CURRENT_USER_ID'),10);check(await evaluate('selectedProductIds.size'),0);check(await evaluate('productKeyword'),'');
            saveGate=deferred();await evaluate('openNewProduct()');for(const [field,value] of [['sku','SLOW'],['name','慢保存'],['specification','白色']])await fill('#p-'+field,value);
            const beforeBusy=mutations.length;await evaluate('void saveProduct()');await until(()=>saveGate.started,'saving');
            await evaluate('void saveProduct();closeEditProduct()');check(mutations.length,beforeBusy+1);check(await evaluate('document.querySelector("#product-editor").open'),true);
            await evaluate('handleSessionExpired();loadCurrentUser()');await ready();await evaluate('openNewProduct()');await fill('#p-name','新会话草稿');saveGate.resolve();await new Promise(resolve=>setTimeout(resolve,100));
            check(await evaluate('document.querySelector("#p-name").value'),'新会话草稿');check(await evaluate('document.querySelector("#product-editor").open'),true);await evaluate('closeEditProduct()');
            await query('EXACT-3');await click('#product-view-list');await screenshot(`products-${label}-desktop`);
            await evaluate('openEditProduct(102)');await screenshot(`products-${label}-editor`);await evaluate('closeEditProduct()');
            await click('#product-view-gallery');await screenshot(`products-${label}-gallery`);
            await send('Emulation.setDeviceMetricsOverride',{width:390,height:1000,deviceScaleFactor:1,mobile:true},sessionId);
            await fill('#product-keyword','狗狗');await evaluate('document.querySelector("#product-keyword").focus()');await until(()=>evaluate('document.querySelectorAll("#product-options [role=option]").length>0'),'mobile suggestions');
            check(await evaluate('document.querySelector("#product-popup").getBoundingClientRect().right<=innerWidth'),true);
            await evaluate('productSearchControl.close()');check(await evaluate('document.documentElement.scrollWidth<=innerWidth'),true);await screenshot(`products-${label}-mobile`);
            await click('#product-view-list');check(await evaluate('document.documentElement.scrollWidth<=innerWidth'),true);
            await evaluate('openNewProduct()');check(await evaluate('document.querySelector("#product-editor").getBoundingClientRect().right<=innerWidth'),true);await evaluate('closeEditProduct()');
            await fill('#product-keyword','expired');await evaluate('searchProducts()');check(await evaluate('CURRENT_USER_ID'),0);check(await evaluate('document.querySelector("#gallery-grid").textContent'),'');
        }
        check(errors,[],'browser uncaught errors');
        console.log(JSON.stringify({pages:2,checks,scope:'real Chrome: shared search/views/pagination/selection, editor, legacy data, Excel workflow/scope, inventory link, races/session reset and responsive layout; fixture API only'}));
    } finally {listGate?.resolve();saveGate?.resolve();inventoryGate?.resolve();if(send)await send('Browser.close').catch(()=>{});socket?.close();browser.kill();await new Promise(resolve=>server.close(resolve));}
}
main().catch(error=>{console.error(error);process.exitCode=1;});
