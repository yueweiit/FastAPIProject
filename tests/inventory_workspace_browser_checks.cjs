// 真实无头浏览器 + 本地静态页面 + 模拟API；不启动main、不连接数据库或外部网络。
// 需现有Playwright；可用INVENTORY_PLAYWRIGHT_MODULE指定现有模块，INVENTORY_QA_DIR保存截图。
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const http = require('node:http');
const {chromium} = require(process.env.INVENTORY_PLAYWRIGHT_MODULE || 'playwright');
let checks = 0;
const check = (actual, expected) => {assert.deepEqual(actual, expected); checks++;};
const wait = () => {let resolve; const promise = new Promise(done => resolve = done); return {promise, resolve};};
const products = ['stable', 'new', 'safe_stock', 'no_impairment'].map((type, i) => ({
    id: i + 1, sku: `CW000${i + 1}`, name: ['狗狗咀嚼玩具', '猫咪自嗨转盘', '不倒翁羽毛款', '<库存安全测试>'][i],
    product_type: type, safe_stock_quantity: type === 'safe_stock' ? 100 : 0,
    stock_quantity: [7281, 1988, 80, 0][i], stock_value: [85842.99, 25495.35, 915.2, 0][i],
    image: null, last_batch_at: '2026-09-23 00:00:00', created_at: '2026-09-01T00:00:00',
    stock_status: i === 2 ? 'low' : i === 3 ? 'zero' : 'normal',
}));
const offPageProduct = {...products[1], id: 99, sku: 'CW0099', name: '猫咪测试商品（其他页）'};
const prefixProduct = {...products[0], id: 100, sku: 'CW00010', name: '相近SKU商品'};
const exactSuggestion = {...products[0],id:200,sku:'SEARCH-EXACT',name:'精确候选'};
const similarSuggestions = Array.from({length:25},(_,i)=>({...products[0],id:325-i,sku:`SEARCH-EXACT-${i}`,name:`近似候选${i}`}));
const catalog = [...products, offPageProduct, prefixProduct, ...similarSuggestions, exactSuggestion];
function overview(url) {
    const keyword = url.searchParams.get('keyword') || '', filter = Number(url.searchParams.get('product_id'));
    const items = filter ? catalog.filter(p => p.id === filter) : keyword && !['slow', 'error'].includes(keyword)
        ? catalog.filter(p => `${p.sku} ${p.name}`.toLowerCase().includes(keyword.toLowerCase())) : products;
    return {items, total: items.length, page: 1, page_size: 20, scope: 'shared',
        summary: {quantity: items.reduce((sum, p) => sum + p.stock_quantity, 0), inventory_amount: items.reduce((sum, p) => sum + p.stock_value, 0),
            low_count: items.filter(p => p.stock_status === 'low').length, zero_count: items.filter(p => p.stock_status === 'zero').length}};
}
function daily(url, role) {
    const month = url.searchParams.get('month').slice(0, 7);
    const end = new Date(Number(month.slice(0, 4)), Number(month.slice(5, 7)), 0).getDate();
    const filter = Number(url.searchParams.get('product_id'));
    return {items: (filter ? catalog : products).filter(p => !filter || p.id === filter).map(product => ({
        product_id: product.id, sku: product.sku, name: product.name, image: null, warnings: [],
        days: Array.from({length: end}, (_, i) => ({date: `${month}-${String(i + 1).padStart(2, '0')}`,
            opening_quantity: role === 'admin' ? 100 + i : null, inbound: 2, outbound: 1,
            adjustment: role === 'admin' ? 0 : null, closing_quantity: role === 'admin' ? 101 + i : null})),
    })), total: filter ? 1 : 4, page: 1, page_size: 20, month: `${month}-01`, start: `${month}-01`, end: `${month}-${end}`,
    scope: role === 'admin' ? 'global' : 'store_outbound',
    warnings: ['inventory_current_documents', ...(role === 'admin' ? [] : ['inventory_scope_restricted'])]};
}

async function checkSalesHeaders(page,suffix) {
    const scroll=page.locator('#sales-ledger-scroll'),picker=page.locator('#sales-pin-picker'),toggle=page.locator('#sales-pin-toggle');
    const definitions=JSON.parse(fs.readFileSync('static/sales-fields.zh-CN.json','utf8')).fields;
    const themes=[...new Set(definitions.map(field=>field.group))];
    const open=async()=>{if(await picker.getAttribute('open')===null)await toggle.click();};
    const choose=async keys=>{
        await open();await page.locator('#sales-pin-clear').click();
        for(const key of keys)await page.locator('[data-sales-pin-key="'+key+'"]').check();
        await toggle.click();
    };
    const setScroll=async value=>page.evaluate(async value=>{
        document.getElementById('sales-ledger-scroll').scrollLeft=value;await new Promise(requestAnimationFrame);
    },value);
    const geometry=key=>page.evaluate(key=>{
        const fieldNode=document.querySelector('[data-sales-field="'+key+'"]');
        const field=fieldNode?.getBoundingClientRect();
        const cell=document.querySelector('#sales-tbody tr [data-sales-value="'+key+'"]')?.getBoundingClientRect();
        const total=document.querySelector('[data-sales-total="'+key+'"]')?.getBoundingClientRect();
        const other=document.querySelector('[data-sales-field="'+(key==='store_name'?'sold_at':'store_name')+'"]').getBoundingClientRect();
        const hit=field&&document.elementFromPoint(field.left+3,field.top+3)?.closest('th');
        return {fieldLeft:field?.left,fieldWidth:field?.width,cellLeft:cell?.left,totalLeft:total?.left,
            otherLeft:other.left,theme:fieldNode?.className.match(/sales-group-(\d+)/)?.[1],hit:hit?.dataset.salesField};
    },key);
    check(await page.locator('#sales-pin-summary').innerText(),'订单号');
    await open();check(await picker.getByRole('checkbox').count(),definitions.length-1);
    check(await picker.locator('[data-sales-pin-key="calculation_action"]').count(),0);
    await page.keyboard.press('Escape');check(await picker.getAttribute('open'),null);
    check(await toggle.evaluate(n=>document.activeElement===n),true);
    check(await page.locator('#sales-ledger-head tr').count(),1);
    check(await page.locator('#sales-ledger-head [scope="colgroup"]').count(),0);
    check(await page.evaluate(()=>[...document.querySelectorAll('#sales-ledger-head th')].every(field=>{
        const expected=getComputedStyle(field),theme=field.className.match(/sales-group-\d+/)?.[0];
        return [...document.querySelectorAll('#sales-ledger-head th.'+theme)].every(other=>{
            const actual=getComputedStyle(other);return actual.backgroundColor===expected.backgroundColor&&actual.color===expected.color;
        });
    })),true);
    check(await page.locator('[data-sales-field="unit_purchase_cost_cny"]').evaluate(n=>getComputedStyle(n).backgroundColor),'rgb(233, 242, 251)');
    check(await page.locator('[data-sales-field="purchase_cost_cny"]').evaluate(n=>getComputedStyle(n).backgroundColor),'rgb(232, 245, 239)');
    check(await page.locator('[data-sales-field="impairment_cny"]').evaluate(n=>getComputedStyle(n).backgroundColor),'rgb(255, 244, 221)');
    for(const key of ['order_no','sku','product_name','purchase_cost_cny','unit_purchase_cost_cny','impairment_cny','quantity']){
        await choose([key]);
        await scroll.scrollIntoViewIfNeeded();await setScroll(0);const before=await geometry(key);
        await setScroll(1800);const after=await geometry(key);
        check(after.hit,key);
        check(Math.abs(after.cellLeft-before.cellLeft)<1,true);check(Math.abs(after.fieldLeft-before.fieldLeft)<1,true);
        check(Math.abs(after.totalLeft-after.cellLeft)<1,true);check(after.otherLeft<before.otherLeft-100,true);
        check(after.theme,String(themes.indexOf(definitions.find(f=>f.key===key).group)));
        check(await page.evaluate(()=>document.querySelector('#sales-tbody tr td')?.dataset.salesValue),key);
        check(await page.evaluate(()=>[...document.querySelectorAll('#sales-ledger-head [id]')].map(n=>n.id).every((id,index,ids)=>ids.indexOf(id)===index)),true);
    }
    await choose(['purchase_cost_cny','order_no','store_name']);
    const multi=()=>page.evaluate(()=>[...document.querySelectorAll('#sales-ledger-head th.sales-pinned')].map(field=>{
        const key=field.dataset.salesField,head=field.getBoundingClientRect(),body=document.querySelector('#sales-tbody [data-sales-value="'+key+'"]').getBoundingClientRect(),
            total=document.querySelector('[data-sales-total="'+key+'"]').getBoundingClientRect();
        return {key,left:head.left,right:head.right,bodyLeft:body.left,totalLeft:total.left};
    }));
    await scroll.scrollIntoViewIfNeeded();await setScroll(0);const before=await multi();await setScroll(1800);const after=await multi();
    check(after.map(f=>f.key),['store_name','order_no','purchase_cost_cny']);
    check(after.every((f,i)=>Math.abs(f.left-before[i].left)<1&&Math.abs(f.left-f.bodyLeft)<1&&Math.abs(f.left-f.totalLeft)<1),true);
    check(after.slice(1).every((f,i)=>Math.abs(f.left-after[i].right)<1),true);
    check(await page.locator('#sales-pin-summary').innerText(),'已固定 3 列');
    if(process.env.INVENTORY_QA_DIR&&suffix==='/'){
        await scroll.screenshot({path:path.join(process.env.INVENTORY_QA_DIR,'sales-pin-multiple-desktop.png')});
        await open();await page.locator('.sales-view-toolbar').screenshot({path:path.join(process.env.INVENTORY_QA_DIR,'sales-pin-multiple-controls.png')});await toggle.click();
    }
    await open();await page.locator('#sales-page-total-label').click();check(await picker.getAttribute('open'),null);
    await choose([]);check(await page.locator('#sales-ledger-head tr').count(),1);
    check(await page.locator('#sales-ledger-table .sales-pinned').count(),0);
    check(await page.locator('#sales-ledger-head [data-sales-field]').first().getAttribute('data-sales-field'),'store_name');
    await choose(['store_name','order_no','sku','product_name']);
    const saved=await page.evaluate(()=>localStorage.getItem('fifo.sales.ledger.pin.v2.user:'+CURRENT_USER_ID));
    await page.setViewportSize({width:390,height:844});await scroll.scrollIntoViewIfNeeded();
    await page.waitForFunction(()=>document.querySelectorAll('#sales-ledger-head th.sales-pinned').length===1);
    check(await page.locator('#sales-pin-summary').innerText(),'固定 1 / 4 列');
    check(await page.evaluate(()=>localStorage.getItem('fifo.sales.ledger.pin.v2.user:'+CURRENT_USER_ID)),saved);
    await open();check(await page.locator('#sales-pin-limit').isVisible(),true);
    const box=await picker.locator('.sales-pin-panel').boundingBox();check(box.x>=0&&box.x+box.width<=391,true);
    await toggle.click();await setScroll(1800);
    const mobile=await geometry('store_name');check(mobile.fieldWidth<=161,true);check(Math.abs(mobile.fieldLeft-mobile.cellLeft)<1,true);
    check(Math.abs(mobile.totalLeft-mobile.cellLeft)<1,true);
    if(process.env.INVENTORY_QA_DIR&&suffix==='/')await scroll.screenshot({path:path.join(process.env.INVENTORY_QA_DIR,'sales-pin-multiple-mobile.png')});
    await page.setViewportSize({width:1440,height:1000});
    await page.waitForFunction(()=>document.querySelectorAll('#sales-ledger-head th.sales-pinned').length===4);
    await page.reload();await page.waitForFunction(()=>CURRENT_USER_ID===10);
    await page.getByRole('button',{name:'销售记录',exact:true}).click();
    await page.waitForFunction(()=>document.querySelectorAll('#sales-ledger-head th.sales-pinned').length===4);
    check(await page.locator('#sales-pin-summary').innerText(),'已固定 4 列');
    await choose(['order_no']);
    await page.evaluate(()=>window.SalesLedger.render([],{}));await scroll.scrollIntoViewIfNeeded();await setScroll(1800);
    check(await page.locator('#sales-tbody .empty').getAttribute('colspan'),String(definitions.length));check((await geometry('order_no')).theme,'0');
    await setScroll(0);await page.evaluate(()=>loadSales());
}

async function checkSalesScrolling(page,suffix,rows) {
    const detail=page.locator('#sales-ledger-scroll'),summary=page.locator('#sales-total-scroll');
    const longRows=Array.from({length:20},(_,i)=>({...rows[0],id:i+1,order_no:`SCROLL-${i+1}`,display_name:'Long product name for scrolling '.repeat(8)}));
    const metrics=()=>page.evaluate(()=>{
        const detail=document.getElementById('sales-ledger-scroll'),summary=document.getElementById('sales-total-scroll');
        const box=detail.getBoundingClientRect(),head=detail.querySelector('thead').getBoundingClientRect();
        const field=detail.querySelector('[data-sales-field="store_name"]').getBoundingClientRect();
        const last=detail.querySelector('[data-sales-field="calculation_action"]').getBoundingClientRect();
        const button=detail.querySelector('[data-sales-value="calculation_action"] button').getBoundingClientRect();
        return {height:box.height,bottom:box.bottom,viewport:innerHeight,pageY:scrollY,top:detail.scrollTop,
            left:detail.scrollLeft,summaryLeft:summary.scrollLeft,headTop:head.top,headerRows:detail.querySelectorAll('thead tr').length,fieldTop:field.top,
            scrollHeight:detail.scrollHeight,clientHeight:detail.clientHeight,detailWidth:detail.clientWidth,summaryWidth:summary.clientWidth,
            contentRight:box.left+detail.clientLeft+detail.clientWidth,lastRight:last.right,buttonRight:button.right};
    });
    for(const width of [1440,390]){
        await page.setViewportSize({width,height:width===390?844:1000});
        await page.evaluate(rows=>window.SalesLedger.render(rows,{}),longRows);
        await detail.evaluate(node=>node.scrollIntoView({block:'center',inline:'nearest'}));await detail.hover();const before=await metrics();
        check(await summary.evaluate(node=>getComputedStyle(node).overflowX),'hidden');
        check(await summary.evaluate(node=>node.offsetHeight-node.clientHeight-node.clientTop*2),0);
        check(await summary.getAttribute('tabindex'),null);
        check(before.height<=before.viewport*.7+1,true);check(before.scrollHeight>before.clientHeight,true);
        assert(before.bottom<=before.viewport+1,JSON.stringify({width,before}));checks++;check(Math.abs(before.detailWidth-before.summaryWidth)<1,true);
        await page.mouse.wheel(0,900);
        await page.waitForFunction(top=>Math.abs(document.getElementById('sales-ledger-scroll').scrollTop-top-900)<1,before.top);
        const vertical=await metrics();
        check(Math.abs(vertical.pageY-before.pageY)<1,true);
        check(Math.abs(vertical.headTop-before.headTop)<1,true);
        check(vertical.headerRows,1);check(Math.abs(vertical.fieldTop-before.fieldTop)<1,true);
        const keptTop=vertical.top;
        await summary.evaluate(node=>{node.scrollLeft=800;});
        await page.waitForFunction(()=>Math.abs(document.getElementById('sales-ledger-scroll').scrollLeft-800)<1);
        check(Math.abs((await metrics()).top-keptTop)<1,true);
        await detail.evaluate(node=>{node.scrollLeft=1800;});
        await page.waitForFunction(()=>Math.abs(document.getElementById('sales-total-scroll').scrollLeft-1800)<1);
        // 经典竖滚动条也不能让两边横向范围不同，最右列须完整可达。
        await detail.evaluate(node=>{node.scrollLeft=node.scrollWidth;});
        await page.waitForFunction(()=>{const a=document.getElementById('sales-ledger-scroll'),b=document.getElementById('sales-total-scroll');return Math.abs(a.scrollLeft-b.scrollLeft)<1;});
        const right=await metrics();
        // 稳定滚动槽会计入scrollWidth，按末列/按钮的实际边界验证完整可见。
        check(right.lastRight<=right.contentRight+1,true);check(right.buttonRight<=right.contentRight+1,true);
        check(await detail.locator('[data-sales-value="calculation_action"]').first().evaluate(cell=>cell.querySelector('button').getBoundingClientRect().right<=cell.getBoundingClientRect().right),true);
        await detail.focus();
        const keyScrollEnded=page.evaluate(()=>new Promise(resolve=>document.getElementById('sales-ledger-scroll').addEventListener('scrollend',resolve,{once:true})));
        await detail.press('PageDown');await keyScrollEnded;
        await page.waitForFunction(top=>document.getElementById('sales-ledger-scroll').scrollTop>top,keptTop);
        check(Math.abs((await metrics()).pageY-before.pageY)<1,true);
        if(process.env.INVENTORY_QA_DIR&&suffix==='/')await detail.screenshot({path:path.join(process.env.INVENTORY_QA_DIR,`sales-scroll-${width===390?'mobile':'desktop'}.png`)});
        await page.evaluate(rows=>window.SalesLedger.render(rows,{}),rows);
        check((await metrics()).top,0);check(Math.abs((await metrics()).left-right.left)<1,true);
    }
    await page.setViewportSize({width:1440,height:1000});
    await page.waitForFunction(()=>document.querySelector('[data-sales-field="order_no"]').getBoundingClientRect().width===200);
    await page.waitForFunction(()=>Math.abs(document.getElementById('sales-ledger-scroll').scrollLeft-document.getElementById('sales-total-scroll').scrollLeft)<1);
    await summary.evaluate(node=>{node.scrollLeft=0;});
    await page.waitForFunction(()=>document.getElementById('sales-ledger-scroll').scrollLeft===0);
    await page.evaluate(()=>loadSales());
    await page.locator('#sales-page-total-label .help-tip-trigger').hover();
    const help=page.locator('.sales-view-toolbar [data-sales-pin-text="pageTotalHelp"]');await help.waitFor({state:'visible'});
    check(await help.innerText().then(value=>value.includes('只合计当前页')&&value.includes('全部筛选记录')),true);
    await page.keyboard.press('Escape');
    await detail.evaluate(node=>{node.scrollLeft=1800;});
    await page.waitForFunction(()=>document.getElementById('sales-total-scroll').scrollLeft===1800);
    // The caption now stays outside the scrolling cells: focus must not move either table.
    await page.locator('#sales-page-total-label .help-tip-trigger').focus();
    await page.waitForFunction(()=>{const a=document.getElementById('sales-ledger-scroll'),b=document.getElementById('sales-total-scroll');return Math.abs(a.scrollLeft-1800)<1&&Math.abs(a.scrollLeft-b.scrollLeft)<1;});
    check(await page.locator('#sales-page-total-label .help-tip-trigger').evaluate(node=>document.activeElement===node),true);
    await help.waitFor({state:'visible'});
    check(await help.innerText().then(value=>value.includes('本页合计会同步移动')&&!value.includes('拖动合计')),true);
    await page.keyboard.press('Escape');await detail.focus();
    if(process.env.INVENTORY_QA_DIR&&suffix==='/'){
        await summary.scrollIntoViewIfNeeded();
        const box=await summary.boundingBox();
        await page.screenshot({path:path.join(process.env.INVENTORY_QA_DIR,'sales-summary-no-scrollbar.png'),clip:{x:box.x,y:box.y,width:box.width,height:Math.min(250,page.viewportSize().height-box.y)}});
    }
}

