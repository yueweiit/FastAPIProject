// 执行真实页面的入库/编辑/撤销函数；浏览器验收另行执行。
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
let checks = 0;
const equal = (a,b) => { assert.deepEqual(a,b); checks++; };
const deferred = () => { let resolve; return {promise:new Promise(r=>resolve=r),resolve:v=>resolve(v)}; };
function setup(file) {
    const html = fs.readFileSync(file,'utf8');
    const elements = new Map();
    const element = id => {
        if (!elements.has(id)) elements.set(id,{value:'',dataset:{},style:{},textContent:'',reportValidity:()=>true});
        return elements.get(id);
    };
    const context = vm.createContext({window:{},document:{getElementById:element,documentElement:{lang:'zh-CN'}},
        api:async()=>{throw new Error('unexpected request');},showMsg:()=>{},loadBatches:()=>{},calcEditUnitCost:()=>{},
        refreshSalesWorkspace:()=>{context.refreshed=true;}});
    vm.runInContext(fs.readFileSync('static/page-interactions.js','utf8'),context);
    context.isOperator=()=>true; context.isAdmin=()=>true;
    vm.runInContext('let editingBatchId=null,editingBatchVersion=null,batchEditSaving=false,batchEditSessionEpoch=0,currentBatchList=[],CURRENT_USER_ID=7,salesRollbackBusy=false,salesPage=1,pendingConfirmationsPage=1;',context);
    for(const name of ['createBatch','batchEditBlockedReason','setBatchEditStatus','resetBatchEditSession','openEditBatch','closeEditBatch','saveEditBatch','rollbackSalesImport']) {
        const source = html.match(new RegExp('^(?:async )?function '+name+'\\([^\\n]*\\) \\{[\\s\\S]*?^\\}', 'm'));
        assert(source,`${file}: missing ${name}`);
        vm.runInContext(source[0],context);
    }
    equal(html.includes('src="/page-interactions.js"'),true);
    return {context,element};
}
async function main() {
    const originalTZ=process.env.TZ;
    try {
        for(const tz of ['Asia/Shanghai','UTC','America/Mexico_City']) {
            process.env.TZ=tz;
            for(const file of ['templates/index.html','index.html']) {
                const {context:c,element:e}=setup(file);
                for(const [id,value] of Object.entries({'b-pid':'1','b-qty':'5','b-total-amount':'25',
                    'b-date':'2026-09-01T00:15','b-shipdate':'2026-08-31T23:59'})) e(id).value=value;
                let payload;
                c.api=async(method,url,body)=>{payload=body;return {id:12,quantity:5,unit_cost:5};};
                await c.createBatch();
                equal(payload.arrived_at,'2026-09-01T00:15');
                equal(payload.shipping_date,'2026-08-31T23:59');
                // 含秒/小数秒的历史记录，不改日期时原样提交；重复编辑也不漂移。
                const batch={...payload,id:12,edit_version:'a'.repeat(64),remaining_quantity:5,arrived_at:'2026-09-01T00:15:37.123456',shipping_date:'2026-08-31T23:59:41'};
                vm.runInContext(`currentBatchList=[${JSON.stringify(batch)}]`,c);
                for(let i=0;i<2;i++) {
                    await c.openEditBatch(12);e('eb-qty').value='6';e('eb-total-amount').value='30';
                    await c.saveEditBatch();
                    equal(payload.arrived_at,batch.arrived_at);equal(payload.shipping_date,batch.shipping_date);
                    vm.runInContext(`currentBatchList=[${JSON.stringify({...batch,...payload,remaining_quantity:payload.quantity})}]`,c);
                }
                await c.openEditBatch(12);e('eb-date').value='2026-10-01T00:01';e('eb-shipdate').value='';
                await c.saveEditBatch();
                equal(payload.arrived_at,'2026-10-01T00:01');equal(payload.shipping_date,undefined);
            }
        }
    } finally { if(originalTZ===undefined) delete process.env.TZ;else process.env.TZ=originalTZ; }
    for(const file of ['templates/index.html','index.html']) {
        const {context:c}=setup(file);let posts=0;
        c.api=async()=>{posts++;return {restored_quantity:1,sales_removed:1};};
        c.window.PageInteractions.confirmSalesRollback=async()=>null;
        await c.rollbackSalesImport(12);equal(posts,0);
        const decision=deferred();c.window.PageInteractions.confirmSalesRollback=()=>decision.promise;
        const task=c.rollbackSalesImport(12);await c.rollbackSalesImport(12);
        equal(posts,0);decision.resolve(true);await task;equal(posts,1);equal(c.refreshed,true);
        const expired=deferred();c.window.PageInteractions.confirmSalesRollback=()=>expired.promise;
        const stale=c.rollbackSalesImport(12);vm.runInContext('CURRENT_USER_ID=8',c);expired.resolve(true);await stale;
        equal(posts,1);
    }
    console.log(JSON.stringify({checks,scope:'actual page functions; three browser timezones and async confirmation guards'}));
}
main().catch(e=>{console.error(e);process.exitCode=1;});
