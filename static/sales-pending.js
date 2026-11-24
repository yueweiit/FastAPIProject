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
        columns:['店铺','导入文件','行号','订单 ID','SKU ID','交易类型','商品名称','来源规格（SKU 名称）','导入指定规格/型号','数量','其他费用','待确认原因'],
        unbound:'未关联店铺',other:'其他原因',cancel:'取消',confirm:'确认并重新处理',
        title:'重新处理待确认记录',resolveTitle:'确认本地商品或组合',
        autoHelp:'将检查这 {count} 条记录；已处理记录自动跳过，未解决的记录保留待确认原因。复用原金额、日期和汇率，不生成重复结算来源。',
        componentHelp:'选择本地商品并填写每份对应的库存数量（倍数）。库存数量 = 来源数量 × 倍数；可添加多个商品组成组合。退款、调整不强制出库。',
        product:'查找本地商品名称 / SKU / 规格',multiplier:'倍数',remove:'移除',needComponent:'请至少选择一个本地商品。',
        sourceSku:'来源 SKU ID',sourceSpecification:'来源规格（SKU 名称）',requestedSpecification:'导入指定规格/型号',
        localSpecification:'本地商品规格/型号',
        saveMapping:'保存为此店铺来源标识的后续映射',effectiveFrom:'映射生效日期',sourceUnit:'来源数量单位',unit:'份',mappingSource:'本次保存标识',
        nameUnique:'我已核实此名称在本店只代表这组商品和装量',mappingHelp:'默认仅处理本次。保存映射后，同店相同来源标识可复用；历史已扣库记录保持原结果。',
        preview:'预览扣库',previewUnavailable:'扣库预览未加载，请更新后端后刷新页面。',
        mappings:'店铺销售映射',chooseStore:'请先在销售页上方选择导入店铺，再查看该店铺映射。',newVersion:'建立新版本',saveVersion:'保存新版本',
        mappingSaved:'店铺映射新版本已保存，历史出库未改变。',mappingEmpty:'此店铺尚无已确认映射，可在待确认记录中选择商品并保存。',
        mappingHistory:'全部历史版本均保留。装量变化请建立新版本并明确生效日期，不会重新扣减历史库存。',disabledMapping:'停用版本',
        mappingKinds:{platform_sku:'平台 SKU ID',merchant_sku:'商家编码',product_name:'名称别名',local_sku:'本地 SKU'},
        invalidMultiplier:'倍数必须是1至2147483647之间的整数。',units:'来源数量 {source} × 倍数 {multiple} = 所需库存数量 {count}',
        submitting:'正在处理，请勿重复提交…',result:'已确认 {confirmed} 条，仍待确认 {pending} 条，已处理跳过 {skipped} 条；新增销售明细 {sales} 条。',
        previewLoading:'正在核对商品和销售日可用库存…',sourceRecord:'来源记录',sourceFile:'来源文件',sourceOrder:'原始订单号',
        sourceProduct:'来源商品',sourceStore:'店铺',selectComponents:'选择本地商品',previewHelp:'先预览扣库，核对后再提交。',
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
    let epoch=0,serial=0,loading=false,busy=false,exporting=false,initialized=false,dialog,target=null,picker,parts=[],mappingDialog;
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
        node('pending-mappings').hidden=!writable();node('pending-mappings').disabled=locked || loading || dirty();
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
            <button class="btn btn-secondary" type="button" id="pending-mappings" data-pending-action="mappings">${h(text('mappings'))}</button>
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
            if(action==='mappings')showMappings().catch(error=>{node('pending-result').textContent=error.message;});
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
            item.transaction_type,item.product_name,item.sku_name,item.requested_local_specification || '-',item.quantity,
            `${Number(item.other_expense || 0).toFixed(2)} ${item.currency || ''}`];
        tbody.innerHTML=rows.length?rows.map(item=>`<tr>${write?`<td><input type="checkbox" data-pending-id="${item.id}" aria-label="${h(text('chooseRow'))}" ${selected.has(item.id)?'checked':''}></td>`:''}`+
            cells(item).map(value=>`<td>${h(value ?? '-')}</td>`).join('')+
            `<td style="min-width:260px;max-width:420px;white-space:normal;overflow-wrap:anywhere">${h(item.mapping_error || text('other'))}</td><td>${h(label(item.reason_code))}</td>`+
            (write?`<td><div style="display:flex;gap:6px;flex-wrap:wrap"><button type="button" class="btn btn-secondary" data-pending-write data-pending-row-action="retry" data-pending-id="${item.id}">${h(text('retry'))}</button>`+
            `<button type="button" class="btn btn-secondary" data-pending-write data-pending-row-action="resolve" data-pending-id="${item.id}">${h(text('resolve'))}</button></div></td>`:'')+'</tr>').join(''):
            `<tr><td class="empty" colspan="${write?15:13}">${h(text('empty'))}</td></tr>`;
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
            node('pending-confirmations-tbody').innerHTML=`<tr><td colspan="15" class="msg msg-err" role="alert">${h(error.message || text('failed'))}</td></tr>`;
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
        dialog=document.createElement('dialog');dialog.id='pending-retry-dialog';dialog.className='sales-review-dialog';
        dialog.setAttribute('aria-labelledby','pending-dialog-title');
        dialog.innerHTML=`<form id="pending-dialog-form"><header class="sales-review-head"><h3 id="pending-dialog-title"></h3><p id="pending-dialog-help"></p></header>
            <div class="sales-review-body"><div class="pending-review-layout"><aside id="pending-dialog-info" class="pending-source-panel"></aside>
            <fieldset id="pending-component-field"><legend>${h(text('selectComponents'))}</legend>
            <div class="form-group inventory-product-picker"><label for="pending-product">${h(text('product'))}</label><div class="inventory-product-control">
                <input id="pending-product" role="combobox" aria-autocomplete="list" aria-expanded="false" aria-controls="pending-product-options" autocomplete="off" maxlength="255">
                <div id="pending-product-popup" class="inventory-product-popup" hidden><div id="pending-product-options" role="listbox" class="inventory-product-options"></div>
                <p id="pending-product-status" role="status" class="inventory-product-search-status"></p></div></div></div>
            <div id="pending-components"></div>
            <div id="pending-mapping-settings"><label class="pending-mapping-toggle"><input type="checkbox" id="pending-save-mapping"> <span>${h(text('saveMapping'))}</span></label><p>${h(text('mappingHelp'))}</p>
            <fieldset id="pending-mapping-fields" hidden disabled><p id="pending-mapping-source"></p><div class="pending-mapping-inputs"><label>${h(text('effectiveFrom'))}<input type="date" id="pending-mapping-date"></label><label>${h(text('sourceUnit'))}<input id="pending-source-unit" maxlength="32" value="${h(text('unit'))}"></label></div>
            <label id="pending-name-unique-label"><input type="checkbox" id="pending-name-unique"> <span>${h(text('nameUnique'))}</span></label></fieldset></div>
            </fieldset></div></div><p id="pending-dialog-status" role="status" hidden></p><p id="pending-dialog-error" class="msg msg-err" role="alert" tabindex="-1" hidden></p>
            <footer class="sales-review-footer"><p id="pending-footer-help"></p><div class="page-action-buttons"><button type="button" id="pending-cancel" class="btn btn-secondary">${h(text('cancel'))}</button>
            <button type="submit" id="pending-submit" class="btn btn-primary">${h(text('preview'))}</button></div></footer></form>`;
        document.body.appendChild(dialog);
        node('pending-cancel').onclick=closeDialog;
        node('pending-save-mapping').onchange=syncMappingFields;
        dialog.addEventListener('cancel',event=>{event.preventDefault();if(!busy)closeDialog();});
        node('pending-dialog-form').onsubmit=event=>{event.preventDefault();submit().catch(()=>{});};
        node('pending-components').onchange=event=>{
            const index=event.target.dataset.partIndex;if(index===undefined)return;
            parts[Number(index)].multiplier=Number(event.target.value);renderParts();
        };
        node('pending-components').oninput=event=>{
            const index=event.target.dataset.partIndex;if(index===undefined)return;
            parts[Number(index)].multiplier=Number(event.target.value);
            node('pending-components').querySelector(`[data-part-units="${index}"]`).textContent=componentUnits(parts[Number(index)]);
        };
        node('pending-components').onclick=event=>{
            const index=event.target.closest('[data-remove-part]')?.dataset.removePart;
            if(index!==undefined){parts.splice(Number(index),1);renderParts();}
        };
        if(window.ProductSearch)picker=window.ProductSearch.bind({input:node('pending-product'),popup:node('pending-product-popup'),list:node('pending-product-options'),status:node('pending-product-status'),
            showSpecification:true,queryParams:()=>({search_specification:true}),
            enabled:()=>dialog.open && !!target?.item && !busy,captureContext:()=>{const opened=target;return context(()=>target===opened && dialog.open);},
            onSelect:product=>{
                const previous=parts.find(part=>part.product_id===product.id);
                if(previous)previous.multiplier++;else parts.push({product_id:product.id,multiplier:1,sku:product.sku,name:product.name,specification:product.specification});
                node('pending-product').value='';renderParts();
            }});
    }
    function syncMappingFields() {
        const fields=node('pending-mapping-fields');
        const enabled=node('pending-save-mapping').checked && !node('pending-mapping-settings').hidden;
        fields.hidden=!enabled;fields.disabled=!enabled;
    }
    const componentUnits=part=>text('units',{source:target.item.quantity,multiple:part.multiplier,count:target.item.quantity*part.multiplier});
    function renderParts() {
        node('pending-components').innerHTML=parts.map((part,index)=>`<section class="pending-component"><div class="pending-component-heading"><div>
            ${part.sku?`<span class="sales-review-code">${h(part.sku)}</span>`:''}<strong>${h(part.name || `#${part.product_id}`)}</strong>
            ${part.specification?`<p class="sales-review-secondary">${h(text('localSpecification'))}：${h(part.specification)}</p>`:''}</div>
            <button type="button" class="btn btn-secondary" data-remove-part="${index}">${h(text('remove'))}</button></div><div class="pending-component-controls">
            <label>${h(text('multiplier'))} <input type="number" min="1" max="2147483647" step="1" required data-part-index="${index}" value="${h(part.multiplier)}"></label>
            <small class="pending-component-equation" data-part-units="${index}">${h(componentUnits(part))}</small></div></section>`).join('');
    }
    function openDialog(payload,count,item=null) {
        if(!writable() || busy || loading || dirty() || exporting)return;
        initializeDialog();target={payload,count,item};parts=[];
        node('pending-dialog-title').textContent=text(item?'resolveTitle':'title');
        node('pending-footer-help').textContent=text('previewHelp');
        dialog.dataset.mode=item?'manual':'auto';node('pending-dialog-info').hidden=!item;
        node('pending-dialog-info').innerHTML=item?`<h4>${h(text('sourceRecord'))}</h4><dl class="pending-source-fields">${[
            ['sourceStore',item.store_name],['sourceFile',item.source_file,true],['sourceOrder',item.order_id,true,true],['sourceProduct',item.product_name,true],
            ['sourceSku',item.platform_sku_id,false,true],['sourceSpecification',item.sku_name],['requestedSpecification',item.requested_local_specification]]
            .filter(([,value])=>value).map(([key,value,wide,code])=>`<div${wide?' class="pending-source-wide"':''}><dt>${h(text(key))}：</dt><dd${code?' class="sales-review-code"':''}>${h(value)}</dd></div>`).join('')}</dl>
            ${item.mapping_error?`<p class="pending-source-issue">${h(item.mapping_error)}</p>`:''}`:'';
        node('pending-mapping-source').textContent=item?.mapping_identity?`${text('mappingSource')}：${text('mappingKinds')[item.mapping_identity.kind]} · ${item.mapping_identity.value}`:'';
        node('pending-dialog-help').textContent=text(item?'componentHelp':'autoHelp',{count});
        node('pending-component-field').hidden=!item;
        node('pending-component-field').disabled=false;node('pending-cancel').disabled=false;node('pending-submit').disabled=false;
        node('pending-dialog-error').hidden=true;node('pending-dialog-status').hidden=true;node('pending-product').value='';picker?.close();
        node('pending-save-mapping').checked=false;node('pending-name-unique').checked=false;
        node('pending-save-mapping').disabled=false;node('pending-submit').textContent=text('preview');
        node('pending-source-unit').value=text('unit');
        node('pending-mapping-date').value=(item?.order_created_at || item?.settlement_date || '').slice(0,10);
        node('pending-mapping-settings').hidden=!item?.mapping_identity || item.mapping_identity.kind==='local_sku';
        node('pending-name-unique-label').hidden=item?.mapping_identity?.kind!=='product_name';
        syncMappingFields();
        if(item)parts=(item.confirmed_components || []).map(part=>{
            const product=(typeof allProducts!=='undefined'?allProducts.find(product=>product.id===part.product_id):null) || {};
            // 接口明确返回空规格时不能被旧商品缓存覆盖。
            return {...part,sku:part.sku || product.sku,name:part.name || product.name || `#${part.product_id}`,
                specification:part.specification === undefined ? product.specification : part.specification};
        });
        renderParts();dialog.showModal();node(item?'pending-product':'pending-cancel').focus();
    }
    function closeDialog() {if(busy)return;picker?.close();target=null;parts=[];if(dialog?.open)dialog.close();}
    async function showMappings(){
        if(!writable() || busy || loading || dirty() || exporting)return;
        const store=node('sales-import-store'),storeId=Number(store?.value);
        if(!storeId)throw new Error(text('chooseStore'));
        const current=context(()=>Number(node('sales-import-store')?.value)===storeId);
        const items=await api('GET',`/sales/product-mappings?store_id=${storeId}`,null,{isCurrent:current});
        if(!current())return;
        if(!mappingDialog){mappingDialog=document.createElement('dialog');mappingDialog.id='sales-mappings-dialog';
            mappingDialog.style.cssText='width:min(1100px,94vw);max-width:94vw;max-height:90vh;overflow:auto;border:1px solid var(--line);border-radius:12px;padding:20px';document.body.appendChild(mappingDialog);}
        mappingDialog.innerHTML=`<h3>${h(text('mappings'))} · ${h(store.selectedOptions?.[0]?.textContent || storeId)}</h3><p>${h(text('mappingHistory'))}</p>
            <div style="overflow:auto">${items.length?`<table style="min-width:760px"><thead><tr>${[text('mappingSource'),text('effectiveFrom'),text('localSpecification'),text('actions')].map(value=>`<th>${h(value)}</th>`).join('')}</tr></thead><tbody>${[...items].reverse().map(item=>`<tr><td>${h(text('mappingKinds')[item.key_kind])} · ${h(item.key_value)} · #${item.id}${item.is_active?'':` · ${h(text('disabledMapping'))}`}</td><td>${h(item.effective_from)}</td><td>${item.components.map(c=>h(`${[c.sku,c.name,c.specification].filter(Boolean).join(' · ')} × ${c.multiplier}`)).join('<br>')}</td><td><button class="btn btn-secondary" data-mapping-version="${item.id}">${h(text('newVersion'))}</button></td></tr>`).join('')}</tbody></table>`:`<p>${h(text('mappingEmpty'))}</p>`}</div>
            <div class="page-action-buttons"><button type="button" class="btn btn-secondary" data-mapping-close>${h(text('cancel'))}</button></div>`;
        mappingDialog.onclick=event=>{
            if(event.target.closest('[data-mapping-close]'))mappingDialog.close();
            const button=event.target.closest('[data-mapping-version]');if(!button)return;
            const selectedMapping=items.find(item=>item.id===Number(button.dataset.mappingVersion));
            mappingDialog.close();
            openDialog({},1,{store_name:store.selectedOptions?.[0]?.textContent,product_name:selectedMapping.key_value,quantity:1,
                mapping_identity:{kind:selectedMapping.key_kind,value:selectedMapping.key_value},latest_mapping_version_id:selectedMapping.latest_version_id,
                confirmed_components:selectedMapping.components});
            if(!target)return;
            target.mapping={store_id:storeId,key_kind:selectedMapping.key_kind,key_value:selectedMapping.key_value};
            node('pending-dialog-title').textContent=text('newVersion');node('pending-submit').textContent=text('saveVersion');
            node('pending-footer-help').textContent=text('mappingHistory');
            node('pending-save-mapping').checked=true;node('pending-save-mapping').disabled=true;
            syncMappingFields();
            node('pending-source-unit').value=selectedMapping.source_unit;node('pending-mapping-date').value='';
        };
        mappingDialog.showModal();
    }
    async function submit() {
        if(busy || !target || !writable())return;
        const error=node('pending-dialog-error'),payload={...target.payload};
        if(target.item) {
            node('pending-components').querySelectorAll('[data-part-index]').forEach(input=>{
                parts[Number(input.dataset.partIndex)].multiplier=Number(input.value);
            });
            if(!parts.length){error.hidden=false;error.textContent=text('needComponent');error.focus({preventScroll:true});return;}
            if(parts.some(part=>!Number.isInteger(part.multiplier) || part.multiplier<1 || part.multiplier>2147483647)) {
                error.hidden=false;error.textContent=text('invalidMultiplier');error.focus({preventScroll:true});return;
            }
            payload.components=parts.map(({product_id,multiplier})=>({product_id,multiplier}));
            if(node('pending-save-mapping').checked && !node('pending-mapping-settings').hidden){
                payload.save_mapping={effective_from:node('pending-mapping-date').value,
                    expected_version_id:target.item.latest_mapping_version_id??null,source_unit:node('pending-source-unit').value,
                    confirm_name_unique:node('pending-name-unique').checked};
            }
        }
        const current=context();busy=true;controls();node('pending-cancel').disabled=true;node('pending-submit').disabled=true;
        node('pending-component-field').disabled=true;
        error.hidden=true;
        const status=node('pending-dialog-status');status.hidden=false;status.textContent=text(target.mapping?'submitting':'previewLoading');
        try {
            if(target.mapping){
                await api('POST','/sales/product-mappings',{...target.mapping,...payload.save_mapping,components:payload.components},{isCurrent:current});
                if(!current())return;
                busy=false;closeDialog();node('pending-result').textContent=text('mappingSaved');
                await load();return;
            }
            if(!window.SalesDeductionPreview)throw new Error(text('previewUnavailable'));
            const plan=await api('POST','/sales/pending-confirmations/preview',payload,{isCurrent:current});
            if(!current())return;
            // 切换到预览步骤，保留原表单与选择；同一时刻只展示一个模态窗口。
            picker?.close();dialog.close();
            const token=await window.SalesDeductionPreview.confirm(plan,{isCurrent:current});
            if(!current())return;
            dialog.showModal();
            if(!token)return;
            status.textContent=text('submitting');
            payload.preview_token=token;
            const result=await api('POST','/sales/pending-confirmations/retry',payload,{isCurrent:current});
            if(!current())return;
            busy=false;closeDialog();selected.clear();busy=true;controls();
            const message=text('result',{confirmed:result.confirmed_rows,pending:result.pending_rows,skipped:result.skipped_rows,sales:result.sales_created});
            try {await refreshSalesWorkspace();if(current())node('pending-result').textContent=message;}
            catch {if(current())node('pending-result').textContent=message+' '+text('refreshFailed');}
        } catch(failure) {
            if(current()){error.hidden=false;error.textContent=text('submitFailed')+failure.message;}
        } finally {
            if(current()){
                busy=false;status.hidden=true;node('pending-cancel').disabled=false;node('pending-submit').disabled=false;node('pending-component-field').disabled=false;controls();
                if(target){if(!dialog.open)dialog.showModal();(error.hidden?node('pending-submit'):error).focus({preventScroll:true});}
            }
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
        window.SalesDeductionPreview?.reset();
        if(mappingDialog?.open)mappingDialog.close();
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
