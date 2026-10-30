// 实际销售模块与两份首页：补录无库存、精度、幂等重试、重复点击及会话隔离。
const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict');
let checks=0;
const check=(actual,expected)=>{assert.deepEqual(actual,expected);checks++;};
const wait=()=>{let resolve;const promise=new Promise(r=>resolve=r);return {promise,resolve};};

// 使用实际api和全部刷新函数，避免仅用会抛错的刷新桩掩盖会话/错误处理问题。
function refreshHarness(file,held) {
 const html=fs.readFileSync(file,'utf8'),elements=new Map(),calls=[];
 const el=id=>{if(!elements.has(id))elements.set(id,{value:'',innerHTML:'',textContent:'',style:{},dataset:{},disabled:false,replaceChildren(){this.innerHTML='';}});return elements.get(id);};
 const responses={
  '/products/options':[{id:1,sku:'OLD-LOCAL-SKU',name:'OLD-PRODUCT'}],
  '/sales':{items:[{id:1,order_no:'OLD-ORDER'}],page:1,page_size:20,total:1},
  '/sales/monthly-summary':[{month:'2026-09',sales_revenue_cny:10}],
  '/sales/imports':[{id:1,file_name:'PRIVATE-STORE-A.csv',store_name:'STORE-A',status:'completed',file_type:'order_detail'}],
  '/sales/pending-confirmations':{page:1,page_size:20,total:1,items:[{id:1,source_file:'PRIVATE-PENDING-A.csv'}]},
 };
 let expired=0;
 const c=vm.createContext({window:{},URLSearchParams,AbortController,crypto:require('node:crypto').webcrypto,
  document:{documentElement:{lang:'zh-CN'},getElementById:el,querySelectorAll:()=>[],addEventListener:()=>{}},
  escapeHtml:String,authHeaders:()=>({}),formatApiError:String,isOperator:()=>true,
  renderMonthlySalesSummary:rows=>{el('sales-monthly-tbody').innerHTML=JSON.stringify(rows);},
  renderPagination:id=>{el(id).innerHTML='PAGES';},
  handleSessionExpired:()=>{expired++;c.window.SalesLedger.reset();vm.runInContext('CURRENT_USER_ID=0',c);},
  fetch:async(url,options={})=>{
   calls.push({url,...options});
   if(options.method==='POST')return {status:200,ok:true,json:async()=>({id:-1})};
   const path=url.split('?')[0],matches=path===held.path&&(!held.once||!held.claimed),status=matches?held.status:200;
   if(matches&&held.once)held.claimed=true;
   async function pause(phase){if(matches&&held.phase===phase){held.started.resolve();await held.gate.promise;if(held.networkError)throw new Error('OLD-NETWORK-ERROR');}}
   await pause('headers');
   return {status,ok:status===200,text:async()=>{await pause('body');return JSON.stringify(status===200?responses[path]:{detail:'REFRESH-FAILED'});}};
  },
 });
 vm.runInContext("let CURRENT_USER_ID=1;const API='';let allProducts=[{id:1,sku:'CACHED'}],allProductsPromise=null,salesPage=1,salesPageSize=20,salesTotal=0,salesMonth='2026-09',salesProductKeyword='',pendingConfirmationsPage=1,pendingConfirmationsPageSize=20,pendingConfirmationsTotal=0;",c);
 vm.runInContext(fs.readFileSync('static/sales-ledger.js','utf8'),c);
 for(const name of ['api','loadProductOptions','loadSales','loadSalesImports','loadPendingConfirmations']){
  const match=html.match(new RegExp('^async function '+name+'\\([^\\n]*\\) \\{[\\s\\S]*?^\\}','m'));
  assert(match,name);vm.runInContext(match[0],c);
 }
 const ledger=c.window.SalesLedger;
 ledger.ready=async()=>{};ledger.render=rows=>{el('sales-tbody').innerHTML=JSON.stringify(rows);};
 for(const [id,value]of Object.entries({'s-store':'7','s-date':'2026-09-03','s-order-no':'MANUAL-1','s-sku':'001',
  's-product-name':'ITEM','s-transaction-type':'订单','s-qty':'1','s-net-sales':'10','s-settlement-total':'9'}))el(id).value=value;
 if(held.path==='/products/options')vm.runInContext('allProducts=[]',c);
 return {c,el,ledger,calls,expired:()=>expired};
}

