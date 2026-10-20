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
    window.PageInteractions = {
        text,
        batchDateTimeInput, batchDateTimeValue,
        reason: options => request({...options, reason: true}),
        confirmSalesRollback: () => request({title: text('rollbackTitle'), message: text('rollbackMessage')}),
        cancel: () => finish(null),
    };
})();
