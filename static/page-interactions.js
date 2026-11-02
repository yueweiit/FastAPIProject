/* 局部交互共用能力；业务日期不转换时区，确认操作使用页面内弹窗。 */
(() => {
    'use strict';
    const messages = {'zh-CN': {
        helpTip: '查看说明',
        confirm: '确认', cancel: '取消', reason: '更正原因', requiredReason: '请填写更正原因。',
        longReason: '更正原因不能超过1000字。', rollbackTitle: '撤销导入',
        rollbackMessage: '确认撤销这次导入？销售记录将删除，已扣库存会恢复。',
        importStore: '导入店铺', chooseImportStore: '请选择导入店铺', importOrders: '导入订单详情',
        importSelectHelp: '请选择这次文件所属店铺', importBoundHelp: '文件将归入账号绑定的店铺',
        importNoBoundStore: '请联系管理员绑定有效店铺后导入', importStoresLoading: '正在加载导入店铺…',
        importFailed: '导入失败',
        importBusy: '正在导入…',
        importDateHelp: '自动按文件日期归月，无需选择导入月份。商品销售使用订单创建日期，缺少时用结算日期；运费调整、退款冲账等独立结算使用结算日期，缺少时用订单创建日期，均缺少才用导入时间。下方月份只控制查询，同一文件可能跨月。',
        importUploading: '正在上传文件', importParsing: '正在读取文件', importValidating: '正在校验数据',
        importExchangeRates: '正在获取汇率', importInventory: '正在核对库存',
        importWriting: '正在计算成本并写入数据', importCommitting: '正在确认保存结果',
        importWorking: '正在处理，请稍候…', importRowProgress: '本阶段 {done} / {total} 行（{percent}%）',
        importFileStore: '{file} · {store}', importComplete: '导入完成', importCompleteReview: '导入完成，部分记录待核对',
        importSuccess: '导入完成：结算行{settlements}条，销售扣库存{sales}条，重复跳过{duplicates}条{pending}{warnings}。按文件日期自动归月，请切换查询月份查看。',
        importPending: '；待确认{count}条', importWarnings: '；提示{count}条',
        importProgressUnavailable: '导入进度模块未加载，请更新并重启后端后刷新页面。',
        importConnectionLost: '进度连接中断，导入可能仍在继续。请刷新导入历史核对结果，避免重复提交。',
        galleryTitle: '产品图册', galleryKeyword: '商品名称 / SKU',
        galleryPlaceholder: '输入商品名称或 SKU', gallerySearch: '查询', galleryRefresh: '刷新', galleryClear: '清空',
        gallerySearchHint: '输入名称或 SKU 查找商品；可点选候选按其 SKU 查询，完整 SKU 匹配优先显示。',
        galleryLoading: '正在加载产品…', galleryEmpty: '暂无产品',
        galleryNoMatches: '没有匹配的产品，请修改名称、SKU 或清空搜索。',
        galleryLoadFailed: '图册加载失败，请重试：', galleryDownload: '下载图片',
        galleryStock: '库存{count}件',
        salesExport: '导出销售记录', salesExportLoading: '正在导出全部匹配的销售记录，请稍候…',
        salesSearch: '商品名称 / SKU / 订单号', salesSearchPlaceholder: '输入名称、SKU 或订单号',
        salesExportReady: '销售记录已导出，请检查浏览器下载列表。',
        salesExportFailed: '销售记录导出失败，请稍后重试。',
        salesExportEmpty: '没有符合条件的销售记录可导出。',
        salesExportTooLarge: '单次最多导出{limit}条记录，请缩小月份或商品范围。',
        salesExportUnavailable: '当前服务尚未加载销售导出接口，请更新并重启后端后刷新页面。',
        salesExportInvalidFile: '服务器未返回有效的Excel文件，请稍后重试。',
        salesCostTotal: '订单总成本（元，本行）', salesCostPurchase: '采购成本（元）',
        salesCostHead: '头程成本（元）', salesCostLast: '尾程成本（元）', salesCostOther: '其他入库成本（元）',
        salesCostImpairment: '出库商品跌价（元，参考）', salesCostPending: '待核对',
        salesCostUnavailable: '销售明细模块尚未加载，请更新并重启后端后刷新页面。',
        salesPendingLoadFailed: '待确认记录加载失败，请重试。',
        salesManualEntry: '手工结算补录',
        profitLossLedger: '使用明细',
        salesCostHelp: '原币核对列展示来源整行金额、币种和保存汇率，便于对照Excel；组合各组件重复展示来源金额，不合计。人民币交易金额按已保存组件份额换算。导入后按实际FIFO出库批次自动拆分采购、头程、尾程和其他入库成本，四项合计为本行订单总成本；组合商品同一订单号各行相加为整单成本。展示按分舍入，尾差按科目金额由大到小分配。出库商品跌价单列，不加到订单成本或重复扣减毛利；店铺损益仍按批次归属分摊，本列不代表销售店铺承担额。横向滚动可查看全部成本列。',
        batchExport: '导出', batchEdit: '编辑',
        batchImpairmentStores: '跌价归属店铺（必选）', batchImpairmentDefault: '待分配（在库存页管理）',
        batchImpairmentHelp: '至少选择一家店铺，可多选；按剩余库存的跌价金额均分，不限制销售。编辑时可选择归属生效范围，旧月度版本保留。',
        batchImpairmentEmpty: '尚未选择，请至少选择一家店铺。',
        batchImpairmentRequired: '请至少选择一家跌价归属店铺后再保存。',
        batchImpairmentMode: '归属生效范围',
        batchImpairmentFromArrival: '从入库日开始（历史更正）',
        batchImpairmentFromChange: '从修改日开始（北京时间）',
        batchImpairmentArrivalHelp: '重算该批次全部历史期间的归属，替换已有归属历史；已保存的月度版本保留。',
        batchImpairmentChangeHelp: '修改前费用保留原归属；修改日开始前的剩余准备转入新店铺，单列转入、转出，不重复计入费用。当天再次修改合并为当日最终归属。',
        batchImpairmentOldServer: '当前服务尚不支持按修改日生效，仅可从入库日更正。',
        batchImpairmentHistory: '已有生效记录：',
        provisionTransferIn: '本月准备转入(元)', provisionTransferOut: '本月准备转出(元)',
        provisionEquation: '期初准备 + 本月计提总额 − 本月销售转销 + 准备转入 − 准备转出 = 期末准备；账面价值 = 现存库存成本金额 − 期末累计跌价准备。准备转移不计入费用；入库当天0天，销售当天先计提再转销。',
        batchImpairmentLoading: '正在加载归属店铺…', batchImpairmentFailed: '归属店铺加载失败，请刷新批次列表后重试。',
        batchImpairmentInactive: '（已停用）',
        batchImpairmentInvalid: '请选择存在且启用的跌价归属店铺。',
        storeHasImpairmentBatches: '店铺被当前或历史跌价归属引用，不能删除；可停用店铺。',
        batchImpairmentSelected: '已选{count}家店铺，跌价金额均分',
        batchDeleteRetired: '直接删除已停用，请刷新页面后使用强制删除并填写作废原因。',
        batchForceDelete: '强制删除', batchVoidConfirm: '作废批次', batchVoidReason: '作废原因',
        batchVoided: '已作废', batchVoidedOnly: '查看已作废批次',
        batchOriginalRemaining: '原剩余',
        batchVoidWarning: '该批次已有 {sold} 件出库。作废后剩余库存不再参与销售及当前核算；原销售、成本明细和旧月度版本保留，受影响成本与利润待核对。请填写原因。',
        batchVoidSaved: '批次已作废。关联销售保留，成本与利润待核对。',
        batchVoidRefreshFailed: '批次已作废，但列表刷新失败，请点击刷新。',
        batchVoidProfitPending: '批次已作废，利润待核对',
        batchVoidMonthlyPending: '条销售引用已作废批次，利润待核对',
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
            shipping_date: '头程发货时间', arrived_at: '入库时间',
            voided_at: '作废时间（UTC）', voided_by: '作废人ID', void_reason: '作废原因',
            impairment_store_ids: '跌价归属店铺ID', impairment_store_names: '跌价归属店铺',
            impairment_store_history: '归属生效历史'},
    }};
    const text = key => (messages[document.documentElement.lang] || messages['zh-CN'])[key];
    const translateProvisionLabels = () => document.querySelectorAll?.('[data-provision-text]')?.forEach(node => {
        node.textContent = text(node.dataset.provisionText);
    });
    translateProvisionLabels();
    document.querySelectorAll?.('[data-sales-export-text]')?.forEach(node => {
        node.textContent = text(node.dataset.salesExportText);
    });
    document.querySelectorAll?.('[data-sales-search-placeholder]')?.forEach(node => {
        node.placeholder = text(node.dataset.salesSearchPlaceholder);
    });
    document.querySelectorAll?.('[data-sales-cost-text]')?.forEach(node => {
        node.textContent = text(node.dataset.salesCostText);
    });

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
    function request({title, message = '', reason = false, confirmLabel, reasonLabel}) {
        // 连点或另一个操作不会替换正在确认的目标。
        if (active) return Promise.resolve(null);
        initialize();
        dialog.querySelector('h3').textContent = title;
        dialog.querySelector('#page-action-message').textContent = message;
        dialog.querySelector('label').hidden = !reason;
        dialog.querySelector('label span').textContent = reasonLabel || text('reason');
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
    const historyLabel = history => history.map(rule => `${rule.effective_date}：${rule.store_names?.join(' / ') || text('batchImpairmentDefault')}`).join('；');
    const logValue = (value, key) => logEscape(value == null ? text('batchLogNull')
        : key === 'impairment_store_history' && Array.isArray(value) ? historyLabel(value) : value);
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
                const changed = Object.keys({...before, ...after}).filter(key => JSON.stringify(before[key]) !== JSON.stringify(after[key]));
                const time = new Date(row.created_at).toLocaleString(document.documentElement.lang || 'zh-CN');
                return `<article class="batch-change-log-entry"><p><strong>${logEscape(row.actor_username)} (#${row.actor_id})</strong> · ${logEscape(time)}</p>
                    ${changed.length ? `<div class="table-scroll"><table><thead><tr><th>${text('batchLogField')}</th><th>${text('batchLogBefore')}</th><th>${text('batchLogAfter')}</th></tr></thead>
                    <tbody>${changed.map(key => `<tr><td>${logEscape(fields[key] || key)}</td><td>${logValue(before[key], key)}</td><td>${logValue(after[key], key)}</td></tr>`).join('')}</tbody></table></div>` : `<p>${text('batchLogNoChanges')}</p>`}</article>`;
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
    let allocationStores = null, allocationSerial = 0;
    const allocationSelections = {b: [], eb: []};
    const allocationNames = {b: [], eb: []};
    let allocationMode = 'from_arrival', allocationHistory = [], allocationHistorySupported = false;
    function allocationModeHelp() {
        return text(!allocationHistorySupported ? 'batchImpairmentOldServer' : allocationMode === 'from_change_date'
            ? 'batchImpairmentChangeHelp' : 'batchImpairmentArrivalHelp');
    }
    function renderAllocationStores(prefix, selected = allocationSelections[prefix], names = allocationNames[prefix]) {
        const root = document.getElementById(`${prefix}-impairment-stores`);
        if (!root) return;
        allocationSelections[prefix] = [...selected];
        allocationNames[prefix] = [...names];
        const ids = new Set(selected.map(Number));
        const options = [...(allocationStores || [])];
        selected.forEach((id, index) => {
            if (!options.some(store => store.id === id)) options.push({id, name: names[index] || String(id), is_active: false});
        });
        root.innerHTML = `<fieldset style="border:1px solid var(--line);border-radius:8px;padding:12px">
            <legend>${escapeHtml(text('batchImpairmentStores'))}</legend>
            <p class="cost-detail" data-help-tip data-help-anchor="legend">${escapeHtml(text('batchImpairmentHelp'))}</p>
            <div style="display:flex;flex-wrap:wrap;gap:12px;max-height:140px;overflow:auto">${options.map(store =>
                `<label style="display:flex;gap:6px;align-items:center;max-width:100%;overflow-wrap:anywhere"><input type="checkbox" value="${Number(store.id)}" ${ids.has(store.id) ? 'checked' : ''}
                    ${allocationStores === null || !store.is_active && !ids.has(store.id) ? 'disabled' : ''}>${escapeHtml(store.name)}${store.is_active ? '' : escapeHtml(text('batchImpairmentInactive'))}</label>`).join('')}</div>
            <small>${escapeHtml(allocationStores === null ? text('batchImpairmentLoading') : selected.length
                ? text('batchImpairmentSelected').replace('{count}', selected.length) : text('batchImpairmentEmpty'))}</small>
            <p data-allocation-error class="msg msg-err" role="alert" hidden></p>
            ${prefix === 'eb' ? `<fieldset style="margin-top:12px;border:1px solid var(--line);border-radius:8px;padding:12px">
                <legend>${escapeHtml(text('batchImpairmentMode'))}</legend>
                <div style="display:flex;flex-wrap:wrap;gap:16px">${[['from_change_date', 'batchImpairmentFromChange'], ['from_arrival', 'batchImpairmentFromArrival']].map(([mode, label]) =>
                    `<label><input type="radio" name="impairment-change-mode" value="${mode}" ${allocationMode === mode ? 'checked' : ''} ${mode === 'from_change_date' && !allocationHistorySupported ? 'disabled' : ''}> ${escapeHtml(text(label))}</label>`).join('')}</div>
                <p data-allocation-mode-help class="cost-detail" data-help-tip data-help-anchor="legend">${escapeHtml(allocationModeHelp())}</p>
                ${allocationHistory.length ? `<small>${escapeHtml(text('batchImpairmentHistory') + historyLabel(allocationHistory))}</small>` : ''}
            </fieldset>` : ''}</fieldset>`;
        root.onchange = () => {
            allocationSelections[prefix] = [...root.querySelectorAll('input[type="checkbox"]:checked')].map(input => Number(input.value));
            root.querySelector('small').textContent = allocationSelections[prefix].length
                ? text('batchImpairmentSelected').replace('{count}', allocationSelections[prefix].length) : text('batchImpairmentEmpty');
            if (allocationSelections[prefix].length) root.querySelector('[data-allocation-error]').hidden = true;
            if (prefix === 'eb') {
                allocationMode = root.querySelector('input[type="radio"]:checked').value;
                root.querySelector('[data-allocation-mode-help]').textContent = allocationModeHelp();
            }
        };
    }
    async function loadAllocationStores() {
        const serial = ++allocationSerial, userId = CURRENT_USER_ID;
        allocationStores = null;
        translateProvisionLabels();
        renderAllocationStores('b');
        try {
            const response = await fetch('/finance-masters/stores', {credentials: 'same-origin', headers: authHeaders()});
            if (serial !== allocationSerial || CURRENT_USER_ID !== userId) return;
            if (response.status === 401) {handleSessionExpired(); return;}
            const data = await response.json();
            if (serial !== allocationSerial || CURRENT_USER_ID !== userId) return;
            if (!response.ok) throw new Error(text('batchImpairmentFailed'));
            allocationStores = data;
            renderAllocationStores('b');
            if (document.getElementById('edit-batch-modal')?.style.display !== 'none') renderAllocationStores('eb');
        } catch (error) {
            if (serial !== allocationSerial || CURRENT_USER_ID !== userId) return;
            const root = document.getElementById('b-impairment-stores');
            if (root) root.textContent = text('batchImpairmentFailed');
        }
    }
    const batchStores = {
        load: loadAllocationStores,
        edit: batch => {
            allocationHistorySupported = Array.isArray(batch.impairment_store_history);
            allocationHistory = batch.impairment_store_history || [];
            allocationMode = allocationHistorySupported ? 'from_change_date' : 'from_arrival';
            renderAllocationStores('eb', batch.impairment_store_ids || [], batch.impairment_store_names || []);
        },
        readMode: () => allocationMode,
        read: prefix => {
            if (allocationStores === null) throw new Error(text('batchImpairmentFailed'));
            if (!allocationSelections[prefix].length) {
                const root = document.getElementById(`${prefix}-impairment-stores`);
                const error = root?.querySelector('[data-allocation-error]');
                if (error) {error.textContent = text('batchImpairmentRequired'); error.hidden = false;}
                root?.querySelector('input[type="checkbox"]:not(:disabled)')?.focus();
                throw new Error(text('batchImpairmentRequired'));
            }
            return [...allocationSelections[prefix]];
        },
        label: batch => batch.impairment_store_names?.join(' / ') || text('batchImpairmentDefault'),
        reset: () => {allocationSerial++; allocationStores = null; allocationSelections.b = []; allocationSelections.eb = [];
            allocationNames.b = []; allocationNames.eb = [];
            allocationMode = 'from_arrival'; allocationHistory = []; allocationHistorySupported = false;},
    };
    window.PageInteractions = {
        batchStores,
        text,
        batchDateTimeInput, batchDateTimeValue,
        reason: options => request({...options, reason: true}),
        confirmSalesRollback: () => request({title: text('rollbackTitle'), message: text('rollbackMessage')}),
        batchLogs,
        cancel: () => { window.HelpTips?.close(); finish(null); closeBatchLogs(); batchStores.reset(); },
    };
})();

