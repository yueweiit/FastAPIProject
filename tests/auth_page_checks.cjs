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
        location: {origin:'https://fifo.test',pathname:'/',search:'',assign: value => context.destination=value},
        document: {}, fetch: async () => context.response,
        showMsg: (message,ok) => {context.notice=message;context.noticeOk=ok;}});
    function include(name) {
        const pattern = new RegExp('^(?:async )?function '+name+'\\([^\\n]*\\) \\{[\\s\\S]*?^\\}', 'm');
        const match=html.match(pattern);
        assert(match, 'Function missing: '+name);
        vm.runInContext(match[0],context);
    }
    vm.runInContext(`const API=''; let AUTH_CSRF='fixture-csrf'; let AUTH_CONFIG={local_enabled:true,sso_available:true};
        let CURRENT_ROLE='',CURRENT_USER='',CURRENT_USER_ID=0;
        let productPage=1,productPageSize=20,productKeyword='',currentProductList=[],productTotal=0;
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

    const nodes = new Map();
    const node = id => {
        if (!nodes.has(id)) nodes.set(id, {value:'',textContent:'',style:{display:'none'},
            classList:{add(){},remove(){}}});
        return nodes.get(id);
    };
    context.document = {documentElement:{lang:'zh-CN'}, querySelectorAll:()=>[],
        getElementById:node,querySelector:node};
    context.window = {};
    for (const name of ['resetSalesImportState','resetBatchEditSession','resetBatchExportSession',
        'resetProductImportPreview','resetStoreProfitLossState','clearLegacyAuth']) context[name]=()=>{};
    context.renderProductPage=()=>context.productsRendered=true;
    context.loadPlatformSkuMappings=async()=>{}; // 根目录副本另有此首屏步骤，保留页面差异。
    context.setTimeout=()=>0;
    for (const name of ['authText','showLogin','handleSessionExpired','isAdmin','updateUserBar',
        'showMsg','loadProducts','loadCurrentUser','initializeAuth','doLogin']) include(name);
    const renderNotice=context.showMsg;
    context.showMsg=(message,ok)=>{renderNotice(message,ok);context.notice=message;context.noticeOk=ok;};
    function resetLogin() {
        context.handleSessionExpired();
        node('l-user').value='fixture';node('l-pass').value='fixture-password';
        node('msg').innerHTML='';
        context.notice=null;context.noticeOk=null;context.productsRendered=false;
    }

    // 公共交互脚本已加载和缺失时，错误均应保留真实内容并显示在登录框。
    for (const withScript of [true,false]) {
        if (withScript) vm.runInContext(fs.readFileSync('static/page-interactions.js','utf8'),context);
        else delete context.window.PageInteractions;
        resetLogin();
        let loginCalls = [];
        context.fetch = async (url, options) => {
            loginCalls.push({url,options});
            return url === '/auth/login' ? {ok:true,status:200,json:async()=>({})} : {ok:false,status:401};
        };
        await context.doLogin();
        assert.equal(loginCalls.map(row=>row.url).join(','),'/auth/login,/auth/me');
        assert.equal(loginCalls[0].options.credentials,'same-origin');
        assert.equal(node('l-pass').value,'');
        assert.equal(node('login-overlay').style.display,'flex');
        assert.equal(node('l-err').style.display,'block');
        assert.equal(node('l-err').textContent,context.authText('authSessionMissing'));

        resetLogin();loginCalls=[];
        context.fetch = async url => {loginCalls.push(url);
            return {ok:false,status:401,json:async()=>({detail:'用户名或密码错误'})};};
        await context.doLogin();
        assert.equal(loginCalls.length,1);
        assert.equal(node('l-err').style.display,'block');
        assert.equal(node('l-err').textContent,'用户名或密码错误');

        resetLogin();
        context.fetch=async()=>({ok:false,status:500,json:async()=>{throw new SyntaxError('not JSON');}});
        await context.doLogin();
        assert.equal(node('l-err').style.display,'block');
        assert.equal(node('l-err').textContent,context.authText('authLoginFailed'));
    }

    context.document.documentElement.lang='en';
    assert.equal(context.authText('authLoginFailed'),'登录失败，请稍后重试。');
    context.document.documentElement.lang='zh-CN';

    // 使用实际登录、会话读取及商品请求，确认首屏失败的消息在遮罩外可见。
    function successfulLogin(productResponse) {
        context.fetch=async url=>{
            if(url==='/auth/login')return {ok:true,status:200,json:async()=>({})};
            if(url==='/auth/config')return {ok:true,status:200,json:async()=>({local_enabled:true,sso_available:false})};
            if(url==='/auth/me')return {ok:true,status:200,json:async()=>({
                id:1,username:'fixture',role:'admin',csrf_token:'fixture-csrf'})};
            assert(url.startsWith('/products?'));
            return productResponse();
        };
    }
    for (const failure of [
        ()=>({ok:false,status:500,text:async()=>'{"detail":"products failed"}'}),
        ()=>({ok:false,status:503,text:async()=>'service unavailable'}),
        ()=>{throw new Error('network unavailable');},
    ]) {
        resetLogin();successfulLogin(failure);
        await context.doLogin();
        assert.equal(node('login-overlay').style.display,'none');
        assert.equal(node('l-err').style.display,'none');
        assert.equal(context.notice,context.authText('authProductsLoadFailed'));
        assert.equal(context.noticeOk,false);
        assert(node('msg').innerHTML.includes(context.authText('authProductsLoadFailed')));
        assert.equal(vm.runInContext('CURRENT_USER_ID',context),1);
        assert.equal(vm.runInContext('AUTH_CSRF',context),'fixture-csrf');
    }

    resetLogin();successfulLogin(()=>({ok:false,status:503,text:async()=>''}));
    await context.initializeAuth();
    assert.equal(node('login-overlay').style.display,'none');
    assert.equal(context.notice,context.authText('authProductsLoadFailed'));

    resetLogin();successfulLogin(()=>({ok:false,status:401}));
    await context.doLogin();
    assert.equal(node('login-overlay').style.display,'flex');
    assert.equal(node('l-err').style.display,'block');
    assert.equal(node('l-err').textContent,'登录已过期');
    assert.equal(context.notice,null);
    assert.equal(vm.runInContext('CURRENT_USER_ID',context),0);
    assert.equal(vm.runInContext('AUTH_CSRF',context),'');

    resetLogin();
    context.fetch=async()=>({ok:false,status:401});
    assert.equal(await context.loadCurrentUser(),false);
    assert.equal(node('l-err').style.display,'none');

    // 从错误状态重试成功后，清除旧错误且保持正常首屏加载。
    node('l-err').textContent='previous failure';node('l-err').style.display='block';
    successfulLogin(()=>({ok:true,status:200,text:async()=>'{"items":[],"total":0,"page":1,"page_size":20}'}));
    await context.doLogin();
    assert.equal(node('login-overlay').style.display,'none');
    assert.equal(node('l-err').style.display,'none');
    assert.equal(node('l-err').textContent,'');
    assert.equal(context.productsRendered,true);
    assert.equal(context.notice,null);

    resetLogin();
    context.fetch=async()=>{throw new Error('network unavailable');};
    await context.doLogin();
    assert.equal(node('l-err').textContent,'network unavailable');
    assert.equal(node('l-err').style.display,'block');
    // 迟到的公共脚本加载不会改变认证文案。
    const authMessage=context.authText('authSessionMissing');
    vm.runInContext(fs.readFileSync('static/page-interactions.js','utf8'),context);
    assert.equal(context.authText('authSessionMissing'),authMessage);
}

(async()=> {
    for (const file of ['templates/index.html','index.html']) await verify(file);
    process.stdout.write(JSON.stringify({pages:2,checks,syntax:'passed',scope:'in-memory helper execution'})+'\n');
})().catch(error=> { process.stderr.write(error.stack+'\n'); process.exitCode=1; });