async function checkRefreshIsolation(file) {
 const paths=['/sales/imports','/sales','/sales/monthly-summary','/products/options','/sales/pending-confirmations'];
 const stages=[{status:200,phase:'headers'},{status:200,phase:'body'},{status:401,phase:'headers'},
  {status:503,phase:'headers'},{status:200,phase:'headers',networkError:true}];
 for(const path of paths)for(const stage of stages)for(const nextUser of [1,2]){
  const held={path,...stage,started:wait(),gate:wait()},h=refreshHarness(file,held);
  const old=h.ledger.createManualSettlement();await held.started.promise;
  h.ledger.reset();vm.runInContext(`CURRENT_USER_ID=${nextUser};allProducts=[{id:2,sku:'NEW-CACHED'}]`,h.c);
  const areas=['sales-tbody','sales-total-summary-body','sales-monthly-tbody','sales-pagination',
   'sales-imports-tbody','pending-confirmations-summary','pending-confirmations-tbody','pending-confirmations-pagination'];
  for(const id of areas){h.el(id).innerHTML='NEW-SESSION';h.el(id).textContent='NEW-SESSION';}
  h.el('s-manual-status').textContent='NEW-STATUS';h.el('s-order-no').value='NEW-DRAFT';h.el('s-manual-fields').disabled=true;
  held.gate.resolve();await old;
  check(h.expired(),0);
  check(areas.every(id=>h.el(id).innerHTML==='NEW-SESSION'&&h.el(id).textContent==='NEW-SESSION'),true);
  check(h.el('s-manual-status').textContent,'NEW-STATUS');check(h.el('s-order-no').value,'NEW-DRAFT');
  check(h.el('s-manual-fields').disabled,true);
  check(vm.runInContext('allProducts[0].sku',h.c),'NEW-CACHED');
  check(h.calls.filter(call=>call.method==='POST').length,1);
 }
 // 当前会话的刷新失败仍应反馈，并保持保存成功；当前401仍正常退出。
 for(const path of paths){
  const held={path,status:503,phase:'headers',started:wait(),gate:wait()},h=refreshHarness(file,held);
  const task=h.ledger.createManualSettlement();await held.started.promise;held.gate.resolve();await task;
  check(h.el('s-manual-status').textContent.includes('部分列表刷新失败'),true);
  check(h.el('s-order-no').value,'');check(h.el('s-manual-fields').disabled,false);
  check(h.calls.filter(call=>call.method==='POST').length,1);
 }
 // 新查询替代保存后的旧刷新不属于失败，不覆盖新查询结果或误报刷新失败。
 const replaced={path:'/sales',status:503,phase:'headers',once:true,started:wait(),gate:wait()},replacement=refreshHarness(file,replaced);
 const old=replacement.ledger.createManualSettlement();await replaced.started.promise;
 await replacement.c.loadSales();const newestRows=replacement.el('sales-tbody').innerHTML;
 replaced.gate.resolve();await old;
 check(replacement.el('sales-tbody').innerHTML,newestRows);
 check(replacement.el('s-manual-status').textContent.includes('结算补录已保存'),true);
 check(replacement.el('s-manual-status').textContent.includes('部分列表刷新失败'),false);
 check(replacement.el('s-manual-fields').disabled,false);
 const held={path:'/sales/imports',status:401,phase:'headers',started:wait(),gate:wait()},h=refreshHarness(file,held);
 const task=h.ledger.createManualSettlement();await held.started.promise;held.gate.resolve();await task;
 check(h.expired(),1);check(vm.runInContext('CURRENT_USER_ID',h.c),0);
}

