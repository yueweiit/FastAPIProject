/* 盘点只保存对照记录；文案集中维护，沿用中文回退。 */
(() => {
    'use strict';
    const messages = {'zh-CN': {
        title:'库存盘点与跌价', overview:'库存总览', daily:'每日进出', keyword:'商品名称 / SKU', date:'截止/盘点日期',
        search:'查询', refresh:'刷新', export:'导出全部匹配记录', product:'商品', inbound:'本月入库', outbound:'本月出库',
        ownOutbound:'本店本月出库', system:'截止日账面库存', unit:'平均单件成本', amount:'库存成本金额', impairment:'期末跌价余额',
        accrual:'本月计提总额', book:'账面价值', countBook:'盘点时账面数', actual:'实盘数量', difference:'盘点差异',
        state:'盘点状态', operation:'操作', enter:'录入实盘', history:'盘点记录', batches:'展开批次', batchDetails:'批次明细', batch:'批次号',
        arrival:'入库日期', age:'入库天数', quantity:'剩余数量', unitCost:'单件成本', stores:'跌价归属 / 分摊',
        save:'保存盘点', cancel:'取消', close:'关闭', note:'备注', uncounted:'未盘点', counted:'已盘点', stale:'账面已变化，需复核',
        saved:'盘点已保存；库存和跌价未调整。', loading:'正在读取…', failed:'操作失败：', empty:'没有符合条件的商品。',
        pending:'待核对', unknownStore:'待分配', allocations:'跌价归属', previous:'上一页', next:'下一页', total:'本页合计',
        page:'共{total}个商品 · 第{page}页', revision:'版本', savedAt:'保存时间（UTC）', actor:'操作人ID',
        help:'表格可左右滚动，录入和记录入口固定在右侧。差异 = 实盘数量 − 盘点时账面数。0是有效实盘，空白表示未盘点；保存仅记录对照，不调整库存、FIFO、成本或跌价。后续补单、更正或撤销会提示账面已变化，旧差异保留。日期按自然日日末核对。',
        restricted:'当前账号仅可查看/盘点今日共享库存；出库及店铺跌价分摊仅含绑定店铺。历史全量盘点由管理员处理。',
        costHelp:'金额均为人民币。库存成本、跌价、账面价值均按系统库存计算，实盘数量不参与。各入库批次独立计算后汇总；规则缺失/异常说明见批次提示。',
        invalid:'实盘数量须为0至2147483647的整数，备注不超过1000字。', noHistory:'暂无盘点记录。',
        fileInvalid:'服务未返回有效的Excel文件。', exported:'导出已生成，请查看下载列表。',
        invalidResponse:'服务返回内容无效，请刷新后重试。', unavailable:'当前服务尚未加载盘点接口，请更新并重启后端后刷新页面。',
        queryFirst:'查询条件已修改，请先查询，再录入盘点、查看记录或导出。',
        inventory_count_schema_required:'盘点记录表尚未准备，请先完成数据库结构准备。',
        inventory_count_conflict:'库存或盘点记录已变化，请保留填写内容，关闭后刷新并核对再保存。',
        inventory_count_untrusted:'库存流水待核对，无法保存可信的盘点差异。',
        inventory_count_date_restricted:'历史全量盘点仅管理员可查看和录入。',
        inventory_future_date:'不能查看未来日期。', inventory_export_empty:'没有符合条件的商品可导出。',
        inventory_assessment_voided_history:'存在本月历史持有的已作废批次，计提总额待核对。',
        inventory_assessment_quantity_mismatch:'逐批数量日结与准备回放不一致，相关成本及跌价待核对。',
        inventory_date_invalid:'请选择1900年及以后的有效日期。',
    }};
    const t=(key,args={})=>Object.entries(args).reduce((value,[name,replacement])=>value.replaceAll(`{${name}}`,String(replacement)),
        (messages[document.documentElement.lang]||messages['zh-CN'])[key]||key);
    const h=value=>escapeHtml(String(value??''));
    const n=value=>value==null?'—':Number(value).toLocaleString(document.documentElement.lang||'zh-CN');
    const money=value=>value==null?t('pending'):Number(value).toLocaleString(document.documentElement.lang||'zh-CN',{minimumFractionDigits:2,maximumFractionDigits:2});
    const today=()=>new Intl.DateTimeFormat('sv-SE',{timeZone:'Asia/Shanghai',year:'numeric',month:'2-digit',day:'2-digit'}).format(new Date());
    const root=()=>document.getElementById('inventory-assessment-root'), node=id=>document.getElementById(`ia-${id}`);
    let serial=0, historySerial=0, busy=false, state=null, editor=null, submission=null, historyProduct=0, historyPage=1;
    const controllers=new Set();
    function reset() {
        window.InventoryAllocations?.reset();
        serial++;historySerial++;controllers.forEach(c=>c.abort());controllers.clear();busy=false;state=null;editor=null;submission=null;
        if(root()){root().querySelectorAll('dialog').forEach(d=>{if(d.open)d.close();});root().replaceChildren();root().hidden=true;}
    }
    const valid=epoch=>epoch===serial&&!!CURRENT_USER_ID&&!!state;
    async function request(url, options={}, file=false) {
        const epoch=serial, controller=new AbortController();controllers.add(controller);
        try {
            const response=await fetch(url,{credentials:'same-origin',...options,headers:{...authHeaders(),...(options.headers||{})},signal:controller.signal});
            if(!valid(epoch))return null;
            if(response.status===401){handleSessionExpired();return null;}
            if(file&&response.ok){
                if(!response.headers.get('content-type')?.includes('application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'))throw new Error(t('fileInvalid'));
                const blob=await response.blob();if(!valid(epoch))return null;
                if(!blob.size)throw new Error(t('fileInvalid'));
                return blob;
            }
            let body;
            try{body=await response.json();}catch(error){if(!valid(epoch))return null;throw new Error(t('invalidResponse'));}
            if(!valid(epoch))return null;
            if(response.status===404&&body?.detail==='Not Found')throw new Error(t('unavailable'));
            if(!response.ok){const code=body?.detail?.code;throw new Error(code&&t(code)!==code?t(code):formatApiError(body?.detail,String(response.status)));}
            return body;
        }finally{controllers.delete(controller);}
    }
    function fields() {
        return ['product','inbound',isAdmin()?'outbound':'ownOutbound','system','unit','amount','impairment','accrual','book','countBook','actual','difference','state','operation'];
    }
    function initialize() {
        root().innerHTML=`<div class="card inventory-workspace"><div class="inventory-heading"><h3>${h(t('title'))}</h3><button type="button" data-ia-action="export" class="btn btn-secondary">${h(t('export'))}</button></div>
          <div class="inventory-tabs"><button type="button" class="btn btn-secondary" data-ia-action="overview">${h(t('overview'))}</button><button type="button" class="btn btn-secondary" data-ia-action="daily">${h(t('daily'))}</button><button type="button" class="btn btn-primary" aria-pressed="true">${h(t('title'))}</button></div>
          <form id="ia-query" class="inventory-filters"><label class="form-group">${h(t('keyword'))}<input id="ia-keyword" type="search" maxlength="255"></label><label class="form-group">${h(t('date'))}<input id="ia-date" type="date" min="1900-01-01" max="${today()}" required></label><button class="btn btn-primary">${h(t('search'))}</button><button type="button" class="btn btn-secondary" data-ia-action="refresh">${h(t('refresh'))}</button></form>
          <p class="inventory-note">${h(t('help'))}</p><p class="inventory-note">${h(t('costHelp'))}</p><p class="inventory-note">${h(window.InventoryWorkspace.text('inventory_current_documents'))}</p><p class="inventory-note">${isAdmin()?'':h(t('restricted'))}</p><p id="ia-status" class="inventory-status" role="status" aria-live="polite"></p>
          <div class="inventory-table-scroll"><table class="inventory-table" id="ia-table"><thead><tr>${fields().map(key=>`<th>${h(t(key))}</th>`).join('')}</tr></thead><tbody id="ia-body"></tbody><tfoot id="ia-total"></tfoot></table></div>
          <div class="inventory-footer"><span id="ia-page"></span><div><button type="button" data-ia-action="previous" class="btn btn-secondary">${h(t('previous'))}</button><button type="button" data-ia-action="next" class="btn btn-secondary">${h(t('next'))}</button></div></div>
          <dialog id="ia-edit" class="product-import-dialog inventory-detail"><form id="ia-edit-form" novalidate><h3 id="ia-edit-title"></h3><p id="ia-edit-basis" class="inventory-note"></p><label class="form-group">${h(t('actual'))}<input id="ia-actual" type="number" min="0" max="2147483647" step="1" required></label><p id="ia-preview" class="inventory-note"></p><label class="form-group">${h(t('note'))}<textarea id="ia-note" maxlength="1000"></textarea></label><p id="ia-edit-status" role="status" aria-live="polite"></p><button class="btn btn-primary" id="ia-save">${h(t('save'))}</button><button type="button" class="btn btn-secondary" data-ia-action="cancel">${h(t('cancel'))}</button></form></dialog>
          <dialog id="ia-batches" class="product-import-dialog inventory-detail"><h3 id="ia-batch-title"></h3><div id="ia-batch-body" class="table-scroll"></div><button class="btn btn-secondary" type="button" data-ia-action="close-batches">${h(t('close'))}</button></dialog>
          <dialog id="ia-history" class="product-import-dialog inventory-detail"><h3>${h(t('history'))}</h3><p id="ia-history-status" role="status"></p><div class="table-scroll"><table><thead><tr>${['revision','countBook','actual','difference','savedAt','actor','note'].map(k=>`<th>${h(t(k))}</th>`).join('')}</tr></thead><tbody id="ia-history-body"></tbody></table></div><button class="btn btn-secondary" type="button" data-ia-action="history-previous">${h(t('previous'))}</button><button class="btn btn-secondary" type="button" data-ia-action="history-next">${h(t('next'))}</button><button class="btn btn-secondary" type="button" data-ia-action="close-history">${h(t('close'))}</button></dialog></div>`;
        node('query').onsubmit=event=>{event.preventDefault();if(busy)return;const keyword=node('keyword').value.trim();if(keyword!==state.keyword)state.productId=0;state.keyword=keyword;state.asOf=node('date').value;state.page=1;load();};
        node('actual').oninput=()=>{node('preview').textContent=`${t('difference')}: ${node('actual').value===''?'—':n(Number(node('actual').value)-editor.system_quantity)}`;};
        node('edit-form').onsubmit=event=>{event.preventDefault();save();};
        node('edit').addEventListener('cancel',event=>{if(busy)event.preventDefault();else editor=null;});
        node('history').addEventListener('close',()=>historySerial++);
        root().onclick=event=>{
            const button=event.target.closest('button');if(!button||busy)return;
            if((button.dataset.iaEdit||button.dataset.iaHistory||button.dataset.iaAction==='export')&&
                (node('date').value!==state.asOf||node('keyword').value.trim()!==state.keyword)){
                node('status').textContent=t('queryFirst');return;
            }
            if(button.dataset.iaBatch){node('batch-title').textContent=`${t('batchDetails')} · ${state.data.items.find(row=>row.product_id===Number(button.dataset.iaBatch)).sku} · ${state.asOf}`;node('batch-body').innerHTML=node(`batches-${button.dataset.iaBatch}`).innerHTML;node('batches').showModal();return;}
            if(button.dataset.iaEdit){openEditor(Number(button.dataset.iaEdit));return;}
            if(button.dataset.iaAllocation){
                const row=state.data.items.find(item=>item.product_id===Number(button.dataset.iaAllocation));
                if(row)window.InventoryAllocations?.open(row.product_id,`${row.sku} · ${row.name}`,()=>load());
                return;
            }
            if(button.dataset.iaHistory){historyProduct=Number(button.dataset.iaHistory);historyPage=1;node('history').showModal();history();return;}
            const action=button.dataset.iaAction;
            if(action==='overview'||action==='daily'){reset();window.InventoryWorkspace.show(action);}
            else if(action==='refresh')load();
            else if(action==='previous'||action==='next'){state.page+=action==='next'?1:-1;load();}
            else if(action==='cancel'){node('edit').close();editor=null;}
            else if(action==='export')download();
            else if(action==='close-batches')node('batches').close();
            else if(action==='close-history')node('history').close();
            else if(action==='history-previous'||action==='history-next'){historyPage+=action==='history-next'?1:-1;history();}
        };
    }
    function render() {
        const rows=state.data.items;
        node('body').innerHTML=rows.length?rows.map(row=>{
            const count=row.count||{};
            const batches=row.batches.map(batch=>`<tr><td>${h(batch.batch_no)}</td><td>${h(batch.arrived_at)}</td><td>${h(n(batch.age_days))}</td><td>${h(n(batch.quantity))}</td><td>${h(money(batch.unit_cost))}</td><td title="${h(batch.note)}">${h(money(batch.impairment_amount))}</td><td>${h(batch.store_names.join(' / ')||t('unknownStore'))}${batch.store_allocations.map(owner=>`<div>${h(owner.store_name)}: ${h(money(owner.impairment_amount))}</div>`).join('')}</td></tr>`).join('');
            const warnings=(row.warnings||[]).map(code=>t(code)!==code?t(code):window.InventoryWorkspace.text(code)).join('；');
            return `<tr><td><strong>${h(row.sku)}</strong><div>${h(row.name)}</div>${warnings?`<span class="tag tag-orange" title="${h(warnings)}">${h(t('pending'))}</span>`:''}<button type="button" class="btn btn-secondary" data-ia-batch="${row.product_id}">${h(t('batches'))}</button><template id="ia-batches-${row.product_id}"><table><thead><tr>${['batch','arrival','age','quantity','unitCost','impairment','stores'].map(key=>`<th>${h(t(key))}</th>`).join('')}</tr></thead><tbody>${batches}</tbody></table></template></td>${['month_inbound','month_outbound','system_quantity'].map(k=>`<td>${h(n(row[k]))}</td>`).join('')}${['average_unit_cost','inventory_amount','impairment_amount','monthly_total_impairment_amount','book_value'].map(k=>`<td>${h(money(row[k]))}</td>`).join('')}<td>${h(n(count.system_quantity))}</td><td>${h(n(count.actual_quantity))}</td><td>${h(n(count.difference))}</td><td>${h(t(row.count_basis_changed?'stale':row.count?'counted':'uncounted'))}</td><td>${state.data.can_write&&row.system_quantity!=null?`<button class="btn btn-secondary" data-ia-edit="${row.product_id}">${h(t('enter'))}</button>`:''}<button class="btn btn-secondary" data-ia-history="${row.product_id}">${h(t('history'))}</button><button class="btn btn-secondary" data-ia-allocation="${row.product_id}">${h(t('allocations'))}</button></td></tr>`;
        }).join(''):`<tr><td colspan="14">${h(t('empty'))}</td></tr>`;
        const sums=['month_inbound','month_outbound','system_quantity','average_unit_cost','inventory_amount','impairment_amount','monthly_total_impairment_amount','book_value'];
        node('total').innerHTML=`<tr><td>${h(t('total'))}</td>${sums.map((key,i)=>{
            if(key==='average_unit_cost')return '<td>—</td>';
            const values=rows.map(row=>row[key]),value=values.some(v=>v==null)?null:values.reduce((a,b)=>a+Number(b),0);
            return `<td>${h(i<3?n(value):money(value))}</td>`;
        }).join('')}<td colspan="5"></td></tr>`;
        node('page').textContent=t('page',{total:state.data.total,page:state.data.page});
        root().querySelector('[data-ia-action="previous"]').disabled=state.page<=1;
        root().querySelector('[data-ia-action="next"]').disabled=state.page*20>=state.data.total;
    }
    async function load() {
        if(busy)return;const epoch=++serial;state.data=null;node('status').textContent=t('loading');node('body').innerHTML='';node('total').innerHTML='';
        node('edit').close();node('history').close();node('batches').close();editor=null;
        const params=new URLSearchParams({as_of:state.asOf,keyword:state.keyword,page:state.page,page_size:20});
        if(state.productId)params.set('product_id',state.productId);
        try{const data=await request(`/inventory/assessment?${params}`);if(!data||!valid(epoch))return;state.data=data;state.page=data.page;node('status').textContent='';render();}
        catch(error){if(valid(epoch))node('status').textContent=t('failed')+error.message;}
    }
    function openEditor(productId) {
        submission=null;
        editor=state.data.items.find(row=>row.product_id===productId);
        node('edit-title').textContent=`${t('enter')} · ${editor.sku} · ${state.asOf}`;
        node('edit-basis').textContent=`${t('countBook')}: ${n(editor.system_quantity)}`;
        node('actual').value=editor.count?.actual_quantity??'';node('note').value=editor.count?.note??'';
        node('edit-status').textContent='';node('actual').oninput();node('edit').showModal();node('actual').focus();
    }
    async function save() {
        if(busy||!editor)return;
        const actual=Number(node('actual').value);
        if(node('actual').value===''||!Number.isInteger(actual)||actual<0||actual>2147483647||node('note').value.length>1000){node('edit-status').textContent=t('invalid');return;}
        const epoch=serial,row=editor;
        const controls=[...node('edit-form').querySelectorAll('input,textarea,button')].map(control=>[control,control.disabled]);
        busy=true;controls.forEach(([control])=>control.disabled=true);node('edit-status').textContent=t('loading');
        try{
            const payload={product_id:row.product_id,count_date:state.asOf,actual_quantity:actual,note:node('note').value,
                ledger_version:row.ledger_version,previous_id:row.count?.id??null};
            const signature=JSON.stringify(payload);
            if(submission?.signature!==signature)submission={signature,key:globalThis.crypto?.randomUUID?.()||`count_${Date.now().toString(36)}_${Math.random().toString(36).slice(2)}_${Math.random().toString(36).slice(2)}`};
            payload.request_key=submission.key;
            const result=await request('/inventory/stock-counts',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload)});
            if(!result||!valid(epoch)||editor!==row)return;row.count=result;row.count_basis_changed=false;render();node('edit').close();editor=null;node('status').textContent=t('saved');}
        catch(error){if(valid(epoch)&&editor===row)node('edit-status').textContent=t('failed')+error.message;}
        finally{if(valid(epoch)){busy=false;controls.forEach(([control,disabled])=>control.disabled=disabled);}}
    }
    async function history() {
        const epoch=serial,revision=++historySerial;
        node('history-status').textContent=t('loading');node('history-body').innerHTML='';
        try{const data=await request(`/inventory/stock-counts?${new URLSearchParams({product_id:historyProduct,count_date:state.asOf,page:historyPage,page_size:20})}`);
            if(!data||!valid(epoch)||revision!==historySerial||!node('history').open)return;
            historyPage=data.page;node('history-status').textContent=data.items.length?'':t('noHistory');
            node('history-body').innerHTML=data.items.map(row=>`<tr>${[row.revision,row.system_quantity,row.actual_quantity,row.difference,row.created_at,row.created_by,row.note].map(value=>`<td>${h(value)}</td>`).join('')}</tr>`).join('');
            root().querySelector('[data-ia-action="history-previous"]').disabled=historyPage<=1;
            root().querySelector('[data-ia-action="history-next"]').disabled=historyPage*20>=data.total;
        }catch(error){if(valid(epoch)&&revision===historySerial)node('history-status').textContent=t('failed')+error.message;}
    }
    async function download() {
        if(busy||!state.data?.items.length)return;
        if(node('date').value!==state.asOf||node('keyword').value.trim()!==state.keyword){node('status').textContent=t('queryFirst');return;}
        const epoch=serial;busy=true;node('status').textContent=t('loading');
        const params=new URLSearchParams({as_of:state.asOf,keyword:state.keyword});if(state.productId)params.set('product_id',state.productId);
        try{const blob=await request(`/inventory/assessment/export?${params}`,{},true);if(!blob||!valid(epoch))return;
            const url=URL.createObjectURL(blob),a=document.createElement('a');a.href=url;a.download=`inventory_assessment_${state.asOf.replaceAll('-','')}.xlsx`;
            document.body.appendChild(a);a.click();a.remove();setTimeout(()=>URL.revokeObjectURL(url),60000);node('status').textContent=t('exported');}
        catch(error){if(valid(epoch))node('status').textContent=t('failed')+error.message;}
        finally{if(valid(epoch))busy=false;}
    }
    async function open(filters={}) {
        if(!state){state={asOf:today(),keyword:filters.keyword||'',productId:filters.productId||0,page:1,data:null};initialize();}
        root().hidden=false;node('keyword').value=state.keyword;node('date').value=state.asOf;
        if(!isAdmin()){state.asOf=today();node('date').value=state.asOf;node('date').readOnly=true;}
        return load();
    }
    window.InventoryAssessment={open,reset,text:t};
})();
