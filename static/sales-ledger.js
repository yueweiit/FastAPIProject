/* 销售台账字段说明与只读计算明细；页面和Excel共用字段资源。 */
(() => {
    'use strict';
    const messages = {'zh-CN': {
        pending:'待核对', na:'—', empty:'暂无销售记录', pageTotal:'本页合计', unknownStore:'未关联店铺',
        detail:'计算明细', close:'关闭', loading:'正在读取完整订单与FIFO明细…',
        failed:'计算明细读取失败，请重试。', unavailable:'销售计算接口或字段资源尚未加载，请更新并重启后端后刷新。',
        formula:'公式', source:'来源', note:'口径', row:'本行成本计算', batches:'实际FIFO批次',
        impairment:'本行出库跌价', whole:'完整订单汇总', lines:'订单出库明细',
        category:'科目', raw:'原始分项金额（元）', shown:'本行金额（元）', unit:'单件成本（元/件）',
        calculation:'本行代入', batch:'批次号', quantity:'出库件数', unitCost:'有效单价（元）',
        total:'成本合计（元）', impairmentUnits:'准备转销折算数量', impairmentAmount:'转销金额（元）',
        purchase:'采购', head_shipping:'头程', last_mile:'尾程', other:'其他入库',
        rowHelp:'单件用未舍入分项除实际库存出库件数，显示4位；各金额按分展示并分配尾差。代入金额最多显示8位，计算仍用服务端原精度。跨批次单价为本次出库加权平均，不能用显示单价反算订单金额。',
        batchHelp:'保存的FIFO单价和数量是成本依据，分项按原始/冻结构成分配，叠加启用的对应成本补差；不是按最新采购价重算历史出库。',
        impairmentHelp:'各出库库存层的准备转销折算数量乘对应有效单位成本后相加。销售当天先计提后转销；跌价单列，不重复加进成本或扣减毛利，也不代表销售店铺的承担额。',
        wholeHelp:'按同店铺同订单全部授权出库明细汇总，不受当前商品、月份和页面筛选限制。不同SKU的单价不相加；本行和整单金额不可重复合计。',
        basis:'数量口径：本地库存实际出库件数；组合商品按组件行展示，不等同于平台销售套数。',
        count:'出库明细数', wholeCost:'整单销售成本（元）', wholeImpairment:'整单出库跌价（元）',
        originalCurrency:'原币', rate:'汇率', rateDate:'汇率日期', rateSource:'汇率来源', missingRate:'汇率缺失，人民币金额待核对。',
        noBatches:'没有可核对的FIFO批次。', noImpairment:'没有可回放的出库跌价明细。',
        localSku:'本地SKU', localProduct:'本地商品', sourceQuantity:'来源数量', sourceProduct:'来源产品名',
    }};
    const manualMessages = {'zh-CN': {
        title:'手工结算补录',sku:'SKU ID',product:'产品名',store:'店铺',date:'结算日期',transaction:'交易类型',
        quantity:'来源数量',net:'净商品销售额（元）',settled:'结算总金额（元）',order:'订单号 / 调整单号',
        summary:'预计入账净额',submit:'保存补录',help:'独立人民币结算补录，不关联本地商品、不扣库存、不生成成本或跌价。普通记录费用为净商品销售额减结算总金额；赔付按结算总金额计入收入。负数可用于退款或冲减。',
        required:'请填写店铺、结算日期、订单号和交易类型。',invalid:'请输入有效整数数量及金额。',
        saving:'正在保存补录…',saved:'结算补录已保存：',failed:'结算补录保存失败，请重试。',refreshFailed:'补录已保存，部分列表刷新失败，请点击刷新。',
        unavailable:'当前服务尚未加载手工结算补录接口，请重启后端并刷新页面。',
        productSearchPlaceholder:'输入商品名称或 SKU',
        productSearchHint:'输入后显示匹配商品，点击或用方向键、回车选择。选择仅填写产品名，也可自由输入。',
    }};
    const mt = key => (manualMessages[document.documentElement.lang] || manualMessages['zh-CN'])[key] || key;
    const t = key => (messages[document.documentElement.lang] || messages['zh-CN'])[key] || key;
    const h = value => escapeHtml(String(value ?? ''));
    const el = id => document.getElementById(id);
    const categories = ['purchase', 'head_shipping', 'last_mile', 'other'];
    const userId = () => typeof CURRENT_USER_ID === 'undefined' ? 0 : CURRENT_USER_ID;
    let resource, pendingResource, epoch = 0, loadSerial = 0, detailSerial = 0, controller;
    let manualBusy=false,manualController,manualAttempt,manualProductSearch;
    const current = token => token.epoch === epoch && token.user === userId();
    const money = (value, places = 2) => value == null || !Number.isFinite(Number(value)) ? t('pending') : `¥${Number(value).toFixed(places)}`;
    const preciseMoney = value => money(value,8).replace(/(\.\d{2}\d*?)0+$/, '$1');
    const unknownSum = values => values.some(v => v == null || !Number.isFinite(Number(v))) ? null : values.reduce((s,v) => s + Number(v), 0);
    const totalCost = row => row.order_total_cost_cny ?? row.total_cost;
    function description(meta) {
        return `<p><strong>${h(t('formula'))}：</strong>${h(meta.formula)}</p><p><strong>${h(t('source'))}：</strong>${h(meta.source)}</p><p><strong>${h(t('note'))}：</strong>${h(meta.note)}</p>`;
    }
    async function ready() {
        if (resource) return;
        if (!pendingResource) pendingResource = fetch('/sales-fields.json', {credentials:'same-origin'}).then(async response => {
            if (!response.ok) throw new Error(t('unavailable'));
            const data = await response.json();
            if (data.version !== 1 || !Array.isArray(data.fields) || data.fields.length !== 24 || !Array.isArray(data.extra_fields)) throw new Error(t('unavailable'));
            resource = data;
        }).finally(() => {pendingResource = null;});
        await pendingResource;
    }
    function headers() {
        const groups = [];
        for (const meta of resource.fields) {
            if (groups.at(-1)?.name === meta.group) groups.at(-1).count++;
            else groups.push({name:meta.group, count:1});
        }
        el('sales-ledger-head').innerHTML = `<tr class="sales-column-groups">${groups.map((group,index) => `<th colspan="${group.count}" scope="colgroup" class="sales-group-${index}">${h(group.name)}</th>`).join('')}</tr><tr>${resource.fields.map((meta,index) =>
            `<th scope="col" data-sales-field="${h(meta.key)}" class="sales-stick-${index}"><span id="sales-field-${h(meta.key)}">${h(meta.label)}</span><div data-help-tip data-help-anchor="#sales-field-${h(meta.key)}">${description(meta)}</div></th>`).join('')}</tr>`;
        const cols = resource.fields.map(meta => `<col style="width:${meta.width}px">`).join('');
        el('sales-ledger-cols').innerHTML = cols; el('sales-total-cols').innerHTML = cols;
        const width = resource.fields.reduce((sum,meta)=>sum+meta.width,0)+'px';
        el('sales-ledger-table').style.width=width; el('sales-total-table').style.width=width;
    }
    function value(row, meta, product) {
        if (meta.key === 'sku') return row.display_sku || product.sku || '-';
        if (meta.key === 'product_name') return row.display_name || product.name || '-';
        if (meta.key === 'store_name') return row.store_name || t('unknownStore');
        if (meta.kind === 'date') return row.sold_at?.slice(0,10) || '-';
        if (meta.key === 'order_total_cost_cny') return totalCost(row);
        return row[meta.key];
    }
    function render(rows, products) {
        headers();
        const columns = resource.fields;
        el('sales-tbody').innerHTML = rows.length ? rows.map(row => `<tr>${columns.map((meta,index) => {
            const v = value(row, meta, products[row.product_id] || {});
            const numeric = meta.kind === 'money' || meta.kind === 'unit';
            const notApplicable = meta.kind === 'unit' && !(row.quantity > 0);
            const text = notApplicable ? t('na') : numeric ? money(v,meta.kind === 'unit' ? 4 : 2) : ['quantity','source_quantity'].includes(meta.kind) ? v == null ? t('na') : Number(v).toLocaleString(document.documentElement.lang) : h(v);
            const transaction = ['selling_price_cny','sales_revenue_cny','platform_fee_cny','profit_cny'].includes(meta.key);
            const fxReason = `${t('originalCurrency')} ${row.currency || 'CNY'}；${t('rate')} ${row.exchange_rate_to_cny ?? t('pending')}；${t('rateDate')} ${row.exchange_rate_date || '—'}；${t('rateSource')} ${row.exchange_rate_source || '—'}`;
            const reason = meta.key === 'impairment_cny' ? row.impairment_review_reason : transaction ?
                [fxReason, row.exchange_rate_to_cny == null && row.currency && row.currency !== 'CNY' ? t('missingRate') : '',meta.key === 'profit_cny' ? row.cost_review_reason : ''].filter(Boolean).join('；') : row.cost_breakdown_review_reason || row.cost_review_reason;
            const content = meta.kind === 'action' ? (row.id > 0 && (row.record_type || 'sale') === 'sale' ? `<button type="button" class="btn btn-secondary" data-sale-calculation="${Number(row.id)}" aria-label="${h(t('detail')+' · '+row.order_no)}">${h(t('detail'))}</button>` : t('na')) : text;
            return `<td data-sales-value="${h(meta.key)}" class="sales-stick-${index} ${numeric && v == null && !notApplicable ? 'fx-missing' : ''}" title="${h(reason || '')}">${content}</td>`;
        }).join('')}</tr>`).join('') : `<tr><td colspan="${columns.length}" class="empty">${h(t('empty'))}</td></tr>`;
        const firstTotal=columns.findIndex(meta=>meta.kind==='quantity');
        el('sales-total-summary-body').innerHTML = `<tr><td colspan="${firstTotal}">${h(t('pageTotal'))}</td>${columns.slice(firstTotal).map(meta => {
            if (!['quantity','money'].includes(meta.kind)) return `<td data-sales-total="${h(meta.key)}"></td>`;
            const values = rows.map(row => value(row,meta,{}));
            return `<td data-sales-total="${h(meta.key)}">${meta.kind === 'quantity' ? values.reduce((sum,v) => sum + Number(v || 0),0).toLocaleString(document.documentElement.lang) : money(unknownSum(values))}</td>`;
        }).join('')}</tr>`;
        window.HelpTips?.refresh();
    }
    function table(head, rows) {
        return `<div class="table-scroll"><table><thead><tr>${head.map(v=>`<th>${h(v)}</th>`).join('')}</tr></thead><tbody>${rows.join('')}</tbody></table></div>`;
    }
    function section(id, label, help, content) {
        return `<section class="sales-calculation-section"><h4 id="sales-calc-${id}">${h(label)}</h4><p data-help-tip data-help-anchor="#sales-calc-${id}">${h(help)}</p>${content}</section>`;
    }
    function detailHtml(data) {
        const sale = data.sale;
        const notes = [...new Set([sale.cost_review_reason, sale.cost_breakdown_review_reason, sale.impairment_review_reason, data.order_summary.review_reason].filter(Boolean))];
        const raw = key => sale[key+'_cost_cny'] == null ? null : unknownSum(data.cost_batches.map(batch => batch[key+'_cost_cny']));
        const lines = [...categories, 'total'].map(key => {
            const original = key === 'total' ? sale.total_cost : raw(key);
            const shown = key === 'total' ? totalCost(sale) : sale[key+'_cost_cny'];
            const unit = sale['unit_'+key+'_cost_cny'];
            return `<tr><td>${h(key === 'total' ? t('total') : t(key))}</td><td>${preciseMoney(original)}</td><td>${money(shown)}</td><td>${money(unit,4)}</td><td>${original == null || unit == null ? t('pending') : `${preciseMoney(original)} ÷ ${h(sale.quantity)} = ${money(unit,4)}`}</td></tr>`;
        });
        const costBatches = data.cost_batches.map(batch => `<tr><td>${h(batch.batch_no)}</td><td>${h(batch.quantity)}</td><td>${money(batch.unit_cost,4)}</td>${categories.map(key=>`<td>${preciseMoney(batch[key+'_cost_cny'])}</td>`).join('')}<td>${money(batch.total_cost_cny,4)}</td></tr>`);
        const impairments = data.impairment_batches.map(batch=>`<tr><td>${h(batch.batch_no)}</td><td>${h(batch.quantity)}</td><td>${batch.impairment_units == null ? t('pending') : h(batch.impairment_units)}</td><td>${money(batch.unit_cost,4)}</td><td>${money(batch.impairment_cny)}</td><td>${batch.impairment_units == null ? t('pending') : `${h(batch.impairment_units)} × ${money(batch.unit_cost,4)} = ${money(batch.impairment_cny)}`}</td></tr>`);
        const order = data.order_summary;
        return `<p class="sales-calculation-identity">${h(data.sku)} · ${h(data.product_name)} · ${h(sale.order_no)}</p><p class="sales-calculation-identity">${h(t('sourceProduct'))}：${h(sale.source_product_name || t('na'))} · ${h(t('sourceQuantity'))}：${h(sale.source_quantity ?? t('na'))} · ${h(sale.transaction_type || '')}</p><p data-help-tip data-help-anchor="#sales-calc-title">${h(t('basis'))}</p>
            ${notes.map(note=>`<p class="msg msg-err" role="alert">${h(note)}</p>`).join('')}
            ${section('row',t('row'),t('rowHelp'),table([t('category'),t('raw'),t('shown'),t('unit'),t('calculation')],lines))}
            ${section('batches',t('batches'),t('batchHelp'),costBatches.length ? table([t('batch'),t('quantity'),t('unitCost'),...categories.map(t),t('total')],costBatches) : `<p>${h(t('noBatches'))}</p>`)}
            ${section('impairment',t('impairment'),t('impairmentHelp'),`<strong>${money(sale.impairment_cny)}</strong>`+(impairments.length ? table([t('batch'),t('quantity'),t('impairmentUnits'),t('unitCost'),t('impairmentAmount'),t('calculation')],impairments) : `<p>${h(t('noImpairment'))}</p>`))}
            ${section('whole',t('whole'),t('wholeHelp'),`<dl class="sales-order-totals"><div><dt>${h(t('count'))}</dt><dd>${h(order.line_count)}</dd></div><div><dt>${h(t('wholeCost'))}</dt><dd>${money(order.total_cost_cny)}</dd></div><div><dt>${h(t('wholeImpairment'))}</dt><dd>${money(order.impairment_cny)}</dd></div></dl>` + table([t('localSku'),t('localProduct'),t('quantity'),t('total'),t('impairmentAmount')],data.order_lines.map(row=>`<tr><td>${h(row.display_sku)}</td><td>${h(row.display_name)}</td><td>${h(row.quantity)}</td><td>${row.cost_status === 'needs_review' ? t('pending') : money(totalCost(row))}</td><td>${money(row.impairment_cny)}</td></tr>`)))} `;
    }
    function ensureDialog() {
        if (el('sales-calc-dialog')) return el('sales-calc-dialog');
        const dialog = document.createElement('dialog'); dialog.id = 'sales-calc-dialog';
        dialog.className = 'product-import-dialog sales-calculation-dialog'; dialog.setAttribute('aria-labelledby','sales-calc-title');
        dialog.innerHTML = `<div class="flex-between"><h3 id="sales-calc-title">${h(t('detail'))}</h3><button type="button" class="btn btn-secondary" data-sales-calc-close>${h(t('close'))}</button></div><p id="sales-calc-status" role="status" aria-live="polite"></p><div id="sales-calc-body"></div>`;
        dialog.addEventListener('close', () => {
            if (dialog.open) return; // 旧关闭事件排队期间已重新打开，不能清空新弹窗。
            detailSerial++; controller?.abort(); controller=null; el('sales-calc-body').replaceChildren();
        });
        dialog.querySelector('[data-sales-calc-close]').onclick = () => dialog.close();
        document.body.appendChild(dialog); return dialog;
    }
    async function open(saleId) {
        const token = {epoch,user:userId()}, request = ++detailSerial;
        controller?.abort(); controller = new AbortController();
        const signal = controller.signal, dialog = ensureDialog();
        el('sales-calc-body').replaceChildren(); el('sales-calc-status').textContent=t('loading');
        if (!dialog.open) dialog.showModal();
        try {
            await ready();
            if (!current(token) || request !== detailSerial) return;
            const response = await fetch(`/sales/${Number(saleId)}/cost-breakdown`,{credentials:'same-origin',headers:authHeaders(),signal});
            if (!current(token) || request !== detailSerial) return;
            if (response.status === 401) {handleSessionExpired(); return;}
            const data = await response.json();
            if (!current(token) || request !== detailSerial) return;
            if (response.status === 404 && data.detail?.code !== 'sale_cost_not_found') throw new Error(t('unavailable'));
            if (!response.ok) throw new Error(typeof data.detail?.message === 'string' ? data.detail.message : t('failed'));
            el('sales-calc-body').innerHTML = detailHtml(data); el('sales-calc-status').textContent='';
            window.HelpTips?.refresh();
        } catch (error) {
            if (current(token) && request === detailSerial && error.name !== 'AbortError') el('sales-calc-status').textContent = error.message || t('failed');
        }
    }
    function updateManualSummary() {
        const raw=el('s-settlement-total')?.value;
        if(el('s-estimated-revenue'))el('s-estimated-revenue').textContent=raw && Number.isFinite(Number(raw)) ? money(raw) : '¥0.00';
    }
    function manualStatus(message,failed=false) {
        const node=el('s-manual-status');if(!node)return;
        node.textContent=message;node.className=failed?'msg msg-err':'msg';
    }
    function manualRequestKey() {
        if(typeof crypto.randomUUID==='function')return crypto.randomUUID();
        return Array.from(crypto.getRandomValues(new Uint8Array(16)),value=>value.toString(16).padStart(2,'0')).join('');
    }
    function initializeManualProductSearch() {
        const input=el('s-product-name');
        if(!window.ProductSearch || !input)return;
        input.placeholder=mt('productSearchPlaceholder');input.title=mt('productSearchHint');
        el('s-product-name-options').setAttribute('aria-label',mt('product'));
        manualProductSearch=window.ProductSearch.bind({
            input,popup:el('s-product-name-popup'),list:el('s-product-name-options'),status:el('s-product-name-search-status'),
            enabled:()=>!manualBusy,
            captureContext:()=>{const token={epoch,user:userId()};return ()=>current(token) && !!token.user;},
            onSelect:product=>{input.value=product.name;},
        });
    }
    async function createManualSettlement() {
        if(manualBusy)return;
        const token={epoch,user:userId()};
        const payload={store_id:Number(el('s-store').value),occurred_on:el('s-date').value,
            order_no:el('s-order-no').value.trim(),sku_id:el('s-sku').value.trim(),
            product_name:el('s-product-name').value.trim(),transaction_type:el('s-transaction-type').value.trim(),
            quantity:Number(el('s-qty').value || 0),net_product_sales:el('s-net-sales').value.trim(),
            settlement_total:el('s-settlement-total').value.trim()};
        if(!payload.store_id||!payload.occurred_on||!payload.order_no||!payload.transaction_type){manualStatus(mt('required'),true);return;}
        if(!Number.isInteger(payload.quantity)||!payload.net_product_sales||!payload.settlement_total||
           ![payload.net_product_sales,payload.settlement_total].every(v=>Number.isFinite(Number(v)))){manualStatus(mt('invalid'),true);return;}
        manualProductSearch?.close();
        const fingerprint=JSON.stringify(payload);
        if(manualAttempt?.fingerprint!==fingerprint)manualAttempt={fingerprint,key:manualRequestKey()};
        payload.request_key=manualAttempt.key;
        manualBusy=true;manualController=new AbortController();const signal=manualController.signal;
        el('s-manual-fields').disabled=true;manualStatus(mt('saving'));
        try{
            const response=await fetch('/sales/manual-settlements',{method:'POST',credentials:'same-origin',
                headers:{...authHeaders(),'Content-Type':'application/json'},body:JSON.stringify(payload),signal});
            if(!current(token))return;
            if(response.status===401){handleSessionExpired();return;}
            if(response.status===404)throw new Error(mt('unavailable'));
            const data=await response.json();if(!current(token))return;
            if(!response.ok)throw new Error(typeof formatApiError==='function'?formatApiError(data.detail,mt('failed')):mt('failed'));
            manualAttempt=null;
            manualStatus(`${mt('saved')} ${payload.order_no} · ${payload.occurred_on}`);
            for(const id of ['s-sku','s-product-name','s-order-no','s-net-sales','s-settlement-total'])el(id).value='';
            el('s-qty').value='1';el('s-order-no').dataset.auto='true';updateManualSummary();
            const refreshed=await Promise.allSettled([loadSales(),loadSalesImports()]);
            if(current(token)&&refreshed.some(r=>r.status==='rejected'||r.value===false))manualStatus(mt('refreshFailed'),true);
        }catch(error){if(current(token)&&error.name!=='AbortError')manualStatus(error.message||mt('failed'),true);}
        finally{if(current(token)){manualBusy=false;manualController=null;el('s-manual-fields').disabled=false;}}
    }
    function reset() {
        epoch++;loadSerial++;detailSerial++;controller?.abort();controller=null;
        manualProductSearch?.close();
        manualController?.abort();manualController=null;manualBusy=false;manualAttempt=null;
        if(el('s-manual-fields'))el('s-manual-fields').disabled=false;
        for(const id of ['s-sku','s-product-name','s-order-no','s-net-sales','s-settlement-total'])if(el(id))el(id).value='';
        if(el('s-qty'))el('s-qty').value='1';
        if(el('s-transaction-type'))el('s-transaction-type').value='订单';
        manualStatus('');updateManualSummary();
        const dialog = el('sales-calc-dialog'); if (dialog?.open) dialog.close();
        el('sales-calc-body')?.replaceChildren();
        el('sales-tbody')?.replaceChildren(); el('sales-total-summary-body')?.replaceChildren();
    }
    document.addEventListener?.('click', event => {
        const button=event.target.closest('[data-sale-calculation]'); if (button) open(Number(button.dataset.saleCalculation));
    });
    document.querySelectorAll?.('[data-sales-manual-text]').forEach(node=>{node.textContent=mt(node.dataset.salesManualText);});
    initializeManualProductSearch();
    updateManualSummary();
    window.SalesLedger = {ready,render,reset,createManualSettlement,updateManualSummary,
        requestOptions:()=>{const token={epoch,user:userId()};return {isCurrent:()=>current(token)};},
        beginLoad:()=>({epoch,user:userId(),load:++loadSerial}),
        currentLoad:token=>current(token)&&token.load===loadSerial,
        fail:(error,token)=>{if(current(token)&&token.load===loadSerial){
            el('sales-tbody').innerHTML=`<tr><td colspan="${resource?.fields.length || 24}" class="msg msg-err" role="alert">${h(error.message || t('failed'))}</td></tr>`;
            el('sales-total-summary-body').replaceChildren();
        }},
    };
})();
