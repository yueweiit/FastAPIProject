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
        if(!elements.has(id)) {
            const node={value:'',textContent:'',style:{},disabled:false,hidden:false,dataset:{},
                elements:{namedItem: name=>element(`${id}-${name}`)},
                querySelectorAll:()=>[],querySelector:()=>null,appendChild:()=>{},
                listeners:{},addEventListener(type,handler){this.listeners[type]=handler;},reportValidity:()=>true};
            Object.defineProperty(node,'innerHTML',{get(){return this.html||'';},set(value){
                this.html=value;
                if(id==='fc-store') {
                    this.options=[...value.matchAll(/<option value="([^"]*)">([^<]*)<\/option>/g)].map(m=>({value:m[1],text:m[2]}));
                    this.value=this.options[0]?.value||'';
                }
            }});
            elements.set(id,node);
        }
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
    context.window.test.state.config={enabled:true,can_write:true,can_post:true,expense_categories:{advertising:'广告'},breakdown_categories:{tax:'税'}};
    context.window.test.state.storesReady=true;
    return {context,element,...context.window.test};
}
const response=(path,store)=>path.includes('/overview')?{profit_layers:{a:store,b:store,c:store},warnings:[`store-${store}`]}:
    path.includes('/expenses')?{sources:{},items:[],total:0}:path.includes('/reconciliation')?{entries:[],payouts:[],receipts:[]}:[];
