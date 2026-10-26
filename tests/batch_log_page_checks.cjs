// 真实批次日志模块的隔离DOM检查；不连接服务、业务库或浏览器。
const fs=require('node:fs'), vm=require('node:vm'), assert=require('node:assert/strict');
let checks=0;
const equal=(a,b)=>{assert.deepEqual(a,b);checks++;};
const ok=value=>{assert(value);checks++;};
const deferred=()=>{let resolve;return {promise:new Promise(r=>resolve=r),resolve:value=>resolve(value)};};
const nodes=new Map();
const node=selector=>{
  if(!nodes.has(selector))nodes.set(selector,{textContent:'',innerHTML:'',disabled:false,focus(){this.focused=true;}});
  return nodes.get(selector);
};
const dialog={open:false,setAttribute(){},querySelector:node,listeners:{},
  addEventListener(type,handler){this.listeners[type]=handler;},
  showModal(){this.open=true;},close(){this.open=false;}};
const context=vm.createContext({window:{},document:{documentElement:{lang:'zh-CN'},
  body:{appendChild(){}},createElement:()=>dialog,activeElement:null},isOperator:()=>true,
  api:async()=>{throw Error('Unexpected request');},Date});
vm.runInContext(fs.readFileSync('static/page-interactions.js','utf8'),context);
const history=context.window.PageInteractions;
const row=(id,name='operator')=>({id,actor_id:7,actor_username:name,created_at:'2026-10-26T04:00:00+00:00',
  before_values:{quantity:13,total_amount:'34.0054',dingtalk_order_no:'OLD'},
  after_values:{quantity:14,total_amount:'35.0001',dingtalk_order_no:null}});
(async()=>{
  let urls=[];context.api=async(_method,url)=>{urls.push(url);return {items:[row(1,'<img src=x>')],page:1,page_size:20,total:21};};
  await history.batchLogs(12,'<批次>');
  equal(dialog.open,true);equal(urls[0],'/batches/12/logs?page=1&page_size=20');
  ok(node('#batch-log-title').textContent.includes('<批次>'));
  const body=node('[data-log-entries]').innerHTML;
  ok(body.includes('&lt;img src=x&gt;')&&!body.includes('<img src=x>'));
  ok(body.includes('34.0054')&&body.includes('35.0001')&&body.includes('未填写'));
  equal(node('[data-log-previous]').disabled,true);equal(node('[data-log-next]').disabled,false);
  context.api=async(_method,url)=>{urls.push(url);return {items:[row(2)],page:2,page_size:20,total:21};};
  await node('[data-log-next]').onclick();
  equal(urls.at(-1),'/batches/12/logs?page=2&page_size=20');
  equal(node('[data-log-page]').textContent,'2 / 2');equal(node('[data-log-next]').disabled,true);
  equal(node('[data-log-previous]').disabled,false);
  // 关闭/切换批次后，延迟响应不覆盖新批次。
  const old=deferred();context.api=()=>old.promise;const pending=history.batchLogs(12,'OLD');
  context.api=async()=>({items:[row(3,'new-operator')],page:1,page_size:20,total:1});
  await history.batchLogs(13,'NEW');old.resolve({items:[row(4,'stale')],page:1,page_size:20,total:1});await pending;
  ok(node('[data-log-entries]').innerHTML.includes('new-operator'));
  ok(!node('[data-log-entries]').innerHTML.includes('stale'));
  equal(node('#batch-log-title').textContent,'批次修改日志 · NEW');
  const logout=deferred();context.api=()=>logout.promise;const inFlight=history.batchLogs(13,'NEW');
  history.cancel();equal(dialog.open,false);const content=node('[data-log-entries]').innerHTML;
  logout.resolve({items:[row(5)],page:1,page_size:20,total:1});await inFlight;
  equal(node('[data-log-entries]').innerHTML,content);
  context.isOperator=()=>false;let calls=0;context.api=async()=>{calls++;};
  await history.batchLogs(12,'hidden');equal(calls,0);equal(dialog.open,false);
  context.isOperator=()=>true;context.api=async()=>{throw Error('<server error>');};
  await history.batchLogs(12,'B12');ok(node('[data-log-status]').textContent.includes('<server error>'));
  equal(node('[data-log-next]').disabled,true);
  context.api=async()=>({items:[],page:1,page_size:20,total:0});
  await history.batchLogs(12,'B12');equal(node('[data-log-status]').textContent,'暂无修改日志');
  context.api=async()=>{const value=row(6);value.after_values=value.before_values;return {items:[value],page:1,page_size:20,total:1};};
  await history.batchLogs(12,'B12');ok(node('[data-log-entries]').innerHTML.includes('本次保存未更改字段'));
  dialog.listeners.cancel({preventDefault(){}});equal(dialog.open,false);
  console.log(JSON.stringify({checks,scope:'actual batch log module; isolated DOM and API'}));
})().catch(error=>{console.error(error);process.exitCode=1;});
