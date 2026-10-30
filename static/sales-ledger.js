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
    }};
    const t = key => (messages[document.documentElement.lang] || messages['zh-CN'])[key] || key;
    const h = value => escapeHtml(String(value ?? ''));
    const el = id => document.getElementById(id);
    const categories = ['purchase', 'head_shipping', 'last_mile', 'other'];
    const userId = () => typeof CURRENT_USER_ID === 'undefined' ? 0 : CURRENT_USER_ID;
    let resource, pendingResource, epoch = 0, loadSerial = 0, detailSerial = 0, controller;
    const current = token => token.epoch === epoch && token.user === userId();
    const money = (value, places = 2) => value == null || !Number.isFinite(Number(value)) ? t('pending') : `¥${Number(value).toFixed(places)}`;
    const preciseMoney = value => money(value,8).replace(/(\.\d{2}\d*?)0+$/, '$1');
    const unknownSum = values => values.some(v => v == null || !Number.isFinite(Number(v))) ? null : values.reduce((s,v) => s + Number(v), 0);
    const totalCost = row => row.order_total_cost_cny ?? row.total_cost;
    const field = key => [...resource.fields, ...resource.extra_fields].find(f => f.key === key);
    function description(meta) {
        return `<p><strong>${h(t('formula'))}：</strong>${h(meta.formula)}</p><p><strong>${h(t('source'))}：</strong>${h(meta.source)}</p><p><strong>${h(t('note'))}：</strong>${h(meta.note)}</p>`;
    }
    async function ready() {
        if (resource) return;
        if (!pendingResource) pendingResource = fetch('/sales-fields.json', {credentials:'same-origin'}).then(async response => {
            if (!response.ok) throw new Error(t('unavailable'));
            const data = await response.json();
            if (data.version !== 1 || !Array.isArray(data.fields) || data.fields.length !== 22 || !Array.isArray(data.extra_fields)) throw new Error(t('unavailable'));
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
            const text = notApplicable ? t('na') : numeric ? money(v,meta.kind === 'unit' ? 4 : 2) : meta.kind === 'quantity' ? Number(v || 0).toLocaleString(document.documentElement.lang) : h(v);
            const transaction = ['selling_price_cny','sales_revenue_cny','platform_fee_cny','profit_cny'].includes(meta.key);
            const fxReason = `${t('originalCurrency')} ${row.currency || 'CNY'}；${t('rate')} ${row.exchange_rate_to_cny ?? t('pending')}；${t('rateDate')} ${row.exchange_rate_date || '—'}；${t('rateSource')} ${row.exchange_rate_source || '—'}`;
            const reason = meta.key === 'impairment_cny' ? row.impairment_review_reason : transaction ?
                [fxReason, row.exchange_rate_to_cny == null && row.currency && row.currency !== 'CNY' ? t('missingRate') : '',meta.key === 'profit_cny' ? row.cost_review_reason : ''].filter(Boolean).join('；') : row.cost_breakdown_review_reason || row.cost_review_reason;
            const content = meta.kind === 'action' ? (row.id > 0 && (row.record_type || 'sale') === 'sale' ? `<button type="button" class="btn btn-secondary" data-sale-calculation="${Number(row.id)}" aria-label="${h(t('detail')+' · '+row.order_no)}">${h(t('detail'))}</button>` : t('na')) : text;
            return `<td data-sales-value="${h(meta.key)}" class="sales-stick-${index} ${numeric && v == null && !notApplicable ? 'fx-missing' : ''}" title="${h(reason || '')}">${content}</td>`;
        }).join('')}</tr>`).join('') : `<tr><td colspan="22" class="empty">${h(t('empty'))}</td></tr>`;
        el('sales-total-summary-body').innerHTML = `<tr><td colspan="5">${h(t('pageTotal'))}</td>${columns.slice(5).map(meta => {
            if (meta.kind === 'unit' || meta.kind === 'action') return `<td data-sales-total="${h(meta.key)}"></td>`;
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
        return `<p class="sales-calculation-identity">${h(data.sku)} · ${h(data.product_name)} · ${h(sale.order_no)}</p><p data-help-tip data-help-anchor="#sales-calc-title">${h(t('basis'))}</p>
            ${notes.map(note=>`<p class="msg msg-err" role="alert">${h(note)}</p>`).join('')}
            ${section('row',t('row'),t('rowHelp'),table([t('category'),t('raw'),t('shown'),t('unit'),t('calculation')],lines))}
            ${section('batches',t('batches'),t('batchHelp'),costBatches.length ? table([t('batch'),t('quantity'),t('unitCost'),...categories.map(t),t('total')],costBatches) : `<p>${h(t('noBatches'))}</p>`)}
            ${section('impairment',t('impairment'),t('impairmentHelp'),`<strong>${money(sale.impairment_cny)}</strong>`+(impairments.length ? table([t('batch'),t('quantity'),t('impairmentUnits'),t('unitCost'),t('impairmentAmount'),t('calculation')],impairments) : `<p>${h(t('noImpairment'))}</p>`))}
            ${section('whole',t('whole'),t('wholeHelp'),`<dl class="sales-order-totals"><div><dt>${h(t('count'))}</dt><dd>${h(order.line_count)}</dd></div><div><dt>${h(t('wholeCost'))}</dt><dd>${money(order.total_cost_cny)}</dd></div><div><dt>${h(t('wholeImpairment'))}</dt><dd>${money(order.impairment_cny)}</dd></div></dl>` + table([field('sku').label,field('product_name').label,t('quantity'),t('total'),t('impairmentAmount')],data.order_lines.map(row=>`<tr><td>${h(row.display_sku)}</td><td>${h(row.display_name)}</td><td>${h(row.quantity)}</td><td>${row.cost_status === 'needs_review' ? t('pending') : money(totalCost(row))}</td><td>${money(row.impairment_cny)}</td></tr>`)))} `;
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
    function reset() {
        epoch++;loadSerial++;detailSerial++;controller?.abort();controller=null;
        const dialog = el('sales-calc-dialog'); if (dialog?.open) dialog.close();
        el('sales-calc-body')?.replaceChildren();
        el('sales-tbody')?.replaceChildren(); el('sales-total-summary-body')?.replaceChildren();
    }
    document.addEventListener?.('click', event => {
        const button=event.target.closest('[data-sale-calculation]'); if (button) open(Number(button.dataset.saleCalculation));
    });
    window.SalesLedger = {ready,render,reset,
        beginLoad:()=>({epoch,user:userId(),load:++loadSerial}),
        currentLoad:token=>current(token)&&token.load===loadSerial,
        fail:(error,token)=>{if(current(token)&&token.load===loadSerial){
            el('sales-tbody').innerHTML=`<tr><td colspan="22" class="msg msg-err" role="alert">${h(error.message || t('failed'))}</td></tr>`;
            el('sales-total-summary-body').replaceChildren();
        }},
    };
})();