function metadata(t,{enabled=true,role='admin',bound=7,stores=[{id:7,name:'店铺7'},{id:8,name:'店铺8'}],batches=[]}={}) {
    t.context.isAdmin=()=>role==='admin';
    return async(_method,path)=>path==='/financial-control/config'?{...t.state.config,enabled,
        can_write:role!=='viewer',can_post:role==='admin',expense_categories:{advertising:'广告'},breakdown_categories:{tax:'税'}}:
        path==='/finance-masters/stores'?stores:path==='/auth/me'?{id:1,store_id:bound}:
        path.startsWith('/batches?')?{items:batches}:response(path,7);
}
async function main(){
    for(const file of ['templates/index.html','index.html']){
        const html=fs.readFileSync(file,'utf8');
        equal(html.includes('src="/financial-control.js"'),true);
        equal(html.includes('id="financial-control-root"'),true);
        equal(html.includes("row.input_source !== 'ledger'"),true);
    }
    {
        const t=setup(),calls=[];t.state.config=null;t.element('fc-month').value='';t.element('fc-store').value='';
        const api=metadata(t,{enabled:false});t.context.api=(method,path)=>{calls.push(path);return api(method,path);};
        await t.open();
        equal(t.element('fc-month').value,'2026-09');equal(t.element('fc-store').value,'7');
        equal(t.element('fc-store').options.length,2);equal(t.element('fc-body').hidden,true);
        equal(t.element('fc-notice').textContent.includes('尚未启用'),true);
        await t.refresh();equal(t.element('fc-notice').textContent.includes('尚未启用'),true);
        await assert.rejects(t.dispatch('preview-cost',{}),/尚未启用/);checks++;
        equal(calls.some(path=>path.startsWith('/batches?')||path.includes('/overview')),false);
        t.context.api=metadata(t);await t.dispatch('refresh',{});
        equal(t.state.config.enabled,true);equal(t.element('fc-body').hidden,false);
    }
    {
        const t=setup(),batch=deferred(),calls=[];t.state.config=null;t.element('fc-month').value='';
        const api=metadata(t);t.context.api=(method,path)=>{calls.push(path);return path.startsWith('/batches?')?batch.promise:api(method,path);};
        const first=t.open(),again=t.open();
        equal(t.element('fc-month').value,'2026-09');
        for(let n=0;n<10&&!calls.some(path=>path.startsWith('/batches?'));n++) await Promise.resolve();
        equal(t.element('fc-store').value,'7');equal(t.element('fc-store').disabled,true);
        const serial=t.state.serial;await t.refresh();equal(t.state.serial,serial);
        t.element('fc-store').listeners.change();equal(t.state.serial,serial);
        batch.resolve({items:[]});await Promise.all([first,again]);
        equal(calls.filter(path=>path==='/financial-control/config').length,1);
        equal(t.element('fc-overview').innerHTML.includes('store-7'),true);
        equal(t.element('fc-store').disabled,false);equal(t.element('fc-body').hidden,false);
    }
    {
        const t=setup(),api=metadata(t);t.state.config=null;
        t.context.api=(method,path)=>path.startsWith('/batches?')?Promise.reject(new Error('batch unavailable')):api(method,path);
        await t.open();equal(t.element('fc-store').value,'7');equal(t.element('fc-month').value,'2026-09');
        equal(t.element('fc-overview').innerHTML.includes('store-7'),true);
        equal(t.element('fc-notice').textContent.includes('batch unavailable'),true);
        t.context.api=metadata(t);t.element('fc-store').value='8';t.element('fc-month').value='2026-08';
        await t.open();equal(t.element('fc-store').value,'8');equal(t.element('fc-month').value,'2026-08');
        equal(t.element('fc-notice').textContent.includes('batch unavailable'),false);
    }
    {
        for(const failure of ['/financial-control/config','/finance-masters/stores']) {
            const t=setup(),api=metadata(t);t.state.config=null;t.element('fc-month').value='';
            t.context.api=(method,path)=>path===failure?Promise.reject(new Error('metadata unavailable')):api(method,path);
            await t.context.window.FinanceControl.open();
            equal(t.element('fc-month').value,'2026-09');equal(t.element('fc-body').hidden,true);
            equal(t.element('fc-notice').textContent.includes('metadata unavailable'),true);
            t.context.api=metadata(t);await t.dispatch('refresh',{});
            equal(t.element('fc-store').value,'7');equal(t.element('fc-body').hidden,false);
        }
    }
    {
        for(const role of ['operator','viewer']) {
            const t=setup();t.state.config=null;t.context.api=metadata(t,{role,bound:'8'});
            await t.open();equal(t.element('fc-store').value,'8');equal(t.element('fc-store').disabled,true);
            equal(t.element('fc-store').options.length,1);equal(t.element('fc-month').value,'2026-09');
            equal(t.element('fc-cost-form').hidden,role==='viewer');
        }
        for(const bound of [null,999]) {
            const t=setup();t.state.config=null;t.context.api=metadata(t,{role:'operator',bound});
            await t.open();equal(t.element('fc-store').value,'');equal(t.element('fc-body').hidden,true);
            equal(t.element('fc-notice').textContent.includes('未绑定'),true);
        }
        const t=setup();t.state.config=null;t.context.api=metadata(t,{stores:[]});
        await t.open();equal(t.element('fc-store').value,'');equal(t.element('fc-body').hidden,true);
        equal(t.element('fc-notice').textContent.includes('暂无店铺'),true);
    }
    {
        const t=setup(),old=deferred();t.state.config=null;t.context.api=()=>old.promise;
        const first=t.open();t.context.window.FinanceControl.reset();
        t.context.api=metadata(t);await t.open();
        old.resolve({enabled:false});await first;
        equal(t.state.config.enabled,true);equal(t.element('fc-store').value,'7');
        equal(t.element('fc-body').hidden,false);equal(t.state.loading,false);
    }
    {
        const t=setup();t.state.config=null;t.context.api=metadata(t);
        t.context.Date=class {toLocaleDateString(){return '2026-10-26';}};
        await t.open();
        const form=t.element('fc-expense-form'),date=form.elements.namedItem('expense_date');
        equal(date.value,'2026-09-01');
        date.value='2026-08-31';const originalKey=t.key('fc-expense-form');
        // 普通刷新及切换导航后重新打开均不改写已填写的费用日期和重试标识。
        await t.dispatch('refresh',{});await t.open();
        equal(date.value,'2026-08-31');equal(t.key('fc-expense-form'),originalKey);
        // 用户明确切月时仍设置该月默认日期，并为变化后的表单换请求标识。
        t.element('fc-month').value='2026-08';await t.element('fc-month').listeners.change();
        equal(date.value,'2026-08-01');equal(t.key('fc-expense-form')===originalKey,false);
        t.element('fc-month').value='2026-10';await t.element('fc-month').listeners.change();
        equal(date.value,'2026-10-26');
    }
    {
        const t=setup();t.state.config=null;const api=metadata(t),requests=[];
        t.context.api=async(method,path,body)=>{
            if(method==='POST'&&path==='/financial-control/expenses') {
                requests.push(JSON.parse(JSON.stringify(body)));
                if(requests.length===1)throw new Error('response lost after commit');
                return {id:42};
            }
            return api(method,path);
        };
        await t.open();
        const form=t.element('fc-expense-form'),date=form.elements.namedItem('expense_date');
        date.value='2026-08-31';
        form.formValues={category:'advertising',source_ref:'retry-expense-001',amount:'123.0001',
            currency:'CNY',exchange_rate:'1',settlement_entry_id:'',note:'保留原发生日期',
            get expense_date(){return date.value;}};
        await assert.rejects(t.dispatch('create-expense',{}),/response lost/);checks++;
        const originalKey=requests[0].request_key;
        await t.dispatch('refresh',{});
        equal(date.value,'2026-08-31');equal(t.key('fc-expense-form'),originalKey);
        await t.dispatch('create-expense',{});
        equal(requests.length,2);equal(requests[1],requests[0]);equal(t.state.busy,false);
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
        for(const action of ['reverse-cost','reverse-expense','reverse-receipt','cancel-expense']) {
            const t=setup(),decision=deferred();let posts=[];
            t.context.window.PageInteractions={reason:()=>decision.promise};
            t.context.api=(method,path,body)=>method==='POST'?(posts.push({path,body}),Promise.resolve({id:9})):Promise.resolve(response(path,7));
            const target={dataset:{id:'42'}};
            const task=t.dispatch(action,target);
            equal(posts.length,0);target.dataset.id='99';decision.resolve('核对更正');await task;
            equal(posts.length,1);equal(posts[0].path.includes('/42/'),true);equal(posts[0].body.note,'核对更正');
        }
        for(const change of ['cancel','scope','reset']) {
            const t=setup(),decision=deferred();let posts=0;
            t.context.window.PageInteractions={reason:()=>decision.promise};
            t.context.api=async()=>{posts++;return {};};
            const task=t.dispatch('reverse-expense',{dataset:{id:'42'}});
            if(change==='scope')t.element('fc-store').value='8';
            if(change==='reset')t.context.window.FinanceControl.reset();
            decision.resolve(change==='cancel'?null:'核对更正');await task;
            equal(posts,0);
        }
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
