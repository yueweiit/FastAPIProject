// Incremental UTF-8 stream, commit boundary, lost connection and session isolation.
const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict');
let checks=0;
const equal=(a,b)=>{assert.deepEqual(a,b);checks++;};
function setup(){
    const nodes=new Map();
    let now=0,nextTimer=0;const timers=new Map();
    const clock={advance(ms){now+=ms;for(const [id,timer] of [...timers])if(timer.at<=now){timers.delete(id);timer.callback();}},
        callbacks:()=>[...timers.values()].map(t=>t.callback),pending:()=>timers.size};
    const node=id=>{if(!nodes.has(id))nodes.set(id,{textContent:'',hidden:true,dataset:{},max:100,value:0,attributes:{},listeners:{},
        setAttribute(name,value){this.attributes[name]=value;},getAttribute(name){return this.attributes[name];},
        addEventListener(name,callback){this.listeners[name]=callback;},dispatch(name,event={}){this.listeners[name]?.(event);},
        contains(target){return target===this||target?.within===this;},
        matches(selector){return selector.split(',').some(s=>Boolean(this[s.trim().slice(1)]));},
        removeAttribute(name){delete this[name];}});return nodes.get(id);};
    const context=vm.createContext({window:{},document:{documentElement:{lang:'zh-CN'},getElementById:node},
        TextDecoder,Number,String,Error,Promise,
        setTimeout(callback,delay){const id=++nextTimer;timers.set(id,{callback,at:now+delay});return id;},
        clearTimeout:id=>timers.delete(id)});
    for(const file of ['static/page-interactions.js','static/sales-import-progress.js'])vm.runInContext(fs.readFileSync(file,'utf8'),context);
    return {ui:context.window.SalesImportProgress,node:id=>node('sales-import-progress-'+id),clock};
}
const encoder=new TextEncoder();
const event=value=>encoder.encode(JSON.stringify(value)+'\n');
const tick=()=>new Promise(resolve=>setImmediate(resolve));
async function main(){
    {
        const {ui,node}=setup(), token=ui.begin({name:'<测试>.xlsx'},'<店铺>');
        equal(node('panel').hidden,false);equal(node('stage').textContent,'正在上传文件');
        equal(node('file').textContent,'<测试>.xlsx · <店铺>');equal(node('bar').value,undefined);
        let writer;
        const response=new Response(new ReadableStream({start(c){writer=c;}}),{headers:{'Content-Type':'application/x-ndjson'}});
        const work=ui.consume(response,token);
        const bytes=event({type:'progress',stage:'writing',processed:3,total:10});
        // Split in the middle of a multi-byte UTF-8 value as well as JSON lines.
        for(const byte of bytes)writer.enqueue(new Uint8Array([byte]));
        await tick();equal(node('bar').value,3);equal(node('count').textContent,'本阶段 3 / 10 行（30%）');
        equal(node('panel').dataset.state,'running');
        writer.enqueue(event({type:'progress',stage:'committing'}));await tick();
        equal(node('bar').value,undefined);equal(node('stage').textContent,'正在确认保存结果');
        writer.enqueue(event({type:'result',status:200,data:{success:true,pending_confirmation:2}}));writer.close();
        const result=await work;equal(result.status,200);equal(node('panel').dataset.state,'running');
        ui.complete(result.data,'已保存，待确认2条',token);
        equal(node('panel').dataset.state,'review');equal(node('bar').value,100);equal(node('result').textContent,'已保存，待确认2条');
        equal(node('details').hidden,true);equal(node('actions').hidden,false);equal(node('count').hidden,true);
        equal(node('brief').textContent.includes('待确认2条'),true);
    }
    {
        const {ui,node}=setup(), old=ui.begin({name:'old'},'old');let writer;
        const work=ui.consume(new Response(new ReadableStream({start(c){writer=c;}}),{headers:{'Content-Type':'application/x-ndjson'}}),old);
        ui.reset();equal(node('panel').hidden,true);const current=ui.begin({name:'new'},'new');
        writer.enqueue(event({type:'progress',stage:'writing',processed:5,total:5}));
        writer.enqueue(event({type:'result',status:401,data:{detail:'old'}}));writer.close();await work;
        ui.fail('old',old);ui.complete({},'old',old);
        equal(node('file').textContent,'new · new');equal(node('stage').textContent,'正在上传文件');
        ui.complete({success:true},'ok',current);equal(node('panel').dataset.state,'complete');
    }
    for(const body of ['', 'invalid\n', JSON.stringify({type:'progress',stage:'writing',processed:1,total:2})+'\n']){
        const {ui,node}=setup(),token=ui.begin({name:'file'},'store');
        let error;
        try{await ui.consume(new Response(body,{headers:{'Content-Type':'application/x-ndjson'}}),token);}catch(e){error=e;}
        assert(error?.message.includes('核对结果'));checks++;
        ui.fail(error.message,token);equal(node('panel').dataset.state,'error');equal(node('bar').value,0);
    }
    {
        const {ui}=setup(),token=ui.begin({name:'file'},'store');
        const error=new Response(JSON.stringify({detail:'forbidden'}),{status:403,headers:{'Content-Type':'application/json'}});
        const result=await ui.consume(error,token);equal(result.status,403);equal(result.data.detail,'forbidden');
    }
    const success={success:true,settlement_rows:100,sales_created:90,skipped_duplicates:0,pending_confirmation:0,warnings:[]};
    {
        const {ui,node,clock}=setup(),token=ui.begin({name:'file'},'store');
        equal(node('actions').hidden,true);node('close').dispatch('click');equal(node('panel').hidden,false);
        ui.complete(success,'完整结果',token);
        equal(node('brief').textContent,'结算行100条，销售扣库存90条，重复跳过0条');
        equal(node('toggle').textContent,'查看详情');equal(node('close').textContent,'关闭提示');
        clock.advance(7999);equal(node('panel').hidden,false);clock.advance(1);equal(node('panel').hidden,true);
    }
    {
        const {ui,node,clock}=setup(),token=ui.begin({name:'file'},'store');ui.complete(success,'完整结果',token);
        clock.advance(4000);node('panel').hover=true;node('panel').dispatch('mouseenter');clock.advance(9000);
        equal(node('panel').hidden,false);equal(clock.pending(),0);
        node('panel').hover=false;node('panel').dispatch('mouseleave');equal(clock.pending(),1);
        node('panel')['focus-within']=true;node('panel').dispatch('focusin');clock.advance(9000);equal(node('panel').hidden,false);
        node('panel').dispatch('focusout',{relatedTarget:{within:node('panel')}});equal(clock.pending(),0);
        node('panel').dispatch('focusout',{relatedTarget:null});node('panel')['focus-within']=false;
        clock.advance(8000);equal(node('panel').hidden,true);
    }
    {
        const {ui,node,clock}=setup(),token=ui.begin({name:'file'},'store');ui.complete(success,'完整结果',token);
        node('toggle').dispatch('click');equal(node('details').hidden,false);equal(node('toggle').getAttribute('aria-expanded'),'true');
        equal(node('toggle').textContent,'收起详情');clock.advance(30000);equal(node('panel').hidden,false);
        node('toggle').dispatch('click');equal(node('toggle').getAttribute('aria-expanded'),'false');
        clock.advance(8000);equal(node('panel').hidden,true);
    }
    for(const result of [{...success,pending_confirmation:17},{...success,warnings:['核对日期']}]){
        const {ui,node,clock}=setup(),token=ui.begin({name:'file'},'store');ui.complete(result,'待核对结果',token);
        equal(node('panel').dataset.state,'review');equal(clock.pending(),0);clock.advance(30000);equal(node('panel').hidden,false);
        node('toggle').dispatch('click');equal(node('details').hidden,false);equal(node('result').textContent,'待核对结果');
        node('close').dispatch('click');equal(node('panel').hidden,true);
    }
    {
        const {ui,node,clock}=setup(),token=ui.begin({name:'file'},'store');ui.fail('<错误>需核对',token);
        equal(node('brief').textContent,'<错误>需核对');clock.advance(30000);equal(node('panel').hidden,false);
        node('close').dispatch('click');equal(node('panel').hidden,true);
    }
    for(const action of ['begin','reset','close']){
        const {ui,node,clock}=setup(),token=ui.begin({name:'old'},'store');ui.complete(success,'旧结果',token);
        const staleTimer=clock.callbacks()[0];
        if(action==='reset')ui.reset();if(action==='close')node('close').dispatch('click');
        const next=ui.begin({name:'new'},'store');equal(clock.pending(),0);
        // Even an already queued old callback must not hide the next completed import.
        ui.complete(success,'新结果',next);staleTimer();equal(node('panel').hidden,false);equal(node('result').textContent,'新结果');
    }
    console.log(JSON.stringify({checks,scope:'real shared progress module and incremental streams; no database writes'}));
}
main().catch(e=>{console.error(e);process.exitCode=1;});