/* 明确标记的说明渐进增强为问号浮层；状态、校验及业务警示继续原位显示。 */
(() => {
    'use strict';
    if (typeof MutationObserver === 'undefined' || !document.body ||
        typeof document.createElement('span').showPopover !== 'function') return;
    const entries = new Map();
    let active = null, sequence = 0, frame = 0, hideTimer;
    const label = () => window.PageInteractions.text('helpTip');
    const visible = node => node.isConnected && node.getClientRects().length > 0 &&
        !node.closest('[hidden], [inert]') && getComputedStyle(node).visibility !== 'hidden';
    function close() {
        clearTimeout(hideTimer);
        if (!active) return;
        const entry = active; active = null; entry.pinned = false;
        entry.overButton = entry.overSource = false;
        if (entry.source.matches(':popover-open')) entry.source.hidePopover();
        entry.button.setAttribute('aria-expanded', 'false');
        if (entry.placeholder) {
            if (entry.placeholder.isConnected) entry.placeholder.replaceWith(entry.source);
            else entry.source.remove();
            entry.placeholder = null;
        }
    }
    function position() {
        if (!active) return;
        const {source, button} = active;
        if (!source.isConnected || !visible(button) || button.disabled) {close(); return;}
        const anchor = button.getBoundingClientRect();
        const width = document.documentElement.clientWidth, height = window.innerHeight;
        if (anchor.bottom < 0 || anchor.top > height) {close(); return;}
        const box = source.getBoundingClientRect(), gap = 8, edge = 12;
        const below = height - anchor.bottom - gap - edge, above = anchor.top - gap - edge;
        const top = box.height <= below || below >= above ? anchor.bottom + gap : anchor.top - gap - box.height;
        const left = `${Math.max(edge, Math.min(anchor.left, width - box.width - edge))}px`;
        const y = `${Math.max(edge, Math.min(top, height - box.height - edge))}px`;
        if (source.style.left !== left) source.style.left = left;
        if (source.style.top !== y) source.style.top = y;
    }
    function show(entry) {
        clearTimeout(hideTimer);
        if (!entry.source.isConnected || !visible(entry.button) || entry.button.disabled) return;
        if (active !== entry) close();
        active = entry;
        // 关闭的details不会渲染其后代，即使进入顶层；展示时临时放到该折叠区旁。
        const closed = entry.source.closest('details:not([open])');
        if (closed && !entry.placeholder) {
            entry.placeholder = document.createComment('help-tip-position');
            entry.source.before(entry.placeholder); closed.after(entry.source);
        }
        if (!entry.source.matches(':popover-open')) entry.source.showPopover();
        entry.button.setAttribute('aria-expanded', 'true');
        position();
    }
    function later(entry) {
        clearTimeout(hideTimer);
        hideTimer = setTimeout(() => {
            if (active === entry && !entry.pinned && !entry.overButton && !entry.overSource &&
                document.activeElement !== entry.button) close();
        }, 180);
    }
    function enhance() {
        frame = 0;
        for (const [source, entry] of entries) {
            if (!source.isConnected) {
                if (active === entry) close();
                entry.button.remove(); entries.delete(source);
            }
        }
        document.querySelectorAll('[data-help-tip]').forEach(source => {
            // 动态错误/权限缺失可明确切回行内；不根据文字、字号或颜色猜测。
            const eligible = !!source.textContent.trim() && !source.hidden &&
                !source.hasAttribute('data-help-inline') && !source.matches('[role="alert"], [role="status"]');
            let entry = entries.get(source);
            if (!eligible) {
                if (entry) {
                    if (active === entry) close();
                    if (!entry.button.hidden) entry.button.hidden = true;
                    if (source.classList.contains('help-tip-source')) source.classList.remove('help-tip-source');
                    if (source.hasAttribute('popover')) source.removeAttribute('popover');
                    if (source.getAttribute('role') === 'tooltip') {
                        if (entry.originalRole === null) source.removeAttribute('role');
                        else source.setAttribute('role', entry.originalRole);
                    }
                }
                return;
            }
            if (!entry) {
                const button = document.createElement('button');
                button.type = 'button'; button.className = 'help-tip-trigger'; button.textContent = '?';
                if (!source.id) source.id = `help-tip-${++sequence}`;
                button.setAttribute('aria-describedby', source.id);
                button.setAttribute('aria-expanded', 'false');
                entry = {source, button, originalRole: source.getAttribute('role'), pinned: false, overButton: false, overSource: false};
                entries.set(source, entry);
                button.addEventListener('mouseenter', () => {entry.overButton = true; show(entry);});
                button.addEventListener('mouseleave', () => {entry.overButton = false; later(entry);});
                button.addEventListener('focus', () => show(entry));
                button.addEventListener('blur', () => later(entry));
                button.addEventListener('click', event => {
                    event.preventDefault(); event.stopPropagation();
                    if (active === entry && entry.pinned) close();
                    else {show(entry); entry.pinned = true;}
                });
                source.addEventListener('mouseenter', () => {entry.overSource = true; clearTimeout(hideTimer);});
                source.addEventListener('mouseleave', () => {entry.overSource = false; later(entry);});
            }
            const selector = source.dataset.helpAnchor;
            const anchor = entry.placeholder ? entry.button.parentElement : selector &&
                (source.parentElement.querySelector(selector) || document.querySelector(selector));
            if (anchor) {
                if (entry.button.parentElement !== anchor) anchor.appendChild(entry.button);
            } else if (entry.button.nextElementSibling !== source) source.before(entry.button);
            const context = anchor ? [...anchor.childNodes].filter(node => node !== entry.button).map(node => node.textContent).join('').trim() : '';
            const accessibleLabel = context ? `${context} · ${label()}` : label();
            if (entry.button.getAttribute('aria-label') !== accessibleLabel) entry.button.setAttribute('aria-label', accessibleLabel);
            if (entry.button.hidden) entry.button.hidden = false;
            if (source.getAttribute('popover') !== 'manual') source.setAttribute('popover', 'manual');
            if (source.getAttribute('role') !== 'tooltip') source.setAttribute('role', 'tooltip');
            if (!source.classList.contains('help-tip-source')) source.classList.add('help-tip-source');
        });
        position();
    }
    const schedule = () => {if (!frame) frame = requestAnimationFrame(enhance);};
    new MutationObserver(schedule).observe(document.body, {subtree: true, childList: true, characterData: true,
        attributes: true, attributeFilter: ['data-help-inline', 'hidden', 'open', 'class', 'style', 'disabled', 'role']});
    document.addEventListener('pointerdown', event => {
        if (active && !active.source.contains(event.target) && !active.button.contains(event.target)) close();
    }, true);
    document.addEventListener('focusin', event => {
        if (active && !active.source.contains(event.target) && active.button !== event.target) close();
    });
    document.addEventListener('keydown', event => {
        if (event.key === 'Escape' && active) {event.preventDefault(); event.stopPropagation(); close();}
    }, true);
    document.addEventListener('close', event => {
        if (active && event.target.contains(active.button)) close();
    }, true);
    window.addEventListener('resize', position);
    document.addEventListener('scroll', position, true);
    window.HelpTips = {close, refresh: schedule, setInline: (source, inline) => {
        source.toggleAttribute('data-help-inline', inline); schedule();
    }};
    enhance();
})();
