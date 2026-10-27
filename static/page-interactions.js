/* 局部交互共用能力；业务日期不转换时区，确认操作使用页面内弹窗。 */
(() => {
    'use strict';
    const messages = {'zh-CN': {
        confirm: '确认', cancel: '取消', reason: '更正原因', requiredReason: '请填写更正原因。',
        longReason: '更正原因不能超过1000字。', rollbackTitle: '撤销导入',
        rollbackMessage: '确认撤销这次导入？销售记录将删除，已扣库存会恢复。',
        importStore: '导入店铺', chooseImportStore: '请选择导入店铺', importOrders: '导入订单详情',
        importSelectHelp: '请选择这次文件所属店铺', importBoundHelp: '文件将归入账号绑定的店铺',
        importNoBoundStore: '请联系管理员绑定有效店铺后导入', importStoresLoading: '正在加载导入店铺…',
        importFailed: '导入失败',
        batchExport: '导出', batchEdit: '编辑', batchDelete: '删除',
        batchExportSelectedLoading: '正在生成所选 {count} 条批次的文件，请稍候…',
        batchExportFilteredLoading: '正在生成当前筛选结果的文件，请稍候…',
        batchExportBusy: '已有批次导出正在进行，请等待当前文件生成。',
        batchExportReady: '文件已生成，请检查浏览器下载列表。',
        batchExportServerError: '批次导出失败（服务器返回 {status}），请联系管理员查看服务器日志。',
        batchEditHelp: '管理员和运营可编辑历史批次，已出库批次也可更正；每次保存记录修改日志。已出库批次修改数量或成本需启用成本追溯功能，已出库数量会保留。',
        batchEditRole: '当前账号没有批次编辑权限。',
        batchEditRestatement: '入库数量不能小于已出库数量。数量或成本更正会追加成本差额并追溯原销售月，旧月度版本保留，核对后另存新版；修改单号不会重算库存。',
        batchEditInvalidField: '请检查“{field}”：{reason}',
        batchEditInvalidValue: '填写内容不符合要求。',
        batchEditSaving: '正在保存批次…',
        batchQuantityInconsistent: '批次余量与销售明细不一致，请先核对库存流水。',
        batchQuantityBelowSold: '入库数量不能小于已出库数量（{quantity}）。',
        batchCostControlsRequired: '已出库批次修改数量或成本需要先准备财务结构并启用成本追溯功能；单号等资料仍可修改。',
        batchRestatementModeRequired: '当前页面或调用方未声明成本追溯编辑模式，请更新后重新打开编辑，避免覆盖已确认成本。',
        batchCostOutOfRange: '更正后的金额或成本差额超出字段范围，请核对入库数量和金额。',
        batchEditSaved: '批次已更新。', batchEditRefreshFailed: '批次已更新，但列表刷新失败，请点击刷新。',
        batchEditVersionRequired: '缺少批次编辑版本，请刷新页面后重新打开编辑。',
        batchEditConflict: '批次已被其他操作修改，本次未保存。请先保留填写内容，关闭编辑并刷新列表，核对最新数据后重新编辑。',
        batchLogs: '日志', batchLogTitle: '批次修改日志', batchLogClose: '关闭',
        batchLogLoading: '正在加载日志…', batchLogEmpty: '暂无修改日志',
        batchLogNoChanges: '本次保存未更改字段', batchLogNull: '未填写',
        batchLogField: '字段', batchLogBefore: '修改前', batchLogAfter: '修改后',
        batchLogPrevious: '上一页', batchLogNext: '下一页',
        batchLogFailed: '日志加载失败，请重新打开日志重试。',
        batchLogFields: {quantity: '入库数量', remaining_quantity: '剩余数量', purchase_price: '采购单价',
            total_amount: '采购总金额', unit_cost: '单件成本', dingtalk_order_no: '采购钉钉单号',
            shipping_cost: '头程运费', shipping_cost_no: '头程运费单号', last_mile_cost: '尾程派送费',
            last_mile_cost_no: '尾程派送费单号', other_cost: '其他成本', other_cost_no: '其他成本单号',
            shipping_date: '头程发货时间', arrived_at: '入库时间'},
    }};
    const text = key => (messages[document.documentElement.lang] || messages['zh-CN'])[key];

    // MySQL DateTime/API 是无时区的业务时间；不能经过 Date/UTC 转换。
    const batchDateTimeInput = value => String(value || '').slice(0, 16);
    const batchDateTimeValue = (value, original) => {
        if (!value) return undefined;
        // datetime-local 以分钟编辑，未改日期时保留原秒数/小数秒。
        return original && value === batchDateTimeInput(original) ? original : value;
    };

    let dialog;
    let active = null;
    function finish(value) {
        if (!active) return;
        const pending = active;
        active = null;
        if (dialog.open) dialog.close();
        if (pending.focus?.isConnected) pending.focus.focus();
        pending.resolve(value);
    }
    function initialize() {
        if (dialog) return;
        dialog = document.createElement('dialog');
        dialog.id = 'page-action-dialog';
        dialog.className = 'product-import-dialog page-action-dialog';
        dialog.setAttribute('aria-labelledby', 'page-action-title');
        dialog.setAttribute('aria-describedby', 'page-action-message');
        dialog.innerHTML = `<form novalidate>
            <h3 id="page-action-title"></h3><p id="page-action-message"></p>
            <label id="page-action-reason-label" for="page-action-reason"><span></span>
                <textarea id="page-action-reason" rows="3" maxlength="1000"></textarea>
            </label><p id="page-action-error" role="alert" hidden></p>
            <div class="page-action-buttons"><button type="button" class="btn btn-secondary" data-cancel></button>
                <button type="submit" class="btn btn-danger" data-confirm></button></div>
        </form>`;
        document.body.appendChild(dialog);
        dialog.querySelector('[data-cancel]').onclick = () => finish(null);
        dialog.querySelector('form').onsubmit = event => {
            event.preventDefault();
            if (!active) return;
            const reason = dialog.querySelector('textarea');
            const value = reason.value.trim();
            if (active.reason && (!value || value.length > 1000)) {
                const error = dialog.querySelector('[role="alert"]');
                error.textContent = text(value ? 'longReason' : 'requiredReason');
                error.hidden = false;
                reason.focus();
                return;
            }
            finish(active.reason ? value : true);
        };
        dialog.addEventListener('cancel', event => { event.preventDefault(); finish(null); });
        dialog.addEventListener('close', () => { if (!dialog.open) finish(null); });
    }
    function request({title, message = '', reason = false, confirmLabel}) {
        // 连点或另一个操作不会替换正在确认的目标。
        if (active) return Promise.resolve(null);
        initialize();
        dialog.querySelector('h3').textContent = title;
        dialog.querySelector('#page-action-message').textContent = message;
        dialog.querySelector('label').hidden = !reason;
        dialog.querySelector('label span').textContent = text('reason');
        const input = dialog.querySelector('textarea');
        input.value = '';
        input.required = reason;
        dialog.querySelector('[role="alert"]').hidden = true;
        const cancel = dialog.querySelector('[data-cancel]');
        cancel.textContent = text('cancel');
        dialog.querySelector('[data-confirm]').textContent = confirmLabel || text('confirm');
        return new Promise(resolve => {
            active = {resolve, reason, focus: document.activeElement};
            try {
                dialog.showModal();
                (reason ? input : cancel).focus();
            } catch (error) {
                active = null;
                throw error;
            }
        });
    }
    let logDialog, logTarget = null, logPage = 1, logTotal = 0, logSerial = 0, logFocus;
    const logNode = selector => logDialog.querySelector(selector);
    const logEscape = value => String(value).replaceAll('&', '&amp;').replaceAll('<', '&lt;')
        .replaceAll('>', '&gt;').replaceAll('"', '&quot;').replaceAll("'", '&#39;');
    const logValue = value => logEscape(value == null ? text('batchLogNull') : value);
    function closeBatchLogs() {
        logSerial++; logTarget = null;
        if (logDialog?.open) logDialog.close();
        if (logDialog) {
            logNode('[data-log-entries]').innerHTML = '';
            logNode('[data-log-status]').textContent = '';
            logNode('#batch-log-title').textContent = '';
            logNode('[data-log-page]').textContent = '';
        }
        if (logFocus?.isConnected) logFocus.focus();
    }
    function initializeBatchLogs() {
        if (logDialog) return;
        logDialog = document.createElement('dialog');
        logDialog.id = 'batch-log-dialog';
        logDialog.className = 'product-import-dialog batch-log-dialog';
        logDialog.setAttribute('aria-labelledby', 'batch-log-title');
        logDialog.innerHTML = `<div class="product-import-heading"><h3 id="batch-log-title"></h3>
            <button type="button" class="btn btn-secondary" data-log-close></button></div>
            <p data-log-status role="status"></p><div data-log-entries></div>
            <div class="page-action-buttons"><button type="button" class="btn btn-secondary" data-log-previous></button>
            <span data-log-page></span><button type="button" class="btn btn-secondary" data-log-next></button></div>`;
        document.body.appendChild(logDialog);
        logNode('[data-log-close]').onclick = closeBatchLogs;
        logNode('[data-log-previous]').onclick = () => loadBatchLogs(logPage - 1);
        logNode('[data-log-next]').onclick = () => loadBatchLogs(logPage + 1);
        logDialog.addEventListener('cancel', event => { event.preventDefault(); closeBatchLogs(); });
    }
    async function loadBatchLogs(page) {
        const serial = ++logSerial, target = logTarget;
        logNode('[data-log-status]').textContent = text('batchLogLoading');
        logNode('[data-log-entries]').innerHTML = '';
        logNode('[data-log-page]').textContent = '';
        logNode('[data-log-previous]').disabled = true;
        logNode('[data-log-next]').disabled = true;
        try {
            const result = await api('GET', `/batches/${target}/logs?page=${page}&page_size=20`);
            if (serial !== logSerial || target !== logTarget || !logDialog.open) return;
            logPage = result.page; logTotal = result.total;
            const fields = text('batchLogFields');
            logNode('[data-log-entries]').innerHTML = result.items.map(row => {
                const before = row.before_values, after = row.after_values;
                const changed = Object.keys({...before, ...after}).filter(key => before[key] !== after[key]);
                const time = new Date(row.created_at).toLocaleString(document.documentElement.lang || 'zh-CN');
                return `<article class="batch-change-log-entry"><p><strong>${logEscape(row.actor_username)} (#${row.actor_id})</strong> · ${logEscape(time)}</p>
                    ${changed.length ? `<div class="table-scroll"><table><thead><tr><th>${text('batchLogField')}</th><th>${text('batchLogBefore')}</th><th>${text('batchLogAfter')}</th></tr></thead>
                    <tbody>${changed.map(key => `<tr><td>${logEscape(fields[key] || key)}</td><td>${logValue(before[key])}</td><td>${logValue(after[key])}</td></tr>`).join('')}</tbody></table></div>` : `<p>${text('batchLogNoChanges')}</p>`}</article>`;
            }).join('');
            logNode('[data-log-status]').textContent = result.items.length ? '' : text('batchLogEmpty');
            logNode('[data-log-page]').textContent = `${logPage} / ${Math.max(1, Math.ceil(logTotal / result.page_size))}`;
            logNode('[data-log-previous]').disabled = logPage <= 1;
            logNode('[data-log-next]').disabled = logPage * result.page_size >= logTotal;
        } catch (error) {
            if (serial !== logSerial || target !== logTarget || !logDialog.open) return;
            logNode('[data-log-status]').textContent = `${text('batchLogFailed')} ${error.message}`;
        }
    }
    async function batchLogs(id, batchNo) {
        if (!isOperator()) return;
        initializeBatchLogs();
        if (!logDialog.open) logFocus = document.activeElement;
        logTarget = id;
        logNode('#batch-log-title').textContent = `${text('batchLogTitle')} · ${batchNo}`;
        for (const [selector, key] of [['close', 'batchLogClose'], ['previous', 'batchLogPrevious'], ['next', 'batchLogNext']]) {
            logNode(`[data-log-${selector}]`).textContent = text(key);
        }
        if (!logDialog.open) logDialog.showModal();
        logNode('[data-log-close]').focus();
        return loadBatchLogs(1);
    }
    window.PageInteractions = {
        text,
        batchDateTimeInput, batchDateTimeValue,
        reason: options => request({...options, reason: true}),
        confirmSalesRollback: () => request({title: text('rollbackTitle'), message: text('rollbackMessage')}),
        batchLogs,
        cancel: () => { finish(null); closeBatchLogs(); },
    };
})();
