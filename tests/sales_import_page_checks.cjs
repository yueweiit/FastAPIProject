// 运行两份实际页面函数，验证导入店铺、异步加载和会话失效；不是浏览器验收。
const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict');
let checks=0;
const equal=(a,b)=>{assert.deepEqual(a,b);checks++;};
const defer=()=>{let resolve;const promise=new Promise(r=>resolve=r);return {promise,resolve};};
function setup(file,role='admin') {
    const elements=new Map();
    const element=id=>{
        if(!elements.has(id)){
            const e={value:'',disabled:false,textContent:'',dataset:{},style:{},files:[{name:'fixture.csv'}],
                parentElement:{style:{},setAttribute:()=>{}}};
            Object.defineProperty(e,'innerHTML',{set(v){this.html=v;if(id.endsWith('store'))this.value='';},get(){return this.html||'';}});
            elements.set(id,e);
        }
        return elements.get(id);
    };
    const c=vm.createContext({window:{},document:{documentElement:{lang:'zh-CN'},getElementById:element,querySelectorAll:()=>[]},
        Number,String,Error,Promise,FormData:class{constructor(){this.values={};}append(k,v){this.values[k]=v;}},
        escapeHtml:v=>String(v).replaceAll('<','&lt;'),authHeaders:()=>({'X-CSRF-Token':'test'}),
        showMsg:(msg)=>c.message=msg,refreshSalesWorkspace:()=>c.refreshed=(c.refreshed||0)+1});
    vm.runInContext(fs.readFileSync('static/page-interactions.js','utf8'),c);
    vm.runInContext(`let salesStoreRequestId=0,salesImportSessionId=0,salesImportBusy=false,salesImportStoresReady=false;
        let CURRENT_USER_ID=1,CURRENT_ROLE=${JSON.stringify(role)},salesPage=1;
        const salesImportText=key=>window.PageInteractions.text(key);
        function isAdmin(){return CURRENT_ROLE==='admin';}function isOperator(){return ['admin','operator'].includes(CURRENT_ROLE);}
        function handleSessionExpired(){resetSalesImportState();CURRENT_USER_ID=0;CURRENT_ROLE='';}`,c);
    const html=fs.readFileSync(file,'utf8');
    for(const name of ['formatApiError','updateSalesImportControls','resetSalesImportState','loadManualSaleStores','importSalesFile']){
        const match=html.match(new RegExp('^(?:async )?function '+name+'\\([^\\n]*\\) \\{[\\s\\S]*?^\\}', 'm'));
        assert(match,name);vm.runInContext(match[0],c);
    }
    c.api=async(_method,path)=>path==='/auth/me'?{id:1,store_id:7}:[{id:7,name:'店铺7'},{id:8,name:'店铺8'}];
    return {c,element};
}
const ok={ok:true,status:200,json:async()=>({settlement_rows:1,sales_created:1,skipped_duplicates:0})};
async function main(){
    for(const file of ['templates/index.html','index.html']){
        {
            const {c,element:e}=setup(file);
            await c.loadManualSaleStores();
            equal(e('import-csv').disabled,true);
            e('sales-import-store').value='8';c.updateSalesImportControls();equal(e('import-csv').disabled,false);
            e('s-store').value='7';let sent,posts=0;const result=defer();
            c.fetch=async(url,options)=>{posts++;sent={url,options};return result.promise;};
            const task=c.importSalesFile(e('import-csv'));await c.importSalesFile(e('import-csv'));
            equal(posts,1);equal(sent.options.body.values.store_id,'8');equal(sent.options.headers['X-CSRF-Token'],'test');
            equal(e('sales-import-store').disabled,true);result.resolve(ok);await task;
            equal(c.refreshed,1);equal(e('sales-import-store').disabled,false);
        }
        {
            const {c,element:e}=setup(file,'operator');await c.loadManualSaleStores();
            equal(e('sales-import-store').value,'7');equal(e('sales-import-store').disabled,true);
            equal(e('import-csv').disabled,false);equal(e('sales-import-store').innerHTML.includes('店铺8'),false);
            c.api=async(_method,path)=>path==='/auth/me'?{id:1,store_id:null}:[];await c.loadManualSaleStores();
            equal(e('import-csv').disabled,true);
        }
        {
            const {c,element:e}=setup(file);const old=defer();c.api=()=>old.promise;
            const task=c.loadManualSaleStores();c.resetSalesImportState();old.resolve([]);await task;
            equal(e('sales-import-store').innerHTML,'');equal(e('import-csv').disabled,true);
        }
        {
            const {c,element:e}=setup(file);await c.loadManualSaleStores();e('sales-import-store').value='8';
            const old=defer();c.fetch=()=>old.promise;const task=c.importSalesFile(e('import-csv'));
            c.resetSalesImportState();await c.loadManualSaleStores();e('sales-import-store').value='7';
            const current=defer();c.fetch=()=>current.promise;const newTask=c.importSalesFile(e('import-csv'));
            old.resolve(ok);await task;equal(e('import-csv').disabled,true);equal(c.refreshed,undefined);
            current.resolve(ok);await newTask;equal(c.refreshed,1);equal(e('import-csv').disabled,false);
        }
        {
            const {c,element:e}=setup(file);await c.loadManualSaleStores();e('sales-import-store').value='8';
            c.fetch=async()=>({ok:false,status:403,json:async()=>({detail:{code:'import_store_forbidden',message:'不能越店'}})});
            await c.importSalesFile(e('import-csv'));equal(c.message,'不能越店');equal(e('import-csv').disabled,false);
            c.fetch=async()=>({ok:false,status:401});await c.importSalesFile(e('import-csv'));
            equal(e('sales-import-store').innerHTML,'');equal(e('import-csv').disabled,true);
        }
        {
            const {c,element:e}=setup(file,'viewer');await c.loadManualSaleStores();equal(e('import-csv').disabled,true);
        }
    }
    console.log(JSON.stringify({checks,scope:'actual import functions in VM; not browser/database acceptance'}));
}
main().catch(e=>{console.error(e);process.exitCode=1;});
