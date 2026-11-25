// 运行真实结果模块、页面 api 和退出清理；验证全文、只读与异步会话隔离。
const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict');
let checks=0;
const equal=(a,b)=>{assert.deepEqual(a,b);checks++;};
const deferred=()=>{let resolve,reject;return {promise:new Promise((a,b)=>{resolve=a;reject=b;}),resolve,reject};};
const tick=()=>new Promise(resolve=>setImmediate(resolve));
const response=(data,status=200)=>({ok:status<400,status,text:async()=>JSON.stringify(data)});
function payload(id=1){return {id,file_name:`<source-${id}>.csv`,store_name:'<店铺>',imported_by:'<导入人>',
    imported_at:'2026-09-02T12:34:56',status:'completed',pending_confirmation:0,settlement_rows:30,sales_created:31,skipped_duplicates:2,
    result:{success:true,settlement_rows:30,sales_created:18,skipped_duplicates:2,pending_confirmation:13,
        warnings:Array.from({length:18},(_,i)=>`<提示 ${id}-${i}>`),
        pending_details:Array.from({length:13},(_,i)=>({row:i+2,message:`原因 ${i}`,identifier:`ORDER-${i}`})),errors:[]}};}
function setup(file){
    const nodes=new Map(),requests=[];
    function element(id){
        if(!nodes.has(id)){
            const node={textContent:'',hidden:true,open:false,value:'',innerHTML:'',style:{},dataset:{},listeners:{},
                setAttribute(){},removeAttribute(){},matches:()=>false,contains:()=>false,
                addEventListener(name,callback){this.listeners[name]=callback;},
                dispatch(name,event={}){this.listeners[name]?.(event);},showModal(){this.open=true;},
                close(){this.open=false;this.dispatch('close');}};
            nodes.set(id,node);
        }
        return nodes.get(id);
    }
    const c=vm.createContext({window:{},document:{documentElement:{lang:'zh-CN'},getElementById:element,querySelectorAll:()=>[]},
        setTimeout:()=>1,clearTimeout(){},authHeaders:()=>({'X-CSRF-Token':'fixture'}),updateSalesImportControls(){},showMsg:()=>{}});
    vm.runInContext("const API='';let CURRENT_USER_ID=1,salesStoreRequestId=0,salesImportSessionId=0,salesImportsRequestId=0,salesImportBusy=false,salesImportStoresReady=false; const salesImportText=key=>window.PageInteractions.text(key);",c);
    for(const module of ['static/page-interactions.js','static/sales-import-progress.js','static/sales-import-results.js'])
        vm.runInContext(fs.readFileSync(module,'utf8'),c);
    const html=fs.readFileSync(file,'utf8');
    for(const name of ['api','formatApiError','resetSalesImportState','viewSalesImportResult']){
        const source=html.match(new RegExp('^(?:async )?function '+name+'\\([^\\n]*\\) \\{[\\s\\S]*?^\\}','m'));
        assert(source,name);vm.runInContext(source[0],c);
    }
    c.expired=0;c.handleSessionExpired=()=>{c.expired++;c.resetSalesImportState();vm.runInContext('CURRENT_USER_ID=0;',c);};
    c.fetch=async(url,options)=>{requests.push({url,options});return response(payload());};
    return {c,ui:c.window.SalesImportResults,node:id=>element('sales-import-result-'+id),requests,
        login:id=>vm.runInContext(`CURRENT_USER_ID=${id};`,c),html};
}
async function main(){
    for(const file of ['templates/index.html','index.html']){
        for(const literal of ['$&', '$`', "$'", '$$', '{store} {file} {message} {identifier} {user}',
            '<img src=x onerror=alert(1)> & "双引号" \'单引号\' 🐾 \\路径\n下一行']){
            const {c,ui,node}=setup(file);
            const data={...payload(),file_name:`订单${literal}.csv`,store_name:`店铺${literal}`,imported_by:`用户${literal}`};
            const message=`未找到本地商品映射: 产品名=玩具${literal}`,identifier=`ORDER-${literal}`,warning=`来源提示：${literal}`;
            data.result={...data.result,warnings:[warning],pending_details:[{row:3,message,identifier}],errors:[]};
            const original=JSON.stringify(data);
            c.fetch=async()=>response(data);await ui.open(1);
            equal(node('file').textContent,`${data.file_name} · ${data.store_name}`);
            equal(node('meta').textContent,`导入时间：2026-09-02 12:34:56　导入人：${data.imported_by}`);
            equal(node('messages').textContent,`提示详情\n${warning}\n\n导入时待确认原因\n第 3 行：${message}（${identifier}）`);
            for(const name of ['file','meta','messages'])equal(node(name).innerHTML,'');
            equal(JSON.stringify(data),original);
            // 无快照的旧记录也必须保留原文件名、店铺和导入人，不能伪造完整提示。
            c.fetch=async()=>response({...data,result:null});await ui.open(1);
            equal(node('file').textContent,`${data.file_name} · ${data.store_name}`);
            equal(node('meta').textContent,`导入时间：2026-09-02 12:34:56　导入人：${data.imported_by}`);
            equal(node('notice').textContent.includes('未保存'),true);equal(node('messages').textContent,'');
        }
        {
            const {c,ui,node,requests,html}=setup(file);
            await c.viewSalesImportResult(17);
            equal(requests[0].url,'/sales/imports/17/result');equal(requests[0].options.method,'GET');
            equal(requests[0].options.cache,'no-store');equal(requests[0].options.credentials,'same-origin');
            equal(node('dialog').open,true);equal(node('body').hidden,false);
            equal(node('file').textContent,'<source-1>.csv · <店铺>');
            equal(node('summary').textContent.includes('销售扣库存18条'),true);
            equal(node('current').textContent.includes('当前待确认：0条'),true);
            for(const warning of payload().result.warnings)equal(node('messages').textContent.includes(warning),true);
            for(const detail of payload().result.pending_details)equal(node('messages').textContent.includes(detail.identifier),true);
            equal(node('messages').innerHTML,''); // 用户内容仅通过 textContent 写入。
            equal(html.includes('position:fixed; top:80px; right:24px'),true);
            equal(html.includes('max-width:640px'),true);equal(html.includes('/sales-import-results.js'),true);
            node('close').dispatch('click');equal(node('dialog').open,false);equal(node('messages').textContent,'');
            await ui.open(-1);equal(requests.length,1);
        }
        {
            const {c,ui,node}=setup(file);
            c.fetch=async()=>response({...payload(),result:null});await ui.open(1);
            equal(node('notice').textContent.includes('未保存'),true);equal(node('messages').textContent,'');
            equal(node('heading').textContent,'现有统计');equal(node('summary').textContent.includes('销售扣库存31条'),true);
            c.fetch=async()=>response({...payload(),status:'rolled_back'});await ui.open(1);
            equal(node('notice').textContent.includes('已撤销'),true);equal(node('current').hidden,true);
            equal(node('messages').textContent.includes('<提示 1-17>'),true);
            let cancelled=false;node('dialog').dispatch('cancel',{preventDefault(){cancelled=true;}});
            equal(cancelled,true);equal(node('dialog').open,false);equal(node('messages').textContent,'');
        }
        for(const phase of ['headers','body','401','500','network'])for(const action of ['close','reopen','same-user','other-user']){
            const {c,ui,node,login}=setup(file),late=deferred();
            c.fetch=phase==='body'?async()=>({...response({}),text:()=>late.promise}):()=>late.promise;
            const old=ui.open(1);await tick();
            if(action==='close')node('close').dispatch('click');
            else {
                if(action!=='reopen'){c.resetSalesImportState();login(action==='same-user'?1:2);}
                c.fetch=async()=>response(payload(99));await ui.open(99);
                node('dialog').dispatch('close'); // 旧 close 事件排队时已重新打开。
            }
            if(phase==='body')late.resolve(JSON.stringify(payload(1)));
            else if(phase==='network')late.reject(new Error('old connection failed'));
            else late.resolve(response(payload(1),phase==='401'?401:phase==='500'?500:200));
            await old;
            equal(c.expired,0);equal(node('status').textContent,'');
            equal(node('dialog').open,action!=='close');
            equal(node('messages').textContent.includes('<提示 99-17>'),action!=='close');
            equal(node('messages').textContent.includes('<提示 1-17>'),false);
        }
        {
            const {c,ui,node}=setup(file);
            c.fetch=async()=>response({detail:{code:'sales_import_result_not_found'}},404);await ui.open(1);
            equal(node('body').hidden,true);equal(node('status').textContent,c.window.PageInteractions.text('importResultNotFound'));
            c.fetch=async()=>response({},401);await ui.open(1);equal(c.expired,1);equal(node('dialog').open,false);
        }
    }
    console.log(JSON.stringify({checks,pages:2,scope:'real history-result module, API wrapper and session cleanup; isolated VM'}));
}
main().catch(error=>{console.error(error);process.exitCode=1;});
