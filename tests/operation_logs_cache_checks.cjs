// 真实HTTP + 实际日志路由 + 隔离SQLite；不使用page.route（保持浏览器缓存启用）。
const fs=require('node:fs'),path=require('node:path'),assert=require('node:assert/strict'),{spawn}=require('node:child_process');
const {chromium}=require(process.env.INVENTORY_PLAYWRIGHT_MODULE||'playwright');
const root=path.resolve(__dirname,'..');
let checks=0;const check=(actual,expected)=>{assert.deepEqual(actual,expected);checks++;};
const delay=ms=>new Promise(resolve=>setTimeout(resolve,ms));

async function main(){
 const python=process.env.INVENTORY_PYTHON||path.join(root,'.venv',process.platform==='win32'?'Scripts/python.exe':'bin/python');
 assert(fs.existsSync(python),'Set INVENTORY_PYTHON to the existing project Python runtime.');
 const child=spawn(python,[path.join(__dirname,'log_cache_server.py')],{cwd:root,windowsHide:true,stdio:['ignore','pipe','pipe']});
 let output='',errors='',base,browser;
 const stopped=new Promise(resolve=>child.once('exit',resolve));
 try{
  const port=await new Promise((resolve,reject)=>{
   const timeout=setTimeout(()=>reject(Error('Cache fixture startup timed out: '+errors)),15000);
   child.once('error',error=>{clearTimeout(timeout);reject(error);});
   child.once('exit',()=>{clearTimeout(timeout);reject(Error('Cache fixture exited: '+errors));});
   child.stdout.on('data',chunk=>{
    output+=chunk.toString();const match=output.match(/"fixture_port":\s*(\d+)/);
    if(match){clearTimeout(timeout);resolve(Number(match[1]));}
   });
   child.stderr.on('data',chunk=>{errors=(errors+chunk.toString()).slice(-3000);});
  });
  assert(port>0&&port<65536);base='http://127.0.0.1:'+port;
  let ready=false;
  for(let i=0;i<50;i++){
   try{ready=(await fetch(base+'/fixture/health')).ok;}catch{}
   if(ready)break;await delay(100);
  }
  assert(ready,'Cache fixture never became ready.');
  browser=await chromium.launch({headless:true,...(process.env.INVENTORY_BROWSER_CHANNEL?{channel:process.env.INVENTORY_BROWSER_CHANNEL}:{})});
  const page=await browser.newPage();await page.goto(base+'/fixture');
  const session=role=>page.evaluate(async role=>{await fetch('/fixture/session/'+role,{method:'POST',cache:'no-store'});},role);
  const read=(url,cache='default')=>page.evaluate(async({url,cache})=>{
   const response=await fetch(url,{cache,credentials:'same-origin'});
   return {status:response.status,cacheControl:response.headers.get('cache-control'),body:await response.json()};
  },{url,cache});

  await session('admin');const baseline=await read('/fixture/cacheable');check(baseline.status,200);
  await session('operator');const cached=await read('/fixture/cacheable','force-cache');
  check(cached.status,200);check(cached.body,baseline.body);
  check((await read('/fixture/cacheable','no-store')).status,403);
  check((await read('/fixture/stats','no-store')).body['/fixture/cacheable'],2);

  const urls=['/operation-logs/options','/operation-logs','/operation-logs/batch/1','/batches/1/logs'];
  for(const url of urls){
   await session('admin');const initial=await read(url);check(initial.status,200);check(initial.cacheControl,'private, no-store');
   for(const [role,status] of [['operator',403],['viewer',403],['anonymous',401]]){
    await session(role);const denied=await read(url,'force-cache');
    check(denied.status,status);check(denied.cacheControl,'private, no-store');
    check(Object.hasOwn(denied.body,'items'),false);
   }
   check((await read('/fixture/stats','no-store')).body[url],4);
  }
  console.log(JSON.stringify({checks,endpoints:urls.length,cacheControlGroup:'cached response reproduced',scope:'actual HTTP log routes, real browser cache, downgrade, account switch and logout'}));
 }finally{
  if(browser)await browser.close();
  if(base)try{await fetch(base+'/fixture/shutdown',{method:'POST'});}catch{}
  if(await Promise.race([stopped.then(()=>true),delay(3000).then(()=>false)])===false){child.kill();await stopped;}
 }
}
main().catch(error=>{console.error(error);process.exitCode=1;});
