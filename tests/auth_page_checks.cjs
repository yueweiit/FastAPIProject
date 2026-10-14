// Node-only execution of real authentication helpers, not browser visual QA.
const realAssert = require('node:assert/strict');
let checks=0;
const assert = (...args) => {checks++;return realAssert(...args);};
assert.equal = (...args) => {checks++;return realAssert.equal(...args);};
assert.rejects = (...args) => {checks++;return realAssert.rejects(...args);};
const fs = require('node:fs');
const vm = require('node:vm');

async function verify(file) {
    const html = fs.readFileSync(file, 'utf8');
    for (const block of html.matchAll(/<script[^>]*>([\s\S]*?)<\/script>/g)) new vm.Script(block[1], {filename: file});
    assert(!html.includes('localStorage.setItem'));
    assert(!html.includes('AUTH_TOKEN'));
    assert(html.includes('id="eims-login-button"'));
    const context = vm.createContext({URL, URLSearchParams, FormData: class {},
        location: {origin:'https://fifo.test', assign: value => context.destination=value},
        document: {}, fetch: async () => context.response,
        showMsg: message => context.notice=message});
    function include(name) {
        const pattern = new RegExp('^(?:async )?function '+name+'\\([^\\n]*\\) \\{[\\s\\S]*?^\\}', 'm');
        const match=html.match(pattern);
        assert(match, 'Function missing: '+name);
        vm.runInContext(match[0],context);
    }
    vm.runInContext(`const API=''; let AUTH_CSRF='fixture-csrf'; let AUTH_CONFIG={local_enabled:true,sso_available:true};
        let expired=0; let portalLogout=0;
        function handleSessionExpired(){expired++; AUTH_CSRF='';}
        function doLogout(){portalLogout++;}`,context);
    for (const name of ['formatApiError','authHeaders','api','productImportRequest','startEimsLogin']) include(name);
    let recorded;
    context.fetch=async (url,options) => {recorded={url,options}; return context.response;};
    context.response={status:200,ok:true,text:async()=>'{"ok":true}',json:async()=>({ok:true})};
    await vm.runInContext(`api('PUT','/products/1',{name:'test'})`,context);
    assert.equal(recorded.options.headers['X-CSRF-Token'],'fixture-csrf');
    assert.equal(recorded.options.credentials,'same-origin');
    assert.equal(recorded.options.headers.Authorization,undefined);
    await vm.runInContext(`productImportRequest('/products/import/preview',new FormData())`,context);
    assert.equal(recorded.options.method,'POST');
    assert.equal(recorded.options.headers['X-CSRF-Token'],'fixture-csrf');
    assert.equal(recorded.options.headers['Content-Type'],undefined);
    context.response={status:401,ok:false};
    await assert.rejects(vm.runInContext(`api('GET','/products')`,context), /登录已过期/);
    assert.equal(vm.runInContext('expired',context),1);
    assert.equal(vm.runInContext('portalLogout',context),0);
    vm.runInContext('startEimsLogin()',context);
    assert.equal(context.destination,'/auth/eims/start');
    include('doLogout');
    context.response={status:200,ok:true,json:async()=>({redirect_url:'/auth/eims/logout/start?ticket=opaque'})};
    vm.runInContext(`AUTH_CSRF='fixture-csrf'`,context);
    await vm.runInContext('doLogout()',context);
    assert.equal(recorded.options.method,'POST');
    assert.equal(recorded.options.headers['X-CSRF-Token'],'fixture-csrf');
    assert.equal(context.destination,'/auth/eims/logout/start?ticket=opaque');
    context.destination=null;
    context.response={status:200,ok:true,json:async()=>({redirect_url:'https://attacker.test/'})};
    await vm.runInContext('doLogout()',context);
    assert.equal(context.destination,null);
    assert.equal(context.notice,'退出地址无效。');
}

(async()=> {
    for (const file of ['templates/index.html','index.html']) await verify(file);
    process.stdout.write(JSON.stringify({pages:2,checks,syntax:'passed',scope:'in-memory helper execution'})+'\n');
})().catch(error=> { process.stderr.write(error.stack+'\n'); process.exitCode=1; });
