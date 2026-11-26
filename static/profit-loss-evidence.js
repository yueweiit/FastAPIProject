// Read-only 07/09/10/12 explanations. All amounts/formulas come from the report snapshot.
(function () {
    'use strict';
    const TEXT = {'zh-CN': {
        definition: '口径', evidence: '依据', close: '关闭', title: '字段口径与计算依据',
        current: '本月', previous: '上月', ytd: '本年累计', value: '报表金额', formula: '计算过程',
        sources: '来源明细', empty: '本期间没有参与计算的明细。', loading: '正在加载依据…',
        legacy: '该版本未保存完整计算依据；保留原金额和已有说明，不使用当前数据补算。',
        snapshot: '依据对应本次查询的已保存数据，未保存的输入不参与。',
        unavailable: '本次明细暂不可用，请重新查询；大范围报表可导出Excel查看完整依据。',
        expired: '查询依据已过期或服务已更新，请重新查询报表。',
        mismatch: '依据与当前查询不一致，请重新查询报表。',
        next: '下一页', prev: '上一页', page: '页', count: '条', note: '字段口径',
        summary: '查看各店铺损益汇总', summaryTitle: '10 · 各店铺损益汇总（本月）',
        summaryRule: '仅含有效店铺；人工空白按0参与，未知金额保持空白。毛利率按合计毛利÷合计营业收入计算。',
        summaryLoading: '正在加载各店铺汇总…', total: '合计', ratio: '占比', field: '项目',
        revenue: '合计营业收入', gross: '合计毛利', sumRule: '各店本月金额相加',
        marginRule: '合计毛利 ÷ 合计营业收入', ratioRule: '该项合计 ÷ 合计营业收入',
        noRatio: '毛利率不再计算占比', missing: '待核对/未提供', blank: '未填写（合计按0）',
        sourceTotal: '来源未舍入合计', reference: '凭据编号', day: '归属日期', amount: '参与金额',
        source: '来源类型', detail: '详细依据', snapshotId: '计算编号', period: '核算期间',
        rowAction: '口径与依据', rowField: '选择字段', numberValue: '参与值',
    }};
    const t = key => TEXT['zh-CN'][key] || key;
    const esc = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
    let current = null, generation = 0, request = 0, dialog = null, returnFocus = null;
    const rowStates = new Map();
    let rowCurrent = null;
    const active = state => current === state && state.generation === generation && state.hooks.isCurrent();
    const money = (value, key) => {
        if (value == null || value === '') return '—';
        const n = Number(value);
        if (!Number.isFinite(n)) return '—';
        return key === 'gross_margin' ? new Intl.NumberFormat('zh-CN', {style:'percent', maximumFractionDigits:2, minimumFractionDigits:2}).format(n)
            : new Intl.NumberFormat('zh-CN', {style:'currency', currency:'CNY'}).format(n);
    };
    const names = {purchase:'采购', head_shipping:'头程', last_mile:'尾程', other:'其他入库',
        currency:'币种', report_month:'核算月份', rate_to_cny:'报表汇率', rate_date:'实际取率日期', source:'来源',
        mode:'取率方式', is_provisional:'临时汇率', status:'状态', target_date:'目标日期', manual:'人工', auto:'自动'};
    function detailText(value) {
        if (value == null) return t('missing');
        if (Array.isArray(value)) return value.map(detailText).join('\n');
        if (typeof value === 'object') return Object.entries(value).map(([key, item]) => `${names[key] || key}：${detailText(item)}`).join('\n');
        return String(value);
    }
    function setup() {
        if (dialog) return;
        const style = document.createElement('style');
        style.textContent = `
            .ple-action {border:0;background:transparent;color:#155e9a;text-decoration:underline;cursor:pointer;font:inherit;font-size:12px;padding:3px 6px;border-radius:4px;}
            .ple-action:focus-visible {outline:2px solid #155e9a;outline-offset:2px;}
            .ple-dialog {margin:0 0 0 auto;border:0;border-left:1px solid #dce4ed;width:min(860px,96vw);max-width:96vw;height:100dvh;max-height:100dvh;padding:24px;color:#16354d;background:#fff;box-sizing:border-box;}
            .ple-dialog::backdrop {background:rgba(15,35,55,.3);}
            .ple-header {display:flex;justify-content:space-between;align-items:center;gap:16px;position:sticky;top:-24px;background:white;padding:12px 0;z-index:1;}
            .ple-dialog h3 {margin:0;} .ple-dialog h4 {margin:22px 0 10px;}
            .ple-dialog select {padding:8px 12px;border:1px solid #cbd9e7;border-radius:6px;font:inherit;color:inherit;max-width:100%;background:#fff;}
            .ple-context {color:#52687d;white-space:pre-wrap;overflow-wrap:anywhere;}
            .ple-note {background:#eff5fa;padding:12px;border-radius:8px;white-space:pre-wrap;line-height:1.7;}
            .ple-value {font-size:24px;font-weight:700;margin:12px 0;}
            .ple-calculation {padding:10px;background:#f6f8fa;border-radius:6px;white-space:pre-wrap;overflow-wrap:anywhere;}
            .ple-record {border:1px solid #dce4ed;border-radius:8px;padding:12px;margin:10px 0;}
            .ple-record pre {white-space:pre-wrap;overflow-wrap:anywhere;font:inherit;font-size:13px;line-height:1.6;margin:8px 0;}
            .ple-error {color:#9f302c;background:#fff1ef;padding:12px;white-space:pre-wrap;}
            .ple-pages {display:flex;gap:12px;align-items:center;justify-content:flex-end;margin:12px 0;}
            .ple-summary {margin-top:24px;border-top:1px solid #dce4ed;padding-top:16px;}
            .ple-summary table {min-width:700px;} .ple-summary td {text-align:right;}
            .ple-summary td:first-child {text-align:left;} .ple-tools {margin:10px 0;}
        `;
        document.head.append(style);
        dialog = document.createElement('dialog');
        dialog.className = 'ple-dialog';
        dialog.setAttribute('aria-label', t('title'));
        dialog.addEventListener('close', () => { request++; returnFocus?.isConnected && returnFocus.focus(); });
        document.body.append(dialog);
        document.addEventListener('change', event => {
            if (['inventory-impairment-date', 'inventory-report-view', 'profit-loss-month', 'profit-loss-store'].includes(event.target.id)) {
                for (const [kind, state] of rowStates) if (!state.hooks.isCurrent()) clearRows(kind);
            }
        });
    }
    function close() { request++; if (dialog?.open) dialog.close(); }
    function clear() {
        generation++; current = null; close();
        document.querySelectorAll('.ple-action[data-ple-added]').forEach(n => n.remove());
        for (const id of ['profit-loss-evidence-tools', 'profit-loss-summary']) {
            const node = document.getElementById(id); if (node) node.replaceChildren();
        }
    }
    function shell(title, context, content) {
        setup();
        if (!dialog.open) returnFocus = document.activeElement;
        dialog.innerHTML = `<div class="ple-header"><h3>${esc(title)}</h3><button class="btn btn-secondary" data-ple-close>${t('close')}</button></div><p class="ple-context">${esc(context)}</p>${content}`;
        dialog.querySelector('[data-ple-close]').onclick = close;
        if (!dialog.open) dialog.showModal();
    }
    function addButton(parent, text, callback) {
        const button = document.createElement('button');
        button.type = 'button'; button.className = 'ple-action'; button.dataset.pleAdded = 'true';
        button.textContent = text; button.addEventListener('click', callback); parent.append(button);
        return button;
    }
    function render(report, hooks) {
        clear(); setup();
        const state = current = {report, hooks, generation};
        const rows = document.querySelectorAll('#store-profit-loss-tbody > tr');
        report.rows.forEach((row, index) => {
            const element = rows[index]; if (!element?.cells?.length) return;
            addButton(element.cells[0], t('definition'), () => openField(state, report, row.key, 'current'));
            ['current', 'ytd'].forEach((period, i) => addButton(element.cells[i + 1], t('evidence'), () => openField(state, report, row.key, period)));
        });
        const tools = document.getElementById('profit-loss-evidence-tools');
        if (tools && hooks.isAdmin) {
            tools.className = 'ple-tools';
            const button = addButton(tools, t('summary'), () => loadSummary(state, button));
        }
    }
    function openField(state, report, key, period) {
        if (!active(state)) return;
        rowCurrent = null;
        const evidence = report.evidence, field = evidence?.schema_version === 'profit_loss_evidence_v1' && evidence.fields?.[key];
        const row = report.rows?.find(r => r.key === key);
        const label = field?.label || row?.label || key;
        const context = `${report.store_name} · ${report.report_month}\n${t('snapshot')}`;
        if (!field) {
            request++;
            shell(label, context, `<p class="ple-error">${t('legacy')}</p><p class="ple-note">${esc(row?.source || '')}</p>`);
            return;
        }
        const cell = field.cells[period], id = ++request;
        const expression = cell.inputs.map((input, i) => `${i ? input.operator + ' ' : ''}${input.label} (${input.blank_as_zero ? t('blank') : money(input.value, input.key)})`).join(' ');
        shell(label, context, `<select aria-label="${t('period')}" data-ple-period>${['current','previous','ytd'].map(p => `<option value="${p}" ${p === period ? 'selected' : ''}>${t(p)}</option>`).join('')}</select>
            <div class="ple-value">${esc(money(cell.value, key))}</div><p>${esc(evidence.status_labels[cell.status] || cell.status)}</p>
            ${(evidence.warnings || []).map(w => `<p class="ple-error">${esc(w)}</p>`).join('')}
            <h4>${t('note')}</h4><div class="ple-note">${esc(field.note)}</div>
            <h4>${t('formula')}</h4><div class="ple-calculation">${esc(field.formula)}\n${esc(expression)}${expression ? ' = ' + esc(money(cell.value, key)) : ''}</div>
            ${cell.inputs.length ? '' : `<p>${t('sourceTotal')}：${esc(cell.source_total ?? t('missing'))}</p>`}
            <div data-ple-inputs>${cell.inputs.map(input => `<button type="button" class="ple-action" data-ple-key="${esc(input.key)}">${esc(input.label)} · ${t('evidence')}</button>`).join('')}</div>
            <p class="ple-context">${esc(evidence.notes.rounding)}\n${esc(t('snapshotId'))}：${esc(evidence.calculation_id)}</p>
            <h4>${t('sources')} · ${cell.source_count} ${t('count')}</h4><div data-ple-records></div><div class="ple-pages" data-ple-pages></div>`);
        dialog.querySelector('[data-ple-period]').onchange = event => openField(state, report, key, event.target.value);
        dialog.querySelectorAll('[data-ple-key]').forEach(button => button.onclick = () => openField(state, report, button.dataset.pleKey, period));
        if (!cell.source_count) {
            dialog.querySelector('[data-ple-records]').textContent = t('empty'); return;
        }
        loadSources(state, report, key, period, 1, id);
    }
    async function loadSources(state, report, key, period, page, viewId) {
        if (!active(state) || request !== viewId) return;
        viewId = ++request;
        const target = dialog.querySelector('[data-ple-records]'), pages = dialog.querySelector('[data-ple-pages]');
        target.className = ''; target.textContent = t('loading'); pages.replaceChildren();
        const evidence = report.evidence;
        try {
            let data;
            if (evidence.sources) {
                const rows = (evidence.sources[key] || []).filter(r => r.periods.includes(period));
                data = {calculation_id:evidence.calculation_id, field:key, period, total:rows.length, items:rows.slice((page - 1) * 50, page * 50)};
            } else {
                if (!evidence.token) throw new Error(t('unavailable'));
                const params = new URLSearchParams({field:key, period, page, page_size:50});
                data = await state.hooks.api('GET', `/reports/store-profit-loss/evidence/${encodeURIComponent(evidence.token)}?${params}`, undefined,
                    {cache:'no-store', isCurrent: () => active(state) && request === viewId && dialog.open});
            }
            if (!active(state) || request !== viewId || !dialog.open) return;
            if (data.calculation_id !== evidence.calculation_id || data.field !== key || data.period !== period) throw new Error(t('mismatch'));
            target.innerHTML = data.items.map(row => `<article class="ple-record"><strong>${esc(evidence.source_labels[row.source] || row.source)} · ${esc(row.reference)}</strong><p>${esc(row.day)} · ${esc(t('amount'))}：${esc(row.amount ?? t('missing'))}</p><pre>${esc(detailText(row.details))}</pre></article>`).join('') || esc(t('empty'));
            const totalPages = Math.max(1, Math.ceil(data.total / 50));
            const prev = addButton(pages, t('prev'), () => loadSources(state, report, key, period, page - 1, viewId));
            prev.disabled = page <= 1;
            const text = document.createElement('span'); text.textContent = `${page} / ${totalPages} ${t('page')} · ${data.total} ${t('count')}`; pages.append(text);
            const next = addButton(pages, t('next'), () => loadSources(state, report, key, period, page + 1, viewId)); next.disabled = page >= totalPages;
        } catch (error) {
            if (!active(state) || request !== viewId || !dialog.open) return;
            target.className = 'ple-error'; target.textContent = error.message || t('expired');
        }
    }
    async function loadSummary(state, button) {
        if (!active(state)) return;
        const target = document.getElementById('profit-loss-summary'); if (!target) return;
        button.disabled = true; target.className = 'ple-summary'; target.textContent = t('summaryLoading');
        try {
            const data = await state.hooks.api('GET', `/reports/store-profit-loss-summary?report_month=${encodeURIComponent(state.report.report_month + '-01')}`, undefined,
                {cache:'no-store', isCurrent: () => active(state)});
            if (!active(state)) return;
            state.summary = data;
            target.innerHTML = `<h3>${t('summaryTitle')} · ${esc(data.report_month)}</h3><p>${t('summaryRule')}</p>
                ${data.stores.flatMap(s => (s.warnings || []).map(w => `<p class="ple-error">${esc(s.name)}：${esc(w)}</p>`)).join('')}
                <div class="table-scroll"><table><thead><tr><th>${t('field')}</th>${data.stores.map(s => `<th>${esc(s.name)}</th>`).join('')}<th>${t('total')}</th><th>${t('ratio')}</th></tr></thead><tbody>${data.rows.map(row => `<tr><td>${esc(row.label)}</td>${data.stores.map((s, i) => `<td><button class="ple-action" data-ple-store="${i}" data-ple-key="${esc(row.key)}">${esc(money(row.values[i], row.key))}</button></td>`).join('')}<td><button class="ple-action" data-ple-total="total" data-ple-key="${esc(row.key)}">${esc(money(row.total, row.key))}</button></td><td><button class="ple-action" data-ple-total="ratio" data-ple-key="${esc(row.key)}">${esc(money(row.ratio, 'gross_margin'))}</button></td></tr>`).join('')}</tbody></table></div>`;
            target.querySelectorAll('[data-ple-store]').forEach(b => b.onclick = () => openStore(state, Number(b.dataset.pleStore), b.dataset.pleKey));
            target.querySelectorAll('[data-ple-total]').forEach(b => b.onclick = () => openTotal(state, b.dataset.pleKey, b.dataset.pleTotal));
        } catch (error) {
            if (active(state)) { target.className = 'ple-error'; target.textContent = error.message; }
        } finally { if (active(state)) button.disabled = false; }
    }
    function openStore(state, index, key) {
        const store = state.summary.stores[index];
        openField(state, {store_id:store.id, store_name:store.name, report_month:state.summary.report_month, evidence:store.evidence}, key, 'current');
    }
    function openTotal(state, key, mode) {
        if (!active(state)) return;
        rowCurrent = null;
        request++;
        const data = state.summary, row = data.rows.find(r => r.key === key);
        const formula = key === 'gross_margin' ? t(mode === 'ratio' ? 'noRatio' : 'marginRule') : t(mode === 'ratio' ? 'ratioRule' : 'sumRule');
        const dependencies = key === 'gross_margin' && mode === 'ratio' ? [] : key === 'gross_margin' ? ['gross_profit', 'operating_revenue'] : mode === 'ratio' ? [key, 'operating_revenue'] : [key];
        const display = (value, field) => value == null ? t('missing') : money(value, field);
        const expression = dependencies.length === 2 ? dependencies.map(dep => {
            const item = data.rows.find(r => r.key === dep);
            return `${item.label.trim()}(${display(item.total, dep)})`;
        }).join(' ÷ ') + ` = ${display(row[mode], 'gross_margin')}` : '';
        shell(`${row.label} · ${t(mode)}`, `${data.report_month}\n${t('summaryRule')}`, `<div class="ple-value">${esc(money(row[mode], mode === 'ratio' ? 'gross_margin' : key))}</div><p class="ple-note">${esc(formula)}</p>${expression ? `<div class="ple-calculation">${esc(expression)}</div>` : ''}${dependencies.map(dep => {
            const item = data.rows.find(r => r.key === dep);
            return `<h4>${esc(item.label)} · ${esc(money(item.total, dep))}</h4>${data.stores.map((s, i) => `<p><button class="ple-action" data-ple-store="${i}" data-ple-key="${esc(dep)}">${esc(s.name)} · ${esc(money(item.values[i], dep))} · ${t('evidence')}</button></p>`).join('')}`;
        }).join('')}`);
        dialog.querySelectorAll('[data-ple-store]').forEach(b => b.onclick = () => openStore(state, Number(b.dataset.pleStore), b.dataset.pleKey));
    }
    const rowActive = state => rowStates.get(state.kind) === state && state.hooks.isCurrent();
    function clearRows(kind) {
        for (const [key, state] of rowStates) {
            if (kind && key !== kind) continue;
            rowStates.delete(key);
            state.buttons.forEach(button => button.remove());
            if (rowCurrent === state) { rowCurrent = null; close(); }
        }
    }
    function renderRows(kind, rows, hooks) {
        clearRows(kind); setup();
        const state = {kind, hooks, buttons:[]};
        rowStates.set(kind, state);
        const add = (parent, meta, title) => {
            if (!parent) return;
            const button = addButton(parent, t('rowAction'), () => openRow(state, meta, title));
            delete button.dataset.pleAdded; button.dataset.pleRowAdded = kind;
            state.buttons.push(button);
        };
        const elements = document.querySelectorAll(`${hooks.tbody} > tr`);
        rows.forEach((row, i) => add(elements[i]?.cells?.[hooks.labelColumn || 0], row.evidence,
            row.name || [row.sku, row.batch_no || row.product_name].filter(Boolean).join(' · ')));
        if (rows.length && hooks.tfoot) {
            const meta = rows[0].evidence;
            add(document.querySelector(`${hooks.tfoot} td`), meta?.total_calculation_id ?
                {...meta, row_key:'__total__', calculation_id:meta.total_calculation_id} : null, t('total'));
        }
    }
    function rowValue(value, format) {
        if (value == null || value === '') return '—';
        if (format === 'money') return money(value);
        if (format === 'percent') return money(value, 'gross_margin');
        return new Intl.NumberFormat('zh-CN', {maximumFractionDigits:8}).format(Number(value));
    }
    async function openRow(state, meta, title, field = '', page = 1) {
        if (!rowActive(state)) return;
        rowCurrent = state;
        const id = ++request;
        if (!meta || meta.schema_version !== 'report_row_evidence_v1') {
            shell(title, state.hooks.context || '', `<p class="ple-note">${t('legacy')}</p>`); return;
        }
        shell(title, state.hooks.context || '', `<p class="ple-context">${t('loading')}</p>`);
        const isCurrent = () => rowActive(state) && rowCurrent === state && request === id && dialog.open;
        try {
            if (!meta.token) throw new Error(t('unavailable'));
            const params = new URLSearchParams({row_key:meta.row_key, field, page, page_size:50});
            const data = await state.hooks.api('GET', `/reports/row-evidence/${encodeURIComponent(meta.token)}?${params}`, undefined,
                {cache:'no-store', isCurrent});
            if (!isCurrent()) return;
            if (data.calculation_id !== meta.calculation_id || String(data.row_key) !== String(meta.row_key)
                || (field && data.field !== field)) throw new Error(t('mismatch'));
            const cell = data.fields[data.field];
            const expression = cell.expression || cell.inputs.map((input, index) => `${index ? input.operator + ' ' : ''}${input.label} (${input.value ?? t('missing')})`).join(' ') + (cell.inputs.length ? ' = ' + (cell.value ?? t('missing')) : '');
            shell(data.title, `${data.context}\n${t('snapshot')}`, `<select data-ple-row-field aria-label="${t('rowField')}">${Object.entries(data.fields).map(([key, item]) => `<option value="${esc(key)}" ${key === data.field ? 'selected' : ''}>${esc(item.label)}</option>`).join('')}</select>
                <div class="ple-value">${esc(rowValue(cell.value, cell.format))}</div><p>${esc(data.status_labels[cell.status] || cell.status)}</p>
                ${data.warning ? `<p class="ple-error">${esc(data.warning)}</p>` : ''}
                <h4>${t('note')}</h4><div class="ple-note">${esc(cell.note)}</div>
                <h4>${t('formula')}</h4><div class="ple-calculation">${esc(cell.formula)}\n${esc(expression)}</div>
                ${!cell.inputs.length ? `<p>${t('sourceTotal')}：${esc(cell.source_total ?? t('missing'))}</p>` : ''}
                <div>${cell.inputs.map(input => `<button class="ple-action" data-ple-row-input="${esc(input.key)}">${esc(input.label)} · ${t('evidence')}</button>`).join('')}</div>
                <p class="ple-context">${esc(data.rounding)}\n${t('snapshotId')}：${esc(data.calculation_id)}</p>
                <h4>${t('sources')} · ${data.total} ${t('count')}</h4><div data-ple-records>${data.items.map(row => `<article class="ple-record"><strong>${esc(row.source)} · ${esc(row.reference)}</strong><p>${esc(row.day)} · ${t('numberValue')}：${esc(row.amount ?? t('missing'))}</p><pre>${esc(detailText(row.details))}</pre></article>`).join('') || esc(t('empty'))}</div><div class="ple-pages" data-ple-pages></div>`);
            dialog.querySelector('[data-ple-row-field]').onchange = event => openRow(state, meta, title, event.target.value);
            dialog.querySelectorAll('[data-ple-row-input]').forEach(b => b.onclick = () => openRow(state, meta, title, b.dataset.pleRowInput));
            if (data.total) {
                const pages = dialog.querySelector('[data-ple-pages]'), totalPages = Math.ceil(data.total / 50);
                addButton(pages, t('prev'), () => openRow(state, meta, title, data.field, page - 1)).disabled = page <= 1;
                const label = document.createElement('span'); label.textContent = `${page} / ${totalPages} ${t('page')}`; pages.append(label);
                addButton(pages, t('next'), () => openRow(state, meta, title, data.field, page + 1)).disabled = page >= totalPages;
            }
        } catch (error) {
            if (isCurrent()) shell(title, state.hooks.context || '', `<p class="ple-error">${esc(error.message || t('expired'))}</p>`);
        }
    }
    window.ProfitLossEvidence = {render, clear, renderRows, clearRows};
})();
