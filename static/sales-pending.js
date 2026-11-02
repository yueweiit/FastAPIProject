/* 待确认筛选及原记录处理；金额、日期和库存规则由服务端保证。 */
(() => {
    'use strict';
    const messages = {'zh-CN': {
        reason:'待确认类型',all:'全部原因',keyword:'订单 / SKU / 商品 / 文件',query:'查询',clear:'清空',
        selected:'重新处理所选',filtered:'重新处理当前筛选',selection:'已选 {count} 条',
        help:'修正商品或库存资料后可直接重新处理，无需重新整理Excel。库存仍按原销售日期校验；晚到库存不能用于早期销售。成功记录按原业务日期归月，请切换对应月份查看。',
        loading:'正在加载待确认记录…',empty:'暂无符合条件的待确认记录',summary:'当前筛选共 {count} 条待确认记录',
        unavailable:'当前服务尚未加载待确认处理功能，请更新后端后刷新页面。',failed:'待确认记录加载失败，请重试。',
        choosePage:'选择本页',chooseRow:'选择记录',kind:'原因分类',actions:'操作',retry:'重新处理',resolve:'确认商品',
        columns:['店铺','导入文件','行号','订单 ID','SKU ID','交易类型','商品名称','SKU 名称','数量','其他费用','待确认原因'],
        unbound:'未关联店铺',other:'其他原因',cancel:'取消',confirm:'确认并重新处理',
        title:'重新处理待确认记录',resolveTitle:'确认本地商品或组合',
        autoHelp:'将检查这 {count} 条记录；已处理记录自动跳过，未解决的记录保留待确认原因。复用原金额、日期和汇率，不生成重复结算来源。',
        componentHelp:'选择本地商品并填写每个来源单位包含的件数（倍数）。本地商品件数 = 来源数量 × 倍数；可添加多个商品组成组合。是否出库仍按原交易类型判断，退款、调整不强制出库。',
        product:'查找本地商品名称 / SKU',multiplier:'倍数',remove:'移除',needComponent:'请至少选择一个本地商品。',
        invalidMultiplier:'倍数必须是1至2147483647之间的整数。',units:'来源数量 {source} × 倍数 {multiple} = 本地组件 {count} 件',
        submitting:'正在处理，请勿重复提交…',result:'已确认 {confirmed} 条，仍待确认 {pending} 条，已处理跳过 {skipped} 条；新增销售明细 {sales} 条。',
        refreshFailed:'处理结果已保存，部分列表刷新失败，请手动刷新核对。',
        submitFailed:'处理未完成，请保留选择并刷新核对后重试：',exportFailed:'待确认列表导出失败：',exported:'待确认筛选结果已导出。',
        reasons:{stock_shortage:'销售日库存不足',product_missing:'未找到本地商品',product_ambiguous:'商品名称或映射不唯一',
            expression_invalid:'产品名或组合格式错误',mapping_invalid:'历史映射配置无效',order_conflict:'订单重复或冲突',source_invalid:'历史来源待核对',other:'其他原因'},
    }};
    const text = (key, params={}) => {
        let value=(messages[document.documentElement.lang] || messages['zh-CN'])[key];
        if(typeof value==='string')for(const [name,item] of Object.entries(params))value=value.replaceAll(`{${name}}`,String(item));
        return value;
    };
    const h=value=>escapeHtml(String(value??''));
    const node=id=>document.getElementById(id);
    const card=()=>node('pending-confirmations-card');
    let reason='',keyword='',page=1,size=20,total=0,rows=[],counts={},supported=false;
    let epoch=0,serial=0,loading=false,busy=false,exporting=false,initialized=false,dialog,target=null,picker,parts=[];
    const selected=new Set();
    const user=()=>typeof CURRENT_USER_ID==='undefined'?0:CURRENT_USER_ID;
    const writable=()=>supported && isOperator();
    const label=code=>text('reasons')[code] || text('other');
    function context(extra=()=>true) {
        const version=epoch,actor=user();
        return ()=>version===epoch && actor===user() && !!actor && extra();
    }
    function params() {
        const query=new URLSearchParams();
        if(reason)query.set('reason',reason);
        if(keyword)query.set('keyword',keyword);
        return query;
    }
    const dirty=()=>node('pending-keyword').value.trim()!==keyword;
    function controls() {
        if(!initialized)return;
        const locked=busy || exporting;
        card().querySelectorAll('[data-pending-write]').forEach(button=>{
            button.hidden=!writable();button.disabled=locked || loading || dirty() || !total;
        });
        node('pending-selected').disabled=locked || loading || dirty() || !selected.size;
        node('pending-selection').textContent=text('selection',{count:selected.size});
        node('pending-reason').disabled=locked || !supported;
        node('pending-keyword').disabled=locked || !supported;
        node('pending-export').disabled=locked || loading || dirty();
        node('pending-refresh').disabled=locked;
        const pageSelect=node('pending-select-page');if(pageSelect)pageSelect.disabled=locked || loading || !rows.length;
        node('pending-tools').querySelectorAll('button').forEach(button=>{
            if(!button.hasAttribute('data-pending-write'))button.disabled=locked;
        });
    }
    function initialize() {
        if(initialized || !card())return;
        initialized=true;
        const tools=document.createElement('div');tools.id='pending-tools';
        tools.innerHTML=`<form class="inventory-filters" id="pending-query" style="margin-top:16px">
            <div class="form-group"><label for="pending-reason">${h(text('reason'))}</label><select id="pending-reason"></select></div>
            <div class="form-group"><label for="pending-keyword">${h(text('keyword'))}</label><input id="pending-keyword" type="search" maxlength="255"></div>
            <button class="btn btn-primary" type="submit">${h(text('query'))}</button>
            <button class="btn btn-secondary" type="button" data-pending-action="clear">${h(text('clear'))}</button>
            <button class="btn btn-secondary" id="pending-selected" type="button" data-pending-action="selected" data-pending-write>${h(text('selected'))}</button>
            <button class="btn btn-primary" type="button" data-pending-action="filtered" data-pending-write>${h(text('filtered'))}</button>
            <span id="pending-selection" class="cost-detail"></span></form>
            <p class="cost-detail">${h(text('help'))}</p><p id="pending-result" role="status" aria-live="polite"></p>`;
        card().querySelector('.flex-between').after(tools);
        const exportButton=card().querySelector('[onclick="exportPendingConfirmations()"]');exportButton.id='pending-export';
        const refreshButton=card().querySelector('[onclick="loadPendingConfirmations()"]');refreshButton.id='pending-refresh';refreshButton.onclick=()=>refresh().catch(()=>{});
        node('pending-query').onsubmit=event=>{event.preventDefault();search().catch(()=>{});};
        node('pending-reason').onchange=()=>search().catch(()=>{});
        node('pending-keyword').oninput=controls;
        tools.onclick=event=>{
            const action=event.target.closest('[data-pending-action]')?.dataset.pendingAction;
            if(action==='clear'){node('pending-keyword').value='';node('pending-reason').value='';search().catch(()=>{});}
            if(action==='selected')openDialog({entry_ids:[...selected]},selected.size);
            if(action==='filtered')openDialog({reason:reason || null,keyword:keyword || null},total);
        };
        node('pending-confirmations-tbody').onchange=event=>{
            const id=Number(event.target.dataset.pendingId);
            if(id){if(event.target.checked)selected.add(id);else selected.delete(id);controls();syncPageSelection();}
        };
        node('pending-confirmations-tbody').onclick=event=>{
            const button=event.target.closest('[data-pending-row-action]');if(!button)return;
            const id=Number(button.dataset.pendingId),item=rows.find(row=>row.id===id);
            if(item)openDialog({entry_ids:[id]},1,button.dataset.pendingRowAction==='resolve'?item:null);
        };
        renderReasons();controls();
    }
    function renderReasons() {
        const totalKnown=Object.values(counts).reduce((sum,value)=>sum+Number(value),0);
        node('pending-reason').innerHTML=`<option value="">${h(text('all'))}${supported?` (${totalKnown})`:''}</option>`+
            Object.entries(text('reasons')).map(([code,name])=>`<option value="${code}">${h(name)}${supported?` (${Number(counts[code]||0)})`:''}</option>`).join('');
        node('pending-reason').value=reason;
    }
    function syncPageSelection() {
        const checkbox=node('pending-select-page');if(!checkbox)return;
        const checked=rows.filter(row=>selected.has(row.id)).length;
        checkbox.checked=!!rows.length && checked===rows.length;checkbox.indeterminate=checked>0 && checked<rows.length;
    }
    function render() {
        const write=writable(),tbody=node('pending-confirmations-tbody');
        card().querySelector('thead').innerHTML=`<tr>${write?`<th><input id="pending-select-page" type="checkbox" aria-label="${h(text('choosePage'))}"></th>`:''}`+
            text('columns').map(value=>`<th>${h(value)}</th>`).join('')+`<th>${h(text('kind'))}</th>${write?`<th>${h(text('actions'))}</th>`:''}</tr>`;
        node('pending-confirmations-summary').textContent=text('summary',{count:total});
        const cells=item=>[item.store_name || text('unbound'),item.source_file || '-',item.source_row_number,item.order_id,item.platform_sku_id,
            item.transaction_type,item.product_name,item.sku_name,item.quantity,
            `${Number(item.other_expense || 0).toFixed(2)} ${item.currency || ''}`];
        tbody.innerHTML=rows.length?rows.map(item=>`<tr>${write?`<td><input type="checkbox" data-pending-id="${item.id}" aria-label="${h(text('chooseRow'))}" ${selected.has(item.id)?'checked':''}></td>`:''}`+
            cells(item).map(value=>`<td>${h(value ?? '-')}</td>`).join('')+
            `<td style="min-width:260px;max-width:420px;white-space:normal;overflow-wrap:anywhere">${h(item.mapping_error || text('other'))}</td><td>${h(label(item.reason_code))}</td>`+
            (write?`<td><div style="display:flex;gap:6px;flex-wrap:wrap"><button type="button" class="btn btn-secondary" data-pending-write data-pending-row-action="retry" data-pending-id="${item.id}">${h(text('retry'))}</button>`+
            `<button type="button" class="btn btn-secondary" data-pending-write data-pending-row-action="resolve" data-pending-id="${item.id}">${h(text('resolve'))}</button></div></td>`:'')+'</tr>').join(''):
            `<tr><td class="empty" colspan="${write?14:12}">${h(text('empty'))}</td></tr>`;
        if(write)node('pending-select-page').onchange=event=>{
            rows.forEach(row=>{if(event.target.checked)selected.add(row.id);else selected.delete(row.id);});
            render();controls();
        };
        syncPageSelection();
        renderPagination('pending-confirmations-pagination',page,Math.max(1,Math.ceil(total/size)),total,size);
        renderReasons();controls();
    }
    async function load(options={}) {
        if(!user())return;
        initialize();const request=++serial,current=context(()=>request===serial && (!options.isCurrent || options.isCurrent()));
        const query=params();query.set('page',page);query.set('page_size',size);
        loading=true;controls();node('pending-confirmations-summary').textContent=text('loading');
        try {
            const result=await api('GET',`/sales/pending-confirmations?${query}`,undefined,{isCurrent:current});
            if(!current())return;
            page=result.page;size=result.page_size;total=result.total;rows=result.items;
            supported=!!result.reason_counts;counts=result.reason_counts || {};
            loading=false;render();
            if(!supported)node('pending-result').textContent=text('unavailable');
        } catch(error) {
            if(!current())return;
            loading=false;rows=[];total=0;selected.clear();controls();syncPageSelection();node('pending-confirmations-summary').textContent=text('failed');
            node('pending-confirmations-tbody').innerHTML=`<tr><td colspan="14" class="msg msg-err" role="alert">${h(error.message || text('failed'))}</td></tr>`;
            node('pending-confirmations-pagination').replaceChildren();throw error;
        }
    }
    function search() {
        if(busy || exporting)return;
        reason=node('pending-reason').value;keyword=node('pending-keyword').value.trim();page=1;selected.clear();
        node('pending-result').textContent='';return load(window.SalesLedger?.requestOptions() || {});
    }
    function refresh() {return dirty()?search():load(window.SalesLedger?.requestOptions() || {});}
    function initializeDialog() {
        if(dialog)return;
        dialog=document.createElement('dialog');dialog.id='pending-retry-dialog';dialog.className='product-import-dialog';
        dialog.setAttribute('aria-labelledby','pending-dialog-title');
        dialog.innerHTML=`<form id="pending-dialog-form"><h3 id="pending-dialog-title"></h3><p id="pending-dialog-info" style="white-space:pre-wrap;overflow-wrap:anywhere"></p>
            <p id="pending-dialog-help"></p><fieldset id="pending-component-field"><legend>${h(text('resolveTitle'))}</legend>
            <div class="form-group inventory-product-picker"><label for="pending-product">${h(text('product'))}</label><div class="inventory-product-control">
                <input id="pending-product" role="combobox" aria-autocomplete="list" aria-expanded="false" aria-controls="pending-product-options" autocomplete="off" maxlength="255">
                <div id="pending-product-popup" class="inventory-product-popup" hidden><div id="pending-product-options" role="listbox" class="inventory-product-options"></div>
                <p id="pending-product-status" role="status" class="inventory-product-search-status"></p></div></div></div>
            <div id="pending-components"></div></fieldset><p id="pending-dialog-error" class="msg msg-err" role="alert" hidden></p>
            <div class="page-action-buttons"><button type="button" id="pending-cancel" class="btn btn-secondary">${h(text('cancel'))}</button>
            <button type="submit" id="pending-submit" class="btn btn-primary">${h(text('confirm'))}</button></div></form>`;
        document.body.appendChild(dialog);
        node('pending-cancel').onclick=closeDialog;
        dialog.addEventListener('cancel',event=>{event.preventDefault();if(!busy)closeDialog();});
        node('pending-dialog-form').onsubmit=event=>{event.preventDefault();submit().catch(()=>{});};
        node('pending-components').onchange=event=>{
            const index=event.target.dataset.partIndex;if(index===undefined)return;
            parts[Number(index)].multiplier=Number(event.target.value);renderParts();
        };
        node('pending-components').onclick=event=>{
            const index=event.target.closest('[data-remove-part]')?.dataset.removePart;
            if(index!==undefined){parts.splice(Number(index),1);renderParts();}
        };
        if(window.ProductSearch)picker=window.ProductSearch.bind({input:node('pending-product'),popup:node('pending-product-popup'),list:node('pending-product-options'),status:node('pending-product-status'),
            enabled:()=>dialog.open && !!target?.item && !busy,captureContext:()=>{const opened=target;return context(()=>target===opened && dialog.open);},
            onSelect:product=>{
                const previous=parts.find(part=>part.product_id===product.id);
                if(previous)previous.multiplier++;else parts.push({product_id:product.id,multiplier:1,name:`${product.sku} · ${product.name}`});
                node('pending-product').value='';renderParts();
            }});
    }
    function renderParts() {
        node('pending-components').innerHTML=parts.map((part,index)=>`<div style="border-top:1px solid var(--line);padding:10px 0">
            <strong>${h(part.name || `#${part.product_id}`)}</strong><div style="display:flex;gap:8px;align-items:center;flex-wrap:wrap">
            <label>${h(text('multiplier'))} <input type="number" min="1" max="2147483647" step="1" required data-part-index="${index}" value="${h(part.multiplier)}" style="width:100px"></label>
            <button type="button" class="btn btn-secondary" data-remove-part="${index}">${h(text('remove'))}</button></div>
            <small>${h(text('units',{source:target.item.quantity,multiple:part.multiplier,count:target.item.quantity*part.multiplier}))}</small></div>`).join('');
    }
    function openDialog(payload,count,item=null) {
        if(!writable() || busy || loading || dirty() || exporting)return;
        initializeDialog();target={payload,count,item};parts=[];
        node('pending-dialog-title').textContent=text(item?'resolveTitle':'title');
        node('pending-dialog-info').textContent=item?[item.store_name,item.source_file,item.order_id,item.product_name,item.mapping_error].filter(Boolean).join('\n'):'';
        node('pending-dialog-help').textContent=text(item?'componentHelp':'autoHelp',{count});
        node('pending-component-field').hidden=!item;
        node('pending-component-field').disabled=false;node('pending-cancel').disabled=false;node('pending-submit').disabled=false;
        node('pending-dialog-error').hidden=true;node('pending-product').value='';picker?.close();
        if(item)parts=(item.confirmed_components || []).map(part=>({...part,name:typeof allProducts!=='undefined'?
            (allProducts.find(product=>product.id===part.product_id)?.name || `#${part.product_id}`):`#${part.product_id}`}));
        renderParts();dialog.showModal();node(item?'pending-product':'pending-cancel').focus();
    }
    function closeDialog() {if(busy)return;picker?.close();target=null;parts=[];if(dialog?.open)dialog.close();}
    async function submit() {
        if(busy || !target || !writable())return;
        const error=node('pending-dialog-error'),payload={...target.payload};
        if(target.item) {
            if(!parts.length){error.hidden=false;error.textContent=text('needComponent');return;}
            if(parts.some(part=>!Number.isInteger(part.multiplier) || part.multiplier<1 || part.multiplier>2147483647)) {
                error.hidden=false;error.textContent=text('invalidMultiplier');return;
            }
            payload.components=parts.map(({product_id,multiplier})=>({product_id,multiplier}));
        }
        const current=context();busy=true;controls();node('pending-cancel').disabled=true;node('pending-submit').disabled=true;
        node('pending-component-field').disabled=true;
        error.hidden=false;error.textContent=text('submitting');
        try {
            const result=await api('POST','/sales/pending-confirmations/retry',payload,{isCurrent:current});
            if(!current())return;
            busy=false;closeDialog();selected.clear();busy=true;controls();
            const message=text('result',{confirmed:result.confirmed_rows,pending:result.pending_rows,skipped:result.skipped_rows,sales:result.sales_created});
            try {await refreshSalesWorkspace();if(current())node('pending-result').textContent=message;}
            catch {if(current())node('pending-result').textContent=message+' '+text('refreshFailed');}
        } catch(failure) {
            if(current()){error.hidden=false;error.textContent=text('submitFailed')+failure.message;}
        } finally {
            if(current()){busy=false;node('pending-cancel').disabled=false;node('pending-submit').disabled=false;node('pending-component-field').disabled=false;controls();}
        }
    }
    async function exportRows() {
        initialize();if(exporting || busy || dirty())return;
        const current=context();exporting=true;controls();
        try {
            const response=await fetch(`/sales/pending-confirmations/export?${params()}`,{credentials:'same-origin',headers:authHeaders()});
            if(!current())return;
            if(response.status===401){handleSessionExpired();return;}
            if(!response.ok){const data=await response.json();if(current())throw new Error(formatApiError(data.detail,String(response.status)));return;}
            const blob=await response.blob();if(!current())return;
            const url=URL.createObjectURL(blob),link=document.createElement('a');link.href=url;link.download='pending_confirmations.xlsx';
            document.body.appendChild(link);link.click();link.remove();setTimeout(()=>URL.revokeObjectURL(url),1000);
            node('pending-result').textContent=text('exported');
        } catch(error) {if(current())node('pending-result').textContent=text('exportFailed')+error.message;}
        finally {if(current()){exporting=false;controls();}}
    }
    function reset() {
        epoch++;serial++;busy=false;loading=false;exporting=false;reason='';keyword='';page=1;size=20;rows=[];total=0;counts={};supported=false;selected.clear();
        closeDialog();if(!initialized)return;
        node('pending-keyword').value='';node('pending-result').textContent='';node('pending-confirmations-summary').textContent='';renderReasons();controls();
        node('pending-confirmations-tbody').replaceChildren();node('pending-confirmations-pagination').replaceChildren();
    }
    window.SalesPending={load,export:exportRows,reset,
        goPage:value=>{if(!busy){page=Number(value);return load(window.SalesLedger?.requestOptions() || {});}},
        pageSize:value=>{if(!busy){size=Number(value);page=1;return load(window.SalesLedger?.requestOptions() || {});}}};
    initialize();
})();