async function main(){
 for(const file of ['templates/index.html','index.html']){
  const html=fs.readFileSync(file,'utf8'),elements=new Map();
  const el=id=>{if(!elements.has(id))elements.set(id,{id,value:'',innerHTML:'',style:{},dataset:{},disabled:false,replaceChildren(){this.innerHTML='';}});return elements.get(id);};
  const labels=[...html.matchAll(/data-sales-manual-text="([^"]+)"/g)].map(m=>({dataset:{salesManualText:m[1]}}));
  const calls=[];let refreshes=0,expired=0,gate,status=200,networkError=false;
  const c=vm.createContext({window:{},document:{documentElement:{lang:'zh-CN'},getElementById:el,querySelectorAll:s=>s==='[data-sales-manual-text]'?labels:[],addEventListener:()=>{}},
   crypto:require('node:crypto').webcrypto,AbortController,escapeHtml:v=>String(v).replaceAll('<','&lt;'),
   authHeaders:()=>({'X-CSRF-Token':'test-csrf'}),formatApiError:detail=>typeof detail==='string'?detail:'保存失败',
   handleSessionExpired:()=>{expired++;},loadSales:async()=>{refreshes++;},loadSalesImports:async()=>{refreshes++;},
   fetch:async(url,options)=>{calls.push({url,...options,payload:JSON.parse(options.body)});const held=gate,responseStatus=status,fails=networkError;gate=null;if(held)await held.promise;
    if(fails)throw new Error('NETWORK-FAILED');return {status:responseStatus,ok:responseStatus===200,json:async()=>({detail:'<请求失败>',id:-1})};},
  });
  vm.runInContext('let CURRENT_USER_ID=1;',c);
  vm.runInContext(fs.readFileSync('static/sales-ledger.js','utf8'),c);
  const ledger=c.window.SalesLedger;
  check(labels.every(n=>n.textContent&&n.textContent!==n.dataset.salesManualText),true);
  check(html.includes('id="s-sku" maxlength="128"'),true);
  check(html.includes("document.getElementById('platform-sku-mappings-card').style.display = 'none'"),true);
  const wrapper=html.match(/^async function createSale\(\) \{[\s\S]*?^\}/m)[0];
  vm.runInContext(wrapper,c);
  await c.createSale();check(calls.length,0);check(el('s-manual-status').textContent.includes('请填写'),true);
  function fill(){for(const [id,value] of Object.entries({'s-store':'7','s-date':'2026-09-03','s-order-no':'MANUAL-1','s-sku':'00123456789012345678',
    's-product-name':'商品*4','s-transaction-type':'订单','s-qty':'2','s-net-sales':'100.1234','s-settlement-total':'90.0000'}))el(id).value=value;}
  fill();ledger.updateManualSummary();check(el('s-estimated-revenue').textContent,'¥90.00');
  gate=wait();const held=gate;const first=c.createSale();await c.createSale();
  check(calls.length,1);check(el('s-manual-fields').disabled,true);held.resolve();await first;
  check(calls[0].url,'/sales/manual-settlements');check(calls[0].credentials,'same-origin');
  check(calls[0].headers['X-CSRF-Token'],'test-csrf');check(calls[0].payload.net_product_sales,'100.1234');
  check(calls[0].payload.sku_id,'00123456789012345678');check('product_id' in calls[0].payload,false);
  check(el('s-manual-fields').disabled,false);check(refreshes,2);check(el('s-order-no').value,'');
  check(el('s-manual-status').textContent.includes('结算补录已保存'),true);
  fill();networkError=true;await c.createSale();const failed=calls.at(-1).payload.request_key;
  check(el('s-order-no').value,'MANUAL-1');check(el('s-manual-fields').disabled,false);
  networkError=false;await c.createSale();check(calls.at(-1).payload.request_key,failed);
  fill();status=422;await c.createSale();const previous=calls.at(-1).payload.request_key;
  check(el('s-manual-status').textContent,'<请求失败>');
  el('s-settlement-total').value='91';await c.createSale();check(calls.at(-1).payload.request_key!==previous,true);
  // 同账号重登后，旧401不得退出新会话，也不得清空新的补录草稿或解除新请求的禁用状态。
  status=401;gate=wait();const oldGate=gate,old=c.createSale();
  ledger.reset();vm.runInContext('CURRENT_USER_ID=1',c);fill();status=200;
  gate=wait();const newGate=gate,newRequest=c.createSale();oldGate.resolve();await old;
  check(expired,0);check(el('s-manual-fields').disabled,true);check(el('s-order-no').value,'MANUAL-1');
  newGate.resolve();await newRequest;check(el('s-manual-fields').disabled,false);
  // 已保存后的刷新异常不得再次提交或误报保存失败。
  fill();c.loadSales=async()=>{throw new Error('REFRESH');};await c.createSale();
  check(el('s-manual-status').textContent.includes('补录已保存'),true);check(el('s-order-no').value,'');
  // 普通HTTP来源没有randomUUID时，仍使用加密随机值创建请求标识。
  c.crypto={getRandomValues:values=>require('node:crypto').webcrypto.getRandomValues(values)};
  fill();status=503;await c.createSale();check(/^[a-f0-9]{32}$/.test(calls.at(-1).payload.request_key),true);
  fill();ledger.reset();check(el('s-sku').value,'');check(el('s-net-sales').value,'');
  check(el('s-manual-status').textContent,'');
  await checkRefreshIsolation(file);
 }
 console.log(JSON.stringify({page_copies:2,checks,scope:'actual manual settlement module and wrappers in VM; no database'}));
}
main().catch(error=>{console.error(error);process.exitCode=1;});
