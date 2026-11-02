// Real module: multi-column geometry metadata, preferences and failure isolation.
const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict');
const resource=JSON.parse(fs.readFileSync('static/sales-fields.zh-CN.json','utf8'));
const original=JSON.stringify(resource);
let checks=0;const check=(a,b)=>{assert.deepEqual(a,b);checks++;};
const key=user=>`fifo.sales.ledger.pin.v2.user:${user}`;
const legacy=user=>`fifo.sales.ledger.pin.v1.user:${user}`;
const hiddenKey=user=>`fifo.sales.ledger.hidden.v1.user:${user}`;
function setup(saved=new Map(),blocked=false){
 const nodes=new Map(),media={matches:false,addEventListener(_event,fn){this.change=fn;}},events={},docEvents={};
 const node=id=>{if(!nodes.has(id))nodes.set(id,{innerHTML:'',textContent:'',value:'',checked:false,style:{},disabled:true,scrollLeft:0,scrollTop:0,listeners:{},attrs:{},
  setAttribute(k,v){this.attrs[k]=v;},getAttribute(k){return this.attrs[k]??null;},focus(){this.focused=true;},
  addEventListener(event,fn){this.listeners[event]=fn;},replaceChildren(){this.innerHTML='';}});return nodes.get(id);};
 let fetches=0;
 const context=vm.createContext({window:{addEventListener:(name,fn)=>{events[name]=fn;},matchMedia:()=>media,
  localStorage:{getItem(k){if(blocked)throw Error('disabled');return saved.get(k)??null;},setItem(k,v){if(blocked)throw Error('quota');saved.set(k,v);}}},
  document:{documentElement:{lang:'zh-CN'},getElementById:node,querySelectorAll:()=>[],addEventListener:(name,fn)=>{docEvents[name]=fn;}},
  escapeHtml:v=>String(v??'').replaceAll('&','&amp;').replaceAll('<','&lt;').replaceAll('>','&gt;').replaceAll('"','&quot;'),
  fetch:async()=>{fetches++;return {ok:true,json:async()=>resource};}});
 vm.runInContext('let CURRENT_USER_ID=7;',context);
 vm.runInContext(fs.readFileSync('static/sales-ledger.js','utf8'),context);
 const clear=()=>node('sales-pin-clear').listeners.click();
 const toggle=(field,checked=true)=>node('sales-pin-field').listeners.change({target:{dataset:{salesPinKey:field},checked}});
 const choose=fields=>{clear();for(const field of fields)toggle(field);};
 const show=(field,checked=true)=>{const target=node('sales-visible-option-'+field);target.dataset={salesVisibleKey:field};target.checked=checked;node('sales-visible-field').listeners.change({target});};
 const showAll=()=>node('sales-visible-all').listeners.click();
 return {context,node,media,saved,events,docEvents,clear,toggle,choose,show,showAll,ui:context.window.SalesLedger,fetches:()=>fetches};
}
const fields=html=>[...html.matchAll(/data-sales-field="([^"]+)"/g)].map(m=>m[1]);
const rowFields=html=>[...html.matchAll(/data-sales-value="([^"]+)"/g)].map(m=>m[1]);
const totalFields=html=>[...html.matchAll(/data-sales-total="([^"]+)"/g)].map(m=>m[1]);
const offsets=(html,attribute)=>[...html.matchAll(new RegExp(attribute+'="([^"]+)"[^>]*style="left:(\\d+)px"','g'))].map(m=>[m[1],Number(m[2])]);
const rows=[{id:1,product_id:1,order_no:'INTERNAL',display_order_no:'00017-A',quantity:2,source_quantity:1,total_cost:20,
 purchase_cost_cny:8,unit_purchase_cost_cny:4,impairment_cny:null,display_name:'<商品>'}];
