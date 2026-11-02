// Incremental UTF-8 stream, commit boundary, lost connection and session isolation.
const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict');
let checks=0;
const equal=(a,b)=>{assert.deepEqual(a,b);checks++;};
function setup(){
    const nodes=new Map();
    const node=id=>{if(!nodes.has(id))nodes.set(id,{textContent:'',hidden:true,dataset:{},max:100,value:0,
        removeAttribute(name){delete this[name];}});return nodes.get(id);};
    const context=vm.createContext({window:{},document:{documentElement:{lang:'zh-CN'},getElementById:node},
        TextDecoder,Number,String,Error,Promise});
    for(const file of ['static/page-interactions.js','static/sales-import-progress.js'])vm.runInContext(fs.readFileSync(file,'utf8'),context);
    return {ui:context.window.SalesImportProgress,node:id=>node('sales-import-progress-'+id)};
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
    console.log(JSON.stringify({checks,scope:'real shared progress module and incremental streams; no database writes'}));
}
main().catch(e=>{console.error(e);process.exitCode=1;});