async function checkSalesColumnVisibility(page,suffix) {
    const definitions=JSON.parse(fs.readFileSync('static/sales-fields.zh-CN.json','utf8')).fields;
    const picker=page.locator('#sales-visible-picker'),toggle=page.locator('#sales-visible-toggle');
    const open=async()=>{if(await picker.getAttribute('open')===null)await toggle.click();};
    const show=async(key,visible)=>{await open();await picker.locator('[data-sales-visible-key="'+key+'"]').setChecked(visible);};
    const fieldKeys=()=>page.locator('#sales-ledger-head [data-sales-field]').evaluateAll(nodes=>nodes.map(node=>node.dataset.salesField));
    await open();check(await picker.getByRole('checkbox').count(),definitions.length);
    check(await picker.getByRole('checkbox',{checked:true}).count(),definitions.length);
    const pinned=await page.evaluate(()=>localStorage.getItem('fifo.sales.ledger.pin.v2.user:'+CURRENT_USER_ID));
    const total=await page.locator('[data-sales-total="order_total_cost_cny"]').innerText();
    await show('order_no',false);
    check(await page.locator('[data-sales-field="order_no"]').count(),0);
    check(await page.locator('[data-sales-value="order_no"]').count(),0);
    check(await page.locator('[data-sales-total="order_no"]').count(),0);
    check(await page.locator('#sales-pin-summary').innerText(),'不固定');
    check(await page.evaluate(()=>localStorage.getItem('fifo.sales.ledger.pin.v2.user:'+CURRENT_USER_ID)),pinned);
    await toggle.click();await page.locator('#sales-pin-toggle').click();
    check(await page.locator('[data-sales-pin-key="order_no"]').isChecked(),true);
    check(await page.locator('[data-sales-pin-key="order_no"]').isDisabled(),true);
    await page.keyboard.press('Escape');await show('order_no',true);
    check(await page.locator('[data-sales-field="order_no"]').getAttribute('class').then(value=>value.includes('sales-pinned')),true);
    for(const field of definitions.filter(field=>field.group==='原币核对'))await show(field.key,false);
    await show('calculation_action',false);await show('purchase_cost_cny',false);
    check(await page.locator('#sales-ledger-head .sales-group-1').count(),0);
    check(await page.locator('#sales-ledger-head tr').count(),1);
    const keys=await fieldKeys(),hidden=definitions.length-keys.length;
    check(await page.locator('#sales-visible-summary').innerText(),`已隐藏 ${hidden} 列`);
    check(await page.locator('#sales-ledger-cols col').count(),keys.length);
    check(await page.locator('#sales-total-cols col').count(),keys.length);
    check(await page.locator('#sales-tbody tr').first().locator('[data-sales-value]').count(),keys.length);
    check(await page.locator('[data-sales-total]').count(),keys.length);
    check(await page.locator('[data-sales-total="order_total_cost_cny"]').innerText(),total);
    await toggle.click();await page.reload();await page.waitForFunction(()=>CURRENT_USER_ID===10);
    await page.getByRole('button',{name:'销售记录',exact:true}).click();
    await page.waitForFunction(count=>document.querySelectorAll('#sales-ledger-head [data-sales-field]').length===count,keys.length);
    check(await fieldKeys(),keys);
    await page.setViewportSize({width:390,height:844});await open();
    const box=await picker.locator('.sales-pin-panel').boundingBox();check(box.x>=0&&box.x+box.width<=391,true);
    check(await toggle.getAttribute('aria-labelledby'),'sales-visible-label-text sales-visible-summary');
    if(process.env.INVENTORY_QA_DIR&&suffix==='/')await picker.locator('.sales-pin-panel').screenshot({path:path.join(process.env.INVENTORY_QA_DIR,'sales-visible-fields-mobile.png')});
    await page.keyboard.press('Escape');check(await picker.getAttribute('open'),null);
    check(await toggle.evaluate(node=>document.activeElement===node),true);
    await page.setViewportSize({width:1440,height:1000});await open();await page.locator('#sales-visible-all').click();
    for(const meta of definitions.slice(0,-1))await show(meta.key,false);
    check(await fieldKeys(),['calculation_action']);
    // 原生checkbox会立刻被恢复，单独点击验证拒绝隐藏最后一列。
    await picker.locator('[data-sales-visible-key="calculation_action"]').click();
    check(await picker.locator('[data-sales-visible-key="calculation_action"]').isChecked(),true);
    check(await page.locator('#sales-visible-status').innerText(),'请至少保留一个显示字段。');
    await page.locator('#sales-visible-all').click();check((await fieldKeys()).length,definitions.length);
    await page.evaluate(()=>{const token=window.SalesLedger.beginLoad();window.SalesLedger.fail(new Error('visibility failure'),token);});
    await show('order_no',false);
    check(await page.locator('#sales-tbody .msg-err').innerText(),'visibility failure');
    check(await page.locator('#sales-tbody .msg-err').getAttribute('colspan'),String(definitions.length-1));
    check(await page.locator('#sales-tbody [data-sales-value]').count(),0);
    check(await page.locator('[data-sales-total]').count(),0);
    await page.locator('#sales-visible-all').click();await toggle.click();await page.evaluate(()=>loadSales());
    await page.waitForFunction(()=>document.querySelector('[data-sales-field="order_no"]').getBoundingClientRect().width===200);
    check(await page.evaluate(()=>localStorage.getItem('fifo.sales.ledger.pin.v2.user:'+CURRENT_USER_ID)),pinned);
    await page.locator('#sales-ledger-scroll').evaluate(node=>{node.scrollLeft=0;});
}

async function checkHelpTips(page, suffix) {
    const button = page.locator('#p-type-label .help-tip-trigger');
    const source = page.locator('#product-form [data-product-type-text="ruleHelp"]');
    await button.waitFor();
    check(await source.isVisible(), false);
    check(await button.getAttribute('type'), 'button');
    check(await button.getAttribute('aria-label'), '商品类型 · 查看说明');
    check(await button.getAttribute('aria-describedby'), await source.getAttribute('id'));
    if (process.env.INVENTORY_QA_DIR && suffix === '/') {
        fs.mkdirSync(process.env.INVENTORY_QA_DIR, {recursive: true});
        await page.screenshot({path: path.join(process.env.INVENTORY_QA_DIR, 'product-help-collapsed.png'), fullPage: true});
    }
    await button.hover();
    await source.waitFor({state: 'visible'});
    check(await source.getAttribute('role'), 'tooltip');
    check(await source.innerText().then(s => s.includes('设置安全库存时仅对超出部分计提')), true);
    const box = await source.boundingBox();
    await page.mouse.move(box.x + box.width / 2, box.y + box.height / 2);
    await page.waitForTimeout(240);
    check(await source.isVisible(), true); // 可以移入浮层阅读，不随离开问号立即消失。
    if (process.env.INVENTORY_QA_DIR && suffix === '/') {
        fs.mkdirSync(process.env.INVENTORY_QA_DIR, {recursive: true});
        await page.screenshot({path: path.join(process.env.INVENTORY_QA_DIR, 'product-help-desktop.png'), fullPage: true});
    }
    await page.keyboard.press('Escape');
    check(await source.isVisible(), false);
    await button.focus();
    check(await source.isVisible(), true);
    await page.keyboard.press('Escape');
    check(await source.isVisible(), false);
    await button.press('Enter');
    check(await source.isVisible(), true);
    await page.locator('#p-name').click();
    check(await source.isVisible(), false);
    await button.click();
    await button.click();
    check(await source.isVisible(), false);
    await page.locator('#p-name').focus();

    // 模态弹窗里的问号使用顶层浮层；Esc 先收起说明，不误关表单。
    await page.evaluate(() => document.getElementById('product-import-dialog').showModal());
    const dialogButton = page.locator('#product-import-title .help-tip-trigger');
    const dialogSource = page.locator('#product-import-dialog [data-help-tip]');
    await dialogButton.focus();
    check(await dialogSource.isVisible(), true);
    await page.keyboard.press('Escape');
    check(await dialogSource.isVisible(), false);
    check(await page.locator('#product-import-dialog').evaluate(n => n.open), true);
    await dialogButton.click();
    await page.evaluate(() => document.getElementById('product-import-dialog').close());
    await page.waitForFunction(() => !document.querySelector('[role="tooltip"]:popover-open'));

    await page.setViewportSize({width: 390, height: 760});
    await button.click();
    const mobileBox = await source.boundingBox();
    check(mobileBox.x >= 11 && mobileBox.x + mobileBox.width <= 379, true);
    check(mobileBox.y >= 11 && mobileBox.y + mobileBox.height <= 749, true);
    if (process.env.INVENTORY_QA_DIR && suffix === '/') await page.screenshot({path: path.join(process.env.INVENTORY_QA_DIR, 'product-help-mobile.png'), fullPage: true});
    await page.keyboard.press('Escape');
    await page.setViewportSize({width: 1440, height: 1000});

    // 动态内容、空说明、真实错误与已移除节点：不重复挂问号，不隐藏警示。
    await page.evaluate(() => {
        const node = document.createElement('p'); node.id = 'qa-help'; node.dataset.helpTip = '';
        node.textContent = '说明 <img src=x onerror="window.unexpectedHelpScript=1">';
        document.getElementById('product-form').appendChild(node);
    });
    const dynamicSource = page.locator('#qa-help');
    const dynamicButton = page.locator('.help-tip-trigger[aria-describedby="qa-help"]');
    await dynamicButton.waitFor();
    await dynamicButton.focus();
    check(await dynamicSource.locator('img').count(), 0);
    check(await page.evaluate(() => !!window.unexpectedHelpScript), false);
    await page.evaluate(() => {document.getElementById('qa-help').textContent = '更新后的说明';});
    await page.waitForFunction(() => document.getElementById('qa-help').textContent === '更新后的说明');
    check(await dynamicButton.count(), 1);
    check(await dynamicSource.isVisible(), true);
    // MutationObserver 和定位不会在空闲时不断触发自身。
    await page.waitForTimeout(50);
    check(await page.evaluate(async () => {
        let changes = 0; const observer = new MutationObserver(records => changes += records.length);
        observer.observe(document.body, {subtree:true, childList:true, attributes:true});
        await new Promise(resolve => setTimeout(resolve, 100)); observer.disconnect(); return changes;
    }), 0);
    await page.evaluate(() => window.HelpTips.setInline(document.getElementById('qa-help'), true));
    await dynamicButton.waitFor({state: 'hidden'});
    check(await dynamicSource.isVisible(), true);
    check(await dynamicSource.getAttribute('role'), null);
    await page.evaluate(() => {
        const node = document.getElementById('qa-help'); node.textContent = '';
        window.HelpTips.setInline(node, false);
    });
    check(await dynamicButton.isVisible(), false);
    await page.evaluate(() => {document.getElementById('qa-help').textContent = '再次显示说明';});
    await dynamicButton.waitFor();
    check(await dynamicButton.count(), 1);
    await page.evaluate(() => document.getElementById('qa-help').setAttribute('role', 'alert'));
    await dynamicButton.waitFor({state:'hidden'});
    check(await dynamicSource.isVisible(), true);
    check(await dynamicSource.getAttribute('role'), 'alert');
    await page.evaluate(() => document.getElementById('qa-help').removeAttribute('role'));
    await dynamicButton.waitFor({state:'visible'});
    // 重建正文到下一帧清理之间，旧问号仍可能收到焦点或悬停，不能打开已脱离DOM的内容。
    await page.evaluate(() => {
        const trigger=document.querySelector('.help-tip-trigger[aria-describedby="qa-help"]');
        document.getElementById('qa-help').remove();
        trigger.dispatchEvent(new MouseEvent('mouseenter'));
        trigger.dispatchEvent(new FocusEvent('focus'));
    });
    await dynamicButton.waitFor({state: 'detached'});
    check(await page.locator('#qa-help').count(),0);
    check(await page.locator('#l-err').getAttribute('data-help-tip'), null);
    check(await page.locator('#product-import-status').getAttribute('data-help-tip'), null);
    check(await page.locator('#pending-confirmations-summary').getAttribute('data-help-tip'), null);

    // 财务工作台的折叠区说明，点问号不打开折叠区；浮层也不受祖先 overflow 裁切。
    await page.evaluate(() => {
        const details = document.createElement('details'); details.id = 'qa-details';
        details.style.cssText = 'overflow:hidden;height:36px';
        details.innerHTML = '<summary>折叠区说明</summary><p id="qa-detail-help" data-help-tip data-help-anchor="summary">折叠时也能查看完整说明，说明不受容器裁切。</p>';
        document.getElementById('product-form').appendChild(details);
    });
    const foldedButton = page.locator('#qa-details .help-tip-trigger');
    await foldedButton.waitFor();
    await foldedButton.click();
    check(await page.locator('#qa-detail-help').isVisible(), true);
    check(await page.locator('#qa-details').evaluate(node => node.open), false);
    await page.keyboard.press('Escape');
    check(await page.locator('#qa-detail-help').evaluate(node => node.parentElement.id), 'qa-details');
    await foldedButton.click();
    await page.evaluate(() => document.getElementById('qa-details').remove());
    await page.locator('#qa-detail-help').waitFor({state:'detached'});

    // 未提供 Popover 能力时保留可见原文，不留下失效问号。
    const fallback = await page.context().browser().newPage();
    try {
        await fallback.setContent('<html lang="zh-CN"><body><p data-help-tip id="fallback-help">原有说明</p></body></html>');
        await fallback.evaluate(() => {HTMLElement.prototype.showPopover = undefined;});
        await fallback.addScriptTag({content: fs.readFileSync(path.resolve('static/page-interactions.js'), 'utf8')});
        check(await fallback.locator('#fallback-help').isVisible(), true);
        check(await fallback.locator('.help-tip-trigger').count(), 0);
    } finally {await fallback.close();}
}

