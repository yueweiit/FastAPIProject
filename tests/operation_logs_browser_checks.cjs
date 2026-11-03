// 两份真实页面 + 原模块 + 本地模拟API；不启动main、不连接业务库。
const fs=require('node:fs'),path=require('node:path'),http=require('node:http'),assert=require('node:assert/strict');
const {chromium}=require(process.env.INVENTORY_PLAYWRIGHT_MODULE||'playwright');
let checks=0;const check=(a,b)=>{assert.deepEqual(a,b);checks++;};
const wait=()=>{let resolve;const promise=new Promise(done=>resolve=done);return {promise,resolve};};
async function main(){
 const files={'/':'templates/index.html','/copy':'index.html'};
 for(const name of ['page-interactions','operation-logs','sales-import-progress','product-search','sales-ledger','sales-pending','inventory-workspace','inventory-assessment','inventory-allocations'])files['/'+name+'.js']='static/'+name+'.js';
 files['/sales-fields.json']='static/sales-fields.zh-CN.json';
 const server=http.createServer((req,res)=>{const file=files[new URL(req.url,'http://fixture').pathname];if(!file){res.writeHead(404);res.end();return;}
  res.setHeader('Content-Type',file.endsWith('.js')?'text/javascript; charset=utf-8':file.endsWith('.json')?'application/json':'text/html; charset=utf-8');res.end(fs.readFileSync(file));});
 await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));let browser;
 try{
  browser=await chromium.launch({headless:true,...(process.env.INVENTORY_BROWSER_CHANNEL?{channel:process.env.INVENTORY_BROWSER_CHANNEL}:{})});
  const page=await browser.newPage({viewport:{width:1440,height:1000}}),errors=[];page.on('pageerror',error=>errors.push(error.message));
  await page.addInitScript(()=>{
   const nativeFetch=window.fetch;window.logFetchModes=[];
   window.fetch=(input,options)=>{
    const url=new URL(typeof input==='string'?input:input.url,location.href);
    if(url.pathname==='/operation-logs'||url.pathname.startsWith('/operation-logs/'))window.logFetchModes.push({path:url.pathname,cache:options?.cache});
    return nativeFetch(input,options);
   };
  });
  let role='admin',bound=7,listStatus=200,optionsStatus=200,slow=null,slowDetail=null,slowOptions=null,financeOn=false,auditOn=false,auditReady=false,logRequests=0;const calls=[];
  const sources=()=>role==='admin'?['batch','stock_count','sales_import','sales_rollback',...(financeOn?['finance']:[]),...(auditReady?['products','masters','accounts','batch_create','sales_operations','report_changes']:[])]:[];
  const records=Array.from({length:25},(_,i)=>({id:i+1,source:['batch','stock_count','sales_import','sales_rollback'][i%4],
   action:['update','count_saved','import','rollback'][i%4],recorded_at:'2026-11-02T12:00:00',time_basis:i%2?'original':'utc',
   actor_id:7,actor_name:'<操作者>',reference:i%4===0?'BATCH-'+(i+1):i%4===1?'SKU-1':'来源文件.xlsx',sku:'SKU-1',product_name:'中文商品',
   related_id:1,store_id:i%4<2?null:7,store_name:i%4<2?null:'店铺A',
   summary:i%4===0?{changed_fields:['quantity','shipping_cost']}:i%4===1?{count_date:'2026-11-02',revision:2,difference:-8}:{settlement_rows:18,sales_created:23,pending_confirmation:2,status:'rolled_back'}}));
  records.push({id:26,source:'finance',action:'confirm',recorded_at:'2026-11-02T12:00:00',time_basis:'original',actor_id:1,actor_name:'财务管理员',reference:'financial_expenses',related_id:10,store_id:7,store_name:'店铺A',summary:{changed_fields:['amount']}});
  await page.route('**/*',async route=>{
   const url=new URL(route.request().url()),p=url.pathname,ok=value=>route.fulfill({status:200,contentType:'application/json',body:JSON.stringify(value)});
   if(files[p])return route.continue();
   if(p==='/auth/config')return ok({mode:'local',local_login_enabled:true});
   if(p==='/auth/me')return ok({id:10,username:'测试账号',role,store_id:bound,csrf_token:'test-only'});
   if(p==='/products')return ok({items:[],total:0,page:1,page_size:20});
   if(p==='/batches')return ok({items:[{id:1,product_id:1,batch_no:'BATCH-TEST',quantity:3,remaining_quantity:3,purchase_price:1,total_amount:3,unit_cost:1,shipping_cost:0,last_mile_cost:0,other_cost:0,arrived_at:'2026-09-01T00:00:00'}],total:1,page:1,page_size:20});
   if(p==='/operation-logs'||p.startsWith('/operation-logs/')){
    logRequests++;
    if(role!=='admin')return route.fulfill({status:403,contentType:'application/json',body:JSON.stringify({detail:'仅管理员可查看操作日志'})});
   }
   if(p==='/operation-logs/options'){
    if(slowOptions){const held=slowOptions;slowOptions=null;held.seen.resolve();await held.promise;return route.fulfill({status:401,contentType:'application/json',body:'{}'});}
    return route.fulfill({status:optionsStatus,contentType:'application/json',body:JSON.stringify(optionsStatus===200?{sources:sources(),stores:[{id:7,name:'店铺A'}],audit_enabled:auditOn,audit_available:auditReady}:{detail:'来源加载失败测试'})});
   }
   if(p==='/operation-logs'){
    const params=Object.fromEntries(url.searchParams);calls.push(params);
    if(params.keyword==='slow'&&slow){const held=slow;held.seen.resolve();await held.promise;return route.fulfill({status:401,contentType:'application/json',body:'{}'});}
    if(listStatus!==200)return route.fulfill({status:listStatus,contentType:'application/json',body:JSON.stringify({detail:'列表加载失败测试'})});
    const filtered=records.filter(row=>sources().includes(row.source)&&(!params.source||row.source===params.source)&&(!params.action||row.action===params.action)&&(!params.group_id||row.group_id===params.group_id)&&(!params.keyword||[row.reference,row.sku,row.product_name].join(' ').includes(params.keyword)));
    const size=Number(params.page_size),current=Math.min(Number(params.page),Math.max(1,Math.ceil(filtered.length/size)));
    return ok({items:filtered.slice((current-1)*size,current*size),total:filtered.length,page:current,page_size:size});
   }
   if(p.startsWith('/operation-logs/')){
    const [source,id]=p.split('/').slice(2);const row=records.find(row=>row.source===source&&row.id===Number(id));
    if(source==='finance'&&row.fixtureDetail)return ok({...row,...row.fixtureDetail,record_kind:'change'});
    if(source==='products')return ok({...row,record_kind:'change',actor_role:'admin',unknown_before_fields:['name'],before_values:{sku:'OLD'},after_values:{sku:'NEW',name:'<新名称>'},changes:[{field:'sku',before:'OLD',after:'NEW',before_known:true},{field:'name',before:null,after:'<新名称>',before_known:false}]});
    if(source==='accounts')return ok({...row,record_kind:'change',actor_role:'admin',before_values:{username:'target'},after_values:{username:'target',password_changed:true},changes:[{field:'password_changed',before:null,after:true}]});
    if(source==='batch'&&slowDetail){const held=slowDetail;slowDetail=null;held.seen.resolve();await held.promise;return ok({...row,reason:'不应显示的旧详情',record_kind:'change',changes:[],before_values:{},after_values:{}});}
    return ok({...row,actor_role:source==='batch'?'operator':undefined,record_kind:['batch','finance'].includes(source)?'change':source==='stock_count'?'snapshot':'business_history',reason:source==='stock_count'?'实盘备注':null,
     before_values:source==='batch'?{quantity:100}:source==='finance'?{amount:'1.0000'}:{},after_values:source==='finance'?{amount:'2.0000'}:source==='batch'?{quantity:120,void_reason:'<说明>'}:source==='stock_count'?{system_quantity:100,actual_quantity:92,difference:-8,revision:2}:{file_name:'来源文件.xlsx',status:'rolled_back',sales_created:23,pending_confirmation:2},
     changes:source==='finance'?[{field:'amount',before:'1.0000',after:'2.0000'}]:source==='batch'?[{field:'quantity',before:100,after:120},{field:'void_reason',before:null,after:'<说明>'}]:[]});
   }
   return ok([]);
  });
  const open=async suffix=>{await page.goto(`http://127.0.0.1:${server.address().port}${suffix}`);await page.waitForFunction(()=>CURRENT_USER_ID===10);await page.locator('[data-operation-nav]').click();await page.locator('#ol-body tr').first().waitFor();};
  const submit=async()=>{await page.locator('#ol-filters button[type="submit"]').click();await page.waitForFunction(()=>document.getElementById('ol-status').textContent==='');};
  const checkBatchActions=async()=>{
   await page.getByRole('button',{name:'入库管理',exact:true}).click();
   await page.waitForFunction(()=>document.getElementById('batches-tbody').textContent.includes('BATCH-TEST'));
   check(await page.locator('#batches-tbody button').filter({hasText:/^日志$/}).count(),0);
   check(await page.locator('#batches-tbody [onclick*="openBatchLogs"]').count(),0);
  };
  const denied=async suffix=>{
   const beforeNavigation=logRequests;
   await page.goto(`http://127.0.0.1:${server.address().port}${suffix}`);await page.waitForFunction(()=>CURRENT_USER_ID===10);
   check(logRequests,beforeNavigation);
   check(await page.locator('[data-operation-nav]').isHidden(),true);
   const before=logRequests;
   await page.evaluate(async()=>{switchTab('operation-logs');await window.OperationLogs.open();await window.PageInteractions.batchLogs(1,'hidden');});
   check(logRequests,before);check(await page.locator('#operation-logs-root').innerHTML(),'');
   check(await page.locator('#ol-dialog:modal, #batch-log-dialog:modal').count(),0);
   check(await page.locator('#sec-operation-logs').evaluate(node=>node.classList.contains('active')),false);
   await checkBatchActions();
  };
  for(const suffix of ['/','/copy']){
   role='admin';bound=7;await open(suffix);
   check(await page.locator('#ol-audit-state').innerText().then(text=>text.includes('尚未启用')),true);
   check(await page.locator('[data-operation-nav]').innerText(),'操作日志');check(await page.locator('#ol-body tr').count(),20);
   check(await page.locator('#ol-body script').count(),0);check(await page.locator('#ol-body tr').first().innerText().then(text=>text.includes('<操作者>')),true);
   check(await page.locator('#ol-body tr').first().innerText().then(text=>text.includes('UTC')),true);
   check(await page.locator('#ol-body tr').first().innerText().then(text=>text.includes('变更 2 项')&&text.includes('头程运费')),true);
   await page.locator('#ol-next').click();await page.waitForFunction(()=>document.getElementById('ol-page').textContent.includes('第 2 页'));
   check(await page.locator('#ol-body tr').count(),5);check(calls.at(-1).page,'2');
   await page.locator('#ol-previous').click();await page.waitForFunction(()=>document.getElementById('ol-page').textContent.includes('第 1 页'));
   const first=page.locator('#ol-body [data-ol-detail]').first();await first.click();
   await page.locator('#ol-detail-body .ol-detail-table').first().waitFor();
   check(await page.locator('#ol-detail-body').innerText().then(text=>text.includes('100')&&text.includes('120')&&text.includes('运营')),true);
   check(await page.locator('#ol-detail-body').innerText().then(text=>text.includes('原记录未保存操作原因')),true);
   check(await page.locator('#ol-detail-body script').count(),0);
   await page.locator('.ol-full summary').click();check(await page.locator('.ol-full table').isVisible(),true);
   if(process.env.INVENTORY_QA_DIR){fs.mkdirSync(process.env.INVENTORY_QA_DIR,{recursive:true});if(suffix==='/')await page.locator('#ol-dialog').screenshot({path:path.join(process.env.INVENTORY_QA_DIR,'operation-log-detail.png')});}
   await page.keyboard.press('Escape');check(await first.evaluate(node=>document.activeElement===node),true);
   await page.locator('#ol-source').selectOption('stock_count');await submit();
   await page.locator('#ol-body [data-ol-detail]').first().click();await page.waitForFunction(()=>document.getElementById('ol-detail-body').textContent.includes('实盘数量'));
   check(await page.locator('#ol-detail-body').innerText().then(text=>text.includes('保存一个版本')&&text.includes('-8')),true);
   await page.locator('#ol-close').click();await page.locator('#ol-source').selectOption('sales_import');await submit();
   await page.locator('#ol-body [data-ol-detail]').first().click();await page.waitForFunction(()=>document.getElementById('ol-detail-body').textContent.includes('不可变快照'));
   check(await page.locator('#ol-detail-body').innerText().then(text=>text.includes('已撤销')&&text.includes('23')),true);await page.locator('#ol-close').click();
   await page.locator('#ol-keyword').fill('无匹配');await submit();check(await page.locator('#ol-body .empty').innerText(),'没有符合条件的记录。');
   await page.locator('#ol-clear').click();await page.waitForFunction(()=>document.querySelectorAll('#ol-body tr').length===20);
   await page.locator('#ol-start').fill('2026-11-01');await page.locator('#ol-end').fill('2026-11-02');await page.locator('#ol-actor').fill('测试人');await page.locator('#ol-store').selectOption('7');await submit();
   check([calls.at(-1).start,calls.at(-1).end,calls.at(-1).actor,calls.at(-1).store_id],['2026-11-01','2026-11-02','测试人','7']);
   listStatus=503;await page.locator('#ol-refresh').click();await page.waitForFunction(()=>document.getElementById('ol-status').textContent.includes('列表加载失败'));
   check(await page.locator('#ol-body tr').count(),0);listStatus=200;await page.locator('#ol-clear').click();await page.waitForFunction(()=>document.querySelectorAll('#ol-body tr').length===20);
   slow={...wait(),seen:wait()};await page.locator('#ol-keyword').fill('slow');await page.locator('#ol-filters button[type="submit"]').click();await slow.seen.promise;
   await page.locator('#ol-keyword').fill('SKU-1');await submit();slow.resolve();slow=null;await page.waitForTimeout(80);
   check(await page.evaluate(()=>CURRENT_USER_ID),10);check(await page.locator('#ol-body tr').count(),20);
   slowDetail={...wait(),seen:wait()};const oldDetail=slowDetail;await page.locator('#ol-body [data-ol-detail]').first().click();await oldDetail.seen.promise;
   await page.evaluate(async()=>{handleSessionExpired();await loadCurrentUser();});oldDetail.resolve();await page.waitForTimeout(80);
   check(await page.locator('#ol-dialog').count(),0);check(await page.locator('#operation-logs-root').innerHTML(),'');
   await page.locator('[data-operation-nav]').click();await page.locator('#ol-body tr').first().waitFor();
   check(await page.locator('#operation-logs-root').innerText().then(text=>text.includes('不应显示的旧详情')),false);
   await page.setViewportSize({width:390,height:844});
   check(await page.locator('#operation-logs-root').evaluate(node=>node.getBoundingClientRect().right<=innerWidth),true);
   await page.locator('#ol-body [data-ol-detail]').first().click();await page.locator('#ol-detail-body table').first().waitFor();
   const box=await page.locator('#ol-dialog').boundingBox();check(box.width<=390&&box.x>=0,true);
   await page.locator('#ol-close').click();
   if(process.env.INVENTORY_QA_DIR&&suffix==='/')await page.locator('#operation-logs-root').screenshot({path:path.join(process.env.INVENTORY_QA_DIR,'operation-logs-mobile.png')});
   await page.setViewportSize({width:1440,height:1000});
   if(process.env.INVENTORY_QA_DIR&&suffix==='/')await page.locator('#operation-logs-root').screenshot({path:path.join(process.env.INVENTORY_QA_DIR,'operation-logs-desktop.png')});
   await checkBatchActions();
   const modes=await page.evaluate(()=>window.logFetchModes);
   check(modes.every(request=>request.cache==='no-store'),true);
   check(['/operation-logs/options','/operation-logs','/operation-logs/batch/1'].every(path=>modes.some(request=>request.path===path)),true);
   for(const name of ['operator','viewer']){role=name;bound=7;await denied(suffix);}
  }
  role='admin';optionsStatus=503;await page.goto(`http://127.0.0.1:${server.address().port}/`);await page.waitForFunction(()=>CURRENT_USER_ID===10);await page.locator('[data-operation-nav]').click();
  await page.waitForFunction(()=>document.getElementById('ol-status').textContent.includes('来源加载失败'));optionsStatus=200;await page.locator('#ol-refresh').click();await page.locator('#ol-body tr').first().waitFor();
  role='admin';financeOn=true;await open('/');await page.locator('#ol-source').selectOption('finance');await submit();
  check(await page.locator('#ol-body tr').count(),1);check(await page.locator('#ol-body tr').innerText().then(text=>text.includes('费用记录')&&text.includes('#10')),true);
  await page.locator('#ol-body [data-ol-detail]').click();await page.waitForFunction(()=>document.getElementById('ol-detail-body').textContent.includes('2.0000'));
  check(await page.locator('#ol-detail-body').innerText().then(text=>text.includes('1.0000')&&text.includes('原币金额')),true);await page.locator('#ol-close').click();
  const financeCases=[
   {id:90,action:'source_selection',reference:'financial_expense_sources',related_id:10,field:'mode',before:'manual',after:'ledger',expect:['切换费用来源','取数方式','人工','明细']},
   {id:91,action:'classify',reference:'settlement_classifications',related_id:11,field:'breakdown',before:{commission:'8.0000',advertising:'2.0000'},after:{commission:'3.0000',advertising:'7.0000'},expect:['结算分类明细','平台及达人佣金','广告费用','8.0000','7.0000']},
   {id:92,action:'capture_version',reference:'financial_report_versions',related_id:12,field:'version',before:null,after:2,extra:{status:'needs_review',calculation_version:'restated-cost-v1'},expect:['保存月度版本','版本号','2','待核对','计算口径版本','restated-cost-v1']}
  ];
  for(const item of financeCases)records.push({...item,source:'finance',time_basis:'original',recorded_at:'2026-11-03T09:00:00',actor_name:'财务管理员',store_id:7,store_name:'店铺A',summary:{changed_fields:[item.field]},
   fixtureDetail:{before_values:item.before==null?{}:{[item.field]:item.before},after_values:{[item.field]:item.after,...item.extra},changes:[{field:item.field,before:item.before,after:item.after},...Object.entries(item.extra||{}).map(([field,after])=>({field,before:null,after}))]}});
  for(const suffix of ['/','/copy']){
   role='admin';await open(suffix);await page.locator('#ol-source').selectOption('finance');
   for(const item of financeCases){
    await page.locator('#ol-action').selectOption(item.action);await submit();
    check(calls.at(-1).action,item.action);check(await page.locator('#ol-body [data-ol-detail]').count(),1);
    await page.locator('#ol-body [data-ol-detail]').click();await page.waitForFunction(()=>!!document.querySelector('#ol-detail-body .ol-detail-table'));
    const detailText=await page.locator('#ol-detail-body').innerText();check(item.expect.every(text=>detailText.includes(text)),true);
    check(await page.locator('#ol-detail-body script').count(),0);
    await page.locator('#ol-close').click();
   }
  }
  await page.goto(`http://127.0.0.1:${server.address().port}/`);await page.waitForFunction(()=>CURRENT_USER_ID===10);
  slowOptions={...wait(),seen:wait()};const oldOptions=slowOptions;await page.locator('[data-operation-nav]').click();await oldOptions.seen.promise;
  await page.locator('#ol-refresh').click();await page.locator('#ol-body tr').first().waitFor();oldOptions.resolve();await page.waitForTimeout(80);
  check(await page.evaluate(()=>CURRENT_USER_ID),10);check(await page.locator('#ol-status').innerText(),'');
  records.push({id:80,source:'products',entity_type:'products',group_id:'a'.repeat(32),action:'update',recorded_at:'2026-11-02T14:00:00',time_basis:'utc',actor_name:'历史操作人',reference:'商品更名',related_id:999,store_id:99,store_name:null,summary:{changed_fields:['sku','name']}},
   {id:81,source:'accounts',entity_type:'users',group_id:'b'.repeat(32),action:'reset_password',recorded_at:'2026-11-02T15:00:00',time_basis:'utc',actor_name:'管理员',reference:'target',related_id:9,store_id:null,summary:{changed_fields:['password_changed']}});
  auditReady=true;
  for(const suffix of ['/','/copy']){
   auditOn=false;role='admin';await open(suffix);
   check(await page.locator('#ol-audit-state').innerText().then(text=>text.includes('已关闭')&&text.includes('仍可查询')),true);
   check(await page.locator('#ol-source option[value="accounts"]').innerText(),'账号管理');
   await page.locator('#ol-source').selectOption('products');await page.locator('#ol-group').fill('a'.repeat(32));await submit();
   check(calls.at(-1).group_id,'a'.repeat(32));check(await page.locator('#ol-body tr').count(),1);
   check(await page.locator('#ol-body').innerText().then(text=>text.includes('店铺 #99（名称未记录）')&&text.includes('商品 · 商品更名')),true);
   await page.locator('#ol-body [data-ol-detail]').click();await page.waitForFunction(()=>document.getElementById('ol-detail-body').textContent.includes('旧值未记录'));
   check(await page.locator('#ol-detail-body').innerText().then(text=>text.includes('a'.repeat(32))&&text.includes('旧值未记录')),true);
   check(await page.locator('#ol-detail-body script').count(),0);
   await page.locator('.ol-full summary').click();check(await page.locator('.ol-full').innerText().then(text=>text.includes('旧值未记录')),true);await page.locator('#ol-close').click();
   await page.locator('#ol-group').fill('');await page.locator('#ol-source').selectOption('accounts');await submit();await page.locator('#ol-body [data-ol-detail]').click();
   await page.waitForFunction(()=>document.getElementById('ol-detail-body').textContent.includes('已变更密码'));
   check(await page.locator('#ol-detail-body').innerText().then(text=>text.includes('重置密码')&&text.includes('不记录内容')),true);await page.locator('#ol-close').click();
   auditOn=true;await open(suffix);check(await page.locator('#ol-audit-state').isHidden(),true);
   if(process.env.INVENTORY_QA_DIR&&suffix==='/'){
    await page.locator('#ol-source').selectOption('products');await submit();
    await page.locator('#operation-logs-root').screenshot({path:path.join(process.env.INVENTORY_QA_DIR,'operation-logs-new-modules.png')});
    await page.locator('#ol-body [data-ol-detail]').click();await page.waitForFunction(()=>document.getElementById('ol-detail-body').textContent.includes('旧值未记录'));
    await page.locator('#ol-dialog').screenshot({path:path.join(process.env.INVENTORY_QA_DIR,'operation-logs-new-detail.png')});await page.locator('#ol-close').click();
   }
   for(const name of ['operator','viewer']){role=name;bound=null;await denied(suffix);}
  }
  auditReady=false;auditOn=false;
  role='admin';bound=7;await open('/');
  slowDetail={...wait(),seen:wait()};const downgraded=slowDetail;
  await page.locator('#ol-body [data-ol-detail]').first().click();await downgraded.seen.promise;
  role='operator';await page.evaluate(()=>loadCurrentUser());downgraded.resolve();await page.waitForTimeout(80);
  check(await page.locator('[data-operation-nav]').isHidden(),true);
  check(await page.locator('#operation-logs-root').innerHTML(),'');check(await page.locator('#ol-dialog').count(),0);
  role='admin';await open('/');
  await page.evaluate(()=>{window.OperationLogs.reset();window.OperationLogs=undefined;});await page.locator('[data-operation-nav]').click();
  check(await page.locator('#operation-logs-root').innerText().then(text=>text.includes('模块尚未加载')&&text.includes('重启后端')),true);
  role='viewer';bound=null;await page.goto(`http://127.0.0.1:${server.address().port}/copy`);await page.waitForFunction(()=>CURRENT_USER_ID===10);
  check(await page.locator('[data-operation-nav]').isHidden(),true);
  check(errors,[]);console.log(JSON.stringify({pages:2,checks,scope:'real log workspace, detail diff, snapshots, filters, paging, errors, mobile and stale auth responses'}));
 }finally{if(browser)await browser.close();await new Promise(resolve=>server.close(resolve));}
}
main().catch(error=>{console.error(error);process.exitCode=1;});
