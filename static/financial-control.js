/* 原生页面财务工作台。文案集中维护，默认zh-CN；不改变全站语言机制。 */
(() => {
    'use strict';
    const messages = {'zh-CN': {
        nav: '成本、费用与核对', title: '成本、费用与月度版本', store: '店铺', month: '核算月份', refresh: '查询 / 刷新',
        disabled: '财务增强功能尚未启用，请联系管理员完成结构核验后启用。', loading: '正在加载…', empty: '暂无记录',
        cost: '批次实际成本补差', costHelp: '填写本次差额，不是整张账单总额。已售部分追溯原销售月，未售部分加入库存；原确认版本保留。',
        source: '来源凭证号（含供应商 / 单号）', amount: '本次差额 / 金额', currency: '原币', rate: '人民币汇率（外币必填）',
        date: '凭证日期', rateDate: '汇率日期', category: '科目', purchase: '采购成本', head_shipping: '头程运费', last_mile: '尾程运费', other: '其他入库成本',
        basis: '批次间分摊依据', quantity: '原入库件数', weight: '重量', volume: '体积', value: '金额',
        batch: '批次ID', allocationWeight: '分摊依据数值（按件数时自动）', addBatch: '增加批次', remove: '移除',
        final: '该科目已按实际凭证核对（零差额也可确认原金额）', note: '原因 / 凭证说明', preview: '预览分摊', postCost: '确认补差',
        sold: '已售部分', remaining: '未售部分', costList: '已确认成本调整', reverse: '追加反向单', reason: '请输入更正原因',
        expense: '费用明细与来源', expenseHelp: '新增明细默认待确认。只有选择“使用明细”才替换该月该科目的人工总额；不与原人工金额相加。',
        expenseDate: '费用发生日', linked: '已在平台结算扣除的结算行ID（选填，仅作核对，不再扣费）', createExpense: '保存待确认费用',
        ledger: '使用明细', manual: '使用人工总额', sourceMode: '该月科目来源', changeSource: '保存来源选择',
        draft: '待确认', posted: '已确认', cancelled: '已作废', confirm: '确认费用', cancel: '作废草稿', status: '状态',
        reconciliation: '平台账单分类与回款核对', reconciliationHelp: '分类只拆解已计入的结算差额，不再次计费。回款按付款ID跨月、按原币核对，差额不会自动补平。',
        payout: '付款ID', settlement: '结算金额', received: '实际回款', difference: '差额', entry: '结算行', unclassified: '未分类差额',
        classification: '确认差额分类', receiptDate: '到账日期', receipt: '记录实际回款', receipts: '回款凭证',
        versions: '月度结果版本', versionHelp: '完整自然月结束后可保存版本；旧版不覆盖。待核对事项会随版本保留，补录后可比较并生成新版。',
        capture: '保存本月新版本', version: '版本', confirmed: '已确认', needs_review: '待核对', created: '保存时间',
        compare: '与当前数据比较', export: '导出该版本', unchanged: '当前数据与该版本一致', changed: '来源或结果已变化，旧版本保持不变',
        before: '旧版', after: '当前', current: '本月', previous: '上月', ytd: '本年累计',
        afterInventory: '商品成本后余额', afterPlatform: '平台费用后余额', netProfit: '经营净利润',
        layerHelp: '前两项用于经营核对：商品净收入减批次成本，再减平台差额费用；正式财务毛利、净利口径以损益表为准。',
        success: '已保存；历史已确认版本保持不变', pending: '请等待当前操作完成', chooseStore: '请选择店铺和月份',
        chooseBatch: '请至少选择一个批次', requiresPreview: '请先预览当前内容', failed: '加载失败，请重试', more: '当前仅显示第一页，完整金额以报表为准',
        previewChanged: '表单已改变，请重新预览', receiptAmount: '实际到账金额', billRows: '所选月结算行（保留平台原始金额）',
        noSource: '该月尚未选择明细来源，默认使用人工总额', sourceLinked: '仅核对', actual: '实际', unverified: '未核对',
        randomUnavailable: '当前浏览器不支持安全请求标识，请升级浏览器后重试。',
        rawComponents: '平台原始科目', net_shipping: '净运费', taxes: '税款', tax_withholding: '代扣税',
        platform_commission: '平台佣金', service_fee: '服务费', sfp_service_fee: 'SFP服务费', per_item_fee: '按件费用',
        affiliate_commission: '联盟佣金', creator_commission: '达人佣金', creator_shop_ad_commission: '达人店铺广告佣金',
        affiliate_shop_ad_commission: '联盟店铺广告佣金', gmv_max_ad_fee: 'GMV Max广告费用', adjustment_amount: '调整金额',
    }};
    const ft = key => (messages[document.documentElement.lang] || messages['zh-CN'])[key] || key;
    const esc = value => escapeHtml(String(value ?? ''));
    const money = value => value == null ? '—' : Number(value).toLocaleString('zh-CN', {minimumFractionDigits: 2, maximumFractionDigits: 4});
    const state = {serial: 0, busy: false, config: null, initialized: false, preview: null, keys: {}};
    const el = id => document.getElementById(`fc-${id}`);
    const uid = () => {
        const random = globalThis.crypto;
        if (typeof random?.randomUUID === 'function') return random.randomUUID();
        if (typeof random?.getRandomValues !== 'function') throw new Error(ft('randomUnavailable'));
        const bytes = random.getRandomValues(new Uint8Array(16));
        bytes[6] = (bytes[6] & 0x0f) | 0x40;
        bytes[8] = (bytes[8] & 0x3f) | 0x80;
        const hex = Array.from(bytes, value => value.toString(16).padStart(2, '0')).join('');
        return `${hex.slice(0,8)}-${hex.slice(8,12)}-${hex.slice(12,16)}-${hex.slice(16,20)}-${hex.slice(20)}`;
    };
    const key = name => state.keys[name] || (state.keys[name] = uid());
    const input = (name, label, type = 'text', extra = '') => `<label>${esc(label)}<input name="${name}" type="${type}" ${extra}></label>`;
    const select = (name, label, options) => `<label>${esc(label)}<select name="${name}">${Object.entries(options).map(([k,v]) => `<option value="${esc(k)}">${esc(v)}</option>`).join('')}</select></label>`;
    const button = (action, label, attrs = '') => `<button type="button" class="btn btn-secondary" data-action="${action}" ${attrs}>${esc(label)}</button>`;
    const table = (headers, rows) => `<div class="table-scroll"><table><thead><tr>${headers.map(h=>`<th>${esc(h)}</th>`).join('')}</tr></thead><tbody>${rows.join('') || `<tr><td colspan="${headers.length}">${esc(ft('empty'))}</td></tr>`}</tbody></table></div>`;
    function context() {
        const store_id = Number(el('store')?.value), month = el('month')?.value;
        if (!store_id || !/^\d{4}-\d{2}$/.test(month || '')) throw new Error(ft('chooseStore'));
        return {store_id, report_month: `${month}-01`};
    }
    const query = () => new URLSearchParams(context()).toString();
    const selection = () => `${el('store')?.value}|${el('month')?.value}`;
    function notice(value, error = false) {
        if (el('notice')) { el('notice').textContent = value; el('notice').style.color = error ? '#b42318' : '#475569'; }
    }
    function permission() {
        el('cost-form').hidden = !state.config.can_write;
        el('expense-form').hidden = !state.config.can_write;
        el('source-form').hidden = !state.config.can_write;
        el('receipt-form').hidden = !state.config.can_write;
        el('classification-form').hidden = !state.config.can_write;
        el('version-form').hidden = !state.config.can_write;
    }
    function initialize() {
        const root = document.getElementById('financial-control-root');
        root.innerHTML = `<h3>${esc(ft('title'))}</h3><div class="form-row"><label>${esc(ft('store'))}<select id="fc-store"></select></label><label>${esc(ft('month'))}<input id="fc-month" type="month"></label>${button('refresh',ft('refresh'))}</div><p id="fc-notice" role="status"></p><div id="fc-body" hidden>
        <div id="fc-overview"></div><details open><summary>${esc(ft('cost'))}</summary><p>${esc(ft('costHelp'))}</p>
        <form id="fc-cost-form"><div class="form-row">${input('source_ref',ft('source'),'text','required maxlength="128"')}${select('category',ft('category'),Object.fromEntries(['purchase','head_shipping','last_mile','other'].map(k=>[k,ft(k)])))}${input('amount',ft('amount'),'text','required inputmode="decimal"')}${input('currency',ft('currency'),'text','value="CNY" required pattern="[A-Z]{3}"')}${input('exchange_rate',ft('rate'),'text','value="1" required inputmode="decimal"')}${input('document_date',ft('date'),'date','required')}${input('rate_date',ft('rateDate'),'date','required')}${select('allocation_basis',ft('basis'),Object.fromEntries(['quantity','weight','volume','value'].map(k=>[k,ft(k)])))}</div><div id="fc-allocations"></div><datalist id="fc-batches"></datalist>${button('add-batch',ft('addBatch'))}<label><input name="is_final" type="checkbox">${esc(ft('final'))}</label>${input('note',ft('note'),'text','maxlength="1000"')}<div>${button('preview-cost',ft('preview'))}${button('post-cost',ft('postCost'))}</div></form><div id="fc-cost-preview"></div><h4>${esc(ft('costList'))}</h4><div id="fc-cost-list"></div></details>
        <details><summary>${esc(ft('expense'))}</summary><p>${esc(ft('expenseHelp'))}</p><form id="fc-expense-form"><div class="form-row">${select('category',ft('category'),state.config.expense_categories)}${input('expense_date',ft('expenseDate'),'date','required')}${input('source_ref',ft('source'),'text','required maxlength="128"')}${input('amount',ft('amount'),'text','required inputmode="decimal"')}${input('currency',ft('currency'),'text','value="CNY" required pattern="[A-Z]{3}"')}${input('exchange_rate',ft('rate'),'text','value="1" required')}${input('settlement_entry_id',ft('linked'),'number','min="1"')}${input('note',ft('note'),'text','maxlength="1000"')}</div>${button('create-expense',ft('createExpense'))}</form>
        <form id="fc-source-form"><div class="form-row">${select('category',ft('category'),state.config.expense_categories)}${select('mode',ft('sourceMode'),{manual:ft('manual'),ledger:ft('ledger')})}${button('select-source',ft('changeSource'))}</div></form><p id="fc-expense-sources"></p><div id="fc-expenses"></div></details>
        <details><summary>${esc(ft('reconciliation'))}</summary><p>${esc(ft('reconciliationHelp'))}</p><div id="fc-payouts"></div><h4>${esc(ft('billRows'))}</h4><div id="fc-entries"></div><form id="fc-classification-form"><div class="form-row"><label>${esc(ft('entry'))}<select name="entry_id" id="fc-classification-entry"></select></label>${Object.entries(state.config.breakdown_categories).map(([k,v])=>input(k,v,'text','value="0" inputmode="decimal"')).join('')}${input('note',ft('note'),'text','maxlength="1000"')}${button('classify',ft('classification'))}</div></form><form id="fc-receipt-form"><div class="form-row">${input('payout_id',ft('payout'),'text','required maxlength="128"')}${input('receipt_date',ft('receiptDate'),'date','required')}${input('source_ref',ft('source'),'text','required maxlength="128"')}${input('amount',ft('receiptAmount'),'text','required inputmode="decimal"')}${input('currency',ft('currency'),'text','value="CNY" required pattern="[A-Z]{3}"')}${input('exchange_rate',ft('rate'),'text','value="1" required')}${input('note',ft('note'),'text','maxlength="1000"')}${button('create-receipt',ft('receipt'))}</div></form><h4>${esc(ft('receipts'))}</h4><div id="fc-receipts"></div></details>
        <details><summary>${esc(ft('versions'))}</summary><p>${esc(ft('versionHelp'))}</p><form id="fc-version-form">${input('note',ft('note'),'text','maxlength="1000"')}${button('capture',ft('capture'))}</form><div id="fc-versions"></div><div id="fc-comparison"></div></details></div>`;
        root.querySelectorAll('label').forEach(label=>{label.style.display='inline-flex';label.style.flexDirection='column';label.style.gap='4px';label.style.margin='6px';});
        root.querySelectorAll('details').forEach(item=>{item.style.borderTop='1px solid #e2e8f0';item.style.padding='16px 0';});
        root.querySelectorAll('summary').forEach(item=>{item.style.cursor='pointer';item.style.fontWeight='700';});
        root.onsubmit = event=>event.preventDefault();
        root.onclick = event=>{
            const target = event.target.closest('[data-action]');
            if (target) dispatch(target.dataset.action, target).catch(e=>notice(e.message,true));
        };
        root.oninput = event=>{
            const form = event.target.closest('form');
            if (form && !state.busy) { delete state.keys[form.id]; if (form.id==='fc-cost-form') {state.preview=null;el('cost-preview').textContent='';} }
            if (event.target.name==='currency') {
                const rate=form?.elements.namedItem('exchange_rate');
                if (rate) rate.value=event.target.value==='CNY'?'1':'';
            }
        };
        root.onchange = event=>{
            if(event.target.id==='fc-classification-entry') fillClassification();
        };
        el('store').addEventListener('change',()=>{state.serial++;state.preview=null;refresh().catch(e=>notice(e.message,true));});
        el('month').addEventListener('change',()=>{state.serial++;state.preview=null;setExpenseDate();refresh().catch(e=>notice(e.message,true));});
        addBatch(); state.initialized=true;
    }
    function addBatch() {
        const row=document.createElement('div'); row.className='form-row';row.dataset.allocation='1';
        row.innerHTML=input('batch_id',ft('batch'),'text','list="fc-batches" required inputmode="numeric"')+input('weight',ft('allocationWeight'),'text','inputmode="decimal"')+button('remove-batch',ft('remove'));
        el('allocations').appendChild(row);
    }
    function data(formId) {
        const form=el(formId); if(!form.reportValidity()) return null;
        return Object.fromEntries(new FormData(form));
    }
    function costPayload() {
        const value=data('cost-form'); if(!value) return null;
        const allocations=[...el('allocations').querySelectorAll('[data-allocation]')].map(row=>({batch_id:Number(row.querySelector('[name=batch_id]').value),weight:row.querySelector('[name=weight]').value || null}));
        if(!allocations.length || allocations.some(row=>!Number.isInteger(row.batch_id)||row.batch_id<=0)) throw new Error(ft('chooseBatch'));
        delete value.batch_id;delete value.weight;
        return {...value, request_key:key('fc-cost-form'), is_final:el('cost-form').elements.is_final.checked, allocations};
    }
    async function write(formId, method, path, payload) {
        if(state.busy) throw new Error(ft('pending'));
        const serial=state.serial, scope=selection(), root=document.getElementById('financial-control-root');
        const fields=[...root.querySelectorAll('input,select,button,textarea')].map(node=>[node,node.disabled]);
        state.busy=true;fields.forEach(([node])=>node.disabled=true);
        try {
            await api(method,`/financial-control${path}`,payload);
            if(serial!==state.serial || scope!==selection()) return;
            delete state.keys[formId];state.preview=null;
            await refresh();notice(ft('success'));
        } finally {state.busy=false;fields.forEach(([node,disabled])=>node.disabled=disabled);}
    }
    async function refresh() {
        const serial=++state.serial, scope=selection(), suffix=query();
        notice(ft('loading'));el('comparison').textContent='';
        for(const id of ['overview','expenses','expense-sources','payouts','entries','receipts','versions','cost-list','cost-preview'])el(id).textContent='';
        el('classification-entry').innerHTML='';state.entries=[];
        const tasks={overview:`overview?${suffix}`,expenses:`expenses?${suffix}`,reconciliation:`reconciliation?${suffix}`,versions:`versions?${suffix}`,costs:'cost-adjustments?limit=50'};
        const results=await Promise.allSettled(Object.entries(tasks).map(async([name,path])=>[name,await api('GET',`/financial-control/${path}`)]));
        if(serial!==state.serial || scope!==selection()) return;
        const failures=[];
        for(const result of results) {
            if(result.status==='rejected'){failures.push(result.reason.message);continue;}
            const [name,value]=result.value;
            if(name==='overview') renderOverview(value);
            if(name==='expenses') renderExpenses(value);
            if(name==='reconciliation') renderReconciliation(value);
            if(name==='versions') renderVersions(value);
            if(name==='costs') renderCosts(value);
        }
        notice(failures.join('；'),failures.length>0);
    }
    function renderOverview(value) {
        el('overview').innerHTML=table([ft('afterInventory'),ft('afterPlatform'),ft('netProfit')],[`<tr>${Object.values(value.profit_layers).map(v=>`<td>${esc(money(v))}</td>`).join('')}</tr>`])+`<p>${esc(ft('layerHelp'))}</p>`+value.warnings.map(w=>`<p style="color:#9a6700">${esc(w)}</p>`).join('');
    }
    function renderCosts(rows) {
        el('cost-list').innerHTML=table(['ID',ft('source'),ft('category'),ft('amount'),ft('batch'),ft('created')],rows.map(row=>`<tr><td>${row.id}</td><td>${esc(row.source_ref)}</td><td>${esc(ft(row.category))}</td><td>${esc(money(row.amount_cny))}</td><td>${esc(row.allocations.map(x=>x.batch_id).join(', '))}</td><td>${esc(row.created_at)} ${state.config.can_write&&!row.reverses_id?button('reverse-cost',ft('reverse'),`data-id="${row.id}"`):''}</td></tr>`));
    }
    function renderExpenses(value) {
        el('expense-sources').textContent=Object.entries(value.sources).map(([key,mode])=>`${state.config.expense_categories[key]||key}：${ft(mode)}`).join('；') || ft('noSource');
        el('expenses').innerHTML=table(['ID',ft('expenseDate'),ft('category'),ft('source'),ft('amount'),ft('status')],value.items.map(row=>`<tr><td>${row.id}</td><td>${esc(row.expense_date)}</td><td>${esc(state.config.expense_categories[row.category]||row.category)}</td><td>${esc(row.source_ref)}</td><td>${esc(money(row.amount_cny))}${row.settlement_entry_id?` (${esc(ft('sourceLinked'))})`:''}</td><td>${esc(ft(row.status))} ${row.status==='draft'&&state.config.can_post?button('post-expense',ft('confirm'),`data-id="${row.id}"`):''} ${row.status==='draft'&&state.config.can_write?button('cancel-expense',ft('cancel'),`data-id="${row.id}"`):''} ${row.status==='posted'&&!row.reverses_id&&state.config.can_post?button('reverse-expense',ft('reverse'),`data-id="${row.id}"`):''}</td></tr>`))+(value.total>value.items.length?`<p>${esc(ft('more'))} (${value.total})</p>`:'');
    }
    function renderReconciliation(value) {
        el('payouts').innerHTML=table([ft('payout'),ft('currency'),ft('settlement'),ft('received'),ft('difference')],value.payouts.map(row=>`<tr><td>${esc(row.payout_id)}</td><td>${esc(row.currency)}</td><td>${esc(money(row.settlement_amount))}</td><td>${esc(money(row.received_amount))}</td><td>${esc(money(row.difference))}</td></tr>`));
        el('entries').innerHTML=table(['ID',ft('source'),ft('currency'),ft('settlement'),ft('unclassified'),ft('rawComponents')],value.entries.map(row=>`<tr><td>${row.id}</td><td>${esc(row.source_file)} / ${row.source_row_number}</td><td>${esc(row.currency)}</td><td>${esc(money(row.settlement_total))}</td><td>${esc(money(row.unclassified_difference))}</td><td><details><summary>${esc(ft('rawComponents'))}</summary>${Object.entries(row.raw_components).map(([k,v])=>`${esc(ft(k))}: ${esc(money(v))}`).join('<br>')}</details></td></tr>`));
        el('classification-entry').innerHTML=value.entries.filter(row=>row.can_classify).map(row=>`<option value="${row.id}">${row.id} / ${esc(row.order_id)} / ${esc(money(row.expense_difference))} ${esc(row.currency)}</option>`).join('');
        state.entries=value.entries;fillClassification();
        el('receipts').innerHTML=table(['ID',ft('payout'),ft('receiptDate'),ft('currency'),ft('amount')],value.receipts.map(row=>`<tr><td>${row.id}</td><td>${esc(row.payout_id)}</td><td>${esc(row.receipt_date)}</td><td>${esc(row.currency)}</td><td>${esc(money(row.amount))} ${state.config.can_write&&!row.reverses_id?button('reverse-receipt',ft('reverse'),`data-id="${row.id}"`):''}</td></tr>`));
    }
    function renderVersions(rows) {
        el('versions').innerHTML=table([ft('version'),ft('status'),ft('created'),ft('note')],rows.map(row=>`<tr><td>v${row.version}</td><td>${esc(ft(row.status))}</td><td>${esc(row.created_at)}</td><td>${esc(row.note)} ${button('compare',ft('compare'),`data-id="${row.id}"`)} ${button('export',ft('export'),`data-id="${row.id}"`)}</td></tr>`));
    }
    function fillClassification() {
        const row=state.entries?.find(row=>String(row.id)===el('classification-entry').value);
        for(const category of Object.keys(state.config.breakdown_categories))el('classification-form').elements.namedItem(category).value=row?.breakdown?.[category] || '0';
        el('classification-form').elements.namedItem('note').value='';
    }
    function setExpenseDate() {
        const current=new Date().toLocaleDateString('sv-SE'),month=el('month').value;
        if(month)el('expense-form').elements.namedItem('expense_date').value=current.startsWith(month)?current:`${month}-01`;
        delete state.keys['fc-expense-form'];
    }
    async function dispatch(action,target) {
        if(action==='refresh') return refresh();
        if(action==='add-batch') {state.preview=null;delete state.keys['fc-cost-form'];return addBatch();}
        if(action==='remove-batch') {target.closest('[data-allocation]').remove();state.preview=null;delete state.keys['fc-cost-form'];return;}
        if(action==='preview-cost') {
            const payload=costPayload();if(!payload)return;
            const serial=state.serial,signature=JSON.stringify(payload);
            const value=await api('POST','/financial-control/cost-adjustments/preview',payload);
            if(serial!==state.serial || signature!==JSON.stringify(costPayload()))return;
            state.preview=signature;
            el('cost-preview').innerHTML=table([ft('batch'),ft('amount'),ft('sold'),ft('remaining')],value.allocations.map(row=>`<tr><td>${esc(row.batch_no)}</td><td>${esc(money(row.amount_cny))}</td><td>${esc(money(row.sold_amount_cny))}</td><td>${esc(money(row.remaining_amount_cny))}</td></tr>`));return;
        }
        if(action==='post-cost') {
            const payload=costPayload();if(!payload)return;
            if(state.preview!==JSON.stringify(payload))throw new Error(ft('requiresPreview'));
            return write('fc-cost-form','POST','/cost-adjustments',payload);
        }
        if(action.startsWith('reverse-')||action==='cancel-expense') {
            const note=prompt(ft('reason'));if(!note?.trim())return;
            const paths={'reverse-cost':`/cost-adjustments/${target.dataset.id}/reverse`,'reverse-expense':`/expenses/${target.dataset.id}/reverse`,'reverse-receipt':`/receipts/${target.dataset.id}/reverse`,'cancel-expense':`/expenses/${target.dataset.id}/cancel`};
            return write(action,'POST',paths[action],{request_key:key(action+target.dataset.id),note});
        }
        if(action==='post-expense') return write('post-expense','POST',`/expenses/${target.dataset.id}/post`,{});
        if(action==='create-expense'||action==='create-receipt') {
            const form=action==='create-expense'?'expense-form':'receipt-form',payload=data(form);if(!payload)return;
            if('settlement_entry_id' in payload)payload.settlement_entry_id=payload.settlement_entry_id?Number(payload.settlement_entry_id):null;
            return write(`fc-${form}`,'POST',action==='create-expense'?'/expenses':'/receipts',{...payload,store_id:context().store_id,request_key:key(`fc-${form}`)});
        }
        if(action==='select-source') {
            const payload=data('source-form');if(payload)return write('fc-source-form','PUT','/expense-source',{...context(),...payload});return;
        }
        if(action==='classify') {
            const payload=data('classification-form');if(!payload?.entry_id)return;
            const breakdown=Object.fromEntries(Object.keys(state.config.breakdown_categories).map(k=>[k,payload[k]]));
            return write('fc-classification-form','PUT',`/settlements/${Number(payload.entry_id)}/classification`,{breakdown,note:payload.note});
        }
        if(action==='capture') {
            const payload=data('version-form');if(payload)return write('fc-version-form','POST','/versions',{...context(),...payload,request_key:key('fc-version-form')});return;
        }
        if(action==='compare') {
            const serial=state.serial,scope=selection(),value=await api('GET',`/financial-control/versions/${target.dataset.id}/compare`);
            if(serial!==state.serial||scope!==selection())return;
            el('comparison').innerHTML=`<p>${esc(ft(value.changed?'changed':'unchanged'))}</p>`+table([ft('category'),ft('month'),ft('before'),ft('after'),ft('difference')],value.changes.map(row=>`<tr><td>${esc(row.label)}</td><td>${esc(ft(row.period))}</td><td>${esc(money(row.previous))}</td><td>${esc(money(row.current))}</td><td>${esc(money(row.difference))}</td></tr>`));return;
        }
        if(action==='export') {
            const response=await fetch(`/financial-control/versions/${target.dataset.id}/export`,{credentials:'same-origin',headers:authHeaders()});
            if(response.status===401){handleSessionExpired();return;}
            if(!response.ok){const value=await response.json();throw new Error(formatApiError(value.detail,ft('failed')));}
            const url=URL.createObjectURL(await response.blob()),link=document.createElement('a');link.href=url;link.download=`financial_version_${target.dataset.id}.xlsx`;link.click();URL.revokeObjectURL(url);
        }
    }
    async function open() {
        const serial=++state.serial;
        if(!state.config){const config=await api('GET','/financial-control/config');if(serial!==state.serial)return;state.config=config;}
        if(serial!==state.serial)return;
        if(!state.initialized)initialize();
        if(!state.config.enabled){notice(ft('disabled'));return;}
        el('body').hidden=false;
        const [stores,me,batches]=await Promise.all([api('GET','/finance-masters/stores'),api('GET','/auth/me'),api('GET','/batches?page=1&page_size=100')]);
        if(serial!==state.serial)return;
        const previous=el('store').value;
        el('store').innerHTML=stores.filter(s=>isAdmin()||s.id===me.store_id).map(s=>`<option value="${s.id}">${esc(s.name)}</option>`).join('');
        if([...el('store').options].some(o=>o.value===previous))el('store').value=previous;
        el('month').value=el('month').value||defaultProfitLossMonth();
        el('batches').innerHTML=batches.items.filter(b=>isAdmin()||b.user_id===me.id).map(b=>`<option value="${b.id}">${esc(b.batch_no)}</option>`).join('');
        const current=new Date().toLocaleDateString('sv-SE');
        for(const form of ['cost-form','expense-form','receipt-form'])el(form).querySelectorAll('input[type=date]').forEach(i=>{if(!i.value)i.value=current;});
        setExpenseDate();
        permission();return refresh();
    }
    function reset() {state.serial++;state.config=null;state.preview=null;state.keys={};state.initialized=false;const root=document.getElementById('financial-control-root');if(root)root.innerHTML='';}
    document.querySelectorAll('[data-finance-text]').forEach(node=>node.textContent=ft(node.dataset.financeText));
    window.FinanceControl={open:()=>open().catch(e=>notice(e.message,true)),reset,text:ft};
})();
