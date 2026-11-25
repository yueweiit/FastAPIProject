// Store/month-scoped report rates. All financial arithmetic stays on the server.
(function () {
    'use strict';
    let current = null, generation = 0;
    // Pending writes outlive the editor so a navigation reload cannot read before commit.
    const pendingSaves = new Set();
    function trackSave(promise) {
        pendingSaves.add(promise);
        const settled = () => pendingSaves.delete(promise);
        promise.then(settled, settled);
        return promise;
    }
    const t = key => window.PageInteractions.text(key);
    const esc = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
    const node = () => document.getElementById('report-fx-editor');
    const active = state => current === state && state.generation === generation && state.hooks.isCurrent();
    const field = key => node()?.querySelector(`[data-fx-${key}]`);
    const settings = state => state.report.exchange_rate_settings || [];
    function selected(state) { return settings(state).find(item => item.currency === field('currency')?.value); }
    function notice(message, error = false) {
        const target = field('message');
        if (!target) return;
        target.textContent = message;
        target.className = error ? 'msg msg-err' : 'msg msg-ok';
        target.hidden = !message;
    }
    function clear() {
        generation++;
        current = null;
        const target = node();
        if (target) { target.innerHTML = ''; target.hidden = true; }
    }
    function controls(state) {
        if (!active(state)) return;
        node().querySelectorAll('input, select, textarea, button').forEach(item => item.disabled = state.busy);
        const saved = selected(state);
        if (field('restore')) field('restore').disabled = state.busy || saved?.mode !== 'manual';
        state.hooks.onBusy();
    }
    function describe(rate) {
        if (!rate || rate.rate_to_cny == null) return t('reportFxReferenceMissing');
        return `1 ${rate.currency} = ${rate.rate_to_cny} CNY · ${rate.rate_date || '—'} · ${rate.source || '—'}`;
    }
    function selectCurrency(state) {
        const setting = selected(state);
        if (!setting) return;
        state.referenceId++;
        field('rate').value = setting.manual_rate || '';
        field('note').value = setting.note || '';
        const effective = (state.report.exchange_rates || []).find(item => item.currency === setting.currency && item.report_month === state.month);
        field('effective').textContent = effective?.rate_to_cny != null
            ? `1 ${setting.currency} = ${effective.rate_to_cny} CNY · ${t(setting.mode === 'manual' ? 'reportFxManual' : 'reportFxAuto')}${effective.is_provisional ? ` · ${t('reportFxProvisional')}` : ''}`
            : t('profitLossRatePending');
        field('reference-value').textContent = setting.mode === 'auto' ? describe(effective) : t('reportFxReferenceMissing');
        field('updated').textContent = setting.updated_at
            ? `${setting.updated_by || '—'} · ${new Date(setting.updated_at).toLocaleString(document.documentElement.lang || 'zh-CN')}` : '';
        notice('');
        controls(state);
    }
    async function reference(state) {
        if (!active(state)) return;
        const setting = selected(state), currency = setting?.currency, requestId = ++state.referenceId;
        if (!currency) return;
        field('reference-value').textContent = t('reportFxReferenceLoading');
        try {
            const params = new URLSearchParams({store_id: state.report.store_id, report_month: `${state.month}-01`, currency});
            const rate = await state.hooks.api('GET', `/reports/store-exchange-rate/reference?${params}`, undefined, {
                isCurrent: () => active(state) && requestId === state.referenceId && selected(state)?.currency === currency,
            });
            if (active(state) && requestId === state.referenceId && selected(state)?.currency === currency) field('reference-value').textContent = describe(rate);
        } catch (error) {
            if (active(state) && requestId === state.referenceId && selected(state)?.currency === currency) field('reference-value').textContent = t('reportFxReferenceMissing');
        }
    }
    async function save(state, mode) {
        if (!active(state) || state.busy || !state.hooks.canStart()) return;
        const setting = selected(state);
        if (!setting) return;
        const rate = field('rate').value.trim();
        if (mode === 'manual' && (!/^(?:\d+(?:\.\d{1,8})?|\.\d{1,8})$/.test(rate) || !Number.isFinite(Number(rate)) || Number(rate) <= 0)) {
            notice(t('reportFxInvalid'), true); return;
        }
        state.busy = true;
        controls(state);
        notice(t('reportFxSaving'));
        try {
            const result = await trackSave(state.hooks.api('PUT', '/reports/store-exchange-rate', {
                store_id: state.report.store_id, report_month: `${state.month}-01`, currency: setting.currency,
                mode, manual_rate: mode === 'manual' ? rate : null,
                note: field('note').value.trim(), expected_revision: setting.revision,
            }, {isCurrent: () => active(state)}));
            if (!active(state)) return;
            Object.assign(setting, result.setting);
            // The amount table is stale until the separate report refresh succeeds.
            state.report.exchange_rates = [];
            state.busy = false;
            selectCurrency(state);
            state.hooks.notify(t('reportFxSaved'));
            await state.hooks.onSaved();
        } catch (error) {
            if (active(state)) notice(error.message || t('reportFxSaveFailed'), true);
        } finally {
            if (active(state)) { state.busy = false; controls(state); }
        }
    }
    function render(report, hooks) {
        clear();
        if (!node() || !report.supports_manual_fx || !report.can_edit || !(report.exchange_rate_settings || []).length) return;
        const state = {report, hooks, month: String(report.report_month).slice(0, 7), generation, busy: false, referenceId: 0};
        current = state;
        node().hidden = false;
        node().innerHTML = `<details class="report-fx-panel"><summary>${esc(t('reportFxTitle'))}</summary>
            <p>${esc(t('reportFxScope'))}</p>
            <div class="report-fx-grid"><label class="form-group">${esc(t('reportFxCurrency'))}<select data-fx-currency>${settings(state).map(item => `<option value="${esc(item.currency)}">${esc(item.currency)} → CNY</option>`).join('')}</select></label>
            <label class="form-group">${esc(t('reportFxRate'))}<input data-fx-rate type="text" inputmode="decimal" autocomplete="off" maxlength="24" placeholder="0.3711"></label>
            <label class="form-group report-fx-note">${esc(t('reportFxNote'))}<input data-fx-note type="text" maxlength="500"></label></div>
            <p><strong>${esc(t('reportFxEffective'))}</strong> <span data-fx-effective></span></p>
            <p><strong>${esc(t('reportFxReference'))}</strong> <span data-fx-reference-value></span> <button type="button" class="btn btn-secondary" data-fx-reference>${esc(t('reportFxRefreshReference'))}</button></p>
            <p data-fx-updated></p><div data-fx-message role="status" aria-live="polite" hidden></div>
            <div class="report-fx-actions"><button type="button" class="btn btn-primary" data-fx-save>${esc(t('reportFxSave'))}</button><button type="button" class="btn btn-secondary" data-fx-restore>${esc(t('reportFxRestore'))}</button></div></details>`;
        field('currency').addEventListener('change', () => { selectCurrency(state); });
        field('reference').addEventListener('click', () => reference(state));
        field('save').addEventListener('click', () => save(state, 'manual'));
        field('restore').addEventListener('click', () => save(state, 'auto'));
        selectCurrency(state);
    }
    window.ReportExchangeRates = {render, clear, isBusy: () => Boolean(current?.busy),
        waitForPendingSaves: () => Promise.allSettled([...pendingSaves])};
})();
