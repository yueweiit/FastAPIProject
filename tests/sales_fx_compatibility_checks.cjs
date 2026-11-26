// 两份实际页面：旧后端逐日汇率不能冒充月度报表口径；金额、合计与月度提示一起保护。
const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict');
let checks=0;
const check=(actual,expected)=>{assert.deepEqual(actual,expected);checks++;};
const resource=JSON.parse(fs.readFileSync('static/sales-fields.zh-CN.json','utf8'));
const cells=(html,attr,key)=>[...html.matchAll(new RegExp(`<td[^>]*${attr}="${key}"[^>]*>([\\s\\S]*?)<\\/td>`,'g'))].map(m=>m[1]);
async function main(){
 for(const file of ['templates/index.html','index.html']){
  const html=fs.readFileSync(file,'utf8'),elements=new Map();
  const el=id=>{if(!elements.has(id))elements.set(id,{innerHTML:'',textContent:'',style:{},scrollLeft:0,scrollTop:0,listeners:{},
   addEventListener(type,listener){this.listeners[type]=listener;},replaceChildren(){this.innerHTML='';}});return elements.get(id);};
  const c=vm.createContext({window:{},URLSearchParams,
   document:{documentElement:{lang:'zh-CN'},getElementById:el,querySelectorAll:()=>[]},
   fetch:async()=>({ok:true,json:async()=>resource}),
   escapeHtml:v=>String(v??'').replaceAll('&','&amp;').replaceAll('<','&lt;').replaceAll('>','&gt;').replaceAll('"','&quot;'),
   isOperator:()=>false,loadProductOptions:async()=>{},loadPendingConfirmations:async()=>{},renderPagination:()=>{},
  });
  vm.runInContext(fs.readFileSync('static/page-interactions.js','utf8'),c);
  vm.runInContext(fs.readFileSync('static/sales-ledger.js','utf8'),c);
  vm.runInContext('let salesPage=1,salesPageSize=20,salesMonth="2026-09",salesProductKeyword="",salesTotal=0,allProducts=[];',c);
  for(const name of ['loadSales','renderMonthlySalesSummary']){
   const fn=html.match(new RegExp(`^(?:async )?function ${name}\\([^\\n]*\\) \\{[\\s\\S]*?^\\}`,'m'));assert(fn);vm.runInContext(fn[0],c);
  }
  const old={id:1,product_id:1,order_no:'OLD',quantity:2,total_cost:10,order_total_cost_cny:10,currency:'MXN',sold_at:'2026-09-10T00:00:00',
   source_row_net_product_sales:100,source_row_settlement_total:90,allocated_net_product_sales:100,allocated_settlement_total:90,
   exchange_rate_to_cny:'.0123',exchange_rate_date:'2026-09-10',exchange_rate_source:'old-daily',selling_price_cny:'.615',
   sales_revenue_cny:'1.23',platform_fee_cny:'.123',profit_cny:'-8.893'};
  const oldSummary={month:'2026-09',sales_count:1,sold_quantity:2,sales_cost_cny:10,sales_revenue_cny:'1.23',platform_fee_cny:'.123',gross_profit_cny:'-8.893'};
  const fresh={...old,exchange_rate_policy:'store_month_report_v1',exchange_rate_store_id:7,exchange_rate_month:'2026-09',
   exchange_rate_mode:'manual',exchange_rate_status:'available',exchange_rate_to_cny:'.4',exchange_rate_date:null,exchange_rate_source:'manual',
   selling_price_cny:20,sales_revenue_cny:40,platform_fee_cny:4,profit_cny:26};
  const freshSummary={...oldSummary,exchange_rate_policy:'store_month_report_v1',fx_provisional_count:0,sales_revenue_cny:40,platform_fee_cny:4,gross_profit_cny:26};
  let rows=[old],summaries=[oldSummary];
  c.api=async(_method,url)=>url.startsWith('/sales/monthly-summary')?summaries:{items:rows,page:1,page_size:20,total:rows.length};
  const body=key=>cells(el('sales-tbody').innerHTML,'data-sales-value',key);
  const total=key=>cells(el('sales-total-summary-body').innerHTML,'data-sales-total',key)[0];
  await c.loadSales();el('sales-visible-all').listeners.click();
  for(const policy of [undefined,null,'daily_import','future-unknown']){
   rows=[{...old,exchange_rate_policy:policy}];summaries=[{...oldSummary,exchange_rate_policy:policy}];await c.loadSales();
   check(el('sales-currency-status').hidden,false);
   check(el('sales-currency-status').textContent.includes('重启后端服务'),true);
   check(el('sales-currency-status').textContent.includes('无需重新导入Excel'),true);
   for(const key of ['exchange_rate_to_cny','exchange_rate_date','exchange_rate_source','selling_price_cny','sales_revenue_cny','platform_fee_cny','profit_cny'])check(body(key),['后端待更新']);
   for(const key of ['sales_revenue_cny','platform_fee_cny','profit_cny'])check(total(key),'后端待更新');
   check(body('source_row_net_product_sales'),['100.00']);check(body('order_total_cost_cny'),['¥10.00']);
   check(body('quantity'),['2']);check(total('order_total_cost_cny'),'¥10.00');
   check(el('sales-tbody').innerHTML.includes('核算月份 2026-09'),false);
   check(el('sales-tbody').innerHTML.includes('old-daily'),false);
   check(el('sales-monthly-tbody').innerHTML.includes('¥1.23'),false);
   check((el('sales-monthly-tbody').innerHTML.match(/后端待更新/g)||[]).length,3);
   check(el('sales-monthly-tbody').innerHTML.includes('¥10.00'),true);
   check(el('sales-monthly-summary-note').textContent.includes('未确认使用店铺月度报表汇率'),true);
   check(el('sales-monthly-summary-note').textContent.includes('共用各店各月汇率'),false);
  }
  // 即使带口径码，缺少明细核算元数据时也不能从销售日期猜测月份。
  for(const invalid of [{exchange_rate_month:undefined},{exchange_rate_month:'2026-13'},{exchange_rate_mode:null},{exchange_rate_status:null}]){
   rows=[{...fresh,...invalid}];summaries=[freshSummary];await c.loadSales();
   check(body('exchange_rate_to_cny'),['后端待更新']);check(el('sales-currency-status').hidden,false);
  }
  // 页面明细和月度接口各自校验，不能互相证明；混合响应不能合计为已确认金额。
  rows=[fresh,{...old,id:2}];summaries=[freshSummary,{...oldSummary,month:'2026-08'}];await c.loadSales();
  check(body('sales_revenue_cny'),['¥40.00','后端待更新']);check(total('sales_revenue_cny'),'后端待更新');
  check(total('order_total_cost_cny'),'¥20.00');check(el('sales-monthly-tbody').innerHTML.includes('¥40.00'),true);
  check(el('sales-monthly-summary-note').textContent.includes('未确认'),true);
  // 完成后端更新后，重新查询恢复显示；服务端明确缺汇率属于业务待核对，不是版本不匹配。
  rows=[fresh];summaries=[freshSummary];await c.loadSales();
  check(el('sales-currency-status').hidden,true);check(body('exchange_rate_to_cny'),['0.40000000']);
  check(body('sales_revenue_cny'),['¥40.00']);check(total('sales_revenue_cny'),'¥40.00');
  check(el('sales-tbody').innerHTML.includes('核算月份 2026-09'),true);
  check(el('sales-monthly-summary-note').textContent.includes('共用各店各月汇率'),true);
  rows=[{...fresh,exchange_rate_to_cny:null,exchange_rate_status:'missing',sales_revenue_cny:null}];await c.loadSales();
  check(el('sales-currency-status').hidden,true);check(body('sales_revenue_cny'),['待核对']);
  rows=[];summaries=[];await c.loadSales();
  check(el('sales-currency-status').hidden,true);check(el('sales-monthly-summary-note').textContent,'2026-09');
 }
 console.log(JSON.stringify({page_copies:2,checks,scope:'actual ledger and monthly summary with old, mixed and updated FX contracts'}));
}
main().catch(error=>{console.error(error);process.exitCode=1;});