async function main() {
    const importRequests=[];
    const files = {'/': 'templates/index.html', '/copy': 'index.html',
        '/page-interactions.js': 'static/page-interactions.js',
        '/operation-logs.js': 'static/operation-logs.js',
        '/sales-ledger.js':'static/sales-ledger.js', '/sales-fields.json':'static/sales-fields.zh-CN.json',
        '/sales-import-progress.js':'static/sales-import-progress.js',
        '/product-search.js':'static/product-search.js',
        '/inventory-workspace.js': 'static/inventory-workspace.js', '/inventory-assessment.js': 'static/inventory-assessment.js', '/inventory-allocations.js': 'static/inventory-allocations.js'};
    const server = http.createServer((req, res) => {
        if (new URL(req.url,'http://unit.invalid').pathname==='/sales/import-stream') {
            const item={body:'',write:event=>res.write(JSON.stringify(event)+'\n'),end:()=>res.end()};
            req.on('data',chunk=>{item.body+=chunk.toString();});
            res.writeHead(200,{'Content-Type':'application/x-ndjson','Cache-Control':'no-cache'});
            res.flushHeaders();item.write({type:'progress',stage:'parsing'});importRequests.push(item);return;
        }
        const file = files[new URL(req.url, 'http://unit.invalid').pathname];
        if (!file) {res.writeHead(404); res.end(); return;}
        res.setHeader('Content-Type', file.endsWith('.js') ? 'text/javascript; charset=utf-8' : file.endsWith('.json') ? 'application/json' : 'text/html; charset=utf-8');
        res.end(fs.readFileSync(path.resolve(file)));
    });
    await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
    let browser;
    try {
        browser = await chromium.launch({headless: true, ...(process.env.INVENTORY_BROWSER_CHANNEL ? {channel: process.env.INVENTORY_BROWSER_CHANNEL} : {})});
        const page = await browser.newPage({viewport: {width: 1440, height: 1000}});
        let role = 'admin', gate = null, detailGate = null, requestCount = 0, dailyWarning = null;
        let exportGate = null, exportStatus = 200, exportHtml = false;
        let productGate = null, productStatus = 200;
        const productCalls = [];
        const exportCalls = [];
        const assignmentCalls = [];
        const allocationCalls=[],allocationQueries=[];let allocationGate=null,allocationStatus=200,allocationStoresStatus=200;let allocationTotal=1,allocationTotalAfterSave=null;
        let batchListStatus=200,allocationReadStatus=200,batchListGate=null;
        const salesExportCalls = [];
        const salesListQueries=[],salesSummaryQueries=[];
        let salesExportStatus = 200;
        let productProfitRows=[],productProfitStatus=200;const productProfitQueries=[];
        let profitLossFixture = null, profitLossStatus = 200;
        let salesRows = [];
        let calculationGate=null,calculationStatus=200,calculationMissingOrderDate=false;
        let fieldHelpStatus=200;
        let pendingStatus=200;
        const manualSettlementCalls=[];let manualStatus=200;
        let countGate=null,countStatus=200,countNetworkError=false,countRecords=[],countCalls=[];
        const assignmentStores = [{id: 7, name: '店铺A', is_active: true}, {id: 8, name: '<店铺B>', is_active: true}];
        const batchFixture = {id: 1, product_id: 1, batch_no: 'IN-TEST-1', user_id: 10, quantity: 100, remaining_quantity: 70,
            purchase_price: '10.0000', total_amount: '1000.0000', unit_cost: '10.0000', shipping_cost: '0.0000', last_mile_cost: '0.0000',
            other_cost: '0.0000', arrived_at: '2026-09-01T00:00:00', shipping_date: null, edit_version: 'a'.repeat(64),
            impairment_store_ids: [7, 8], impairment_store_names: ['店铺A', '<店铺B>'], impairment_store_history: []};
        const pageErrors = [];
        page.on('pageerror', error => {pageErrors.push(error.message);console.error('Browser page error:', error.message);});
        await page.route('**/*', async route => {
            const url = new URL(route.request().url());
            if (url.hostname !== '127.0.0.1') {await route.abort(); return;}
            const fulfill = data => route.fulfill({status: 200, contentType: 'application/json', body: JSON.stringify(data)});
            if(url.pathname==='/sales/import-stream')return route.continue();
            if (url.pathname === '/sales-fields.json' && fieldHelpStatus !== 200) return route.fulfill({status:fieldHelpStatus,body:'unavailable'});
            if (url.pathname === '/auth/config') return fulfill({local_enabled: true, sso_available: false});
            if (url.pathname === '/auth/me') return fulfill({id: 10, username: '库存测试', role, store_id: role === 'admin' ? null : 7, csrf_token: 'test-only'});
            if (url.pathname === '/products') {
                const keyword = url.searchParams.get('keyword') || '';
                if (!keyword) return fulfill({items: products, total: 4, page: 1, page_size: 20});
                productCalls.push(Object.fromEntries(url.searchParams));
                const status = productStatus;
                if (productGate) {const held = productGate; productGate = null; await held.promise;}
                if (status !== 200) return route.fulfill({status, contentType: 'application/json', body: JSON.stringify({detail: '商品查询失败测试'})});
                const matches = catalog.filter(product => `${product.sku} ${product.name}`.toLowerCase().includes(keyword.toLowerCase()));
                if(url.searchParams.get('prioritize_exact_sku')==='true')matches.sort((a,b)=>Number(b.sku.toLowerCase()===keyword.toLowerCase())-Number(a.sku.toLowerCase()===keyword.toLowerCase()));
                const pageNumber=Number(url.searchParams.get('page')||1),pageSize=Number(url.searchParams.get('page_size')||20);
                const items=matches.slice((pageNumber-1)*pageSize,pageNumber*pageSize);
                return fulfill({items,total:matches.length,page:pageNumber,page_size:pageSize});
            }
            if (url.pathname === '/products/options') return fulfill(products);
            if (url.pathname === '/finance-masters/platform-sku-mappings') return fulfill([]);
            if(url.pathname==='/finance-masters/stores'&&allocationStoresStatus!==200)return route.fulfill({status:allocationStoresStatus,contentType:'application/json',body:JSON.stringify({detail:'store load failed'})});
            if (url.pathname === '/finance-masters/stores') return fulfill(assignmentStores);
            if (url.pathname === '/inventory/assessment/export') return route.fulfill({status:200,
                contentType:'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',body:Buffer.from('PK\x03\x04fixture')});
            if (url.pathname === '/inventory/assessment') return fulfill({items:products.map(p=>({
                product_id:p.id,sku:p.sku,name:p.name,as_of:url.searchParams.get('as_of'),system_quantity:p.stock_quantity,
                month_inbound:100,month_outbound:20,average_unit_cost:p.id===4?null:10,inventory_amount:p.id===4?null:p.stock_quantity*10,
                impairment_amount:p.id===4?null:10,monthly_total_impairment_amount:p.id===4?null:12,book_value:p.id===4?null:p.stock_quantity*10-10,
                ledger_version:'a'.repeat(64),warnings:p.id===4?['inventory_assessment_quantity_mismatch']:[],count:countRecords.filter(r=>r.product_id===p.id).at(-1)||null,count_basis_changed:false,
                batches:[{batch_no:'<BATCH>',arrived_at:'2026-09-01',age_days:29,quantity:p.id===4?null:p.stock_quantity,unit_cost:10,
                    impairment_amount:p.id===4?null:10,store_names:['<店铺A>'],store_allocations:[{store_name:'<店铺A>',impairment_amount:p.id===4?null:10}],note:'<script>unsafe</script>'}]
            })),total:4,page:1,page_size:20,can_write:role!=='viewer',scope:role==='admin'?'global':'shared_current'});
            if (url.pathname === '/inventory/stock-counts') {
                if(route.request().method()==='POST') {
                    const payload=route.request().postDataJSON();countCalls.push(payload);
                    if(countNetworkError)return route.abort('failed');
                    const status=countStatus;
                    if(countGate){const held=countGate;countGate=null;await held.promise;}
                    if(status!==200)return route.fulfill({status,contentType:'application/json',body:JSON.stringify({detail:{code:'inventory_count_conflict'}})});
                    const result={...payload,id:countRecords.length+1,revision:countRecords.length+1,created_at:'2026-10-29T10:00:00',created_by:10,
                        system_quantity:products.find(p=>p.id===payload.product_id).stock_quantity};result.difference=result.actual_quantity-result.system_quantity;
                    countRecords.push(result);return fulfill(result);
                }
                const items=countRecords.filter(r=>r.product_id===Number(url.searchParams.get('product_id'))).reverse();
                return fulfill({items,total:items.length,page:1,page_size:20});
            }
            if (url.pathname === '/sales/export') {
                salesExportCalls.push(Object.fromEntries(url.searchParams));
                if (salesExportStatus !== 200) return route.fulfill({status: salesExportStatus, contentType: 'application/json',
                    body: JSON.stringify({detail: {code: 'sales_export_empty'}})});
                return route.fulfill({status: 200, contentType: 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
                    body: Buffer.from('PK\x03\x04sales-download-transport-fixture')});
            }
            if (url.pathname === '/sales/monthly-summary') {salesSummaryQueries.push(Object.fromEntries(url.searchParams));return fulfill([]);}
            if (url.pathname === '/reports/store-profit-loss' && profitLossFixture) {
                if (profitLossStatus !== 200) return route.fulfill({status: profitLossStatus, contentType: 'application/json', body: JSON.stringify({detail: '损益读取失败测试'})});
                return fulfill(profitLossFixture);
            }
            if (url.pathname === '/reports/product-line-profit') {
                productProfitQueries.push(Object.fromEntries(url.searchParams));
                if(productProfitStatus!==200)return route.fulfill({status:productProfitStatus,contentType:'application/json',body:JSON.stringify({detail:'商品汇总读取失败测试'})});
                return fulfill(productProfitRows);
            }
            if (url.pathname === '/sales/imports') return fulfill([]);
            if(url.pathname==='/sales/manual-settlements'){
                const payload=route.request().postDataJSON();manualSettlementCalls.push(payload);
                if(manualStatus!==200)return route.fulfill({status:manualStatus,contentType:'application/json',body:JSON.stringify({detail:'补录失败测试'})});
                return fulfill({id:-100,quantity:0,source_quantity:payload.quantity,total_cost:0});
            }
            if (/^\/sales\/\d+\/cost-breakdown$/.test(url.pathname)) {
                const status=calculationStatus;
                if(calculationGate){const held=calculationGate;calculationGate=null;await held.promise;}
                if(status!==200)return route.fulfill({status,contentType:'application/json',body:JSON.stringify({detail:{code:'sale_cost_not_found',message:'读取失败测试'}})});
                const missingDate=calculationMissingOrderDate;
                const sale={...salesRows[0],display_sku:'CW0001',display_name:'狗狗咀嚼玩具',...(missingDate?{impairment_cny:null,impairment_review_reason:'订单创建日期缺失，不能使用结算日期计算出库跌价'}:{})};
                return fulfill({sale,sku:'CW0001',product_name:'狗狗咀嚼玩具',quantity_basis:'inventory_units',
                    cost_batches:[{batch_id:1,batch_no:'COST-BATCH',quantity:2,unit_cost:10,total_cost_cny:20,purchase_cost_cny:8,head_shipping_cost_cny:4,last_mile_cost_cny:2,other_cost_cny:6}],
                    impairment_batches:[{batch_id:1,batch_no:'COST-BATCH',quantity:2,unit_cost:10,impairment_units:missingDate?null:'.09',impairment_cny:missingDate?null:'.9',arrival_date:'2026-09-01',order_date:missingDate?null:'2026-09-10',age_days:missingDate?null:9,product_type:missingDate?null:'new',daily_rate:missingDate?null:'.005',total_cost_cny:20}],
                    order_summary:{order_no:sale.order_no,store_id:7,store_name:'店铺A',line_count:2,complete:true,total_cost_cny:25,impairment_cny:missingDate?null:'1.1'},
                    order_lines:[sale,{...sale,id:101,display_sku:'COMPONENT-OFF-PAGE',display_name:'筛选外组合组件',quantity:1,order_total_cost_cny:5,impairment_cny:'.2'}]});
            }
            if (url.pathname === '/sales') {salesListQueries.push(Object.fromEntries(url.searchParams));return fulfill({items: salesRows, total: salesRows.length, page: 1, page_size: 20});}
            if (url.pathname === '/sales/pending-confirmations') {
                if(pendingStatus!==200)return route.fulfill({status:pendingStatus,contentType:'application/json',body:JSON.stringify({detail:'<待确认失败测试>'})});
                return fulfill({items: [], total: 0, page: 1, page_size: 20});
            }
            if (/^\/batches\/\d+\/impairment-stores$/.test(url.pathname)) {
                const payload=route.request().postDataJSON();allocationCalls.push(payload);
                const status=allocationStatus;
                if(allocationGate){const held=allocationGate;allocationGate=null;await held.promise;}
                if(status!==200)return route.fulfill({status,contentType:'application/json',body:JSON.stringify({detail:{code:'batch_edit_conflict'}})});
                Object.assign(batchFixture,payload,{edit_version:'b'.repeat(64),impairment_store_names:payload.impairment_store_ids.map(id=>assignmentStores.find(store=>store.id===id).name)});
                if(allocationTotalAfterSave!==null){allocationTotal=allocationTotalAfterSave;allocationTotalAfterSave=null;}
                return fulfill({...batchFixture,id:Number(url.pathname.split('/')[2])});
            }
            if (url.pathname === '/batches' || url.pathname === '/batches/1') {
                if (route.request().method() !== 'GET') {
                    const payload = route.request().postDataJSON(); assignmentCalls.push(payload);
                    return fulfill({...batchFixture,...payload,impairment_store_ids:payload.impairment_store_ids||batchFixture.impairment_store_ids,impairment_store_names:(payload.impairment_store_ids||batchFixture.impairment_store_ids).map(id=>assignmentStores.find(store=>store.id===id).name)});
                }
                allocationQueries.push(Object.fromEntries(url.searchParams));
                const batchId=Number(url.searchParams.get('batch_id'));
                const readStatus=batchId?allocationReadStatus:batchListStatus;
                if(!batchId&&batchListGate){const held=batchListGate;batchListGate=null;held.entered?.resolve();await held.promise;}
                if(readStatus!==200)return route.fulfill({status:readStatus,contentType:'application/json',body:JSON.stringify({detail:'列表刷新失败测试'})});
                if(batchId)return fulfill({items:batchId<=allocationTotal?[{...batchFixture,id:batchId,batch_no:batchId===1?batchFixture.batch_no:`IN-TEST-${batchId}`}]:[],total:batchId<=allocationTotal?1:0,page:1,page_size:20});
                const pageNumber=Math.min(Number(url.searchParams.get('page')||1),Math.max(1,Math.ceil(allocationTotal/20)));
                const start=(pageNumber-1)*20;
                const items=Array.from({length:Math.min(20,Math.max(0,allocationTotal-start))},(_,i)=>({
                    ...batchFixture,id:start+i+1,batch_no:start+i===0?batchFixture.batch_no:`IN-TEST-${start+i+1}`}));
                return fulfill({items,total:allocationTotal,page:pageNumber,page_size:20});
            }
            if (url.pathname === '/inventory/export') {
                exportCalls.push(Object.fromEntries(url.searchParams));
                const status = exportStatus, html = exportHtml;
                if (exportGate) {const held = exportGate; exportGate = null; await held.promise;}
                if (status !== 200) return route.fulfill({status, contentType: 'application/json', body: JSON.stringify({
                    detail: status === 404 ? 'Not Found' : status === 401 ? 'Login expired' : {code: 'inventory_export_too_large', message: '导出范围过大'},
                })});
                return route.fulfill({status: 200, contentType: html ? 'text/html' : 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
                    headers: {'Content-Disposition': `attachment; filename="inventory_${url.searchParams.get('view')}_test.xlsx"`},
                    body: html ? '<html>unexpected page</html>' : Buffer.from('PK\x03\x04inventory-download-transport-fixture')});
            }
            if (url.pathname === '/inventory/overview') {
                requestCount++;
                if (url.searchParams.get('keyword') === 'slow' && gate) {const held = gate; await held.promise;}
                if (url.searchParams.get('keyword') === 'error') return route.fulfill({status: 503, contentType: 'application/json', body: JSON.stringify({detail: '读取失败测试'})});
                return fulfill(overview(url));
            }
            if (url.pathname === '/inventory/daily') {
                requestCount++; const data = daily(url, role);
                if (dailyWarning) data.warnings.push(dailyWarning);
                return fulfill(data);
            }
            if (url.pathname === '/inventory/movements') {
                if (detailGate) {const held = detailGate; detailGate = null; await held.promise;}
                return fulfill({items: [{occurred_at: `${url.searchParams.get('day')}T12:00:00`, kind: url.searchParams.get('kind'),
                    batch_no: '<批次测试>', reference: '<订单测试>', quantity: url.searchParams.get('kind') === 'outbound' ? -1 : 2}],
                    total: 1, page: 1, page_size: 20, sku: 'CW0001', name: '狗狗咀嚼玩具', day: url.searchParams.get('day')});
            }
            await route.continue();
        });
        const downloadExport = async () => {
            const pending = page.waitForEvent('download');
            await page.locator('#iw-export').click();
            const download = await pending;
            check(await download.failure(), null);
            await page.waitForFunction(() => document.getElementById('iw-export')?.disabled === false);
            return download;
        };
        const address = `http://127.0.0.1:${server.address().port}`;
        for (const suffix of ['/', '/copy']) {
            await page.goto(address + suffix);
            await page.waitForFunction(() => CURRENT_USER_ID === 10 && !!window.InventoryWorkspace);
            await checkHelpTips(page, suffix);
            await page.locator('[data-inventory-nav]').click();
            await page.locator('#iw-tbody tr').first().waitFor();
            check(await page.locator('#iw-tbody tr').count(), 4);
            // 清空每日月份后返回总览，隐藏月份不能阻止查询；每日仍保留必填校验。
            const validMonth = await page.locator('#iw-month').inputValue();
            await page.locator('[data-iw-action="daily"]').click();
            // 问号不能成为隐式label的首个控件，丢失下拉框的名称或劫持标签点击。
            const layoutField = page.getByRole('combobox', {name:'查看方式', exact:true});
            const layoutHelp = page.locator('.help-tip-trigger[aria-describedby="iw-equation"]');
            await layoutHelp.waitFor();
            check(await layoutField.count(), 1);
            check(await page.getByLabel('查看方式', {exact:true}).count(), 1);
            check(await layoutField.evaluate(field => field.labels.length), 1);
            const layoutLabel = page.locator('#iw-layout-label');
            check(await layoutLabel.evaluate(label => label.control?.id), 'iw-layout');
            check(await layoutHelp.evaluate(button => button.closest('label') === null), true);
            await layoutLabel.click();
            check(await layoutField.evaluate(field => document.activeElement === field), true);
            check(await page.locator('#iw-equation').isVisible(), false);
            await page.keyboard.press('Escape');
            await layoutHelp.click();
            check(await page.locator('#iw-equation').isVisible(), true);
            check(await layoutLabel.evaluate(label => label.control?.id), 'iw-layout');
            check(await layoutField.count(), 1);
            await page.keyboard.press('Escape');
            check(await page.locator('#iw-equation').isVisible(), false);
            await page.locator('#iw-month').fill('');
            await page.locator('[data-iw-action="overview"]').click();
            await page.locator('#iw-tbody tr').first().waitFor();
            check(await page.locator('#iw-month').isDisabled(), true);
            check(await page.locator('#iw-query').evaluate(form => form.checkValidity()), true);
            const overviewQuery = page.waitForResponse(response => new URL(response.url()).pathname === '/inventory/overview');
            await page.locator('#iw-query button[type="submit"]').click();
            await overviewQuery;
            await page.locator('#iw-tbody tr').first().waitFor();
            await page.locator('[data-iw-action="daily"]').click();
            check(await page.locator('#iw-month').isDisabled(), false);
            check(await page.locator('#iw-query').evaluate(form => form.checkValidity()), false);
            await page.locator('#iw-month').fill(validMonth);
            await page.locator('#iw-tbody tr').first().waitFor();
            await page.locator('[data-iw-action="overview"]').click();
            await page.locator('#iw-tbody tr').first().waitFor();
            check(await page.locator('#iw-tbody').innerText().then(value => value.includes('<库存安全测试>')), true);
            check(await page.locator('#iw-tbody img').count(), 0);
            check(await page.locator('#iw-tbody').innerText().then(value => value.includes('低于安全库存')), true);
            const overviewFile = await downloadExport();
            check(overviewFile.suggestedFilename(), 'inventory_overview_test.xlsx');
            check(exportCalls.at(-1).view, 'overview');
            check('page' in exportCalls.at(-1) || 'page_size' in exportCalls.at(-1), false);
            if (process.env.INVENTORY_QA_DIR && suffix === '/') {
                fs.mkdirSync(process.env.INVENTORY_QA_DIR, {recursive: true});
                await page.screenshot({path: path.join(process.env.INVENTORY_QA_DIR, 'inventory-overview-desktop.png'), fullPage: true});
            }
            // 顶部关键字联想复用商品候选，不输入即查询；点选按ID精确过滤并同步导出。
            const beforeKeyword = requestCount;
            await page.locator('#iw-keyword').fill('猫咪');
            await page.locator('#iw-keyword-options [role="option"]').nth(1).waitFor();
            check(requestCount, beforeKeyword);
            check(await page.locator('#iw-keyword-options').innerText().then(value => value.includes('CW0099')), true);
            check(await page.locator('#iw-export').isDisabled(), true);
            if (process.env.INVENTORY_QA_DIR && suffix === '/') await page.screenshot({path: path.join(process.env.INVENTORY_QA_DIR, 'inventory-keyword-suggestions-desktop.png'), fullPage: true});
            await page.locator('#iw-keyword-options [role="option"]').nth(1).click();
            await page.waitForFunction(() => document.getElementById('iw-page-label').textContent.includes('共 1 个商品'));
            check(await page.locator('#iw-keyword').inputValue(), 'CW0099');
            check(await page.locator('#iw-tbody tr').count(), 1);
            await downloadExport();
            check(exportCalls.at(-1).product_id, '99');
            check(exportCalls.at(-1).keyword, 'CW0099');
            // 切换日结时保留候选选择，不切换现有查看方式。
            await page.locator('[data-iw-action="daily"]').click();
            await page.locator('#iw-tbody [data-iw-move]').first().waitFor();
            check(await page.locator('#iw-layout').inputValue(), 'day');
            check(await page.locator('#iw-tbody tr').count(), 1);
            await downloadExport();
            check(exportCalls.at(-1).product_id, '99');
            await page.locator('[data-iw-action="overview"]').click();
            await page.locator('#iw-tbody [data-iw-trace]').first().waitFor();
            // 即使SKU有前缀相近商品，键盘选中后也只显示和导出目标ID。
            await page.locator('#iw-keyword').fill('CW0001');
            await page.locator('#iw-keyword-options [role="option"]').nth(1).waitFor();
            await page.locator('#iw-keyword').press('ArrowDown');
            check(await page.locator('#iw-keyword').getAttribute('aria-activedescendant'), 'iw-keyword-option-0');
            await page.locator('#iw-keyword').press('Enter');
            await page.waitForFunction(() => document.getElementById('iw-tbody').textContent.includes('狗狗咀嚼玩具'));
            check(await page.locator('#iw-tbody tr').count(), 1);
            await downloadExport();
            check(exportCalls.at(-1).product_id, '1');
            // 不选候选直接回车保留模糊搜索；Esc保留草稿，不误选第一项。
            await page.locator('#iw-keyword').fill('猫咪');
            await page.locator('#iw-keyword-options [role="option"]').nth(1).waitFor();
            await page.locator('#iw-keyword').press('Enter');
            await page.waitForFunction(() => document.getElementById('iw-page-label').textContent.includes('共 2 个商品'));
            check(await page.locator('#iw-tbody tr').count(), 2);
            await downloadExport();
            check('product_id' in exportCalls.at(-1), false);
            await page.locator('#iw-keyword').fill('missing-product');
            await page.waitForFunction(() => document.getElementById('iw-keyword-search-status').textContent.includes('没有匹配'));
            await page.locator('#iw-keyword').press('Escape');
            check(await page.locator('#iw-keyword').inputValue(), 'missing-product');
            check(await page.locator('#iw-keyword-popup').isHidden(), true);
            await page.locator('#iw-keyword').press('Enter');
            await page.waitForFunction(() => document.getElementById('iw-tbody').textContent.includes('没有匹配'));
            check(await page.locator('#iw-export').isDisabled(), true);
            productStatus = 503;
            await page.locator('#iw-keyword').fill('failure');
            await page.waitForFunction(() => document.getElementById('iw-keyword-search-status').textContent.includes('商品查询失败测试'));
            await page.locator('#iw-keyword').press('Escape');
            check(await page.locator('#iw-keyword').inputValue(), 'failure');
            productStatus = 200;
            const beforeKeywordComposition = productCalls.length;
            await page.locator('#iw-keyword').evaluate(input => {
                input.value = '羽毛'; input.dispatchEvent(new InputEvent('input', {bubbles: true, isComposing: true}));
            });
            await page.waitForTimeout(300);
            check(productCalls.length, beforeKeywordComposition);
            await page.locator('#iw-keyword').dispatchEvent('compositionend');
            await page.locator('#iw-keyword-options [role="option"]').first().waitFor();
            check(productCalls.at(-1).keyword, '羽毛');
            // 候选中的名称转义，空输入不会请求全量商品建议。
            await page.locator('#iw-keyword').fill('库存安全');
            await page.locator('#iw-keyword-options [role="option"]').first().waitFor();
            check(await page.locator('#iw-keyword-options').innerText().then(value => value.includes('<库存安全测试>')), true);
            check(await page.locator('#iw-keyword-options 库存安全测试').count(), 0);
            await page.locator('#iw-keyword').fill('');
            check(await page.locator('#iw-keyword-popup').isHidden(), true);
            await page.locator('#iw-query button[type="submit"]').click();
            await page.waitForFunction(() => document.getElementById('iw-page-label').textContent.includes('共 4 个商品'));
            await page.locator('[data-iw-action="daily"]').click();
            await page.locator('#iw-tbody [data-iw-move]').first().waitFor();
            await page.locator('#iw-month').fill('2026-09');
            await page.locator('#iw-month').dispatchEvent('change');
            await page.waitForFunction(() => document.getElementById('iw-day').value.startsWith('2026-09'));
            check(await page.locator('#iw-scope').innerText().then(value => value.includes('撤销')), true);
            check(await page.locator('#iw-help').isVisible(), false);
            dailyWarning = 'inventory_history_inconsistent';
            await page.locator('[data-iw-action="refresh"]').click();
            await page.waitForFunction(() => document.getElementById('iw-help').hasAttribute('data-help-inline'));
            await page.locator('#iw-help').waitFor({state:'visible'});
            check(await page.locator('#iw-help').isVisible(), true);
            check(await page.locator('#iw-scope').innerText().then(value => value.includes('不一致')), true);
            dailyWarning = null;
            await page.locator('[data-iw-action="refresh"]').click();
            await page.waitForFunction(() => !document.getElementById('iw-help').hasAttribute('data-help-inline'));
            await page.waitForFunction(() => document.getElementById('iw-help').classList.contains('help-tip-source'));
            check(await page.locator('#iw-help').isVisible(), false);
            const dailyFile = await downloadExport();
            check(dailyFile.suggestedFilename(), 'inventory_daily_test.xlsx');
            check(exportCalls.at(-1).month, '2026-09-01');
            await page.locator('#iw-layout').selectOption('product');
            check(await page.locator('#iw-tbody tr').count(), 30);
            await downloadExport();
            check(exportCalls.at(-1).product_id, '1');
            // 超过20个近似SKU时，较早的精确SKU须先置顶再分页；库存选择与导出一致。
            await page.locator('#iw-product').fill('SEARCH-EXACT');
            await page.waitForFunction(()=>document.querySelectorAll('#iw-product-options [role="option"]').length===20);
            check(await page.locator('#iw-product-options [role="option"]').first().locator('strong').innerText(),'SEARCH-EXACT');
            check(productCalls.at(-1).prioritize_exact_sku,'true');
            await page.locator('#iw-product').press('ArrowDown');await page.locator('#iw-product').press('Enter');
            await page.waitForFunction(()=>document.getElementById('iw-stat-scope').textContent.includes('SEARCH-EXACT'));
            await downloadExport();check(exportCalls.at(-1).product_id,'200');
            // 输入检索全部商品，可选择不在当前库存页的商品，导出必须跟随选择。
            await page.locator('#iw-product').fill('猫咪');
            check(await page.locator('#iw-export').isDisabled(), true);
            await page.locator('#iw-product-options [role="option"]').nth(1).waitFor();
            check(productCalls.at(-1), {keyword: '猫咪', page: '1', page_size: '20',prioritize_exact_sku:'true'});
            check(await page.locator('#iw-product-options').innerText().then(value => value.includes('CW0099')), true);
            if (process.env.INVENTORY_QA_DIR && suffix === '/') await page.screenshot({path: path.join(process.env.INVENTORY_QA_DIR, 'inventory-product-search-desktop.png'), fullPage: true});
            await page.locator('#iw-product-options [role="option"]').nth(1).click();
            await page.waitForFunction(() => document.getElementById('iw-product').value.startsWith('CW0099'));
            check(await page.locator('#iw-tbody tr').count(), 30);
            await downloadExport();
            check(exportCalls.at(-1).product_id, '99');
            // 连续输入只提交最后一次查询，大小写SKU与键盘选择可用。
            const productsBefore = productCalls.length;
            await page.locator('#iw-product').fill('C');
            await page.locator('#iw-product').fill('CW');
            await page.locator('#iw-product').fill('cw0002');
            await page.locator('#iw-product-options [role="option"]').first().waitFor();
            check(productCalls.length - productsBefore, 1);
            await page.locator('#iw-product').press('ArrowDown');
            check(await page.locator('#iw-product').getAttribute('aria-activedescendant'), 'iw-product-option-0');
            await page.locator('#iw-product').press('Enter');
            await page.waitForFunction(() => document.getElementById('iw-product').value.startsWith('CW0002'));
            await downloadExport();
            check(exportCalls.at(-1).product_id, '2');
            // 输入未匹配文字不会切换或导出错误商品；取消恢复已选商品。
            await page.locator('#iw-product').fill('missing-product');
            await page.waitForFunction(() => document.getElementById('iw-product-search-status').textContent.includes('没有匹配'));
            const beforeMissing = requestCount;
            await page.locator('#iw-product').press('Enter');
            check(requestCount, beforeMissing);
            check(await page.locator('#iw-export').isDisabled(), true);
            await page.locator('#iw-product').press('Escape');
            check(await page.locator('#iw-product').inputValue(), 'CW0002 · 猫咪自嗨转盘');
            check(await page.locator('#iw-product').getAttribute('aria-expanded'), 'false');
            await page.locator('#iw-product').press('ArrowUp');
            check(await page.locator('#iw-product-options [aria-selected="true"]').count(), 1);
            await page.locator('#iw-product').press('Escape');
            // 候选名称按文本展示，查询失败后保留当前商品；中文输入法完成后再检索。
            await page.locator('#iw-product').fill('库存安全');
            await page.locator('#iw-product-options [role="option"]').first().waitFor();
            check(await page.locator('#iw-product-options').innerText().then(value => value.includes('<库存安全测试>')), true);
            check(await page.locator('#iw-product-options 库存安全测试').count(), 0);
            await page.locator('#iw-product').press('Escape');
            productStatus = 503;
            await page.locator('#iw-product').fill('failure');
            await page.waitForFunction(() => document.getElementById('iw-product-search-status').textContent.includes('商品查询失败测试'));
            await page.locator('#iw-product').press('Escape');
            check(await page.locator('#iw-export').isEnabled(), true);
            productStatus = 200;
            const beforeComposition = productCalls.length;
            await page.locator('#iw-product').evaluate(input => {
                input.value = '羽毛'; input.dispatchEvent(new InputEvent('input', {bubbles: true, isComposing: true}));
            });
            await page.waitForTimeout(300);
            check(productCalls.length, beforeComposition);
            await page.locator('#iw-product').dispatchEvent('compositionend');
            await page.locator('#iw-product-options [role="option"]').first().waitFor();
            check(productCalls.at(-1).keyword, '羽毛');
            await page.locator('#iw-product').press('Escape');
            // 清除指定商品查询后，横向表继续覆盖全查询范围。
            await page.locator('#iw-query button[type="submit"]').click();
            await page.waitForFunction(() => document.getElementById('iw-product').value.startsWith('CW0001'));
            await page.locator('#iw-layout').selectOption('matrix');
            check(await page.locator('#iw-thead th').count(), 31);
            await downloadExport();
            check(exportCalls.at(-1).view, 'matrix');
            check('product_id' in exportCalls.at(-1), false);
            check(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth), true);
            if (process.env.INVENTORY_QA_DIR && suffix === '/') await page.screenshot({path: path.join(process.env.INVENTORY_QA_DIR, 'inventory-daily-matrix.png'), fullPage: true});
            await page.locator('#iw-layout').selectOption('day');
            await page.locator('[data-iw-move="outbound"]').first().click();
            await page.waitForFunction(() => document.getElementById('iw-detail-body').textContent.includes('<订单测试>'));
            check(await page.locator('#iw-detail-body').innerText().then(value => value.includes('-1')), true);
            check(await page.locator('#iw-detail-body script').count(), 0);
            await page.keyboard.press('Escape');
            check(await page.locator('#iw-detail').evaluate(node => node.open), false);
            // 连续退出/重进后，根节点点击处理器不能累积。
            for (let i = 0; i < 2; i++) await page.evaluate(async () => {
                handleSessionExpired(); CURRENT_USER_ID = 10; CURRENT_ROLE = 'admin';
                document.getElementById('login-overlay').style.display = 'none';
                await window.InventoryWorkspace.open();
            });
            const before = requestCount;
            await page.locator('[data-iw-action="refresh"]').click();
            await page.waitForFunction(() => document.getElementById('iw-status').textContent === '');
            check(requestCount - before, 1);
        }
        // 导出期间固定查询条件、拒绝重复请求；结束后保留原分页禁用状态。
        exportGate = wait();
        const heldExport = exportGate;
        const exportBefore = exportCalls.length;
        const exportStarted = page.waitForRequest(request => request.url().includes('/inventory/export'));
        const exportingDownload = page.waitForEvent('download');
        await page.locator('#iw-export').click();
        await exportStarted;
        check(await page.locator('#iw-export').isDisabled(), true);
        check(await page.locator('#iw-keyword').isDisabled(), true);
        await page.locator('#iw-export').dispatchEvent('click');
        check(exportCalls.length - exportBefore, 1);
        heldExport.resolve();
        await exportingDownload;
        await page.waitForFunction(() => document.getElementById('iw-export')?.disabled === false);
        check(await page.locator('[data-iw-action="previous"]').isDisabled(), true);
        exportStatus = 422;
        await page.locator('#iw-export').click();
        await page.waitForFunction(() => document.getElementById('iw-export-status').textContent.includes('2000'));
        check(await page.locator('#iw-export').isEnabled(), true);
        exportStatus = 404;
        await page.locator('#iw-export').click();
        await page.waitForFunction(() => document.getElementById('iw-export-status').textContent.includes('重启'));
        check(await page.locator('#iw-export').isEnabled(), true);
        exportStatus = 200; exportHtml = true;
        await page.locator('#iw-export').click();
        await page.waitForFunction(() => document.getElementById('iw-export-status').textContent.includes('文件无效'));
        check(await page.locator('#iw-export').isEnabled(), true);
        exportHtml = false;
        // 导出中的旧会话401不能清掉同账号刚恢复的新会话。
        exportGate = wait(); exportStatus = 401;
        const oldExport = exportGate;
        const oldExportStarted = page.waitForRequest(request => request.url().includes('/inventory/export'));
        await page.locator('#iw-export').click();
        await oldExportStarted;
        await page.evaluate(async () => {handleSessionExpired(); await loadCurrentUser();});
        await page.locator('[data-inventory-nav]').click();
        await page.locator('#iw-tbody tr').first().waitFor();
        exportStatus = 200;
        oldExport.resolve();
        await page.waitForTimeout(100);
        check(await page.evaluate(() => CURRENT_USER_ID), 10);
        check(await page.locator('#login-overlay').isVisible(), false);
        // 顶部联想也须丢弃慢响应，旧会话401不能清除刚恢复的会话。
        productGate = wait();
        const keywordSlow = productGate;
        const keywordSlowStarted = page.waitForRequest(request => new URL(request.url()).searchParams.get('keyword') === 'slow-keyword');
        await page.locator('#iw-keyword').fill('slow-keyword');
        await keywordSlowStarted;
        await page.locator('#iw-keyword').fill('CW0003');
        await page.locator('#iw-keyword-options [role="option"]').first().waitFor();
        keywordSlow.resolve();
        await page.waitForTimeout(100);
        check(await page.locator('#iw-keyword-options').innerText().then(value => value.includes('CW0003')), true);
        productGate = wait(); productStatus = 401;
        const keywordOld401 = productGate;
        const keyword401Started = page.waitForRequest(request => new URL(request.url()).searchParams.get('keyword') === 'old-keyword-session');
        await page.locator('#iw-keyword').fill('old-keyword-session');
        await keyword401Started;
        await page.evaluate(async () => {handleSessionExpired(); await loadCurrentUser();});
        productStatus = 200;
        await page.locator('[data-inventory-nav]').click();
        await page.locator('#iw-tbody tr').first().waitFor();
        keywordOld401.resolve();
        await page.waitForTimeout(100);
        check(await page.evaluate(() => CURRENT_USER_ID), 10);
        check(await page.locator('#login-overlay').isVisible(), false);
        // 商品搜索慢响应不能覆盖新词；退出重登后的旧401不能清除新会话。
        await page.locator('[data-iw-action="daily"]').click();
        await page.locator('#iw-tbody tr').first().waitFor();
        await page.locator('#iw-layout').selectOption('product');
        productGate = wait();
        const oldProducts = productGate;
        const oldProductsStarted = page.waitForRequest(request => new URL(request.url()).searchParams.get('keyword') === 'slow-products');
        await page.locator('#iw-product').fill('slow-products');
        await oldProductsStarted;
        await page.locator('#iw-product').fill('CW0003');
        await page.locator('#iw-product-options [role="option"]').first().waitFor();
        oldProducts.resolve();
        await page.waitForTimeout(100);
        check(await page.locator('#iw-product-options [role="option"]').count(), 1);
        check(await page.locator('#iw-product-options').innerText().then(value => value.includes('CW0003')), true);
        productGate = wait(); productStatus = 401;
        const oldProducts401 = productGate;
        const oldProducts401Started = page.waitForRequest(request => new URL(request.url()).searchParams.get('keyword') === 'old-session');
        await page.locator('#iw-product').fill('old-session');
        await oldProducts401Started;
        await page.evaluate(async () => {handleSessionExpired(); await loadCurrentUser();});
        productStatus = 200;
        await page.locator('[data-inventory-nav]').click();
        await page.locator('#iw-tbody tr').first().waitFor();
        oldProducts401.resolve();
        await page.waitForTimeout(100);
        check(await page.evaluate(() => CURRENT_USER_ID), 10);
        check(await page.locator('#login-overlay').isVisible(), false);
        // 关闭/重开来源弹窗后，前一条慢响应不能覆盖当前来源。
        await page.locator('[data-iw-action="daily"]').click();
        await page.locator('#iw-tbody [data-iw-move]').first().waitFor();
        detailGate = wait();
        const blockedDetail = detailGate;
        const detailRequest = page.waitForRequest(request => request.url().includes('/inventory/movements'));
        await page.locator('[data-iw-move="outbound"]').first().click();
        await detailRequest;
        await page.keyboard.press('Escape');
        await page.locator('[data-iw-move="inbound"]').first().click();
        await page.waitForFunction(() => document.getElementById('iw-detail-body').textContent.includes('+2'));
        blockedDetail.resolve();
        await page.waitForTimeout(100);
        check(await page.locator('#iw-detail').evaluate(node => node.open), true);
        check(await page.locator('#iw-detail-body tr').first().locator('td').last().innerText(), '+2');
        await page.keyboard.press('Escape');
        await page.locator('[data-iw-action="overview"]').click();
        await page.locator('#iw-tbody [data-iw-trace]').first().waitFor();
        // 旧请求在会话清理后完成，不得重新渲染。
        gate = wait();
        await page.locator('#iw-keyword').fill('slow');
        const slowRequest = page.waitForRequest(request => request.url().includes('keyword=slow'));
        await page.locator('#iw-query button[type="submit"]').click();
        await slowRequest;
        await page.evaluate(() => {handleSessionExpired();});
        gate.resolve();
        await page.waitForTimeout(100);
        check(await page.locator('#inventory-workspace-root').innerHTML(), '');
        // 同账号重新登录，旧会话不影响新工作台。
        await page.evaluate(() => loadCurrentUser());
        await page.locator('[data-inventory-nav]').click();
        await page.locator('#iw-tbody tr').first().waitFor();
        await page.locator('#iw-keyword').fill('error');
        await page.locator('#iw-query button[type="submit"]').click();
        await page.waitForFunction(() => document.getElementById('iw-status').textContent.includes('读取失败测试'));
        check(await page.locator('#iw-tbody tr').count(), 0);
        await page.locator('#iw-keyword').fill('');
        await page.locator('#iw-query button[type="submit"]').click();
        await page.locator('#iw-tbody tr').first().waitFor();
        await page.locator('[data-iw-trace="1"]').click();
        await page.waitForFunction(() => document.getElementById('iw-layout').value === 'product' && document.getElementById('iw-tbody').textContent.includes('2026-'));
        // 窄屏只有表格内部滚动，页面不横向溢出。
        await page.setViewportSize({width: 390, height: 844});
        check(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth), true);
        await page.locator('#iw-keyword').fill('猫咪');
        await page.locator('#iw-keyword-options [role="option"]').nth(1).waitFor();
        check(await page.evaluate(() => {
            const bounds = document.getElementById('iw-keyword-popup').getBoundingClientRect();
            return bounds.left >= 0 && bounds.right <= window.innerWidth;
        }), true);
        if (process.env.INVENTORY_QA_DIR) await page.screenshot({path: path.join(process.env.INVENTORY_QA_DIR, 'inventory-keyword-suggestions-mobile.png'), fullPage: true});
        await page.locator('#iw-keyword').fill('');
        await page.locator('#iw-keyword').press('Escape');
        await page.locator('#iw-product').fill('猫咪');
        await page.locator('#iw-product-options [role="option"]').nth(1).waitFor();
        check(await page.evaluate(() => {
            const bounds = document.getElementById('iw-product-popup').getBoundingClientRect();
            return bounds.left >= 0 && bounds.right <= window.innerWidth;
        }), true);
        if (process.env.INVENTORY_QA_DIR) await page.screenshot({path: path.join(process.env.INVENTORY_QA_DIR, 'inventory-product-search-mobile.png'), fullPage: true});
        await page.locator('#iw-product').press('Escape');
        if (process.env.INVENTORY_QA_DIR) await page.screenshot({path: path.join(process.env.INVENTORY_QA_DIR, 'inventory-mobile.png'), fullPage: true});
        // 归属移入库存：入库/批次资料编辑不再选择；独立维护只提交归属和版本。
        await page.setViewportSize({width:1440,height:1000});role='admin';
        for (const suffix of ['/', '/copy']) {
            Object.assign(batchFixture,{impairment_store_ids:[],impairment_store_names:[],edit_version:'a'.repeat(64),
                impairment_store_history:[{effective_date:'2026-09-01',store_ids:[],store_names:[],is_explicit:true}]});
            await page.goto(address+suffix);await page.waitForFunction(()=>CURRENT_USER_ID===10);
            check(await page.locator('#p-product-type option').allTextContents(),['稳健商品','新品','不跌价商品']);
            await page.getByRole('button',{name:'入库管理',exact:true}).click();
            check(await page.locator('#b-impairment-stores').count(),0);
            const beforeCreate=assignmentCalls.length;
            await page.locator('#b-pid').selectOption('1');await page.locator('#b-qty').fill('100');await page.locator('#b-total-amount').fill('1000');
            await page.getByRole('button',{name:'入库',exact:true}).click();
            await page.waitForFunction(count=>document.getElementById('batches-tbody').textContent.includes('IN-TEST-1'),beforeCreate);
            check(assignmentCalls.length,beforeCreate+1);check(assignmentCalls.at(-1).quantity,100);
            check('impairment_store_ids' in assignmentCalls.at(-1),false);
            await page.locator('#batches-tbody button').filter({hasText:'编辑'}).click();
            check(await page.locator('#eb-impairment-stores').count(),0);
            await page.locator('#eb-dingtalk-order').fill('OWNERSHIP-MOVED');await page.locator('#eb-save').click();
            await page.waitForFunction(()=>document.getElementById('edit-batch-modal').style.display==='none');
            check('impairment_store_ids' in assignmentCalls.at(-1),false);
            await page.getByRole('button',{name:'库存',exact:true}).click();
            allocationStoresStatus=503;await page.locator('[data-iw-allocation="1"]').click();
            await page.locator('#ial-status').getByText('操作失败：',{exact:false}).waitFor();
            check(await page.locator('[data-ial-edit]').count(),0);
            allocationStoresStatus=200;await page.locator('[data-ial-action="refresh"]').click();
            await page.locator('#ial-rows [data-ial-edit="1"]').waitFor();
            check(await page.locator('#ial-rows').innerText().then(text=>text.includes('待分配')),true);
            await page.locator('#ial-rows [data-ial-edit="1"]').click();
            check(await page.locator('#ial-editor input[value="from_arrival"]').isChecked(),true);
            const beforeAllocation=allocationCalls.length;
            await page.getByRole('button',{name:'保存归属',exact:true}).click();
            check(await page.locator('#ial-edit-status').innerText(),'请至少选择一家归属店铺。');check(allocationCalls.length,beforeAllocation);
            await page.locator('#ial-editor input[value="7"]').check();await page.locator('#ial-editor input[value="8"]').check();
            const held=wait();allocationGate=held;
            await page.getByRole('button',{name:'保存归属',exact:true}).click();
            await page.locator('#ial-edit-status').getByText('正在读取…',{exact:true}).waitFor();
            check(await page.locator('#ial-editor input[value="7"]').isDisabled(),true);
            check(await page.getByRole('button',{name:'保存归属',exact:true}).isDisabled(),true);
            held.resolve();await page.locator('#ial-editor').waitFor({state:'hidden'});
            await page.locator('#ial-status').getByText('归属已保存，库存数量及成本未修改。',{exact:true}).waitFor();
            check(allocationCalls.length,beforeAllocation+1);check(allocationCalls.at(-1).impairment_store_ids,[7,8]);
            check(Object.keys(allocationCalls.at(-1)).sort(),['edit_version','impairment_change_mode','impairment_store_ids']);
            check(allocationCalls.at(-1).impairment_change_mode,'from_arrival');
            check(await page.locator('#ial-rows').innerText().then(text=>text.includes('<店铺B>')),true);
            check(await page.locator('#ial-rows 店铺B').count(),0);
            // 保存期间跨页增长和缩减：刷新结果必须决定分页，不能恢复旧disabled值。
            allocationTotal=20;await page.locator('[data-ial-action="refresh"]').click();
            await page.waitForFunction(()=>document.getElementById('ial-page').textContent.includes('共20批'));
            check(await page.locator('#ial-next').isDisabled(),true);
            await page.locator('[data-ial-edit="1"]').click();allocationTotalAfterSave=21;
            await page.getByRole('button',{name:'保存归属',exact:true}).click();
            await page.waitForFunction(()=>document.getElementById('ial-page').textContent.includes('共21批'));
            await page.locator('#ial-status').getByText('归属已保存，库存数量及成本未修改。',{exact:true}).waitFor();
            check(await page.locator('#ial-next').isEnabled(),true);
            await page.locator('#ial-next').click();await page.locator('[data-ial-edit="21"]').waitFor();
            check(await page.locator('#ial-previous').isEnabled(),true);
            await page.locator('[data-ial-edit="21"]').click();allocationTotalAfterSave=20;
            await page.getByRole('button',{name:'保存归属',exact:true}).click();
            await page.waitForFunction(()=>document.getElementById('ial-page').textContent.includes('共20批')&&document.getElementById('ial-page').textContent.includes('第1页'));
            await page.locator('#ial-status').getByText('归属已保存，库存数量及成本未修改。',{exact:true}).waitFor();
            check(await page.locator('#ial-previous').isDisabled(),true);
            check(await page.locator('#ial-next').isDisabled(),true);
            check(await page.locator('#ial-rows tr').count(),20);
            allocationTotal=1;await page.locator('[data-ial-action="refresh"]').click();
            await page.waitForFunction(()=>document.getElementById('ial-page').textContent.includes('共1批'));
            if(process.env.INVENTORY_QA_DIR&&suffix==='/')await page.screenshot({path:path.join(process.env.INVENTORY_QA_DIR,'inventory-allocations-desktop.png'),fullPage:true});
            await page.locator('#ial-rows [data-ial-edit="1"]').click();
            check(await page.locator('#ial-editor input[value="from_change_date"]').isChecked(),true);
            await page.locator('#ial-editor input[value="7"]').uncheck();allocationStatus=409;
            await page.getByRole('button',{name:'保存归属',exact:true}).click();
            await page.locator('#ial-edit-status').getByText('操作失败：',{exact:false}).waitFor();
            check(await page.locator('#ial-editor input[value="8"]').isChecked(),true);
            check(await page.getByRole('button',{name:'保存归属',exact:true}).isEnabled(),true);
            allocationStatus=200;await page.getByRole('button',{name:'保存归属',exact:true}).click();await page.locator('#ial-editor').waitFor({state:'hidden'});
            check(allocationCalls.at(-1).impairment_store_ids,[8]);check(allocationCalls.at(-1).impairment_change_mode,'from_change_date');
            await page.locator('[data-ial-action="close"]').click();
            await page.locator('[data-iw-action="assessment"]').click();await page.locator('[data-ia-allocation="1"]').click();
            await page.locator('#ial-rows [data-ial-edit="1"]').waitFor();check(await page.locator('#ial-rows').innerText().then(text=>text.includes('<店铺B>')),true);
            await page.locator('#ial-rows [data-ial-edit="1"]').click();
            await page.locator('#ial-editor input[value="7"]').check();
            const stale=wait();allocationGate=stale;allocationStatus=401;
            await page.getByRole('button',{name:'保存归属',exact:true}).click();
            await page.locator('#ial-edit-status').getByText('正在读取…',{exact:true}).waitFor();
            await page.evaluate(async()=>{handleSessionExpired();await loadCurrentUser();});
            await page.getByRole('button',{name:'库存',exact:true}).click();await page.locator('[data-iw-allocation="1"]').click();
            await page.locator('#ial-rows [data-ial-edit="1"]').waitFor();stale.resolve();allocationStatus=200;
            check(await page.locator('#ial-dialog').isVisible(),true);
            check(await page.locator('#login-box').isVisible(),false);
            await page.locator('#ial-rows [data-ial-edit="1"]').click();
            check(await page.getByRole('button',{name:'保存归属',exact:true}).isEnabled(),true);
            await page.setViewportSize({width:390,height:844});
            check(await page.locator('#ial-dialog').evaluate(node=>{const r=node.getBoundingClientRect();return r.left>=0&&r.right<=innerWidth;}),true);
            check(await page.locator('#ial-editor').evaluate(node=>node.scrollWidth<=node.clientWidth+1),true);
            check(await page.locator('.allocation-editor-grid').evaluate(node=>getComputedStyle(node).gridTemplateColumns.split(' ').length),1);
            await page.getByRole('button',{name:'保存归属',exact:true}).scrollIntoViewIfNeeded();
            check(await page.locator('.allocation-actions').evaluate(node=>{const r=node.getBoundingClientRect();return r.left>=0&&r.right<=innerWidth;}),true);
            if(process.env.INVENTORY_QA_DIR&&suffix==='/')await page.screenshot({path:path.join(process.env.INVENTORY_QA_DIR,'inventory-allocations-mobile.png'),fullPage:true});
            await page.setViewportSize({width:1440,height:1000});
            await page.locator('[data-ial-action="close"]').click();
        }
        // 入库列表第二页的旧售完批次：精确重新读取版本，直接编辑，不回到商品批次第一页。
        for(const suffix of ['/','/copy']){
            allocationTotal=21;
            Object.assign(batchFixture,{remaining_quantity:0,impairment_store_ids:[],impairment_store_names:[],impairment_store_history:[],edit_version:'a'.repeat(64)});
            await page.goto(address+suffix);await page.waitForFunction(()=>CURRENT_USER_ID===10);
            await page.getByRole('button',{name:'入库管理',exact:true}).click();
            await page.waitForFunction(()=>document.querySelector('#batches-tbody tr')?.textContent.includes('IN-TEST-1'));
            await page.evaluate(()=>goBatchPage(2));
            await page.waitForFunction(()=>document.querySelector('#batches-tbody tr')?.textContent.includes('IN-TEST-21'));
            check(await page.locator('#batches-tbody').innerText().then(text=>text.includes('未手工分配（兼容历史归属）')),true);
            await page.getByRole('button',{name:'修改归属',exact:true}).click();
            await page.locator('#ial-editor').waitFor({state:'visible'});
            check(allocationQueries.at(-1).batch_id,'21');
            check(await page.locator('#ial-editor h4').evaluate(node=>node.firstChild.textContent),'IN-TEST-21');
            check(await page.locator('#ial-rows tr').count(),1);
            check(await page.locator('#ial-editor input[value="from_arrival"]').isChecked(),true);
            check(await page.locator('.allocation-editor-grid').evaluate(node=>getComputedStyle(node).gridTemplateColumns.split(' ').length),2);
            check(await page.locator('.allocation-mode-option').count(),2);
            check(await page.locator('#ial-rows .is-editing').count(),1);
            await page.locator('.allocation-store-option').filter({hasText:'店铺A'}).click();
            check(await page.locator('#ial-editor input[value="7"]').isChecked(),true);
            await page.locator('#ial-editor input[value="7"]').focus();await page.keyboard.press('Space');
            check(await page.locator('#ial-editor input[value="7"]').isChecked(),false);
            if(process.env.INVENTORY_QA_DIR&&suffix==='/')await page.locator('#ial-dialog').screenshot({path:path.join(process.env.INVENTORY_QA_DIR,'batch-ownership-direct-desktop.png')});
            await page.locator('#ial-editor input[value="7"]').check();
            await page.getByRole('button',{name:'保存归属',exact:true}).click();
            await page.locator('#ial-status').getByText('归属已保存，库存数量及成本未修改。',{exact:true}).waitFor();
            check(Object.keys(allocationCalls.at(-1)).sort(),['edit_version','impairment_change_mode','impairment_store_ids']);
            check(await page.locator('#ial-rows').innerText().then(text=>text.includes('IN-TEST-21')),true);
            check(batchFixture.remaining_quantity,0);
            await page.locator('[data-ial-action="close"]').click();
            await page.waitForFunction(()=>document.querySelector('#batches-tbody tr')?.textContent.includes('店铺A'));
            await page.getByRole('button',{name:'修改归属',exact:true}).click();
            await page.locator('#ial-editor').waitFor({state:'visible'});
            check(await page.locator('#ial-editor input[value="from_change_date"]').isChecked(),true);
            // PUT已成功、原入库列表GET失败：说明已保存，重试只读刷新，不重复PUT。
            const beforeRefreshFailure=allocationCalls.length;
            batchListStatus=503;await page.locator('#ial-editor input[value="8"]').check();
            await page.getByRole('button',{name:'保存归属',exact:true}).click();
            const refreshWarning='归属已保存，但列表刷新失败，请重试刷新。无需再次保存归属。';
            await page.locator('#ial-status').getByText(refreshWarning,{exact:true}).waitFor();
            check(allocationCalls.length,beforeRefreshFailure+1);
            check(await page.locator('#ial-retry-refresh').isVisible(),true);
            check(await page.locator('#batches-tbody').innerText().then(text=>text.includes('<店铺B>')),false);
            const retryGate=wait();batchListGate=retryGate;batchListStatus=200;
            const readsBeforeRetry=allocationQueries.length;
            await page.locator('#ial-retry-refresh').click();
            await page.waitForFunction(()=>document.querySelector('#ial-rows [data-ial-edit]')?.disabled===true);
            check(await page.locator('[data-ial-action="refresh"]').isDisabled(),true);
            await page.evaluate(()=>{document.getElementById('ial-retry-refresh').click();document.querySelector('[data-ial-action="refresh"]').click();});
            retryGate.resolve();
            await page.locator('#ial-status').getByText('归属已保存，库存数量及成本未修改。',{exact:true}).waitFor();
            check(allocationCalls.length,beforeRefreshFailure+1);
            check(allocationQueries.length,readsBeforeRetry+2);
            check(await page.locator('#ial-retry-refresh').isVisible(),false);
            check(await page.locator('#batches-tbody').innerText().then(text=>text.includes('<店铺B>')),true);
            // 弹窗自身刷新失败也不能被成功文案覆盖；普通刷新按钮可恢复原列表。
            await page.locator('#ial-rows [data-ial-edit="21"]').click();
            allocationReadStatus=503;await page.locator('#ial-editor input[value="7"]').uncheck();
            await page.getByRole('button',{name:'保存归属',exact:true}).click();
            await page.locator('#ial-status').getByText(refreshWarning,{exact:true}).waitFor();
            check(await page.locator('#ial-rows [data-ial-edit]').count(),0);
            const savesBeforeRetry=allocationCalls.length;allocationReadStatus=200;
            await page.locator('[data-ial-action="refresh"]').click();
            await page.locator('#ial-status').getByText('归属已保存，库存数量及成本未修改。',{exact:true}).waitFor();
            check(allocationCalls.length,savesBeforeRetry);
            check(await page.locator('#batches-tbody').innerText().then(text=>text.includes('店铺A')),false);
            // 关闭重开及同账号重登后，旧刷新失败/401不得污染新弹窗或清理新登录态。
            for(const staleAction of ['close','reauth']){
                await page.locator('#ial-rows [data-ial-edit="21"]').click();
                await page.locator('#ial-editor input[value="7"]').check();
                const staleRefresh=wait(),staleStatus=staleAction==='close'?503:401;
                staleRefresh.entered=wait();batchListGate=staleRefresh;batchListStatus=staleStatus;
                await page.getByRole('button',{name:'保存归属',exact:true}).click();
                await staleRefresh.entered.promise;
                await page.waitForFunction(()=>document.querySelector('#ial-rows [data-ial-edit]')?.disabled===true);
                if(staleAction==='close')await page.locator('[data-ial-action="close"]').click();
                else await page.evaluate(async()=>{handleSessionExpired();await loadCurrentUser();});
                batchListStatus=200;
                if(staleAction==='reauth'){
                    await page.getByRole('button',{name:'入库管理',exact:true}).click();
                    await page.waitForFunction(()=>document.querySelector('#batches-tbody tr')?.textContent.includes('IN-TEST-21'));
                }
                await page.getByRole('button',{name:'修改归属',exact:true}).click();
                await page.locator('#ial-editor').waitFor({state:'visible'});
                const staleResponse=page.waitForResponse(response=>{const url=new URL(response.url());return url.pathname==='/batches'&&!url.searchParams.has('batch_id')&&response.status()===staleStatus;});
                staleRefresh.resolve();await(await staleResponse).finished();
                await page.evaluate(()=>new Promise(requestAnimationFrame));
                check(await page.locator('#ial-editor').isVisible(),true);
                check(await page.locator('#ial-retry-refresh').isVisible(),false);
                check(await page.locator('#ial-status').innerText(),'');
                check(await page.evaluate(()=>CURRENT_USER_ID),10);
                check(await page.locator('#login-box').isVisible(),false);
            }
            allocationTotal=20;await page.locator('[data-ial-action="refresh"]').click();
            await page.locator('#ial-status').getByText('该批次已不存在或已作废，请刷新批次列表。',{exact:true}).waitFor();
            check(await page.locator('#ial-editor').isVisible(),false);
            check(await page.locator('[data-ial-edit]').count(),0);
            await page.locator('[data-ial-action="close"]').click();
        }
        allocationTotal=1;batchFixture.remaining_quantity=70;
        role='viewer';await page.reload();await page.waitForFunction(()=>CURRENT_ROLE==='viewer');
        await page.getByRole('button',{name:'库存',exact:true}).click();await page.locator('[data-iw-allocation="1"]').click();
        await page.locator('#ial-rows tr').waitFor();check(await page.locator('[data-ial-edit]').count(),0);
        check(await page.getByText('当前账号只读，管理员和运营可维护归属。',{exact:true}).isVisible(),true);
        await page.locator('[data-ial-action="close"]').click();role='admin';await page.reload();await page.waitForFunction(()=>CURRENT_ROLE==='admin');
        salesRows = [{id: 1, product_id: 1, order_no: 'ORDER-COST-SOURCE-SKU', display_order_no:'00042-ORDER-COST', quantity: 2, sold_at: '2026-09-10T00:00:00',
            display_sku:'00123456789012345678',display_name:'商品*2+组合 '+ 'ABCDEFGHIJKLMNOPQRSTUVWXYZ'.repeat(10),
            source_sku_id:'00123456789012345678',source_product_name:'Juguete para Perro',transaction_type:'订单',source_quantity:2,
            selling_price: '30', selling_price_cny: '30', sales_revenue_cny: '60', platform_fee_cny: '5',
            profit_cny: '35', total_cost: '20', order_total_cost_cny: '20', cost_status: 'unverified',
            unit_purchase_cost_cny:'4',unit_head_shipping_cost_cny:'2',unit_last_mile_cost_cny:'1',unit_other_cost_cny:'3',unit_total_cost_cny:'10',
            purchase_cost_cny: '8', head_shipping_cost_cny: '4', last_mile_cost_cny: '2', other_cost_cny: '6', impairment_cny: '0.9'},
            {id: 2, product_id: 1, order_no: 'UNKNOWN-COST', quantity: 1, sold_at: '2026-09-11T00:00:00',
             selling_price: '30', selling_price_cny: '30', sales_revenue_cny: '30', platform_fee_cny: '0', profit_cny: '20', total_cost: '10',
             cost_breakdown_review_reason: '<script>unsafe</script>', impairment_review_reason: '<img onerror="unsafe">'},
            {id: -3, record_type: 'settlement_adjustment', quantity: 0, sold_at: '2026-09-12T00:00:00',
             selling_price_cny: '-3', sales_revenue_cny: '-3', platform_fee_cny: '0', profit_cny: '-3', total_cost: '0',
             order_total_cost_cny: '0', purchase_cost_cny: '0', head_shipping_cost_cny: '0', last_mile_cost_cny: '0',
             other_cost_cny: '0', impairment_cny: '0'}];
        for (const suffix of ['/', '/copy']) {
            await page.evaluate(()=>localStorage.removeItem('fifo.sales.ledger.hidden.v1.user:10'));
            await page.goto(address + suffix);
            await page.waitForFunction(() => CURRENT_USER_ID === 10);
            fieldHelpStatus=503;
            await page.getByRole('button', {name: '销售记录', exact: true}).click();
            await page.waitForFunction(()=>document.getElementById('sales-tbody').textContent.includes('字段资源尚未加载'));
            check(await page.evaluate(()=>CURRENT_USER_ID),10);
            fieldHelpStatus=200;
            pendingStatus=503;
            await page.locator('#sales-search-button').click();
            await page.waitForFunction(() => document.querySelectorAll('#sales-tbody tr').length === 3);
            await page.locator('#pending-confirmations-tbody [role="alert"]').waitFor();
            check(await page.locator('#pending-confirmations-summary').innerText(),'待确认记录加载失败，请重试。');
            check(await page.locator('#pending-confirmations-tbody').innerText(),'<待确认失败测试>');
            check(await page.locator('#pending-confirmations-tbody 待确认失败测试').count(),0);
            check(await page.locator('[data-sales-total="order_total_cost_cny"]').innerText(),'¥30.00');
            check(await page.locator('#sales-pagination').innerText().then(v=>v.includes('共 3 条')),true);
            pendingStatus=200;
            await page.evaluate(()=>loadSales());
            check(await page.locator('#pending-confirmations-tbody').innerText(),'暂无待确认记录');
            check(await page.locator('#sales-tbody tr').count(),3);
            const definitions=JSON.parse(fs.readFileSync('static/sales-fields.zh-CN.json','utf8')).fields;
            check(await page.locator('#sales-ledger-head [data-sales-field]').evaluateAll(nodes=>nodes.map(node=>node.dataset.salesField).sort()),
                definitions.filter(field=>field.default_hidden!==true).map(field=>field.key).sort());
            await page.locator('#sales-visible-toggle').click();
            await page.locator('#sales-visible-all').click();
            await page.locator('#sales-visible-toggle').click();
            check(await page.locator('[data-sales-field]').count(),definitions.length);
            check(await page.locator('#sales-ledger-head tr').count(),1);
            await checkSalesHeaders(page,suffix);
            await checkSalesScrolling(page,suffix,salesRows);
            await checkSalesColumnVisibility(page,suffix);
            const firstSale=page.locator('#sales-tbody tr').first();
            const saleValue=key=>firstSale.locator(`[data-sales-value="${key}"]`);
            check(await saleValue('sku').innerText(),'00123456789012345678');
            check(await saleValue('transaction_type').innerText(),'订单');
            check(await saleValue('source_quantity').innerText(),'2');
            check(await saleValue('product_name').evaluate(node=>node.scrollWidth<=node.clientWidth),true);
            check(await saleValue('unit_purchase_cost_cny').innerText(),'¥4.0000');
            check(await saleValue('unit_total_cost_cny').innerText(),'¥10.0000');
            check(await saleValue('order_total_cost_cny').innerText(),'¥20.00');
            check(await saleValue('profit_cny').innerText(),'¥35.00');
            check(await saleValue('impairment_cny').innerText(),'¥0.90');
            check(await page.locator('#sales-tbody tr').nth(1).locator('[data-sales-value="impairment_cny"]').innerText(),'待核对');
            check(await page.locator('[data-sales-total="impairment_cny"]').innerText(),'待核对');
            check(await page.locator('[data-sales-total="unit_total_cost_cny"]').innerText(),'');
            check(await page.locator('#sales-tbody script, #sales-tbody img').count(),0);
            const unitHelp=page.locator('[data-sales-field="unit_purchase_cost_cny"] .help-tip-trigger');
            await unitHelp.hover();
            const unitTip=page.locator('[data-sales-field="unit_purchase_cost_cny"] [data-help-tip]');
            check(await unitTip.isVisible(),true);
            check(await unitTip.innerText().then(v=>v.includes('未舍入')&&v.includes('来源')),true);
            await page.keyboard.press('Escape');
            await firstSale.locator('[data-sale-calculation]').click();
            await page.locator('#sales-calc-body .sales-order-totals').waitFor();
            check(await firstSale.locator('[data-sales-value="order_no"]').innerText(),'00042-ORDER-COST');
            check(await firstSale.locator('[data-sale-calculation]').getAttribute('aria-label'),'计算明细 · 00042-ORDER-COST');
            check(await page.locator('#sales-calc-body .sales-calculation-identity').first().innerText().then(v=>v.includes('00042-ORDER-COST')&&!v.includes('SOURCE-SKU')),true);
            check(await page.locator('#sales-calc-body').innerText().then(v=>v.includes('COST-BATCH')&&v.includes('COMPONENT-OFF-PAGE')&&v.includes('25.00')),true);
            check(await page.locator('#sales-calc-body').innerText().then(v=>v.includes('¥4.0000')),true);
            check(await page.locator('#sales-calc-body').innerText().then(v=>v.includes('9 × 0.5% × ¥20.00 = ¥0.90')),true);
            check(await page.locator('#sales-calc-body').innerText().then(v=>v.includes('2026-09-01')&&v.includes('2026-09-10')&&v.includes('订单日商品类型')),true);
            check(await page.locator('#sales-calc-body').innerText().then(v=>v.includes('来源产品名：Juguete para Perro')&&v.includes('狗狗咀嚼玩具')),true);
            check(await page.locator('#sales-calc-body script, #sales-calc-body img').count(),0);
            // 缺失来源创建日时不伪造日期或代入算式，但保留已知的本批成本。
            await page.locator('[data-sales-calc-close]').click();calculationMissingOrderDate=true;
            await firstSale.locator('[data-sale-calculation]').click();
            await page.locator('#sales-calc-body').getByText('订单创建日期缺失，不能使用结算日期计算出库跌价',{exact:true}).waitFor();
            const pendingImpairment=page.locator('.sales-calculation-section').filter({has:page.locator('#sales-calc-impairment')}).locator('tbody tr').first().locator('td');
            check(await pendingImpairment.nth(2).innerText(),'待核对');
            check(await pendingImpairment.nth(3).innerText(),'待核对');
            check(await pendingImpairment.nth(6).innerText(),'¥20.00');
            check(await pendingImpairment.nth(7).innerText(),'待核对');
            check(await pendingImpairment.nth(8).innerText(),'待核对');
            await page.locator('[data-sales-calc-close]').click();calculationMissingOrderDate=false;
            await firstSale.locator('[data-sale-calculation]').click();
            await page.locator('#sales-calc-body .sales-order-totals').waitFor();
            // 浏览器旧close事件晚到时，不得取消同一事件循环内新打开的计算明细。
            await page.evaluate(()=>{document.getElementById('sales-calc-dialog').close();document.querySelector('[data-sale-calculation]').click();});
            await page.locator('#sales-calc-body .sales-order-totals').waitFor();
            check(await page.locator('#sales-calc-dialog').evaluate(d=>d.open),true);
            if(process.env.INVENTORY_QA_DIR&&suffix==='/')await page.screenshot({path:path.join(process.env.INVENTORY_QA_DIR,'sales-cost-calculation-desktop.png'),fullPage:true});
            await page.setViewportSize({width:390,height:760});
            check(await page.locator('#sales-calc-dialog').evaluate(d=>d.getBoundingClientRect().width<=390),true);
            check(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),true);
            if(process.env.INVENTORY_QA_DIR&&suffix==='/')await page.screenshot({path:path.join(process.env.INVENTORY_QA_DIR,'sales-cost-calculation-mobile.png'),fullPage:true});
            await page.locator('[data-sales-calc-close]').click();
            await page.setViewportSize({width:1440,height:1000});
            // 产品名与库存共用联想；选择只填名称，平台SKU及独立补录口径保持不变。
            const productInput=page.locator('#s-product-name'),productOptions=page.locator('#s-product-name-options [role="option"]');
            await page.locator('#s-sku').fill('00123456789012345678');
            await productInput.fill('猫咪');await productOptions.nth(1).waitFor();
            check(await productOptions.count(),2);
            check(await productOptions.first().innerText().then(value=>value.includes('CW0002')&&value.includes('猫咪自嗨转盘')),true);
            if(process.env.INVENTORY_QA_DIR&&suffix==='/')await page.locator('#sales-manual-entry-card').screenshot({path:path.join(process.env.INVENTORY_QA_DIR,'sales-product-suggestions-desktop.png')});
            await productOptions.nth(1).click();
            check(await productInput.inputValue(),offPageProduct.name);
            check(await page.locator('#s-sku').inputValue(),'00123456789012345678');
            check(await page.locator('#s-product-name-popup').isVisible(),false);
            await productInput.fill('CW0001');await productOptions.nth(1).waitFor();
            check(await productOptions.first().locator('strong').innerText(),'CW0001');
            await productInput.press('ArrowDown');await productInput.press('Enter');
            check(await productInput.inputValue(),products[0].name);
            await productInput.fill('SEARCH-EXACT');
            await page.waitForFunction(()=>document.querySelectorAll('#s-product-name-options [role="option"]').length===20);
            check(await productOptions.first().locator('strong').innerText(),'SEARCH-EXACT');
            check(productCalls.at(-1).prioritize_exact_sku,'true');
            await productOptions.first().click();check(await productInput.inputValue(),exactSuggestion.name);
            check(await page.locator('#s-sku').inputValue(),'00123456789012345678');
            await productInput.fill('<库存');await productOptions.first().waitFor();
            check(await productOptions.first().locator('span').innerText(),'<库存安全测试>');
            check(await page.locator('#s-product-name-options 库存安全测试').count(),0);
            await productOptions.first().click();check(await productInput.inputValue(),'<库存安全测试>');
            const beforeEmpty=productCalls.length;await productInput.fill('');
            await page.waitForTimeout(300);check(productCalls.length,beforeEmpty);
            check(await page.locator('#s-product-name-popup').isVisible(),false);
            // 输入法组合期间不发请求，组合完成后再检索。
            const beforeComposition=productCalls.length;
            await productInput.evaluate(input=>{
                input.dispatchEvent(new CompositionEvent('compositionstart',{bubbles:true}));input.value='猫咪';
                input.dispatchEvent(new InputEvent('input',{bubbles:true,isComposing:true}));
            });
            await page.waitForTimeout(300);check(productCalls.length,beforeComposition);
            await productInput.evaluate(input=>input.dispatchEvent(new CompositionEvent('compositionend',{bubbles:true})));
            await productOptions.nth(1).waitFor();await productInput.press('Escape');
            check(await productInput.inputValue(),'猫咪');
            await productInput.fill('自由填写商品*4+组合');
            await page.waitForFunction(()=>document.getElementById('s-product-name-search-status').textContent.includes('没有匹配'));
            await page.locator('#s-sku').focus();check(await productInput.inputValue(),'自由填写商品*4+组合');
            productStatus=503;await productInput.fill('error');
            await page.waitForFunction(()=>document.getElementById('s-product-name-search-status').textContent.includes('商品查询失败测试'));
            productStatus=200;await productInput.press('Escape');
            await page.setViewportSize({width:390,height:844});await productInput.fill('猫咪');await productOptions.nth(1).waitFor();
            check(await page.locator('#s-product-name-popup').evaluate(node=>{const box=node.getBoundingClientRect();return box.left>=0&&box.right<=innerWidth;}),true);
            if(process.env.INVENTORY_QA_DIR&&suffix==='/')await page.screenshot({path:path.join(process.env.INVENTORY_QA_DIR,'sales-product-suggestions-mobile.png'),fullPage:true});
            await productOptions.first().click();await page.setViewportSize({width:1440,height:1000});
            // 新输入替代旧请求，旧候选不能覆盖新结果。
            productGate=wait();const oldSuggestion=productGate;
            const oldSuggestionStarted=page.waitForRequest(r=>new URL(r.url()).pathname==='/products'&&new URL(r.url()).searchParams.get('keyword')==='狗狗');
            await productInput.fill('狗狗');await oldSuggestionStarted;
            await productInput.fill('猫咪');await productOptions.nth(1).waitFor();oldSuggestion.resolve();
            check(await productOptions.first().innerText().then(value=>value.includes('猫咪')),true);
            await productInput.press('Escape');
            productStatus=401;productGate=wait();const oldSuggestion401=productGate;
            const old401Started=page.waitForRequest(r=>new URL(r.url()).pathname==='/products'&&new URL(r.url()).searchParams.get('keyword')==='狗狗');
            await productInput.fill('狗狗');await old401Started;
            const old401Cancelled=page.waitForEvent('requestfailed',{predicate:r=>new URL(r.url()).pathname==='/products'});
            await page.evaluate(async()=>{handleSessionExpired();await loadCurrentUser();});await old401Cancelled;
            productStatus=200;oldSuggestion401.resolve();
            await page.getByRole('button',{name:'销售记录',exact:true}).click();
            await page.waitForFunction(()=>document.querySelectorAll('#sales-tbody tr').length===3);
            check(await page.evaluate(()=>CURRENT_USER_ID),10);
            check(await page.locator('#login-overlay').isVisible(),false);
            check(await productInput.inputValue(),'');check(await page.locator('#s-product-name-popup').isVisible(),false);
            // 输入法组合中退出也必须清理组合状态，重登后可继续检索。
            await productInput.focus();
            await productInput.evaluate(input=>input.dispatchEvent(new CompositionEvent('compositionstart',{bubbles:true})));
            await page.evaluate(async()=>{handleSessionExpired();await loadCurrentUser();});
            await page.getByRole('button',{name:'销售记录',exact:true}).click();
            await page.waitForFunction(()=>document.querySelectorAll('#sales-tbody tr').length===3);
            await productInput.fill('猫咪');await productOptions.nth(1).waitFor();
            check(await productOptions.count(),2);await productInput.press('Escape');
            // 手工结算补录允许自由填写来源字段，失败后重试复用请求标识。
            await page.locator('#s-store').selectOption('7');
            await page.locator('#s-date').fill('2026-09-03');
            await page.locator('#s-sku').fill('00123456789012345678');
            await page.locator('#s-product-name').fill('商品*4');
            await page.locator('#s-transaction-type').fill('物流赔付');
            await page.locator('#s-qty').fill('0');
            await page.locator('#s-net-sales').fill('0');
            await page.locator('#s-settlement-total').fill('117.5000');
            await page.locator('#s-order-no').fill('MANUAL-CLAIM');
            manualStatus=503;
            await page.locator('#s-submit').click();
            await page.waitForFunction(()=>document.getElementById('s-manual-status').textContent.includes('补录失败测试'));
            const retryKey=manualSettlementCalls.at(-1).request_key;
            check(await page.locator('#s-order-no').inputValue(),'MANUAL-CLAIM');
            manualStatus=200;await page.locator('#s-submit').click();
            await page.waitForFunction(()=>document.getElementById('s-manual-status').textContent.includes('结算补录已保存'));
            check(manualSettlementCalls.at(-1).request_key,retryKey);
            check(manualSettlementCalls.at(-1).settlement_total,'117.5000');
            check('product_id' in manualSettlementCalls.at(-1),false);
            check(await page.locator('#s-order-no').inputValue(),'');
            await page.waitForFunction(()=>!document.getElementById('s-manual-fields').disabled);
            check(await page.locator('#s-submit').isEnabled(),true);
            calculationStatus=503;
            await firstSale.locator('[data-sale-calculation]').click();
            await page.waitForFunction(()=>document.getElementById('sales-calc-status').textContent.includes('读取失败测试'));
            await page.locator('[data-sales-calc-close]').click();calculationStatus=200;
            // 已关闭的旧请求不得覆盖后来打开的计算明细。
            calculationGate=wait();const oldCalculation=calculationGate;
            const waitingCalculation=page.waitForRequest(r=>r.url().includes('/cost-breakdown'));
            await firstSale.locator('[data-sale-calculation]').click();await waitingCalculation;
            await page.keyboard.press('Escape');
            await firstSale.locator('[data-sale-calculation]').click();
            await page.locator('#sales-calc-body .sales-order-totals').waitFor();oldCalculation.resolve();
            await page.waitForTimeout(80);
            check(await page.locator('#sales-calc-body').innerText().then(v=>v.includes('COMPONENT-OFF-PAGE')),true);
            await page.locator('[data-sales-calc-close]').click();
            // 同账号重登后，旧计算请求的401不能退出新会话，也不能恢复旧财务内容。
            calculationGate=wait();calculationStatus=401;const oldCostSession=calculationGate;
            const oldCostStarted=page.waitForRequest(r=>r.url().includes('/cost-breakdown'));
            await firstSale.locator('[data-sale-calculation]').click();await oldCostStarted;
            await page.evaluate(async()=>{handleSessionExpired();await loadCurrentUser();});
            check(await page.locator('#sales-calc-dialog').isVisible(),false);
            check(await page.locator('#sales-calc-body').innerText(),'');
            calculationStatus=200;oldCostSession.resolve();await page.waitForTimeout(80);
            check(await page.evaluate(()=>CURRENT_USER_ID),10);
            check(await page.locator('#login-overlay').isVisible(),false);
            await page.getByRole('button',{name:'销售记录',exact:true}).click();
            await page.waitForFunction(()=>document.querySelectorAll('#sales-tbody tr').length===3);
            check(await page.locator('.sales-ledger-scroll').evaluate(e => e.scrollWidth > e.clientWidth), true);
            await page.evaluate(() => {document.querySelector('.sales-ledger-scroll').scrollLeft = 9999;});
            await page.evaluate(() => {document.querySelector('.sales-ledger-scroll').scrollLeft = 0;});
            await page.locator('#sales-month').fill('2026-09');
            const orderSearch=page.getByRole('searchbox',{name:'商品名称 / SKU / 订单号',exact:true});
            check(await orderSearch.getAttribute('placeholder'),'输入名称、SKU 或订单号');
            await orderSearch.fill(' 00042-ORDER-COST ');await page.evaluate(()=>{salesPage=5;});
            const searched=page.waitForResponse(r=>new URL(r.url()).pathname==='/sales'&&new URL(r.url()).searchParams.get('keyword')==='00042-ORDER-COST');
            const summarized=page.waitForResponse(r=>new URL(r.url()).pathname==='/sales/monthly-summary'&&new URL(r.url()).searchParams.get('keyword')==='00042-ORDER-COST');
            await orderSearch.press('Enter');await Promise.all([searched,summarized]);
            check(salesListQueries.at(-1).page,'1');check(salesListQueries.at(-1).keyword,'00042-ORDER-COST');
            check(salesSummaryQueries.at(-1),{month:'2026-09',keyword:'00042-ORDER-COST'});
            await page.evaluate(() => {salesPage = 5;});
            const download = page.waitForEvent('download');
            await page.locator('#sales-export-button').click();
            check((await download).suggestedFilename(), 'sales_202609.xlsx');
            check(salesExportCalls.at(-1), {month: '2026-09', keyword: '00042-ORDER-COST'});
            await page.waitForFunction(() => !salesExportBusy);
            check(await page.locator('#sales-month').isEnabled(), true);
            salesExportStatus = 404;
            await page.locator('#sales-export-button').click();
            await page.waitForFunction(() => document.getElementById('sales-export-status').textContent.includes('没有符合条件'));
            check(await page.locator('#sales-export-button').isEnabled(), true);
            salesExportStatus = 200;
        }
        for(const suffix of ['/', '/copy']) {
            countRecords=[];countCalls=[];
            await page.goto(address+suffix);await page.waitForFunction(()=>CURRENT_USER_ID===10);
            await page.locator('[data-inventory-nav]').click();
            await page.locator('[data-iw-action="assessment"]').click();
            await page.locator('[data-ia-edit="1"]').waitFor();
            check(await page.locator('#inventory-workspace-root').isVisible(),false);
            check(await page.locator('#ia-body > tr').count(),4);
            check(await page.locator('#ia-body > tr').first().locator(':scope > td').count(),14);
            check(await page.locator('#ia-body .tag-orange').getAttribute('title').then(value=>value.includes('逐批数量')),true);
            check(await page.locator('#ia-total td').nth(5).innerText(),'待核对');
            const countButton=await page.locator('[data-ia-edit="1"]').boundingBox();
            check(countButton.x+countButton.width<=1440,true);
            if(process.env.INVENTORY_QA_DIR&&suffix==='/')await page.screenshot({path:path.join(process.env.INVENTORY_QA_DIR,'inventory-assessment-desktop.png'),fullPage:true});
            await page.locator('[data-ia-batch="1"]').click();
            check(await page.locator('#ia-batch-body').innerText().then(v=>v.includes('<BATCH>')),true);
            if(process.env.INVENTORY_QA_DIR&&suffix==='/')await page.screenshot({path:path.join(process.env.INVENTORY_QA_DIR,'inventory-assessment-batches.png'),fullPage:true});
            await page.locator('[data-ia-action="close-batches"]').click();
            check(await page.locator('#ia-body script').count(),0);
            await page.locator('#ia-date').fill('2026-09-30');
            await page.locator('[data-ia-edit="1"]').click();
            check(await page.locator('#ia-edit').isVisible(),false);
            check(await page.locator('#ia-status').innerText().then(v=>v.includes('请先查询')),true);
            await page.locator('#ia-query button.btn-primary').click();
            await page.waitForFunction(()=>document.getElementById('ia-status').textContent==='');
            await page.locator('[data-ia-edit="1"]').click();
            await page.locator('#ia-save').click();
            if(process.env.INVENTORY_QA_DIR&&suffix==='/')await page.screenshot({path:path.join(process.env.INVENTORY_QA_DIR,'inventory-assessment-editor.png'),fullPage:true});
            check(countCalls.length,0);
            check(await page.locator('#ia-edit-status').innerText().then(v=>v.includes('整数')),true);
            await page.locator('#ia-actual').fill('0');await page.locator('#ia-note').fill('<script>unsafe</script>');
            await page.evaluate(()=>Object.defineProperty(globalThis.crypto,'randomUUID',{value:undefined,configurable:true}));
            const held=wait();countGate=held;
            await page.locator('#ia-save').click();await page.waitForFunction(()=>document.getElementById('ia-save').disabled);
            check(await page.locator('#ia-actual').isEditable(),false);
            check(await page.locator('#ia-note').isEditable(),false);
            check(await page.locator('[data-ia-action="cancel"]').isDisabled(),true);
            await page.evaluate(()=>document.getElementById('ia-save').click());
            check(countCalls.length,1);held.resolve();
            await page.locator('#ia-edit').waitFor({state:'hidden'});
            check(countRecords[0].actual_quantity,0);check(countRecords[0].difference,-7281);
            check(/^[A-Za-z0-9_-]{16,64}$/.test(countCalls[0].request_key),true);
            check(await page.locator('#ia-body > tr').first().locator(':scope > td').nth(10).innerText(),'0');
            await page.locator('[data-ia-history="1"]').click();
            await page.locator('#ia-history-body tr').waitFor();
            check(await page.locator('#ia-history-body').innerText().then(v=>v.includes('<script>unsafe</script>')),true);
            check(await page.locator('#ia-history-body script').count(),0);
            await page.locator('[data-ia-action="close-history"]').click();
            await page.locator('[data-ia-edit="1"]').click();await page.locator('#ia-actual').fill('5');countStatus=409;
            await page.locator('#ia-save').click();await page.waitForFunction(()=>document.getElementById('ia-edit-status').textContent.includes('已变化'));
            check(await page.locator('#ia-edit').isVisible(),true);check(await page.locator('#ia-actual').inputValue(),'5');
            check(await page.locator('#ia-actual').isEnabled(),true);
            check(await page.locator('#ia-note').isEnabled(),true);
            check(await page.locator('#ia-save').isEnabled(),true);
            await page.locator('[data-ia-action="cancel"]').click();countStatus=200;
            await page.locator('[data-ia-edit="1"]').click();
            await page.locator('#ia-actual').fill('6');await page.locator('#ia-note').fill('retry-note');countNetworkError=true;
            const recordsBeforeFailure=countRecords.length;
            await page.locator('#ia-save').click();
            await page.waitForFunction(()=>!document.getElementById('ia-save').disabled&&document.getElementById('ia-edit-status').textContent.includes('操作失败'));
            check(await page.locator('#ia-actual').inputValue(),'6');check(await page.locator('#ia-note').inputValue(),'retry-note');
            check(await page.locator('#ia-actual').isEnabled(),true);check(await page.locator('#ia-note').isEnabled(),true);
            check(countRecords.length,recordsBeforeFailure);
            const retryKey=countCalls.at(-1).request_key;countNetworkError=false;
            await page.locator('#ia-save').click();await page.locator('#ia-edit').waitFor({state:'hidden'});
            check(countCalls.at(-1).request_key,retryKey);
            check(countRecords.length,recordsBeforeFailure+1);check(countRecords.at(-1).actual_quantity,6);
            const downloaded=page.waitForEvent('download');await page.locator('[data-ia-action="export"]').click();
            check((await downloaded).suggestedFilename().startsWith('inventory_assessment_'),true);
            await page.waitForFunction(()=>document.getElementById('ia-status').textContent.includes('导出已生成'));
            // 同账号重登也须忽略旧盘点POST的401，不能退出新会话。
            await page.locator('[data-ia-edit="1"]').click();
            const old=wait();countGate=old;countStatus=401;
            const oldRequest=page.waitForRequest(r=>new URL(r.url()).pathname==='/inventory/stock-counts'&&r.method()==='POST');
            await page.locator('#ia-save').click();await oldRequest;
            const oldCancelled=page.waitForEvent('requestfailed',{predicate:r=>new URL(r.url()).pathname==='/inventory/stock-counts'});
            await page.evaluate(async()=>{handleSessionExpired();await loadCurrentUser();});
            await oldCancelled;old.resolve();countStatus=200;
            await page.waitForFunction(()=>CURRENT_USER_ID===10);
            check(await page.locator('#inventory-assessment-root').innerHTML(),'');
            check(await page.locator('#login-overlay').isVisible(),false);
            await page.locator('[data-inventory-nav]').click();await page.locator('[data-iw-action="assessment"]').click();
            await page.locator('[data-ia-edit="1"]').waitFor();
            check(await page.locator('#ia-actual').isEnabled(),true);
            check(await page.locator('#ia-note').isEnabled(),true);
            await page.locator('[data-ia-action="overview"]').click();
            check(await page.locator('#inventory-assessment-root').isVisible(),false);
            check(await page.locator('#inventory-workspace-root').isVisible(),true);
        }
        // 损益说明收进问号；动态警示保持可见，汇率折叠且不能掩盖缺失汇率。
        role = 'admin';
        for (const suffix of ['/', '/copy']) {
            const reason = '存在历史流水异常或跨店/缺失店铺归属，跌价相关金额及受影响利润待核对，未以0代替。';
            profitLossFixture = {report_month: '2026-09-01', period_label: '2026年9月', store_id: 7, store_name: '店铺A',
                can_edit: true, calculation_policy: 'month_end_fx_mxn8_v1', updated_at: '2026-11-03T10:20:30',
                warnings: ['本月' + reason, '上月' + reason, '本年累计' + reason, '本月' + reason, '<img src=x onerror=alert(1)>待核对'],
                exchange_rates: [{report_month: '2026-09', currency: 'MXN', rate_to_cny: '.36899', rate_date: '2026-09-30', source: '<汇率来源>'}], rows: []};
            await page.goto(address + suffix);
            await page.waitForFunction(() => CURRENT_USER_ID === 10);
            await page.evaluate(() => {document.getElementById('profit-loss-month').value = '2026-09';});
            await page.locator('button[onclick="switchTab(\'reports\')"]').click();
            await page.locator('[data-report-view="profit-loss"]').click();
            await page.waitForFunction(() => document.getElementById('profit-loss-updated').textContent.includes('上次保存'));
            const policy = page.locator('#profit-loss-policy'), help = page.locator('#profit-loss-title .help-tip-trigger');
            const warnings = page.locator('#profit-loss-warnings'), details = page.locator('#profit-loss-fx-details');
            await help.waitFor();
            check(await policy.isVisible(), false);
            check(await warnings.isVisible(), true);
            check(await warnings.locator('div').count(), 2);
            check(await warnings.innerText(), '本月、上月、本年累计' + reason + '\n<img src=x onerror=alert(1)>待核对');
            check(await warnings.locator('img').count(), 0);
            check(await page.locator('#profit-loss-updated').innerText(), '店铺A · 2026年9月 · 上次保存 2026-11-03 10:20:30');
            await help.focus(); await policy.waitFor({state: 'visible'});
            check(await policy.innerText().then(value => value.includes('每单8 MXN')), true);
            await page.keyboard.press('Escape');
            await policy.waitFor({state: 'hidden'});
            check(await details.getAttribute('open'), null);
            check(await page.locator('#profit-loss-fx').isVisible(), false);
            await details.locator('summary').click();
            check(await page.locator('#profit-loss-fx').innerText().then(value => value.includes('2026-09-30') && value.includes('<汇率来源>')), true);
            check(await details.locator('img, script').count(), 0);
            if (process.env.INVENTORY_QA_DIR && suffix === '/') await page.locator('#report-pane-profit-loss').screenshot({path: path.join(process.env.INVENTORY_QA_DIR, 'profit-loss-context.png')});
            delete profitLossFixture.calculation_policy;
            await page.evaluate(() => loadStoreProfitLoss());
            await page.waitForFunction(() => document.getElementById('profit-loss-policy').getAttribute('role') === 'alert' && !document.getElementById('profit-loss-policy').classList.contains('help-tip-source'));
            check(await policy.isVisible(), true);
            check(await help.isVisible(), false);
            check(await policy.innerText().then(value => value.includes('后端尚未加载')), true);
            profitLossFixture.calculation_policy = 'month_end_fx_mxn8_v1';
            profitLossFixture.warnings = [];
            profitLossFixture.exchange_rates[0].rate_to_cny = null;
            await page.evaluate(() => loadStoreProfitLoss());
            await help.waitFor({state: 'visible'});
            check(await policy.isVisible(), false);
            check(await warnings.isVisible(), true);
            check(await warnings.innerText(), '月末汇率待核对');
            check(await details.getAttribute('open'), null);
            profitLossFixture.exchange_rates = [];
            await page.evaluate(() => loadStoreProfitLoss());
            check(await warnings.isVisible(), false);
            check(await details.isVisible(), false);
            profitLossStatus = 503;
            await page.evaluate(() => loadStoreProfitLoss());
            check(await page.locator('#profit-loss-updated').innerText(), '');
            check(await warnings.isVisible(), false);
            check(await details.isVisible(), false);
            check(await page.locator('#store-profit-loss-tbody').innerText(), '损益读取失败测试');
            profitLossStatus = 200;
        }
        profitLossFixture = null;
        // 保留名称的逐商品毛利；两个页面均读取商品分组，旧后端/异常/空结果可恢复。
        role='admin';
        for(const suffix of ['/','/copy']){
            productProfitRows=[
                {group_by:'product',name:'P10 · <测试商品>',current_quantity:4,current_revenue:100,current_cost:40,current_gross:60,current_margin:.6,note:'<核对>'},
                {group_by:'product',name:'P11 · 同名不同编码商品',current_quantity:2,current_revenue:20,current_cost:null,current_gross:null},
                {group_by:'product',name:'未分配商品（结算调整/赔付）',current_quantity:0,current_revenue:5,current_cost:0,current_gross:5}];
            await page.goto(`http://127.0.0.1:${server.address().port}${suffix}`);
            await page.waitForFunction(()=>CURRENT_USER_ID===10);
            await page.locator('button[onclick="switchTab(\'reports\')"]').click();
            await page.evaluate(()=>{document.getElementById('profit-loss-month').value='2026-09';document.getElementById('inventory-impairment-date').value='2026-09-30';});
            await page.locator('[data-report-view="product-line"]').click();
            await page.waitForFunction(()=>document.querySelectorAll('#product-line-profit-tbody tr').length===3);
            const pane=page.locator('#report-pane-product-line');
            check(await pane.locator('h3').evaluate(node=>[...node.childNodes].filter(child=>child.nodeType===Node.TEXT_NODE).map(child=>child.textContent).join('').trim()),'各产品线毛利分析表');
            check(await pane.locator('thead th').first().innerText(),'商品编码 / 名称');
            check(await pane.locator('thead th').nth(1).innerText(),'本月出库数量');
            check(await pane.locator('tbody tr').first().locator('td').nth(0).innerText(),'P10 · <测试商品>');
            check(await pane.locator('tbody tr').first().locator('td').nth(1).innerText(),'4');
            check(await pane.locator('tbody tr').nth(1).locator('td').nth(3).innerText(),'-');
            check(await pane.locator('tbody tr').nth(2).locator('td').nth(1).innerText(),'0');
            check(await pane.locator('tbody img, tbody script').count(),0);
            check(productProfitQueries.at(-1).report_month,'2026-09-01');
            const help=page.locator('[data-profit-loss-text="productProfitHelp"]');
            await pane.locator('.help-tip-trigger').hover();await help.waitFor({state:'visible'});
            check(await help.innerText().then(value=>value.includes('本地商品编码')&&value.includes('组件分摊')),true);
            await page.keyboard.press('Escape');
            if(process.env.INVENTORY_QA_DIR&&suffix==='/')await pane.screenshot({path:path.join(process.env.INVENTORY_QA_DIR,'product-profit-rows.png')});
            productProfitRows=[{name:'旧分组',sku_count:21}];await page.evaluate(()=>loadProductLineProfit());
            check(await pane.locator('tbody').innerText().then(value=>value.includes('接口尚未更新')),true);
            productProfitRows=[];await page.evaluate(()=>loadProductLineProfit());
            check(await pane.locator('tbody').innerText(),'当前核算期间无商品数据');
            productProfitStatus=503;await page.evaluate(()=>loadProductLineProfit());
            check(await pane.locator('tbody').innerText().then(value=>value.includes('读取失败测试')),true);
            productProfitStatus=200;
        }
        // Actual HTTP streaming against both real pages, with no business import.
        role='admin';
        for (const suffix of ['/', '/copy']) {
            await page.goto(`http://127.0.0.1:${server.address().port}${suffix}`);
            await page.waitForFunction(()=>CURRENT_USER_ID===10);
            await page.locator('button[onclick="switchTab(\'sales\')"]').click();
            await page.locator('#sales-import-store option[value="7"]').waitFor({state:'attached'});
            await page.locator('#sales-import-store').selectOption('7');
            const help=page.locator('button[aria-describedby="sales-import-date-help"]');
            await help.waitFor();await help.hover();
            await page.waitForFunction(()=>document.getElementById('sales-import-date-help').getAttribute('role')==='tooltip');
            check(await page.locator('#sales-import-date-help').innerText().then(v=>v.includes('下方月份只控制查询')),true);
            await page.mouse.move(0,0);await page.keyboard.press('Escape');
            await page.waitForFunction(()=>document.querySelector('button[aria-describedby="sales-import-date-help"]').getAttribute('aria-expanded')==='false');
            const before=importRequests.length;
            await page.locator('#import-csv').setInputFiles({name:'<进度测试>.csv',mimeType:'text/csv',buffer:Buffer.from('fixture')});
            await page.waitForFunction(()=>document.getElementById('sales-import-progress-stage').textContent==='正在读取文件');
            check(importRequests.length,before+1);check(await page.locator('#import-csv').isDisabled(),true);
            check(await page.locator('#sales-import-progress-actions').isVisible(),false);
            check(await page.locator('#sales-import-button-text').textContent(),'正在导入…');
            const request=importRequests.at(-1);
            check(request.body.includes('name="store_id"'),true);check(request.body.includes('name="month"'),false);
            request.write({type:'progress',stage:'writing',processed:40,total:100});
            await page.waitForFunction(()=>document.getElementById('sales-import-progress-count').textContent.includes('40%'));
            check(await page.locator('#sales-import-progress-bar').getAttribute('value'),'40');
            check(await page.locator('#sales-import-progress-file').textContent(),'<进度测试>.csv · 店铺A');
            check(await page.locator('#sales-import-progress-file img').count(),0);
            for(const width of [1440,390]){
                await page.setViewportSize({width,height:1000});
                const box=await page.locator('#sales-import-progress-panel').boundingBox();
                check(box.x>=0&&box.x+box.width<=width,true);
                if(process.env.INVENTORY_QA_DIR&&suffix==='/')await page.locator('#sales-import-progress-panel').screenshot({path:path.join(process.env.INVENTORY_QA_DIR,`sales-import-progress-${width}.png`)});
            }
            await page.setViewportSize({width:1440,height:1000});
            const runningHeight=(await page.locator('#sales-import-progress-panel').boundingBox()).height;
            request.write({type:'progress',stage:'committing'});
            await page.waitForFunction(()=>document.getElementById('sales-import-progress-stage').textContent==='正在确认保存结果');
            check(await page.locator('#sales-import-progress-bar').getAttribute('value'),null);
            request.write({type:'result',status:200,data:{success:true,settlement_rows:100,sales_created:90,skipped_duplicates:0,pending_confirmation:10,warnings:[]}});request.end();
            await page.waitForFunction(()=>document.getElementById('sales-import-progress-panel').dataset.state==='review');
            check(await page.locator('#sales-import-progress-count').textContent(),'100%');
            check(await page.locator('#import-csv').isEnabled(),true);
            check(await page.locator('#sales-import-button-text').textContent(),'导入订单详情');
            check(await page.locator('#sales-import-progress-details').isVisible(),false);
            check(await page.locator('#sales-import-progress-bar').isVisible(),false);
            check(await page.locator('#sales-import-progress-count').isVisible(),false);
            check(await page.locator('#sales-import-progress-brief').innerText().then(v=>v.includes('待确认10条')),true);
            check((await page.locator('#sales-import-progress-panel').boundingBox()).height<runningHeight,true);
            for(const width of [1440,390]){
                await page.setViewportSize({width,height:1000});
                const box=await page.locator('#sales-import-progress-panel').boundingBox();
                check(box.x>=0&&box.x+box.width<=width,true);
                if(process.env.INVENTORY_QA_DIR&&suffix==='/')await page.locator('#sales-import-progress-panel').screenshot({path:path.join(process.env.INVENTORY_QA_DIR,`sales-import-complete-${width}.png`)});
            }
            await page.setViewportSize({width:1440,height:1000});
            await page.locator('#sales-import-progress-toggle').focus();await page.keyboard.press('Enter');
            check(await page.locator('#sales-import-progress-toggle').getAttribute('aria-expanded'),'true');
            check(await page.locator('#sales-import-progress-file').isVisible(),true);
            check(await page.locator('#sales-import-progress-bar').isVisible(),false);
            await page.locator('#sales-import-progress-toggle').click();
            check(await page.locator('#sales-import-progress-details').isVisible(),false);
            await page.locator('#sales-import-progress-close').click();
            check(await page.locator('#sales-import-progress-panel').isVisible(),false);
            check(importRequests.length,before+1);
            await page.locator('#import-csv').setInputFiles({name:'retry.csv',mimeType:'text/csv',buffer:Buffer.from('fixture')});
            await page.waitForFunction(()=>document.getElementById('sales-import-progress-stage').textContent==='正在读取文件');
            const retry=importRequests.at(-1);retry.write({type:'result',status:400,data:{detail:'错误路径测试'}});retry.end();
            await page.waitForFunction(()=>document.getElementById('sales-import-progress-panel').dataset.state==='error');
            check(await page.locator('#sales-import-progress-brief').textContent(),'错误路径测试');
            check(await page.locator('#import-csv').isEnabled(),true);
            await page.locator('#sales-import-progress-close').click();
            check(await page.locator('#sales-import-progress-panel').isVisible(),false);
            await page.locator('#import-csv').setInputFiles({name:'success.csv',mimeType:'text/csv',buffer:Buffer.from('fixture')});
            await page.waitForFunction(()=>document.getElementById('sales-import-progress-stage').textContent==='正在读取文件');
            const success=importRequests.at(-1);
            await page.mouse.move(0,0);await page.locator('#sales-month').focus();
            success.write({type:'result',status:200,data:{success:true,settlement_rows:100,sales_created:90,skipped_duplicates:0,pending_confirmation:0,warnings:[]}});success.end();
            await page.waitForFunction(()=>document.getElementById('sales-import-progress-panel').dataset.state==='complete');
            check(await page.locator('#sales-import-progress-panel').isVisible(),true);
            await page.waitForFunction(()=>document.getElementById('sales-import-progress-panel').hidden,{},{timeout:12000});
            check(await page.locator('#import-csv').isEnabled(),true);
        }
        role = 'viewer';
        await page.evaluate(async () => {handleSessionExpired(); await loadCurrentUser();});
        await page.locator('[data-inventory-nav]').click();
        await page.locator('[data-iw-action="daily"]').click();
        await page.locator('#iw-tbody tr').first().waitFor();
        check(await page.locator('#iw-scope').innerText().then(value => value.includes('仅含绑定店铺')), true);
        check(await page.locator('#iw-tbody tr').first().locator('td').nth(1).innerText(), '—');
        check(await page.locator('#iw-tbody [data-iw-move="adjustment"]').count(), 0);
        await downloadExport();
        exportStatus = 401;
        await page.locator('#iw-export').click();
        await page.waitForFunction(() => CURRENT_USER_ID === 0);
        check(await page.locator('#inventory-workspace-root').innerHTML(), '');
        check(await page.locator('#login-overlay').isVisible(), true);
        check(pageErrors, []);
        console.log(JSON.stringify({page_copies: 2, checks, browser: 'headless Chromium', scope: 'real DOM with local fixture APIs; no business database'}));
    } finally {
        if (browser) await browser.close();
        await new Promise(resolve => server.close(resolve));
    }
}
main().catch(error => {console.error(error); process.exitCode = 1;});
