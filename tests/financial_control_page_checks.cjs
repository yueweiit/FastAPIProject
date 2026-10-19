// 执行实际工作台脚本，控制响应顺序；不启动服务或连接业务库。
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const {webcrypto} = require('node:crypto');
let checks=0;
const equal=(actual,expected)=>{assert.deepEqual(actual,expected);checks++;};
const deferred=()=>{let resolve;const promise=new Promise(r=>resolve=r);return {promise,resolve};};
function setup() {
    const elements=new Map();
    const element=id=>{
        if(!elements.has(id))elements.set(id,{value:'',textContent:'',innerHTML:'',style:{},disabled:false,
            dataset:{},elements:{namedItem: name=>element(`${id}-${name}`)},
            querySelectorAll:()=>[],appendChild:()=>{},addEventListener:()=>{},reportValidity:()=>true});
        return elements.get(id);
    };
    element('fc-store').value='7';element('fc-month').value='2026-09';
    const source=fs.readFileSync('static/financial-control.js','utf8').replace('window.FinanceControl={',
        'window.test={state,initialize,refresh,write,dispatch,costPayload,renderCosts,key,open}; window.FinanceControl={');
    const context={window:{},document:{documentElement:{lang:'zh-CN'},getElementById:element,
        querySelectorAll:()=>[],createElement:()=>({style:{},dataset:{}})},
        escapeHtml:v=>String(v).replaceAll('&','&amp;').replaceAll('<','&lt;').replaceAll('>','&gt;').replaceAll('"','&quot;'),
        crypto:{randomUUID:()=>`request-${++context.requestId}`},requestId:0,
        api:async()=>{throw new Error('Unexpected API call');},isAdmin:()=>true,defaultProfitLossMonth:()=> '2026-09',
        FormData:class {constructor(form){return Object.entries(form.formValues);}},URLSearchParams,Date,Number,Error,
    };
    vm.createContext(context);vm.runInContext(source,context);
    context.window.test.state.config={can_write:true,can_post:true,expense_categories:{advertising:'广告'},breakdown_categories:{tax:'税'}};
    return {context,element,...context.window.test};
}
const response=(path,store)=>path.includes('/overview')?{profit_layers:{a:store,b:store,c:store},warnings:[`store-${store}`]}:
    path.includes('/expenses')?{sources:{},items:[],total:0}:path.includes('/reconciliation')?{entries:[],payouts:[],receipts:[]}:[];
async function main(){
    for(const file of ['templates/index.html','index.html']){
        const html=fs.readFileSync(file,'utf8');
        equal(html.includes('src="/financial-control.js"'),true);
        equal(html.includes('id="financial-control-root"'),true);
        equal(html.includes("row.input_source !== 'ledger'"),true);
    }
    {
        const t=setup(),ids=[];
        // 非本机HTTP环境提供getRandomValues，但不提供randomUUID。
        t.context.crypto={getRandomValues:bytes=>webcrypto.getRandomValues(bytes)};
        for(const name of ['fc-cost-form','fc-expense-form','fc-receipt-form','fc-version-form']){
            const id=t.key(name);ids.push(id);
            equal(/^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/.test(id),true);
            equal(t.key(name),id);
        }
        equal(new Set(ids).size,4);
        t.context.api=async()=>{throw new Error('network lost');};
        await assert.rejects(t.write('fc-expense-form','POST','/expenses',{request_key:ids[1]}),/network lost/);checks++;
        equal(t.key('fc-expense-form'),ids[1]);
        t.context.crypto=undefined;
        assert.throws(()=>t.key('unsupported-form'),/不支持安全请求标识/);checks++;
    }
    {
        const t=setup();t.initialize();t.initialize();
        equal(typeof t.element('financial-control-root').onclick,'function');
        const first=[],second=[];let wave=first;
        t.context.api=(_,path)=>{const d=deferred();wave.push({d,path});return d.promise;};
        t.element('fc-expenses').textContent='old store';
        const a=t.refresh();equal(t.element('fc-expenses').textContent,'');
        t.element('fc-store').value='8';wave=second;const b=t.refresh();
        for(const {d,path} of second)d.resolve(response(path,8));await b;
        equal(t.element('fc-overview').innerHTML.includes('store-8'),true);
        for(const {d,path} of first)d.resolve(response(path,7));await a;
        equal(t.element('fc-overview').innerHTML.includes('store-7'),false);
        equal(t.element('fc-overview').innerHTML.includes('store-8'),true);
        t.renderCosts([{id:1,source_ref:'<img src=x>',category:'purchase',amount_cny:'1.23',allocations:[],created_at:'',reverses_id:null}]);
        equal(t.element('fc-cost-list').innerHTML.includes('&lt;img'),true);
        equal(t.element('fc-cost-list').innerHTML.includes('<img'),false);
    }
    {
        const t=setup(),d=deferred();t.state.config=null;
        t.context.api=()=>d.promise;
        const task=t.open();t.context.window.FinanceControl.reset();d.resolve({enabled:false});await task;
        equal(t.state.config,null);equal(t.state.initialized,false);
    }
    {
        const t=setup(),d=deferred();let posts=0;
        t.context.api=(method,path)=>method==='POST'?(posts++,d.promise):Promise.resolve(response(path,7));
        const task=t.write('expense','POST','/expenses',{amount:'0.1234'});
        await assert.rejects(t.write('expense','POST','/expenses',{}),/请等待/);checks++;
        equal(posts,1);d.resolve({id:1});await task;equal(t.state.busy,false);
    }
    {
        const t=setup(),form=t.element('fc-cost-form');
        form.formValues={amount:'123456.0001',currency:'CNY',exchange_rate:'1',category:'head_shipping',allocation_basis:'weight'};
        form.elements.is_final={checked:true};
        t.element('fc-allocations').querySelectorAll=()=>[{querySelector:selector=>({value:selector.includes('batch_id')?'42':'0.00000001'})}];
        const first=t.costPayload(),second=t.costPayload();
        equal(first.amount,'123456.0001');equal(first.allocations[0].weight,'0.00000001');equal(first.request_key,second.request_key);
        await t.dispatch('add-batch',{});
        equal(t.costPayload().request_key===first.request_key,false);
        t.state.preview='old-signature';
        await assert.rejects(t.dispatch('post-cost',{}),/请先预览/);checks++;
    }
    console.log(JSON.stringify({checks,scope:'actual workbench functions in isolated VM; no browser or database'}));
}
main().catch(error=>{console.error(error);process.exitCode=1;});
