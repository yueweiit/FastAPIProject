/* 导入历史只读结果；复用 api 的认证及旧会话响应隔离，不触发重试或库存写入。 */
(() => {
    'use strict';
    let serial = 0;
    const node = id => document.getElementById(`sales-import-result-${id}`);
    const dialog = node('dialog');
    const userId = () => typeof CURRENT_USER_ID === 'undefined' ? 0 : CURRENT_USER_ID;
    const text = (key, values) => window.PageInteractions.formatText(key, values);
    function clear() {
        node('body').hidden = true;
        for (const id of ['status', 'file', 'meta', 'notice', 'current', 'heading', 'summary', 'messages']) node(id).textContent = '';
    }
    function reset() {
        serial++;
        clear();
        if (dialog.open) dialog.close();
    }
    function render(data) {
        const result = data.result, rolledBack = data.status === 'rolled_back';
        node('file').textContent = text('importFileStore', {file:data.file_name, store:data.store_name || '—'});
        node('meta').textContent = text('importResultMeta', {
            time:String(data.imported_at || '').replace('T', ' '), user:data.imported_by || '—'});
        node('notice').textContent = [rolledBack ? text('importResultRolledBack') : '',
            result ? '' : text('importResultLegacy')].filter(Boolean).join('\n');
        node('notice').hidden = !node('notice').textContent;
        node('current').hidden = rolledBack || !result;
        node('current').textContent = result ? text('importResultCurrent', {pending:data.pending_confirmation}) : '';
        node('heading').textContent = text(result ? 'importResultSnapshot' : 'importResultExisting');
        node('summary').textContent = window.SalesImportProgress.summary(result || data);
        node('messages').textContent = result
            ? window.SalesImportProgress.detailText(result) || text('importResultNoIssues') : '';
        node('status').textContent = '';
        node('body').hidden = false;
    }
    async function open(batchId) {
        if (!Number.isSafeInteger(batchId) || batchId <= 0 || !userId()) return;
        const request = ++serial, user = userId();
        const isCurrent = () => request === serial && dialog.open && user === userId();
        clear();
        node('title').textContent = text('importResultTitle');
        node('close').textContent = text('importResultClose');
        node('status').textContent = text('importResultLoading');
        if (!dialog.open) dialog.showModal();
        try {
            const data = await api('GET', `/sales/imports/${batchId}/result`, undefined, {isCurrent, cache:'no-store'});
            if (isCurrent()) render(data);
        } catch (error) {
            if (isCurrent() && error.name !== 'AbortError') node('status').textContent = error.message || text('importResultFailed');
        }
    }
    node('close').addEventListener('click', reset);
    dialog.addEventListener('cancel', event => {event.preventDefault(); reset();});
    dialog.addEventListener('close', () => {
        if (!dialog.open) {serial++; clear();} // 已重新打开时忽略排队中的旧 close。
    });
    window.SalesImportResults = {open, reset};
})();
