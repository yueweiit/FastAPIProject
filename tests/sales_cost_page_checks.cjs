// 两份实际销售页面+公共台账模块：单件精度、分组、合计与未知传播。
const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict');
let checks=0;
const check=(actual,expected)=>{assert.deepEqual(actual,expected);checks++;};
const resource=JSON.parse(fs.readFileSync('static/sales-fields.zh-CN.json','utf8'));
const values=(html,attribute,key)=>[...html.matchAll(new RegExp(`<td[^>]*${attribute}="${key}"[^>]*>([\\s\\S]*?)<\\/td>`,'g'))].map(m=>m[1]);
const deferred=()=>{let resolve,reject;const promise=new Promise((yes,no)=>{resolve=yes;reject=no;});return {promise,resolve,reject};};
async function main(){
 for(const file of ['templates/index.html','index.html']){
  const html=fs.readFileSync(file,'utf8'),elements=new Map();
  const element=id=>{if(!elements.has(id))elements.set(id,{style:{},innerHTML:'',replaceChildren(){this.innerHTML='';}});return elements.get(id);};
  const translated=[...html.matchAll(/data-sales-cost-text="([^"]+)"/g)].map(m=>({dataset:{salesCostText:m[1]}}));
  const c=vm.createContext({window:{},URLSearchParams,
   document:{documentElement:{lang:'zh-CN'},getElementById:element,querySelectorAll:s=>s==='[data-sales-cost-text]'?translated:[]},
   fetch:async()=>({ok:true,status:200,json:async()=>resource}),
   escapeHtml:v=>String(v).replaceAll('&','&amp;').replaceAll('<','&lt;').replaceAll('>','&gt;').replaceAll('"','&quot;'),
   isOperator:()=>false,loadProductOptions:async()=>{},renderMonthlySalesSummary:summary=>{element('sales-monthly-tbody').innerHTML=JSON.stringify(summary);},
   renderPagination:(id,...args)=>{element(id).innerHTML=JSON.stringify(args);},loadPendingConfirmations:async()=>{},
  });
  vm.runInContext(fs.readFileSync('static/page-interactions.js','utf8'),c);
  vm.runInContext(fs.readFileSync('static/sales-ledger.js','utf8'),c);
  check(translated.every(n=>n.textContent?.length>0),true);
  check(resource.fields.length,22);check(resource.fields.every(f=>f.formula&&f.source&&f.note),true);
  check(html.includes('/sales-ledger.js'),true);
  vm.runInContext('let salesPage=1,salesPageSize=20,salesMonth="2026-09",salesProductKeyword="SKU",salesTotal=0,allProducts=[];',c);
  const source=html.match(/^async function loadSales\(\) \{[\s\S]*?^\}/m);assert(source);vm.runInContext(source[0],c);
  const sale={id:1,product_id:1,order_no:'ORDER-1',quantity:2,total_cost:'20.004',order_total_cost_cny:'20.00',
   selling_price_cny:'30',sales_revenue_cny:'60',platform_fee_cny:'5',profit_cny:'34.996',
   unit_purchase_cost_cny:'4.0001',unit_head_shipping_cost_cny:'2',unit_last_mile_cost_cny:'1',unit_other_cost_cny:'3',unit_total_cost_cny:'10.002',
   purchase_cost_cny:'8',head_shipping_cost_cny:'4',last_mile_cost_cny:'2',other_cost_cny:'6',impairment_cny:'.9'};
  let rows=[sale];c.api=async(_m,url)=>url.startsWith('/sales/monthly-summary')?[]:{items:rows,total:rows.length,page:1,page_size:20};
  const body=k=>values(element('sales-tbody').innerHTML,'data-sales-value',k);
  const total=k=>values(element('sales-total-summary-body').innerHTML,'data-sales-total',k)[0];
  await c.loadSales();
  check((element('sales-ledger-head').innerHTML.match(/data-sales-field=/g)||[]).length,22);
  check(element('sales-ledger-head').innerHTML.includes('单件成本'),true);
  check(element('sales-ledger-head').innerHTML.includes('本行销售成本'),true);
  check(body('unit_purchase_cost_cny'),['¥4.0001']);check(body('unit_total_cost_cny'),['¥10.0020']);
  check(body('order_total_cost_cny'),['¥20.00']);check(body('profit_cny'),['¥35.00']);
  check(body('impairment_cny'),['¥0.90']);check(total('impairment_cny'),'¥0.90');
  check(total('unit_purchase_cost_cny'),'');check(total('unit_total_cost_cny'),'');
  check(body('calculation_action')[0].includes('data-sale-calculation="1"'),true);
  rows=[sale,{...sale,id:-2,record_type:'other_expense',quantity:0,total_cost:0,order_total_cost_cny:0,
   purchase_cost_cny:0,head_shipping_cost_cny:0,last_mile_cost_cny:0,other_cost_cny:0,impairment_cny:0}];
  await c.loadSales();check(body('unit_total_cost_cny')[1],'—');check(body('calculation_action')[1],'—');
  check(total('order_total_cost_cny'),'¥20.00');
  rows=[sale,{...sale,id:2,order_no:'<script>unsafe</script>',purchase_cost_cny:null,unit_purchase_cost_cny:null,impairment_cny:null,
   cost_breakdown_review_reason:'<script>unsafe</script>',impairment_review_reason:'" onmouseover="unsafe'}];
  await c.loadSales();check(body('purchase_cost_cny')[1],'待核对');check(body('unit_purchase_cost_cny')[1],'待核对');
  check(total('purchase_cost_cny'),'待核对');check(total('impairment_cny'),'待核对');
  check(element('sales-tbody').innerHTML.includes('<script>'),false);
  check(element('sales-tbody').innerHTML.includes('title="&quot; onmouseover=&quot;unsafe"'),true);
  rows=[{id:3,quantity:1,total_cost:10}];await c.loadSales();
  check(body('unit_total_cost_cny'),['待核对']);check(body('impairment_cny'),['待核对']);check(body('order_total_cost_cny'),['¥10.00']);
  rows=[{...sale,currency:'MXN',exchange_rate_to_cny:null,sales_revenue_cny:null}];await c.loadSales();
  check(element('sales-tbody').innerHTML.includes('原币 MXN；汇率 待核对'),true);
  check(element('sales-tbody').innerHTML.includes('汇率缺失，人民币金额待核对'),true);
  // 附属待确认区域失败不应清空已成功渲染的台账、合计、月度统计和分页。
  rows=[sale];c.loadPendingConfirmations=async()=>{throw new Error('<待确认失败>');};
  element('pending-confirmations-pagination').innerHTML='old-pages';
  await c.loadSales();
  check(body('order_no'),['ORDER-1']);check(total('order_total_cost_cny'),'¥20.00');
  check(element('sales-monthly-tbody').innerHTML,'[]');
  check(element('sales-pagination').innerHTML.length>0,true);
  check(element('pending-confirmations-summary').textContent,'待确认记录加载失败，请重试。');
  check(element('pending-confirmations-tbody').innerHTML.includes('&lt;待确认失败&gt;'),true);
  check(element('pending-confirmations-pagination').innerHTML,'');
  const pendingReady=async()=>{element('pending-confirmations-summary').textContent='ready';element('pending-confirmations-tbody').innerHTML='ready';};
  c.loadPendingConfirmations=pendingReady;await c.loadSales();
  check(element('pending-confirmations-tbody').innerHTML,'ready');
  // 旧会话中的待确认失败也不能覆盖新会话的成功内容。
  const pendingStarted=deferred(),pendingResponse=deferred();
  c.loadPendingConfirmations=()=>{pendingStarted.resolve();return pendingResponse.promise;};
  const oldLoad=c.loadSales();await pendingStarted.promise;
  c.window.SalesLedger.reset();c.loadPendingConfirmations=pendingReady;await c.loadSales();
  pendingResponse.reject(new Error('old-pending-failure'));await oldLoad;
  check(element('pending-confirmations-summary').textContent,'ready');
  check(element('pending-confirmations-tbody').innerHTML,'ready');
  check(body('order_no'),['ORDER-1']);check(total('order_total_cost_cny'),'¥20.00');
  const failedLoad=c.window.SalesLedger.beginLoad();
  c.window.SalesLedger.fail(new Error('<失败>'),failedLoad);
  check(element('sales-total-summary-body').innerHTML,'');
  check(element('sales-tbody').innerHTML.includes('&lt;失败&gt;'),true);
  rows=[];await c.loadSales();check(element('sales-tbody').innerHTML.includes('colspan="22"'),true);
  check(total('purchase_cost_cny'),'¥0.00');check(total('unit_total_cost_cny'),'');
  delete c.window.SalesLedger;await c.loadSales();
  check(element('sales-tbody').innerHTML.includes('销售明细模块尚未加载'),true);
 }
 console.log(JSON.stringify({page_copies:2,checks,scope:'real ledger module, grouped columns and accessible field resources in VM'}));
}
main().catch(error=>{console.error(error);process.exitCode=1;});