async function main(){
 const defaultHidden=['currency','source_row_net_product_sales','source_row_settlement_total','exchange_rate_to_cny','exchange_rate_date','exchange_rate_source'];
 check(resource.fields.filter(field=>field.default_hidden===true).map(field=>field.key),defaultHidden);
 const defaults=setup();await defaults.ui.ready();defaults.ui.render(rows,{});
 const initiallyVisible=resource.fields.filter(field=>!defaultHidden.includes(field.key)).map(field=>field.key);
 const expectedDefault=['order_no',...initiallyVisible.filter(key=>key!=='order_no')];
 check(fields(defaults.node('sales-ledger-head').innerHTML),expectedDefault);
 check(rowFields(defaults.node('sales-tbody').innerHTML),expectedDefault);
 check(totalFields(defaults.node('sales-total-summary-body').innerHTML),expectedDefault);
 check(defaults.node('sales-visible-summary').textContent,'已隐藏 6 列');
 check(defaults.saved.has(hiddenKey(7)),false);
 for(const field of defaultHidden)check(defaults.node('sales-visible-option-'+field).checked,false);
 defaults.show('currency');const changed=setup(defaults.saved);await changed.ui.ready();changed.ui.render(rows,{});
 check(fields(changed.node('sales-ledger-head').innerHTML).includes('currency'),true);
 check(fields(changed.node('sales-ledger-head').innerHTML).includes('exchange_rate_to_cny'),false);
 changed.showAll();const allReload=setup(changed.saved);await allReload.ui.ready();allReload.ui.render(rows,{});
 check(fields(allReload.node('sales-ledger-head').innerHTML).length,resource.fields.length);
 const custom=setup(new Map([[hiddenKey(7),'["sku"]']]));await custom.ui.ready();custom.ui.render(rows,{});
 check(fields(custom.node('sales-ledger-head').innerHTML).includes('sku'),false);
 check(fields(custom.node('sales-ledger-head').innerHTML).includes('currency'),true);
 const s=setup();await s.ui.ready();s.ui.render(rows,{});s.showAll();
 check(fields(s.node('sales-ledger-head').innerHTML)[0],'order_no');
 check(s.node('sales-pin-field').innerHTML.includes('data-sales-pin-key="calculation_action"'),false);
 check((s.node('sales-pin-field').innerHTML.match(/type="checkbox"/g)||[]).length,resource.fields.length-1);
 for(const meta of resource.fields.filter(field=>field.kind!=='action')){
  s.node('sales-ledger-scroll').scrollLeft=800;s.node('sales-ledger-scroll').scrollTop=170;s.choose([meta.key]);
  const head=s.node('sales-ledger-head').innerHTML,body=s.node('sales-tbody').innerHTML,total=s.node('sales-total-summary-body').innerHTML;
  const expected=[meta.key,...resource.fields.filter(f=>f.key!==meta.key).map(f=>f.key)];
  check(fields(head),expected);check(rowFields(body),expected);check(totalFields(total),expected);
  check((head.match(/<tr>/g)||[]).length,1);check(head.includes('scope="colgroup"'),false);
  check(offsets(head,'data-sales-field'),[[meta.key,0]]);check(offsets(body,'data-sales-value'),[[meta.key,0]]);
  check(offsets(total,'data-sales-total'),[[meta.key,0]]);
  const ids=[...head.matchAll(/ id="([^"]+)"/g)].map(m=>m[1]);check(new Set(ids).size,ids.length);
  check(s.node('sales-ledger-scroll').scrollTop,170);check(s.node('sales-ledger-scroll').scrollLeft,800);
  check(s.saved.get(key(7)),JSON.stringify([meta.key]));check(s.fetches(),1);
 }
 s.choose(['purchase_cost_cny','order_no','store_name']);
 const expected=['store_name','order_no','purchase_cost_cny'];
 check(fields(s.node('sales-ledger-head').innerHTML).slice(0,3),expected);
 const wanted=[['store_name',0],['order_no',120],['purchase_cost_cny',320]];
 check(offsets(s.node('sales-ledger-head').innerHTML,'data-sales-field'),wanted);
 check(offsets(s.node('sales-tbody').innerHTML,'data-sales-value'),wanted);
 check(offsets(s.node('sales-total-summary-body').innerHTML,'data-sales-total'),wanted);
 check(s.node('sales-pin-summary').textContent,'已固定 3 列');
 check(JSON.parse(s.saved.get(key(7))),expected);
 s.toggle('order_no',false);check(fields(s.node('sales-ledger-head').innerHTML).slice(0,2),['store_name','purchase_cost_cny']);
 check(s.node('sales-pin-option-order_no').checked,false);
 s.choose(['unit_purchase_cost_cny','purchase_cost_cny']);
 check(s.node('sales-total-summary-body').innerHTML.match(/data-sales-total="unit_purchase_cost_cny"[^>]*>(.*?)<\/td>/)[1],'');
 check(s.node('sales-total-summary-body').innerHTML.includes('>¥8.00</td>'),true);
 check(s.node('sales-total-summary-body').innerHTML.includes('>待核对</td>'),true);
 s.choose(['store_name','order_no','sku','product_name']);const saved=s.saved.get(key(7));
 s.node('sales-ledger-scroll').clientWidth=280;s.media.matches=true;s.media.change();
 check(offsets(s.node('sales-ledger-head').innerHTML,'data-sales-field').length,1);
 check(s.node('sales-pin-summary').textContent,'固定 1 / 4 列');check(s.node('sales-pin-limit').hidden,false);
 check(s.saved.get(key(7)),saved);
 s.node('sales-ledger-scroll').clientWidth=1300;s.media.matches=false;s.media.change();
 check(offsets(s.node('sales-ledger-head').innerHTML,'data-sales-field').length,4);
 check(s.node('sales-pin-limit').hidden,true);
 s.node('sales-ledger-scroll').clientWidth=700;s.events.resize();
 check(offsets(s.node('sales-ledger-head').innerHTML,'data-sales-field').length,3);
 check(s.saved.get(key(7)),saved);
 s.clear();check(fields(s.node('sales-ledger-head').innerHTML),resource.fields.map(f=>f.key));
 check(offsets(s.node('sales-ledger-head').innerHTML,'data-sales-field'),[]);
 check(s.node('sales-pin-summary').textContent,'不固定');
 s.choose(['product_name']);s.ui.reset();check(s.node('sales-tbody').innerHTML,'');check(s.node('sales-pin-clear').disabled,true);
 vm.runInContext('CURRENT_USER_ID=8;',s.context);s.ui.render(rows,{});check(fields(s.node('sales-ledger-head').innerHTML)[0],'order_no');
 s.choose(['profit_cny']);s.ui.reset();vm.runInContext('CURRENT_USER_ID=7;',s.context);s.ui.render(rows,{});
 check(fields(s.node('sales-ledger-head').innerHTML)[0],'product_name');
 const reload=setup(s.saved);await reload.ui.ready();reload.ui.render(rows,{});check(fields(reload.node('sales-ledger-head').innerHTML)[0],'product_name');
 const token=s.ui.beginLoad();s.ui.fail(new Error('failure'),token);s.choose(['sku']);check(s.node('sales-tbody').innerHTML.includes('failure'),true);
 check(s.node('sales-total-summary-body').innerHTML,'');check(s.node('sales-tbody').innerHTML.includes('00017-A'),false);
 s.ui.render([],{});s.choose(['quantity']);check(s.node('sales-tbody').innerHTML.includes(`colspan="${resource.fields.length}"`),true);
 for(const bad of ['["calculation_action"]','["<script>bad</script>"]','invalid','{}']){
  const invalid=setup(new Map([[key(7),bad]]));await invalid.ui.ready();invalid.ui.render(rows,{});
  check(fields(invalid.node('sales-ledger-head').innerHTML)[0],'order_no');check(invalid.node('sales-ledger-head').innerHTML.includes('<script>'),false);
 }
 for(const value of ['product_name','none']){
  const migrated=setup(new Map([[legacy(7),value]]));await migrated.ui.ready();migrated.ui.render(rows,{});
  check(fields(migrated.node('sales-ledger-head').innerHTML)[0],value==='none'?'store_name':'product_name');
  check(offsets(migrated.node('sales-ledger-head').innerHTML,'data-sales-field').length,value==='none'?0:1);
 }
 const unavailable=setup(new Map(),true);await unavailable.ui.ready();unavailable.ui.render(rows,{});unavailable.choose(['sku','product_name']);
 check(fields(unavailable.node('sales-ledger-head').innerHTML).slice(0,2),['sku','product_name']);
 s.node('sales-pin-picker').open=true;s.docEvents.keydown({key:'Escape',preventDefault(){}});check(s.node('sales-pin-picker').open,false);
 check(s.node('sales-pin-toggle').focused,true);check(JSON.stringify(resource),original);
 s.node('sales-ledger-scroll').scrollLeft=1600;s.ui.render(rows,{});
 s.node('sales-total-scroll').scrollLeft=0;s.node('sales-ledger-scroll').listeners.scroll();
 check(s.node('sales-total-scroll').scrollLeft,0);
 s.node('sales-total-scroll').listeners.scroll();check(s.node('sales-ledger-scroll').scrollLeft,0);
 const v=setup();await v.ui.ready();v.ui.render(rows,{});v.showAll();
 check((v.node('sales-visible-field').innerHTML.match(/data-sales-visible-key=/g)||[]).length,resource.fields.length);
 check(v.node('sales-visible-summary').textContent,'全部显示');
 const width=parseInt(v.node('sales-ledger-table').style.width,10);
 v.choose(['store_name','order_no','purchase_cost_cny']);const pinPreference=v.saved.get(key(7));
 for(const meta of resource.fields){
  v.show(meta.key,false);
  check(fields(v.node('sales-ledger-head').innerHTML).includes(meta.key),false);
  check((v.node('sales-ledger-head').innerHTML.match(/<tr>/g)||[]).length,1);
  check(rowFields(v.node('sales-tbody').innerHTML).includes(meta.key),false);
  check(totalFields(v.node('sales-total-summary-body').innerHTML).includes(meta.key),false);
  check((v.node('sales-ledger-cols').innerHTML.match(/<col /g)||[]).length,resource.fields.length-1);
  check(v.node('sales-total-cols').innerHTML,v.node('sales-ledger-cols').innerHTML);
  check(parseInt(v.node('sales-ledger-table').style.width,10),width-meta.width);
  check(v.node('sales-visible-summary').textContent,'已隐藏 1 列');
  if(meta.kind!=='action')check(v.node('sales-pin-option-'+meta.key).disabled,true);
  v.show(meta.key);
  check(fields(v.node('sales-ledger-head').innerHTML).includes(meta.key),true);
 }
 v.show('order_no',false);
 check(JSON.parse(v.saved.get(hiddenKey(7))),['order_no']);check(v.saved.get(key(7)),pinPreference);
 check(offsets(v.node('sales-ledger-head').innerHTML,'data-sales-field'),[['store_name',0],['purchase_cost_cny',120]]);
 check(v.node('sales-pin-summary').textContent,'已固定 2 列');
 v.toggle('order_no',false);check(v.saved.get(key(7)),pinPreference);
 v.show('order_no');check(offsets(v.node('sales-ledger-head').innerHTML,'data-sales-field'),wanted);
 check(v.node('sales-pin-option-order_no').disabled,false);
 v.show('purchase_cost_cny',false);v.show('calculation_action',false);
 const visibleReload=setup(v.saved);await visibleReload.ui.ready();visibleReload.ui.render(rows,{});
 check(fields(visibleReload.node('sales-ledger-head').innerHTML).includes('purchase_cost_cny'),false);
 check(fields(visibleReload.node('sales-ledger-head').innerHTML).includes('calculation_action'),false);
 check(visibleReload.node('sales-pin-option-purchase_cost_cny').checked,true);
 v.ui.reset();check(v.node('sales-visible-field').innerHTML,'');check(v.node('sales-visible-all').disabled,true);
 vm.runInContext('CURRENT_USER_ID=8;',v.context);v.ui.render(rows,{});
 check(fields(v.node('sales-ledger-head').innerHTML).length,resource.fields.length-defaultHidden.length);
 v.show('sku',false);v.ui.reset();vm.runInContext('CURRENT_USER_ID=7;',v.context);v.ui.render(rows,{});
 check(fields(v.node('sales-ledger-head').innerHTML).includes('sku'),true);
 check(fields(v.node('sales-ledger-head').innerHTML).includes('purchase_cost_cny'),false);
 v.showAll();check(v.node('sales-visible-summary').textContent,'全部显示');
 for(const meta of resource.fields.slice(0,-1))v.show(meta.key,false);
 check(fields(v.node('sales-ledger-head').innerHTML),['calculation_action']);
 v.show('calculation_action',false);
 check(v.node('sales-visible-option-calculation_action').checked,true);
 check(v.node('sales-visible-status').textContent,'请至少保留一个显示字段。');
 check(fields(v.node('sales-ledger-head').innerHTML),['calculation_action']);
 v.showAll();check(v.node('sales-visible-status').textContent,'');
 check(JSON.parse(v.saved.get(hiddenKey(7))),[]);check(v.saved.get(key(7)),pinPreference);
 v.ui.render([],{});v.show('quantity',false);
 check(v.node('sales-tbody').innerHTML.includes(`colspan="${resource.fields.length-1}"`),true);
 v.ui.render(rows,{});const failing=v.ui.beginLoad();v.ui.fail(new Error('visibility failure'),failing);
 const errorCell={colSpan:resource.fields.length-1};v.node('sales-tbody').querySelector=()=>errorCell;
 v.show('order_no',false);check(errorCell.colSpan,resource.fields.length-2);
 check(v.node('sales-tbody').innerHTML.includes('visibility failure'),true);
 check(v.node('sales-tbody').innerHTML.includes('00017-A'),false);
 check(v.node('sales-total-summary-body').innerHTML,'');
 v.showAll();check(errorCell.colSpan,resource.fields.length);check(v.fetches(),1);
 for(const bad of ['["<script>bad</script>"]','invalid','{}',JSON.stringify(resource.fields.map(f=>f.key))]){
  const invalid=setup(new Map([[hiddenKey(7),bad]]));await invalid.ui.ready();invalid.ui.render(rows,{});
  check(fields(invalid.node('sales-ledger-head').innerHTML).length,resource.fields.length-defaultHidden.length);
 }
 unavailable.show('sku',false);check(rowFields(unavailable.node('sales-tbody').innerHTML).includes('sku'),false);
 unavailable.showAll();check(rowFields(unavailable.node('sales-tbody').innerHTML).includes('sku'),true);
 v.node('sales-visible-picker').open=true;v.docEvents.keydown({key:'Escape',preventDefault(){}});
 check(v.node('sales-visible-picker').open,false);check(v.node('sales-visible-toggle').focused,true);
 v.node('sales-visible-picker').open=true;v.node('sales-visible-picker').contains=()=>false;
 v.docEvents.click({target:{}});check(v.node('sales-visible-picker').open,false);
 check(JSON.stringify(resource),original);
 console.log(JSON.stringify({checks,scope:'real column visibility and pinning, all fields, offsets, preferences, minimum column, storage and failure isolation'}));
}
main().catch(e=>{console.error(e);process.exitCode=1;});
