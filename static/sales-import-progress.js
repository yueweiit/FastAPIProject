/* Actual per-stage row progress. Completion is acknowledged only after commit. */
(() => {
    'use strict';
    let generation = 0;
    let dismissTimer = null;
    const dismissDelay = 8000;
    const text = (key, values = {}) => Object.entries(values).reduce(
        (value, [name, replacement]) => value.replaceAll(`{${name}}`, String(replacement)),
        window.PageInteractions.text(key));
    const node = id => document.getElementById(`sales-import-progress-${id}`);
    const current = token => token === generation;
    function clearDismissTimer() {
        if (dismissTimer !== null) clearTimeout(dismissTimer);
        dismissTimer = null;
    }
    function scheduleDismiss(leavingFocus = false) {
        clearDismissTimer();
        const panel = node('panel');
        if (panel.hidden || panel.dataset.state !== 'complete' || !node('details').hidden ||
            panel.matches(':hover') || (!leavingFocus && panel.matches(':focus-within'))) return;
        const token = generation;
        dismissTimer = setTimeout(() => {
            if (current(token) && panel.dataset.state === 'complete' && node('details').hidden &&
                !panel.matches(':hover, :focus-within')) reset();
        }, dismissDelay);
    }
    function toggleDetails() {
        if (node('panel').dataset.state === 'running') return;
        const expanded = node('details').hidden;
        node('details').hidden = !expanded;
        node('toggle').setAttribute('aria-expanded', String(expanded));
        node('toggle').textContent = text(expanded ? 'importHideDetails' : 'importShowDetails');
        scheduleDismiss();
    }
    function finish() {
        clearDismissTimer();
        node('actions').hidden = false;
        node('count').hidden = true;
        node('details').hidden = true;
        node('brief').hidden = false;
        node('toggle').textContent = text('importShowDetails');
        node('toggle').setAttribute('aria-expanded', 'false');
        node('close').textContent = text('importDismiss');
    }
    function stage(event, token) {
        if (!current(token) || node('panel').dataset.state !== 'running') return;
        const names = {uploading:'importUploading', parsing:'importParsing', validating:'importValidating',
            exchange_rates:'importExchangeRates', inventory:'importInventory', writing:'importWriting', committing:'importCommitting'};
        if (!names[event.stage]) return;
        node('stage').textContent = text(names[event.stage]);
        const processed = event.processed, total = event.total;
        if (Number.isInteger(total) && total > 0 && Number.isInteger(processed) && processed >= 0 && processed <= total) {
            node('bar').max = total; node('bar').value = processed;
            node('count').textContent = text('importRowProgress', {done:processed, total, percent:Math.floor(processed / total * 100)});
        } else {
            node('bar').removeAttribute('value');
            node('count').textContent = text('importWorking');
        }
    }
    function begin(file, store) {
        clearDismissTimer();
        const token = ++generation;
        node('panel').hidden = false;
        node('panel').dataset.state = 'running';
        node('actions').hidden = true;
        node('count').hidden = false;
        node('details').hidden = false;
        node('brief').hidden = true;
        node('brief').textContent = '';
        node('file').textContent = text('importFileStore', {file:file.name, store});
        node('result').textContent = '';
        stage({stage:'uploading'}, token);
        return token;
    }
    async function consume(response, token) {
        if (response.status === 404) throw new Error(text('importProgressUnavailable'));
        if (!response.ok || !response.headers?.get('content-type')?.includes('application/x-ndjson')) {
            return {status:response.status, data:await response.json()};
        }
        const reader = response.body.getReader(), decoder = new TextDecoder();
        let buffer = '', result = null;
        const line = value => {
            if (!value.trim()) return;
            const event = JSON.parse(value);
            if (event.type === 'progress') stage(event, token);
            if (event.type === 'result') result = event;
        };
        try {
            while (true) {
                const {done, value} = await reader.read();
                buffer += decoder.decode(value, {stream:!done});
                let end;
                while ((end = buffer.indexOf('\n')) >= 0) {
                    line(buffer.slice(0, end)); buffer = buffer.slice(end + 1);
                }
                if (done) break;
            }
            if (buffer.trim()) line(buffer);
            if (!result || !Number.isInteger(result.status) || !result.data) throw new Error('Incomplete import stream');
            return result;
        } catch (_error) {
            throw new Error(text('importConnectionLost'));
        } finally {
            reader.releaseLock();
        }
    }
    function complete(data, message, token) {
        if (!current(token)) return;
        const review = data.pending_confirmation > 0 || data.warnings?.length > 0;
        node('panel').dataset.state = review ? 'review' : 'complete';
        node('stage').textContent = text(review ? 'importCompleteReview' : 'importComplete');
        node('bar').max = 100; node('bar').value = 100;
        node('count').textContent = '100%';
        node('result').textContent = message;
        finish();
        node('brief').textContent = text('importCompactSummary', {
            settlements:data.settlement_rows, sales:data.sales_created, duplicates:data.skipped_duplicates,
            pending:data.pending_confirmation ? text('importPending', {count:data.pending_confirmation}) : '',
            warnings:data.warnings?.length ? text('importWarnings', {count:data.warnings.length}) : '',
        });
        scheduleDismiss();
    }
    function fail(message, token) {
        if (!current(token)) return;
        node('panel').dataset.state = 'error';
        node('stage').textContent = text('importFailed');
        node('bar').max = 1; node('bar').value = 0;
        node('count').textContent = '';
        node('result').textContent = '';
        finish();
        node('brief').textContent = message;
    }
    function reset() { clearDismissTimer(); ++generation; node('panel').hidden = true; }
    function summary(data) {
        return text('importSuccess', {settlements:data.settlement_rows, sales:data.sales_created,
            duplicates:data.skipped_duplicates,
            pending:data.pending_confirmation ? text('importPending', {count:data.pending_confirmation}) : '',
            warnings:data.warnings?.length ? text('importWarnings', {count:data.warnings.length}) : ''});
    }
    node('toggle').addEventListener('click', toggleDetails);
    node('close').addEventListener('click', () => {
        if (node('panel').dataset.state !== 'running') reset();
    });
    node('panel').addEventListener('mouseenter', clearDismissTimer);
    node('panel').addEventListener('mouseleave', () => scheduleDismiss());
    node('panel').addEventListener('focusin', clearDismissTimer);
    node('panel').addEventListener('focusout', event => {
        if (!node('panel').contains(event.relatedTarget)) scheduleDismiss(true);
    });
    window.SalesImportProgress = {begin, consume, complete, fail, reset, summary};
})();
