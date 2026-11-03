/* 库存内逐批维护跌价归属；只提交归属字段，沿用批次版本和审计。 */
(() => {
    'use strict';
    const messages = {'zh-CN': {
        title:'跌价归属管理', batch:'批次号', arrival:'入库日期', quantity:'入库数量', remaining:'剩余库存',
        stores:'归属店铺', pending:'待分配', legacy:'未手工分配（兼容历史归属）', edit:'设置归属', save:'保存归属',
        close:'关闭', cancel:'取消编辑', refresh:'刷新', previous:'上一页', next:'下一页', loading:'正在读取…',
        empty:'该商品没有可管理的活动批次。', batchMissing:'该批次已不存在或已作废，请刷新批次列表。', failed:'操作失败：', saved:'归属已保存，库存数量及成本未修改。',
        savedRefreshFailed:'归属已保存，但列表刷新失败，请重试刷新。无需再次保存归属。', retryRefresh:'重试刷新',
        required:'请至少选择一家归属店铺。', readonly:'当前账号只读，管理员和运营可维护归属。',
        help:'按入库批次手工分配，可多选店铺并均分跌价，不限制销售。待分配批次继续计算跌价，店铺分摊待核对。',
        mode:'归属生效范围', from_arrival:'从入库日起更正', from_change_date:'从今天起生效',
        editorTitle:'设置跌价归属', storeHint:'至少选择一家店铺，可多选。', splitHint:'多店铺均分跌价金额',
        arrivalHelp:'重算历史期间的跌价归属，已保存的月度版本保留。',
        changeHelp:'保留此前费用归属，剩余跌价准备转入新店铺。',
        saveHint:'仅调整跌价归属，不改变库存数量或采购成本。', action:'操作',
        modeHelp:'从入库日起更正会重算历史查询归属；从今天起生效保留以前归属并转移剩余准备。已保存月度版本保留。',
        conflict:'批次已变化，请保留选择，取消编辑并刷新后重新核对。', invalid:'归属数据无效，请刷新后重试。',
        unavailable:'归属管理接口尚未加载，请更新并重启本地后端。', page:'共{total}批 · 第{page}页',
    }};
    const t=(key,args={})=>Object.entries(args).reduce((s,[k,v])=>s.replaceAll(`{${k}}`,String(v)),
        (messages[document.documentElement.lang]||messages['zh-CN'])[key]||key);
    const h=value=>escapeHtml(String(value??''));
    const root=()=>document.getElementById('inventory-allocations-root');
    const node=id=>document.getElementById(`ial-${id}`);
    let epoch=0, listSerial=0, state=null, editor=null, busy=false, loading=false, refreshing=false;
    const controllers=new Set();
    const valid=token=>token===epoch&&!!state&&state.userId===CURRENT_USER_ID&&!!CURRENT_USER_ID;
    const canWrite=()=>CURRENT_ROLE==='admin'||CURRENT_ROLE==='operator';
    const label=batch=>batch.impairment_store_names?.join(' / ')||t(batch.impairment_store_history?.some(rule=>rule.is_explicit)?'pending':'legacy');
    function syncRefreshButtons(){
        node('dialog').querySelectorAll('[data-ial-edit],[data-ial-action="refresh"],[data-ial-action="retry-refresh"]').forEach(button=>button.disabled=refreshing);
        const data=state.data;
        node('previous').disabled=refreshing||loading||!data||data.page<=1;
        node('next').disabled=refreshing||loading||!data||data.page*data.page_size>=data.total;
    }
    function reset(){
        epoch++;listSerial++;controllers.forEach(c=>c.abort());controllers.clear();state=null;editor=null;busy=false;loading=false;refreshing=false;
        if(root()){root().querySelectorAll('dialog').forEach(d=>{if(d.open)d.close();});root().replaceChildren();}
    }
    async function request(url, options={}){
        const token=epoch, controller=new AbortController();controllers.add(controller);
        try{
            const response=await fetch(url,{credentials:'same-origin',...options,
                headers:{...authHeaders(),...(options.headers||{})},signal:controller.signal});
            if(!valid(token))return null;
            if(response.status===401){handleSessionExpired();return null;}
            if(response.status===404&&options.method)throw new Error(t('unavailable'));
            let data;try{data=await response.json();}catch{throw new Error(t('invalid'));}
            if(!valid(token))return null;
            if(!response.ok){
                const code=data.detail?.code;
                throw new Error(response.status===409?t('conflict'):code==='batch_impairment_store_required'?t('required'):
                    (typeof data.detail?.message==='string'?data.detail.message:t('invalid')));
            }
            return data;
        }finally{controllers.delete(controller);}
    }
    async function load(){
        if(busy||!state)return;const token=epoch, serial=++listSerial;loading=true;editor=null;state.data=null;
        node('editor').hidden=true;node('status').textContent=t('loading');node('rows').replaceChildren();
        node('previous').disabled=node('next').disabled=true;
        try{
            if(!Array.isArray(state.stores)){
                const stores=await request('/finance-masters/stores');
                if(!stores||!valid(token)||serial!==listSerial)return;
                if(!Array.isArray(stores))throw new Error(t('invalid'));
                state.stores=stores;
            }
            const data=await request(`/batches?product_id=${state.productId}&page=${state.page}&page_size=20${state.batchId?`&batch_id=${state.batchId}`:''}`);
            if(!data||!valid(token)||serial!==listSerial)return;
            if(!Array.isArray(data.items))throw new Error(t('invalid'));
            if(state.batchId&&data.items.some(batch=>batch.id!==state.batchId))throw new Error(t('invalid'));
            state.data=data;state.page=data.page;
            node('rows').innerHTML=data.items.length?data.items.map(batch=>`<tr data-ial-batch="${Number(batch.id)}"><td class="allocation-batch-no">${h(batch.batch_no)}</td><td>${h(batch.arrived_at?.slice(0,10))}</td><td>${h(batch.quantity)}</td><td>${h(batch.remaining_quantity)}</td><td><span class="allocation-store-label">${h(label(batch))}</span></td><td>${canWrite()?`<button type="button" class="btn btn-secondary" data-ial-edit="${Number(batch.id)}">${h(t('edit'))}</button>`:''}</td></tr>`).join(''):`<tr><td colspan="6">${h(t('empty'))}</td></tr>`;
            node('status').textContent=state.batchId&&!data.items.length?t('batchMissing'):'';node('page').textContent=t('page',data);
            node('previous').disabled=data.page<=1;node('next').disabled=data.page*20>=data.total;
            if(state.editOnLoad&&data.items.some(batch=>batch.id===state.batchId)){
                state.editOnLoad=false;edit(state.batchId);
            }
            return true;
        }catch(error){if(valid(token)&&serial===listSerial)node('status').textContent=t('failed')+error.message;return false;}
        finally{if(valid(token)&&serial===listSerial){loading=false;syncRefreshButtons();}}
    }
    async function refreshSaved(){
        if(!state||refreshing)return;
        const token=epoch;refreshing=true;node('retry-refresh').hidden=true;syncRefreshButtons();
        try{
            const loaded=await load();
            if(!valid(token))return;
            if(!loaded)throw new Error(t('invalid'));
            await state.onSaved?.();
            if(!valid(token))return;
            state.refreshPending=false;
            node('status').textContent=t('saved')+(state.batchId&&!state.data.items.length?' '+t('batchMissing'):'');
        }catch(error){
            if(valid(token)){
                state.refreshPending=true;node('status').textContent=t('savedRefreshFailed');node('retry-refresh').hidden=false;
            }
        }finally{if(valid(token)){refreshing=false;syncRefreshButtons();}}
    }
    function edit(batchId){
        editor=state.data.items.find(batch=>batch.id===batchId);if(!editor||!canWrite())return;
        const selected=new Set(editor.impairment_store_ids||[]), stores=[...state.stores];
        [...selected].forEach((id,index)=>{if(!stores.some(store=>store.id===id))stores.push({id,name:editor.impairment_store_names[index],is_active:false});});
        node('editor').innerHTML=`<div class="allocation-editor-heading"><div><span class="allocation-eyebrow">${h(t('editorTitle'))}</span><h4>${h(editor.batch_no)}</h4></div><span class="allocation-split-hint">${h(t('splitHint'))}</span></div>
            <div class="allocation-editor-grid"><fieldset class="allocation-fieldset" aria-describedby="ial-store-hint"><legend>${h(t('stores'))}</legend><p id="ial-store-hint" class="allocation-field-hint">${h(t('storeHint'))}</p><div class="allocation-store-options">${stores.map(store=>`<label class="allocation-store-option"><input type="checkbox" name="store" value="${Number(store.id)}" ${selected.has(store.id)?'checked':''} ${!store.is_active&&!selected.has(store.id)?'disabled':''}><span>${h(store.name)}</span></label>`).join('')}</div></fieldset>
            <fieldset class="allocation-fieldset"><legend>${h(t('mode'))}</legend><p data-help-tip data-help-anchor="legend">${h(t('modeHelp'))}</p><div class="allocation-mode-options">${['from_arrival','from_change_date'].map(mode=>`<label class="allocation-mode-option"><input type="radio" name="mode" value="${mode}" ${(selected.size?'from_change_date':'from_arrival')===mode?'checked':''}><span><strong>${h(t(mode))}</strong><small>${h(t(mode==='from_arrival'?'arrivalHelp':'changeHelp'))}</small></span></label>`).join('')}</div></fieldset></div>
            <p id="ial-edit-status" class="allocation-edit-status" role="status" aria-live="polite"></p><div class="allocation-actions"><p>${h(t('saveHint'))}</p><div><button class="btn btn-secondary" type="button" data-ial-action="cancel">${h(t('cancel'))}</button><button class="btn btn-primary" type="submit">${h(t('save'))}</button></div></div>`;
        node('rows').querySelectorAll('[data-ial-batch]').forEach(row=>row.classList.toggle('is-editing',Number(row.dataset.ialBatch)===batchId));
        node('editor').hidden=false;node('editor').querySelector('input:not(:disabled)')?.focus();
    }
    async function save(event){
        event.preventDefault();if(busy||refreshing||!editor||!canWrite())return;
        const ids=[...node('editor').querySelectorAll('input[name="store"]:checked')].map(input=>Number(input.value));
        if(!ids.length){node('edit-status').textContent=t('required');return;}
        const token=epoch, batch=editor, controls=[...node('dialog').querySelectorAll('input,button')].map(control=>[control,control.disabled]);
        let controlsRestored=false;
        const mode=node('editor').querySelector('input[name="mode"]:checked').value;
        busy=true;controls.forEach(([control])=>control.disabled=true);node('edit-status').textContent=t('loading');
        try{
            const result=await request(`/batches/${batch.id}/impairment-stores`,{method:'PUT',headers:{'Content-Type':'application/json'},
                body:JSON.stringify({impairment_store_ids:ids,edit_version:batch.edit_version,impairment_change_mode:mode})});
            if(!result||!valid(token))return;
            // 先解除本次提交的锁，再由新列表计算分页，不能在load后恢复旧分页状态。
            controls.forEach(([control,disabled])=>control.disabled=disabled);controlsRestored=true;
            busy=false;state.refreshPending=true;await refreshSaved();
        }catch(error){if(valid(token)&&editor===batch)node('edit-status').textContent=t('failed')+error.message;}
        finally{if(valid(token)){busy=false;if(!controlsRestored)controls.forEach(([control,disabled])=>control.disabled=disabled);}}
    }
    async function open(productId, title, onSaved, batchId=null){
        reset();if(!CURRENT_USER_ID||!root())return;
        state={productId:Number(productId),batchId:Number(batchId)||null,editOnLoad:!!batchId,refreshPending:false,page:1,userId:CURRENT_USER_ID,onSaved};const token=epoch;
        root().innerHTML=`<dialog id="ial-dialog" class="product-import-dialog inventory-detail allocation-dialog" aria-labelledby="ial-title ial-product"><div class="inventory-heading allocation-heading"><div><h3 id="ial-title">${h(t('title'))}</h3><p id="ial-product">${h(title)}</p></div><button type="button" class="btn btn-secondary" data-ial-action="close">${h(t('close'))}</button></div><p data-help-tip data-help-anchor="#ial-title">${h(t('help'))}</p>${canWrite()?'':`<p>${h(t('readonly'))}</p>`}<p id="ial-status" class="allocation-status" role="status" aria-live="polite"></p><button id="ial-retry-refresh" type="button" class="btn btn-secondary" data-ial-action="retry-refresh" hidden>${h(t('retryRefresh'))}</button><div class="table-scroll allocation-batches"><table><thead><tr>${['batch','arrival','quantity','remaining','stores','action'].map(key=>`<th>${h(t(key))}</th>`).join('')}</tr></thead><tbody id="ial-rows"></tbody></table></div><div class="inventory-footer allocation-list-footer"><span id="ial-page"></span><div><button id="ial-previous" class="btn btn-secondary" type="button" data-ial-action="previous">${h(t('previous'))}</button> <button id="ial-next" class="btn btn-secondary" type="button" data-ial-action="next">${h(t('next'))}</button> <button type="button" class="btn btn-secondary" data-ial-action="refresh">${h(t('refresh'))}</button></div></div><form id="ial-editor" class="allocation-editor" hidden></form></dialog>`;
        node('dialog').addEventListener('cancel',event=>{if(busy)event.preventDefault();else reset();});
        node('editor').onsubmit=save;
        root().onclick=event=>{
            const button=event.target.closest('button');if(!button||busy)return;
            const action=button.dataset.ialAction;
            if(action==='close'){reset();return;}
            if(refreshing)return;
            if(button.dataset.ialEdit){if(!loading)edit(Number(button.dataset.ialEdit));return;}
            if(action==='cancel'){editor=null;node('editor').hidden=true;node('rows').querySelectorAll('.is-editing').forEach(row=>row.classList.remove('is-editing'));}
            else if(!loading&&action==='retry-refresh')refreshSaved();
            else if(!loading&&action==='refresh'){state.stores=null;if(state.refreshPending)refreshSaved();else load();}
            else if(!loading&&(action==='previous'||action==='next')){state.page+=action==='next'?1:-1;load();}
        };
        node('dialog').showModal();node('status').textContent=t('loading');
        await load();
    }
    window.InventoryAllocations={open,reset};
})();
