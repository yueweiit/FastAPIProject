// 两份实际页面函数与既有语言字典；无服务、凭据或业务数据库。
const assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
let checks=0;const check=(a,b)=>{assert.deepEqual(a,b);checks++;};
const labelsSource=fs.readFileSync('static/page-interactions.js','utf8');
const start=labelsSource.indexOf('const messages = ')+17;
const labels=vm.runInNewContext('('+labelsSource.slice(start,labelsSource.indexOf('\n    const ',start)).trim().replace(/;$/,'')+')')['zh-CN'];
async function main(){
 for(const file of ['templates/index.html','index.html']){
  const source=fs.readFileSync(file,'utf8'),nodes=new Map(),calls=[];
  const el=id=>{if(!nodes.has(id))nodes.set(id,{value:'',innerHTML:''});return nodes.get(id);};
  el('profit-loss-month').value='2026-09';el('profit-loss-store').value='7';el('inventory-impairment-date').value='2026-09-30';
  let rows=[{group_by:'product',name:'P10 · <商品>',current_quantity:4,sku_count:1,current_revenue:100,current_cost:40,
    current_gross:60,current_margin:.6,previous_margin:null,margin_change:null,ytd_revenue:100,ytd_gross:60,ytd_margin:.6,note:'<核对>'},
   {group_by:'product',name:'P11 · 商品',current_quantity:2,current_cost:null,current_gross:null},
   {group_by:'product',name:'未分配商品（结算调整/赔付）',current_quantity:0,current_revenue:5,current_cost:0,current_gross:5}];
  let admin=true,error=null;
  const context=vm.createContext({URLSearchParams,Date,document:{getElementById:el,documentElement:{lang:'zh-CN'}},
   window:{PageInteractions:{text:key=>labels[key]||key}},isAdmin:()=>admin,
   api:async(method,url)=>{calls.push({method,url});if(error)throw Error(error);return rows;}});
  for(const name of ['escapeHtml','reportMoney','reportPercent','defaultProfitLossMonth','loadProductLineProfit']){
   const fn=source.match(new RegExp('^(?:async )?function '+name+'\\([^\\n]*\\) \\{[\\s\\S]*?^\\}','m'));
   assert(fn,name);vm.runInContext(fn[0],context);
  }
  const load=()=>vm.runInContext('loadProductLineProfit()',context);
  await load();let html=el('product-line-profit-tbody').innerHTML;
  check((html.match(/<tr>/g)||[]).length,3);check(html.includes('P10 · &lt;商品&gt;'),true);
  check(html.includes('<td>4</td>'),true);check(html.includes('<td>2</td>'),true);check(html.includes('<td>0</td>'),true);
  check(html.includes('<td>-</td>'),true);check(html.includes('&lt;核对&gt;'),true);
  let query=new URL('http://unit.invalid'+calls.at(-1).url).searchParams;
  check(query.get('report_month'),'2026-09-01');check(query.get('report_date'),'2026-09-30');check(query.get('store_id'),'7');
  admin=false;await load();check(new URL('http://unit.invalid'+calls.at(-1).url).searchParams.has('store_id'),false);
  rows=[{name:'旧分组',sku_count:21}];await load();
  check(el('product-line-profit-tbody').innerHTML.includes(labels.productProfitUnavailable),true);
  rows=[];await load();check(el('product-line-profit-tbody').innerHTML.includes(labels.productProfitEmpty),true);
  error='<失败>';await load();check(el('product-line-profit-tbody').innerHTML.includes('&lt;失败&gt;'),true);
  check(source.includes('>各产品线毛利分析表</h3>'),true);check(source.includes('>产品线毛利</button>'),true);
  for(const key of ['productProfitHelp','productProfitProduct','productProfitQuantity']){
   check(source.includes('data-profit-loss-text="'+key+'"'),true);check(typeof labels[key],'string');
  }
  check(calls.every(call=>call.method==='GET'),true);
 }
 console.log(JSON.stringify({pages:2,checks,scope:'real product-profit page functions, labels, quantities, scope, failures and old-backend guard'}));
}
main().catch(error=>{console.error(error);process.exitCode=1;});
