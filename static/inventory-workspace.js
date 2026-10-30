/* 库存只读工作台：所有新文案集中维护，按页面语言取词并回退zh-CN。 */
(() => {
    'use strict';
    const messages = {'zh-CN': {
        nav: '库存', title: '库存查看', overview: '库存总览', daily: '每日进出', assessment: '库存盘点与跌价',
        lead: '查看共享库存，按日期追踪商品的入库、出库和结余。',
        keyword: '商品名称 / SKU', search: '查询', refresh: '刷新', month: '查看月份',
        layout: '查看方式', dayView: '按天查看', productView: '按商品查看', matrixView: '横向日期表',
        day: '日期', product: '商品', sku: 'SKU', type: '商品类型', safe: '安全库存',
        current: '当前库存', amount: '库存成本金额', lastArrival: '最近入库', status: '库存状态',
        opening: '期初', inbound: '入库', outbound: '出库', adjustment: '调整', closing: '期末',
        allVisible: '本页商品', selectProduct: '查找商品', trace: '查看每日变化',
        productPlaceholder: '输入 SKU 或商品名称', productSearchHint: '搜索全部商品，点击候选或按回车选择。',
        keywordSearchHint: '输入后显示相似商品，点击候选精确查询；也可直接按关键字查询。',
        productSearching: '正在查找商品…', productSearchEmpty: '没有匹配商品，请修改 SKU 或名称。',
        productSearchMore: '仅显示前20项，请继续输入以缩小范围。',
        normal: '正常', low: '低于安全库存', zero: '无库存', pending: '待核对',
        pageQuantity: '本页库存数量', pageAmount: '本页库存成本', pageLow: '本页预警商品', pageZero: '本页无库存商品',
        shared: '库存总览为共享库存。成本金额含现有成本补差，不扣减跌价准备；财务账面价值请查看报表。',
        inventory_current_documents: '历史日结按当前有效单据及已有数量更正日志重算；撤销导入后原出库明细不再保留。此处不代表撤销、更正前的完整历史流水。',
        inventory_scope_restricted: '入库按可见批次统计，出库仅含绑定店铺；期初、调整和期末不展示，避免将本店出库与共享库存混算。全量日结仅管理员可查看。',
        inventory_history_inconsistent: '批次数量、销售明细或更正记录不一致，期初、调整和期末待核对。',
        inventory_arrival_restatement: '存在入库日期更正，本页按当前入库日期重算。',
        inventory_month_invalid: '请选择当月或以前月份。', inventory_future_date: '不能查看未来日期流水。',
        inventory_sources_too_large: '历史记录较多，请搜索具体商品或减少每页商品数。',
        inventory_product_missing: '商品不存在，请刷新列表。', inventory_adjustment_restricted: '完整库存调整日结仅管理员可查看。',
        loading: '正在读取库存…', failed: '读取失败，请重试：', empty: '没有匹配的商品，请调整名称或 SKU。',
        noMovements: '当天没有此类有效记录。', detail: '来源明细', close: '关闭', batch: '批次号',
        occurred: '业务时间', quantity: '变动数量', reference: '来源单号', kind: '类型',
        quantity_correction: '数量更正', void: '批次作废', previous: '上一页', next: '下一页',
        pagination: '共 {total} 个商品 · 第 {page} / {pages} 页', pageSize: '每页商品数',
        detailPagination: '共 {total} 条 · 第 {page} / {pages} 页',
        dataScope: '每日统计为本页商品合计', equation: '期末 = 期初 + 入库 − 出库 + 调整',
        matrixIn: '入', matrixOut: '出', matrixAdjustment: '调', matrixBalance: '余', noImage: '无图',
        stable: '稳健商品', new: '新品', safe_stock: '稳健商品', no_impairment: '不跌价商品',
        exportOverview: '导出库存 Excel', exportDaily: '导出整月 Excel', exportProduct: '导出所选商品 Excel',
        exportScope: '导出当前查询全部结果，不限当前页；每日进出包含所选月份。',
        exportLoading: '正在生成 Excel，请稍候…', exportReady: 'Excel 已生成，请查看浏览器下载列表。',
        exportFailed: '导出失败：', exportInvalidResponse: '返回的文件无效，请重试或联系管理员。',
        exportUnavailable: '导出接口尚未加载，请重启本地服务并刷新页面。',
        inventory_export_month_required: '请选择导出月份。', inventory_export_empty: '没有符合条件的商品可导出。',
        inventory_export_too_large: '单次最多导出2000个商品，请缩小查询范围。',
        inventory_export_sources_too_large: '导出的历史记录较多，请缩小商品范围后重试。',
    }};
    const text = (key, args = {}) => Object.entries(args).reduce(
        (value, [name, replacement]) => value.replaceAll(`{${name}}`, String(replacement)),
        (messages[document.documentElement.lang] || messages['zh-CN'])[key] || key);
    const h = value => escapeHtml(String(value ?? ''));
    const money = value => value == null ? '—' : Number(value).toLocaleString(document.documentElement.lang || 'zh-CN',
        {minimumFractionDigits: 2, maximumFractionDigits: 2});
    const number = value => value == null ? '—' : Number(value).toLocaleString(document.documentElement.lang || 'zh-CN');
    const sum = values => values.some(value => value == null) ? null : values.reduce((a, b) => a + Number(b), 0);
    const today = () => {
        const parts = new Intl.DateTimeFormat('en-CA', {timeZone: 'Asia/Shanghai', year: 'numeric',
            month: '2-digit', day: '2-digit'}).formatToParts(new Date());
        const value = type => parts.find(part => part.type === type).value;
        return `${value('year')}-${value('month')}-${value('day')}`;
    };
    const initial = () => ({mode: 'overview', layout: 'day', month: today().slice(0, 7), day: today(),
        keyword: '', page: 1, pageSize: 20, selected: 0, productId: 0, data: null, detail: null});
    let state = initial(), serial = 0, detailSerial = 0, initialized = false, exportRequest = null;
    const makePicker = id => ({id, serial: 0, timer: null, controller: null, items: [], active: -1, editing: false, product: null});
    const pickers = {product: makePicker('product'), keyword: makePicker('keyword')};
    const picker = pickers.product, keywordPicker = pickers.keyword;
    const root = () => document.getElementById('inventory-workspace-root');
    const node = id => document.getElementById(`iw-${id}`);
    const button = (action, label, extra = '') => `<button type="button" class="btn btn-secondary" data-iw-action="${action}" ${extra}>${h(text(label))}</button>`;
    const searchField = (id, label, hint, extra = '') => `<div class="form-group inventory-product-picker ${id === 'keyword' ? 'inventory-keyword-picker' : ''}" id="iw-${id}-field" ${extra}>
        <label for="iw-${id}">${h(text(label))}</label>
        <div class="inventory-product-control"><input id="iw-${id}" role="combobox" aria-autocomplete="list" aria-expanded="false" aria-controls="iw-${id}-options"
            aria-describedby="iw-${id}-hint" title="${h(text(hint))}" autocomplete="off" maxlength="255" placeholder="${h(text('productPlaceholder'))}">
        <div class="inventory-product-popup" id="iw-${id}-popup" hidden>
            <div id="iw-${id}-options" class="inventory-product-options" role="listbox" aria-label="${h(text(label))}"></div>
            <p id="iw-${id}-search-status" class="inventory-product-search-status" role="status" aria-live="polite"></p>
        </div></div><small id="iw-${id}-hint" hidden>${h(text(hint))}</small></div>`;

    function initialize() {
        if (initialized || !root()) return;
        initialized = true;
        root().innerHTML = `<div class="card inventory-workspace">
            <div class="inventory-heading"><div><h3>${h(text('title'))}</h3><p>${h(text('lead'))}</p></div>${button('export', 'exportOverview', `id="iw-export" title="${h(text('exportScope'))}" disabled`)}</div>
            <div class="inventory-tabs" role="group" aria-label="${h(text('title'))}">
                ${button('overview', 'overview')}${button('daily', 'daily')}${button('assessment', 'assessment')}
            </div>
            <form id="iw-query" class="inventory-filters">
                ${searchField('keyword', 'keyword', 'keywordSearchHint')}
                <label class="form-group" id="iw-month-field" hidden><span>${h(text('month'))}</span><input id="iw-month" type="month" max="${today().slice(0, 7)}" value="${state.month}" required></label>
                <button type="submit" class="btn btn-primary">${h(text('search'))}</button>${button('refresh', 'refresh')}
            </form>
            <p id="iw-scope" class="inventory-note"></p>
            <p id="iw-status" class="inventory-status" role="status" aria-live="polite"></p>
            <p id="iw-export-status" class="inventory-status" role="status" aria-live="polite"></p>
            <p id="iw-stat-scope" class="inventory-note"></p>
            <div id="iw-stats" class="inventory-stats"></div>
            <div id="iw-views" class="inventory-filters" hidden>
                <label class="form-group"><span>${h(text('layout'))}</span><select id="iw-layout">
                    <option value="day">${h(text('dayView'))}</option><option value="product">${h(text('productView'))}</option><option value="matrix">${h(text('matrixView'))}</option></select></label>
                <label class="form-group" id="iw-day-field"><span>${h(text('day'))}</span><select id="iw-day"></select></label>
                ${searchField('product', 'selectProduct', 'productSearchHint', 'hidden')}
                <span id="iw-equation" class="inventory-note">${h(text('equation'))}</span>
            </div>
            <div id="iw-table-scroll" class="inventory-table-scroll" role="region" aria-label="${h(text('title'))}" tabindex="0"><table class="inventory-table"><thead id="iw-thead"></thead><tbody id="iw-tbody"></tbody></table></div>
            <div class="inventory-footer"><span id="iw-page-label"></span><div>${button('previous', 'previous')}${button('next', 'next')}</div>
                <label class="form-group"><span>${h(text('pageSize'))}</span><select id="iw-page-size"><option>10</option><option selected>20</option><option>50</option></select></label></div>
        </div>
        <dialog id="iw-detail" class="product-import-dialog inventory-detail" aria-labelledby="iw-detail-title">
            <div class="inventory-heading"><h3 id="iw-detail-title"></h3>${button('close-detail', 'close')}</div>
            <div id="iw-detail-tabs" class="inventory-tabs">${button('detail-inbound', 'inbound')}${button('detail-outbound', 'outbound')}${button('detail-adjustment', 'adjustment')}</div>
            <p id="iw-detail-status" class="inventory-status" role="status" aria-live="polite"></p>
            <div class="table-scroll"><table><thead><tr>${['occurred', 'kind', 'batch', 'reference', 'quantity'].map(key => `<th>${h(text(key))}</th>`).join('')}</tr></thead><tbody id="iw-detail-body"></tbody></table></div>
            <div class="inventory-footer"><span id="iw-detail-page"></span><div>${button('detail-previous', 'previous')}${button('detail-next', 'next')}</div></div>
        </dialog>`;
        root().onclick = event => {
            if (exportRequest) return;
            const option = event.target.closest('[data-iw-product-option]');
            if (option) {chooseProduct(Number(option.dataset.iwProductOption), pickers[option.dataset.iwPicker]); return;}
            const move = event.target.closest('[data-iw-move]');
            if (move) { openDetail(Number(move.dataset.product), move.dataset.day, move.dataset.iwMove); return; }
            const trace = event.target.closest('[data-iw-trace]');
            if (trace) { traceProduct(Number(trace.dataset.iwTrace)); return; }
            const action = event.target.closest('[data-iw-action]')?.dataset.iwAction;
            if (!action) return;
            if (action === 'assessment') {
                closeSearches(true); closeDetail(); serial++; state.mode='assessment'; root().hidden=true;
                window.InventoryAssessment.open({keyword:state.keyword,productId:state.productId}); return;
            }
            if (action === 'export') {exportInventory(); return;}
            if (action === 'overview' || action === 'daily') {
                state.mode = action; applyKeywordQuery();
            } else if (action === 'refresh') refresh();
            else if (action === 'previous' || action === 'next') {state.page += action === 'next' ? 1 : -1; refresh();}
            else if (action === 'close-detail') closeDetail();
            else if (action.startsWith('detail-') && state.detail) {
                if (action === 'detail-next' || action === 'detail-previous') state.detail.page += action === 'detail-next' ? 1 : -1;
                else {state.detail.kind = action.slice(7); state.detail.page = 1;}
                loadDetail();
            }
        };
        node('query').addEventListener('submit', event => {
            event.preventDefault();
            if (exportRequest) return;
            applyKeywordQuery();
        });
        node('month').addEventListener('change', () => {state.month = node('month').value; state.page = 1; refresh();});
        node('layout').addEventListener('change', () => {state.layout = node('layout').value; closeProductSearch(true); render();});
        node('day').addEventListener('change', () => {state.day = node('day').value; render();});
        Object.values(pickers).forEach(bindProductSearch);
        node('page-size').addEventListener('change', () => {state.pageSize = Number(node('page-size').value); state.page = 1; refresh();});
        node('detail').addEventListener('close', event => {
            if (event.currentTarget === node('detail') && !event.currentTarget.open) {detailSerial++; state.detail = null;}
        });
    }

    function syncControls() {
        const daily = state.mode === 'daily';
        root().querySelectorAll('.inventory-tabs [data-iw-action="overview"], .inventory-tabs [data-iw-action="daily"]').forEach(button => {
            button.setAttribute('aria-pressed', String(button.dataset.iwAction === state.mode));
        });
        node('month-field').hidden = !daily;
        node('month').disabled = !daily;
        node('views').hidden = !daily || !state.data;
        node('layout').value = state.layout;
        node('day-field').hidden = state.layout !== 'day';
        node('product-field').hidden = state.layout !== 'product';
        node('page-size').value = String(state.pageSize);
        node('export').disabled = !!exportRequest || picker.editing || node('keyword').value.trim() !== state.keyword || !state.data?.items.length;
        node('export').textContent = text(daily ? (state.layout === 'product' ? 'exportProduct' : 'exportDaily') : 'exportOverview');
    }

    async function refresh() {
        initialize();
        if (!CURRENT_USER_ID || !root() || exportRequest) return;
        if (state.mode === 'assessment') return window.InventoryAssessment.open();
        root().hidden=false;
        closeSearches(true);
        closeDetail();
        const request = ++serial;
        state.data = null;
        syncControls();
        node('tbody').innerHTML = ''; node('thead').innerHTML = ''; node('stats').innerHTML = ''; node('stat-scope').textContent = '';
        node('status').textContent = text('loading'); node('page-label').textContent = '';
        node('export-status').textContent = '';
        root().querySelectorAll('[data-iw-action="previous"], [data-iw-action="next"]').forEach(button => button.disabled = true);
        if (state.mode === 'daily' && !/^\d{4}-\d{2}$/.test(state.month)) {
            node('status').textContent = text('inventory_month_invalid'); return;
        }
        const params = new URLSearchParams({keyword: state.keyword, page: state.page, page_size: state.pageSize});
        if (state.productId) params.set('product_id', state.productId);
        if (state.mode === 'daily') {
            params.set('month', `${state.month}-01`);
        }
        try {
            const data = await api('GET', `/inventory/${state.mode === 'daily' ? 'daily' : 'overview'}?${params}`);
            if (request !== serial) return;
            state.data = data; state.page = data.page;
            node('status').textContent = '';
            render();
        } catch (error) {
            if (request === serial) node('status').textContent = `${text('failed')}${error.message}`;
        }
    }

    function identity(product) {
        const image = /^\/uploads\/[A-Za-z0-9_.-]+$/.test(product.image || '')
            ? `<img src="${h(product.image)}" alt="" loading="lazy">` : `<span class="inventory-image-empty">${h(text('noImage'))}</span>`;
        return `<div class="inventory-product">${image}<span>${h(product.name)}<small>${h(product.sku)}</small></span></div>`;
    }
    const stat = (label, value) => `<div><span>${h(text(label))}</span><strong>${h(value)}</strong></div>`;
    function movementButton(productId, day, kind, value) {
        if (value == null || (kind === 'adjustment' && state.data?.scope !== 'global')) return h(number(value));
        return `<button type="button" class="inventory-quantity" data-iw-move="${kind}" data-product="${Number(productId)}" data-day="${h(day)}" aria-label="${h(`${day} ${text(kind)} ${number(value)}`)}">${h(number(value))}</button>`;
    }
    function dayCells(product, day) {
        return `<td>${h(number(day.opening_quantity))}</td><td>${movementButton(product.product_id, day.date, 'inbound', day.inbound)}</td>
            <td>${movementButton(product.product_id, day.date, 'outbound', day.outbound)}</td><td>${movementButton(product.product_id, day.date, 'adjustment', day.adjustment)}</td>
            <td class="inventory-balance">${h(number(day.closing_quantity))}</td><td>${product.warnings?.length ? `<span class="tag tag-orange" data-iw-warning>${h(text('pending'))}</span>` : ''}</td>`;
    }

    function render() {
        const data = state.data;
        if (!data) return;
        syncControls();
        const pages = Math.max(1, Math.ceil(data.total / data.page_size));
        node('page-label').textContent = text('pagination', {total: data.total, page: data.page, pages});
        root().querySelector('[data-iw-action="previous"]').disabled = data.page <= 1;
        root().querySelector('[data-iw-action="next"]').disabled = data.page >= pages;
        if (state.mode === 'overview') {
            node('scope').textContent = text('shared');
            node('stat-scope').textContent = text('allVisible');
            node('stats').innerHTML = stat('pageQuantity', number(data.summary.quantity)) + stat('pageAmount', money(data.summary.inventory_amount))
                + stat('pageLow', number(data.summary.low_count)) + stat('pageZero', number(data.summary.zero_count));
            node('thead').innerHTML = `<tr>${['product', 'type', 'safe', 'current', 'amount', 'lastArrival', 'status', 'detail'].map(key => `<th>${h(text(key))}</th>`).join('')}</tr>`;
            node('tbody').innerHTML = data.items.map(product => `<tr><td class="inventory-fixed">${identity(product)}</td>
                <td>${h(text(product.product_type))}</td><td>${['stable','safe_stock'].includes(product.product_type) ? h(number(product.safe_stock_quantity)) : '—'}</td>
                <td class="inventory-balance">${h(number(product.stock_quantity))}</td><td>${h(money(product.stock_value))}</td>
                <td>${h(product.last_batch_at || '—')}</td><td><span class="tag ${product.stock_status === 'low' || product.stock_status === 'pending' ? 'tag-orange' : product.stock_status === 'zero' ? 'tag-red' : 'tag-green'}">${h(text(product.stock_status))}</span></td>
                <td><button type="button" class="btn btn-secondary" data-iw-trace="${Number(product.id)}">${h(text('trace'))}</button></td></tr>`).join('');
        } else {
            const warningKeys = [...new Set([...(data.warnings || []), ...data.items.flatMap(product => product.warnings || [])])];
            node('scope').textContent = warningKeys.map(key => text(key)).join(' ');
            const days = data.items[0]?.days || [];
            if (!days.some(day => day.date === state.day)) state.day = data.end;
            node('day').innerHTML = days.map(day => `<option value="${h(day.date)}">${h(day.date)}</option>`).join('');
            node('day').value = state.day;
            if (!data.items.some(product => product.product_id === state.selected)) state.selected = data.items[0]?.product_id || 0;
            node('product').value = productLabel(data.items.find(product => product.product_id === state.selected));
            const statisticProducts = state.layout === 'product' ? data.items.filter(product => product.product_id === state.selected) : data.items;
            const current = statisticProducts.map(product => product.days.find(day => day.date === state.day)).filter(Boolean);
            const statistic = key => {
                if (state.layout !== 'matrix') return sum(current.map(day => day[key]));
                if (key === 'opening_quantity') return sum(data.items.map(product => product.days[0]?.opening_quantity));
                if (key === 'closing_quantity') return sum(data.items.map(product => product.days.at(-1)?.closing_quantity));
                return sum(data.items.flatMap(product => product.days.map(day => day[key])));
            };
            node('stat-scope').textContent = `${state.layout === 'matrix' ? state.month : state.day} · ${state.layout === 'product' ? (statisticProducts[0]?.sku || '') : text('dataScope')}`;
            node('stats').innerHTML = ['opening_quantity', 'inbound', 'outbound', 'closing_quantity'].map((key, index) =>
                stat(['opening', 'inbound', 'outbound', 'closing'][index], number(data.scope !== 'global' && key.endsWith('_quantity') ? null : statistic(key)))).join('');
            node('stats').setAttribute('aria-label', `${state.day} ${text('dataScope')}`);
            if (state.layout === 'matrix') {
                node('thead').innerHTML = `<tr><th>${h(text('product'))}</th>${days.map(day => `<th>${h(day.date.slice(5))}</th>`).join('')}</tr>`;
                node('tbody').innerHTML = data.items.map(product => `<tr><td class="inventory-fixed">${identity(product)}</td>${product.days.map(day =>
                    `<td class="inventory-matrix-cell"><div>${h(text('matrixIn'))} ${movementButton(product.product_id, day.date, 'inbound', day.inbound)}</div><div>${h(text('matrixOut'))} ${movementButton(product.product_id, day.date, 'outbound', day.outbound)}</div>${day.adjustment !== 0 && data.scope === 'global' ? `<div>${h(text('matrixAdjustment'))} ${movementButton(product.product_id, day.date, 'adjustment', day.adjustment)}</div>` : ''}<div>${h(text('matrixBalance'))} ${h(number(day.closing_quantity))}</div></td>`).join('')}</tr>`).join('');
            } else {
                node('thead').innerHTML = `<tr>${[state.layout === 'product' ? 'day' : 'product', 'opening', 'inbound', 'outbound', 'adjustment', 'closing', 'status'].map(key => `<th>${h(text(key))}</th>`).join('')}</tr>`;
                if (state.layout === 'product') {
                    const product = data.items.find(item => item.product_id === state.selected);
                    node('tbody').innerHTML = product ? product.days.map(day => `<tr><td class="inventory-fixed">${h(day.date)}</td>${dayCells(product, day)}</tr>`).join('') : '';
                } else node('tbody').innerHTML = data.items.map(product => {
                    const day = product.days.find(item => item.date === state.day);
                    return day ? `<tr><td class="inventory-fixed">${identity(product)}</td>${dayCells(product, day)}</tr>` : '';
                }).join('');
            }
        }
        if (!data.items.length) node('tbody').innerHTML = `<tr><td colspan="40" class="empty">${h(text('empty'))}</td></tr>`;
    }

    const productLabel = product => product ? `${product.sku} · ${product.name}` : '';
    function applyKeywordQuery() {
        state.keyword = node('keyword').value.trim(); state.month = node('month').value;
        const product = keywordPicker.product;
        state.productId = product?.sku === state.keyword ? Number(product.id) : 0;
        state.selected = state.productId; state.page = 1;
        return refresh();
    }
    function bindProductSearch(control) {
        const input = node(control.id);
        input.addEventListener('focus', event => {
            if (control === keywordPicker) {
                if (input.value.trim()) searchProducts(event, control);
            } else {
                input.select(); control.items = state.data?.items || []; control.active = -1;
                renderProductOptions('', control);
            }
        });
        for (const event of ['input', 'compositionend']) input.addEventListener(event, event => searchProducts(event, control));
        input.addEventListener('keydown', event => {
            if (event.isComposing) return;
            if (event.key === 'Escape') {event.preventDefault(); closeProductSearch(true, control);}
            if (event.key === 'ArrowDown' || event.key === 'ArrowUp') {
                event.preventDefault();
                if (node(`${control.id}-popup`).hidden) {
                    if (control === keywordPicker) {searchProducts(event, control); return;}
                    control.items = state.data?.items || [];
                }
                if (!control.items.length) return;
                control.active = control.active < 0 ? (event.key === 'ArrowDown' ? 0 : control.items.length - 1)
                    : (control.active + (event.key === 'ArrowDown' ? 1 : -1) + control.items.length) % control.items.length;
                renderProductOptions('', control);
                node(`${control.id}-option-${control.active}`).scrollIntoView({block: 'nearest'});
            }
            if (event.key === 'Enter' && (control === picker || control.active >= 0)) {
                event.preventDefault(); chooseProduct(Math.max(0, control.active), control);
            }
        });
        input.addEventListener('blur', () => closeProductSearch(true, control));
        node(`${control.id}-options`).addEventListener('mousedown', event => event.preventDefault());
    }
    function closeSearches(restore = false) {
        Object.values(pickers).forEach(control => closeProductSearch(restore, control));
    }
    function closeProductSearch(restore = false, control = picker) {
        clearTimeout(control.timer); control.controller?.abort(); control.serial++;
        control.items = []; control.active = -1; control.editing = false;
        const input = node(control.id);
        if (!input) return;
        node(`${control.id}-popup`).hidden = true;
        input.setAttribute('aria-expanded', 'false'); input.removeAttribute('aria-activedescendant');
        if (restore && control === picker) input.value = productLabel(state.data?.items.find(product => product.product_id === state.selected));
        syncControls();
    }
    function renderProductOptions(message = '', control = picker) {
        node(`${control.id}-options`).innerHTML = control.items.map((product, index) =>
            `<button type="button" role="option" tabindex="-1" id="iw-${control.id}-option-${index}" data-iw-product-option="${index}" data-iw-picker="${control.id}"
                aria-selected="${index === control.active}"><strong>${h(product.sku)}</strong><span>${h(product.name)}</span></button>`).join('');
        node(`${control.id}-search-status`).textContent = message;
        node(`${control.id}-popup`).hidden = false;
        node(control.id).setAttribute('aria-expanded', 'true');
        if (control.active >= 0) node(control.id).setAttribute('aria-activedescendant', `iw-${control.id}-option-${control.active}`);
        else node(control.id).removeAttribute('aria-activedescendant');
    }
    function chooseProduct(index, control = picker) {
        const product = control.items[index];
        if (!product || exportRequest) return;
        closeSearches();
        if (control === keywordPicker) {
            keywordPicker.product = product; node('keyword').value = product.sku;
            applyKeywordQuery();
        } else traceProduct(Number(product.product_id ?? product.id));
    }
    function searchProducts(event, control = picker) {
        if (exportRequest) return;
        clearTimeout(control.timer); control.controller?.abort();
        const request = ++control.serial, workspace = serial, query = node(control.id).value.trim();
        control.items = []; control.active = -1; control.editing = true;
        if (control === keywordPicker && event.type === 'input') control.product = null;
        node('export-status').textContent = '';
        syncControls();
        if (!query) {
            if (control === keywordPicker) closeProductSearch(false, control);
            else {control.items = state.data?.items || []; renderProductOptions('', control);}
            return;
        }
        renderProductOptions(text('productSearching'), control);
        if (event.isComposing) return;
        control.timer = setTimeout(async () => {
            const controller = new AbortController(); control.controller = controller;
            const current = () => request === control.serial && workspace === serial && CURRENT_USER_ID;
            try {
                const params = new URLSearchParams({keyword: query, page: 1, page_size: 20});
                const response = await fetch(`/products?${params}`, {credentials: 'same-origin', headers: authHeaders(), signal: controller.signal});
                if (!current()) return;
                if (response.status === 401) {handleSessionExpired(); return;}
                const data = await response.json();
                if (!current()) return;
                if (!response.ok) throw new Error(formatApiError(data?.detail, `${response.status}`));
                control.items = data.items.sort((a, b) => Number(b.sku.toLowerCase() === query.toLowerCase()) - Number(a.sku.toLowerCase() === query.toLowerCase()));
                renderProductOptions(!data.items.length ? text('productSearchEmpty') : data.total > data.items.length ? text('productSearchMore') : '', control);
            } catch (error) {
                if (current()) renderProductOptions(`${text('failed')}${error.message}`, control);
            }
        }, 250);
    }
    function traceProduct(productId) {
        keywordPicker.product = null;
        state.mode = 'daily'; state.layout = 'product'; state.selected = productId;
        state.productId = productId; state.keyword = ''; node('keyword').value = ''; state.page = 1;
        return refresh();
    }
    function closeDetail() {
        detailSerial++; state.detail = null;
        if (node('detail')?.open) node('detail').close();
    }
    function openDetail(productId, day, kind) {
        state.detail = {productId, day, kind, page: 1};
        if (!node('detail').open) node('detail').showModal();
        return loadDetail();
    }
    async function loadDetail() {
        const detail = state.detail;
        if (!detail) return;
        const request = ++detailSerial;
        const snapshot = {...detail};
        node('detail-status').textContent = text('loading'); node('detail-body').innerHTML = ''; node('detail-page').textContent = '';
        node('detail-title').textContent = `${text('detail')} · ${detail.day}`;
        node('detail-tabs').querySelector('[data-iw-action="detail-adjustment"]').hidden = state.data?.scope !== 'global';
        node('detail-tabs').querySelectorAll('button').forEach(button => button.setAttribute('aria-pressed', String(button.dataset.iwAction === `detail-${detail.kind}`)));
        for (const action of ['detail-previous', 'detail-next']) root().querySelector(`[data-iw-action="${action}"]`).disabled = true;
        try {
            const params = new URLSearchParams({product_id: snapshot.productId, day: snapshot.day, kind: snapshot.kind, page: snapshot.page, page_size: 20});
            const data = await api('GET', `/inventory/movements?${params}`);
            if (request !== detailSerial || state.detail !== detail || !node('detail').open) return;
            detail.page = data.page;
            node('detail-title').textContent = `${data.sku} · ${data.name} · ${data.day}`;
            node('detail-status').textContent = '';
            node('detail-body').innerHTML = data.items.length ? data.items.map(item => `<tr><td>${h(String(item.occurred_at).replace('T', ' '))}</td><td>${h(text(item.kind))}</td><td>${h(item.batch_no)}</td><td>${h(item.reference || '—')}</td><td>${h(item.quantity == null ? text('pending') : `${item.quantity > 0 ? '+' : ''}${number(item.quantity)}`)}</td></tr>`).join('') : `<tr><td colspan="5" class="empty">${h(text('noMovements'))}</td></tr>`;
            const pages = Math.max(1, Math.ceil(data.total / data.page_size));
            node('detail-page').textContent = text('detailPagination', {total: data.total, page: data.page, pages});
            root().querySelector('[data-iw-action="detail-previous"]').disabled = data.page <= 1;
            root().querySelector('[data-iw-action="detail-next"]').disabled = data.page >= pages;
        } catch (error) {
            if (request === detailSerial && state.detail === detail) node('detail-status').textContent = `${text('failed')}${error.message}`;
        }
    }
    async function exportInventory() {
        if (exportRequest || picker.editing || node('keyword').value.trim() !== state.keyword || !state.data?.items.length) return;
        closeSearches(true);
        const request = {controller: new AbortController(), serial};
        const view = state.mode === 'overview' ? 'overview' : state.layout === 'matrix' ? 'matrix' : 'daily';
        const params = new URLSearchParams({view, keyword: state.keyword});
        if (state.mode === 'overview' && state.productId) params.set('product_id', state.productId);
        if (state.mode === 'daily') {
            params.set('month', `${state.month}-01`);
            const productId = state.layout === 'product' ? state.selected : state.productId;
            if (productId) params.set('product_id', productId);
        }
        exportRequest = request;
        const controls = [...root().querySelectorAll('button,input,select')].map(control => [control, control.disabled]);
        controls.forEach(([control]) => control.disabled = true);
        node('export-status').textContent = text('exportLoading');
        try {
            const response = await fetch(`/inventory/export?${params}`, {credentials: 'same-origin',
                headers: authHeaders(), signal: request.controller.signal});
            if (exportRequest !== request || serial !== request.serial || !CURRENT_USER_ID) return;
            if (response.status === 401) {handleSessionExpired(); return;}
            if (!response.ok) {
                const body = await response.json().catch(() => null);
                if (response.status === 404 && body?.detail === 'Not Found') throw new Error(text('exportUnavailable'));
                const code = body?.detail?.code;
                throw new Error(code && text(code) !== code ? text(code) : formatApiError(body?.detail, `${response.status}`));
            }
            if (!response.headers.get('content-type')?.includes('application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')) {
                throw new Error(text('exportInvalidResponse'));
            }
            const blob = await response.blob();
            if (exportRequest !== request || serial !== request.serial || !CURRENT_USER_ID) return;
            const filename = response.headers.get('content-disposition')?.match(/filename="?([^";]+)"?/i)?.[1]
                || `inventory_${view}.xlsx`;
            const url = URL.createObjectURL(blob), link = document.createElement('a');
            try {
                link.href = url; link.download = filename.replace(/[\\/\r\n]/g, '_');
                document.body.appendChild(link); link.click();
            } finally {link.remove(); setTimeout(() => URL.revokeObjectURL(url), 1000);}
            node('export-status').textContent = text('exportReady');
        } catch (error) {
            if (exportRequest === request && serial === request.serial) node('export-status').textContent = `${text('exportFailed')}${error.message}`;
        } finally {
            if (exportRequest === request) {
                exportRequest = null;
                controls.forEach(([control, disabled]) => control.disabled = disabled);
                syncControls();
            }
        }
    }
    function reset() {
        window.InventoryAssessment?.reset();
        closeSearches(); keywordPicker.product = null;
        const previousExport = exportRequest;
        exportRequest = null;
        previousExport?.controller.abort();
        serial++; closeDetail(); state = initial(); initialized = false;
        if (root()) root().replaceChildren();
    }
    document.querySelectorAll('[data-inventory-nav]').forEach(element => element.textContent = text('nav'));
    window.InventoryWorkspace = {open: refresh, reset, text, show: mode => {state.mode=mode;root().hidden=false;return refresh();}};
})();
