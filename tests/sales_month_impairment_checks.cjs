// 实际销售模块：月初公式、整单与本页合计、旧接口保护，成本和金额未知状态保持区分。
const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict');
let checks=0;
const check=(actual,expected)=>{assert.deepEqual(actual,expected);checks++;};
const resource=JSON.parse(fs.readFileSync('static/sales-fields.zh-CN.json','utf8'));
const values=(html,attr,key)=>[...html.matchAll(new RegExp(`<td[^>]*${attr}="${key}"[^>]*>([\\s\\S]*?)<\\/td>`,'g'))].map(m=>m[1]);
async function main(){
 for(const file of ['templates/index.html','index.html']){
  const html=fs.readFileSync(file,'utf8'),elements=new Map(),listeners={};
  check(html.includes('/sales-ledger.js'),true);
  const el=id=>{if(!elements.has(id))elements.set(id,{innerHTML:'',textContent:'',style:{},open:false,scrollLeft:0,scrollTop:0,
   addEventListener(){},replaceChildren(){this.innerHTML='';},showModal(){this.open=true;},close(){this.open=false;}});return elements.get(id);};
  const sale={id:1,product_id:1,order_no:'ORDER',quantity:1,sold_at:'2026-09-30T23:59:59',total_cost:'22.64',order_total_cost_cny:'22.64',
   impairment_policy:'sale_month_elapsed_v1',impairment_cny:'3.2828',currency:'CNY',exchange_rate_policy:'store_month_report_v1',
   exchange_rate_month:'2026-09',exchange_rate_mode:'auto',exchange_rate_status:'available',exchange_rate_to_cny:1,
   selling_price_cny:50,sales_revenue_cny:50,platform_fee_cny:0,profit_cny:'27.36',
   source_row_net_product_sales:50,source_row_settlement_total:50,allocated_net_product_sales:50,allocated_settlement_total:50,
   unit_purchase_cost_cny:'12.3',purchase_cost_cny:'12.3',unit_head_shipping_cost_cny:'10.34',head_shipping_cost_cny:'10.34',
   unit_last_mile_cost_cny:0,last_mile_cost_cny:0,unit_other_cost_cny:0,other_cost_cny:0,unit_total_cost_cny:'22.64'};
  const batch={batch_id:1,batch_no:'OLD-STOCK',quantity:1,unit_cost:'22.64',arrival_date:'2026-08-01',order_date:'2026-09-30',
   sale_date:'2026-09-30',month_start:'2026-09-01',age_days:29,product_type:'new',daily_rate:'.005',total_cost_cny:'22.64',impairment_cny:'3.2828'};
  let data={sale,sku:'SKU',product_name:'商品',cost_batches:[{...batch,purchase_cost_cny:'12.3',head_shipping_cost_cny:'10.34',last_mile_cost_cny:0,other_cost_cny:0}],
   impairment_batches:[batch],order_lines:[sale],order_summary:{line_count:1,total_cost_cny:'22.64',impairment_cny:'3.2828'}};
  const c=vm.createContext({window:{},AbortController,authHeaders:()=>({}),
   document:{documentElement:{lang:'zh-CN'},getElementById:el,querySelectorAll:()=>[],addEventListener(type,listener){(listeners[type]??=[]).push(listener);}},
   escapeHtml:v=>String(v??'').replaceAll('&','&amp;').replaceAll('<','&lt;').replaceAll('>','&gt;').replaceAll('"','&quot;'),
   fetch:async url=>({ok:true,status:200,json:async()=>url==='/sales-fields.json'?resource:data}),
  });
  vm.runInContext(fs.readFileSync('static/page-interactions.js','utf8'),c);
  vm.runInContext(fs.readFileSync('static/sales-ledger.js','utf8'),c);
  await c.window.SalesLedger.ready();
  const render=rows=>c.window.SalesLedger.render(rows,{});
  const body=key=>values(el('sales-tbody').innerHTML,'data-sales-value',key);
  const total=key=>values(el('sales-total-summary-body').innerHTML,'data-sales-total',key)[0];
  async function open(){
   for(const listener of listeners.click||[])listener({target:{closest:selector=>selector==='[data-sale-calculation]'?{dataset:{saleCalculation:'1'}}:null}});
   await new Promise(resolve=>setImmediate(resolve));
   check(el('sales-calc-dialog').open,true);
   check(el('sales-calc-status').textContent,'');
   return el('sales-calc-body').innerHTML;
  }
  render([sale]);
  check(body('impairment_cny'),['¥3.28']);check(total('impairment_cny'),'¥3.28');
  check(body('order_total_cost_cny'),['¥22.64']);check(body('profit_cny'),['¥27.36']);
  check(el('sales-currency-status').hidden,true);
  const detail=await open();
  for(const text of ['销售日期','销售当月月初','当月相隔天数','2026-09-01','2026-09-30','2026-08-01','29 × 0.5% × ¥22.64 = ¥3.28'])check(detail.includes(text),true);
  check(detail.includes('60 ×'),false);
  const formula=resource.fields.find(field=>field.key==='impairment_cny');
  check(formula.formula,'Σ[(销售日期−销售当月月初日期)×日比例×本批订单成本]');
  check(formula.note.includes('月初当天0天'),true);
  // 混合接口版本时，仅跌价列和合计待更新；原成本、利润保留。
  for(const policy of [undefined,null,'holding_days_v0']){
   const old={...sale,id:2,impairment_policy:policy,impairment_cny:'6.792'};
   render([sale,old]);check(body('impairment_cny'),['¥3.28','后端待更新']);check(total('impairment_cny'),'后端待更新');
   check(body('order_total_cost_cny'),['¥22.64','¥22.64']);check(body('profit_cny'),['¥27.36','¥27.36']);
   check(el('sales-currency-status').hidden,false);
   check(el('sales-currency-status').textContent.includes('无需重新导入销售'),true);
   data={...data,sale:old,order_lines:[old],order_summary:{...data.order_summary,impairment_cny:'6.792'},
    impairment_batches:[{...batch,age_days:60,impairment_cny:'6.792',sale_date:undefined,month_start:undefined}]};
   const oldDetail=await open();
   check(oldDetail.includes('当前接口未确认采用销售当月跌价公式'),true);
   check(oldDetail.includes('¥6.79'),false);check(oldDetail.includes('60 ×'),false);
   check(oldDetail.includes('¥22.64'),true);
  }
  // 已更新接口的真实未知仍待核对；零值不会被兼容保护误隐藏。
  render([{...sale,impairment_cny:null,impairment_review_reason:'订单创建日期缺失'}]);
  check(body('impairment_cny'),['待核对']);check(total('impairment_cny'),'待核对');check(el('sales-currency-status').hidden,true);
  render([{...sale,impairment_cny:0,sold_at:'2026-10-01T00:00:00'}]);check(body('impairment_cny'),['¥0.00']);check(total('impairment_cny'),'¥0.00');
  data={...data,sale,order_lines:[sale],impairment_batches:[batch],order_summary:{...data.order_summary,impairment_cny:'3.2828'}};
  check((await open()).includes('29 × 0.5% × ¥22.64 = ¥3.28'),true);
  render([]);check(el('sales-currency-status').hidden,true);
 }
 console.log(JSON.stringify({page_copies:2,checks,scope:'monthly impairment list, calculation dialog, totals, formula and old API guard'}));
}
main().catch(error=>{console.error(error);process.exitCode=1;});
