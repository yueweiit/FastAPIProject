/* 操作日志只读工作台；既有来源、角色边界与原始时间语义保留。 */
(() => {
    'use strict';
    const messages={'zh-CN':{
        title:'操作日志',help:'仅管理员可查看操作日志。汇总已有历史与启用后的业务操作，不补造过去未记录的日志；财务审计仍需启用财务增强。',
        source:'分类',all:'全部',action:'操作类型',actor:'操作人',actorPlaceholder:'输入用户名',
        start:'开始日期',end:'结束日期',keyword:'商品 / SKU / 批次 / 文件',placeholder:'输入名称、编码、批次号或文件名',
        store:'店铺',storeHelp:'按店铺筛选匹配保存的店铺ID；共享商品、批次、盘点等无单店归属的记录会被排除。',
        group:'同次操作编号',groupPlaceholder:'输入详情中的32位编号',groupHelp:'同一次保存涉及的商品或组件共用此编号；仍按当前账号权限显示。',
        auditNotReady:'扩展操作留痕尚未启用。需先准备审计表并启用配置，商品、账号等新增日志才开始保存；历史不会补录。',
        auditPaused:'扩展操作留痕已关闭，已有日志仍可查询；关闭期间的新操作不会保存到此审计表。',
        auditMissing:'扩展操作留痕已启用，但审计表尚未就绪；相关业务将禁止提交，请联系管理员完成结构准备。',
        unknownBefore:'旧值未记录',unknownBeforeHelp:'部分字段修改前未加载，原值无法确认，以下明确标为“旧值未记录”。',
        missingStore:'店铺 #{id}（名称未记录）',
        query:'查询',clear:'清空',refresh:'刷新',time:'记录时间',reference:'关联单据 / 商品',operator:'操作人',
        detail:'详情',loading:'正在读取记录…',failed:'读取失败，请重试。',empty:'没有符合条件的记录。',
        records:'日志记录',
        previous:'上一页',next:'下一页',size:'每页',page:'第 {page} 页，共 {total} 条',
        timeHelp:'时间沿用原始记录，UTC时间明确标注，未标注时区的旧记录不推定。按原记录时间倒序，日期筛选也采用原记录日期。',
        originalTime:'原记录时间',unknownActor:'操作人未记录',none:'未填写',blank:'空白',shared:'共享范围',
        noSources:'当前账号没有可见的记录分类，请核对权限及店铺绑定。',
        summaryChanges:'变更 {count} 项：{fields}',summaryCount:'{date} · 第 {revision} 版 · 差异 {difference}',
        summaryImport:'结算 {settlements} 行 · 销售 {sales} 条 · 当前待确认 {pending} 条 · {status}',
        close:'关闭',detailTitle:'记录详情',reason:'操作原因 / 备注',noReason:'原记录未保存操作原因。',
        field:'字段',before:'修改前',after:'修改后',value:'记录值',changes:'变更内容（{count}项）',noChanges:'没有记录字段值变化。',
        full:'查看完整业务字段',snapshot:'历史保存版本',business_history:'业务历史记录',change:'字段修改日志',
        historyHelp:'导入行数、待确认数和状态是导入批次当前保存结果，后续处理可能改变；不是每次操作的不可变快照。',
        snapshotHelp:'盘点记录是保存时的账面数、实盘数和差异；每次保存一个版本，不代表库存已调整。',
        labels:{batch:'批次更正 / 作废',stock_count:'库存盘点',sales_import:'销售导入 / 补录',sales_rollback:'撤销销售导入',finance:'财务审计',
            products:'商品管理',accounts:'账号管理',masters:'店铺 / 映射管理',batch_create:'新批次入库',sales_operations:'销售处理',report_changes:'损益表保存'},
        entities:{inventory_cost_adjustments:'成本调整',financial_expenses:'费用记录',financial_expense_sources:'费用取数来源',
            financial_receipts:'回款记录',settlement_classifications:'结算分类',financial_report_versions:'月度报表版本',
            products:'商品',users:'账号',stores:'店铺',product_lines:'产品线',store_products:'店铺商品映射',platform_sku_mappings:'平台SKU映射',
            platform_sku_components:'映射组件',inventory_batches:'入库批次',sales:'销售记录',settlement_entries:'结算来源',settlement_entry_allocations:'结算组件分配',store_profit_loss_reports:'店铺损益表'},
        roles:{admin:'管理员',operator:'运营',viewer:'查看者'},
        actions:{update:'修改',void:'作废',count_saved:'保存盘点',import:'导入',manual_settlement:'手工结算补录',rollback:'撤销导入',post:'入账',confirm:'确认',
            reverse:'冲销',cancel:'取消',create:'创建',delete:'删除',save:'保存',set_source:'切换来源',select_source:'切换来源',
            import_create:'批量新增商品',reset_password:'重置密码',change_password:'修改本人密码',change_status:'启停账号',change_role:'调整角色',bind_store:'绑定店铺',process_pending:'处理待确认',
            source_selection:'切换费用来源',receipt:'记录回款',classify:'结算分类',capture_version:'保存月度版本'},
        fields:{product_id:'本地商品ID',batch_no:'批次号',quantity:'入库数量',remaining_quantity:'原剩余数量',
            purchase_price:'采购单价',total_amount:'采购总金额',unit_cost:'单件成本',dingtalk_order_no:'采购钉钉单号',
            shipping_cost:'头程运费',shipping_cost_no:'头程运费单号',last_mile_cost:'尾程派送费',last_mile_cost_no:'尾程派送费单号',
            other_cost:'其他入库成本',other_cost_no:'其他成本单号',shipping_date:'头程发货时间',arrived_at:'入库时间',
            impairment_store_ids:'归属店铺ID',impairment_store_names:'归属店铺',impairment_store_history:'归属生效历史',
            voided_at:'作废时间（UTC）',voided_by:'作废人ID',void_reason:'作废原因',
            count_date:'盘点日期',revision:'保存版本',system_quantity:'盘点时账面数量',actual_quantity:'实盘数量',difference:'盘点差异',note:'备注',
            file_name:'文件名',file_type:'文件类型',sheet_name:'工作表',total_rows:'来源行数',settlement_rows:'结算行数',
            sales_created:'销售明细数',skipped_duplicates:'重复跳过数',pending_confirmation:'当前待确认数',status:'当前状态',
            imported_at:'导入时间（原记录）',rolled_back_at:'撤销时间（原记录）',id:'编号',store_id:'店铺ID',source_ref:'来源单号',
            category:'成本类别',allocation_basis:'分摊依据',amount:'原币金额',currency:'币种',exchange_rate:'汇率',rate_date:'汇率日期',
            document_date:'单据日期',amount_cny:'人民币金额',report_month:'核算月份',version_no:'版本号',report_type:'报表类型',subject:'科目',
            source_mode:'取数方式',source:'来源',platform_type:'平台类型',transaction_type:'交易类型',classification:'分类',
            settlement_entry_id:'结算来源ID',payout_id:'付款批次号',receipt_date:'回款日期',received_at:'回款时间',
            reversed_by_id:'冲销记录ID',reverses_id:'被冲销记录ID',sku:'商品编码',name:'名称',image:'图片路径',product_type:'商品类型',safe_stock_quantity:'安全库存数量',
            username:'用户名',role:'角色',is_active:'启用状态',password_set:'已设置密码（不记录内容）',password_changed:'已变更密码（不记录内容）',
            platform:'平台',platform_store_id:'平台店铺ID',country_or_region:'国家 / 地区',settlement_currency:'结算币种',timezone:'时区',legal_entity:'主体',code:'编码',
            product_line_id:'产品线ID',platform_product_id:'平台商品ID',platform_sku_id:'平台SKU ID',store_sku:'店铺SKU',effective_from:'生效日期',effective_to:'失效日期',
            platform_sku_mapping_id:'平台映射ID',quantity_per_sale:'每份组件数量',order_no:'销售订单号',order_id:'来源订单号',selling_price:'销售单价',platform_fee:'其他费用',
            total_cost:'销售成本',profit:'毛利',sold_at:'销售日期',product_name:'来源产品名',net_product_sales:'净商品销售额',settlement_total:'结算总金额',
            mapping_status:'商品确认状态',mapping_error:'待确认原因',sale_id:'销售明细ID',component_name:'组件名称',multiplier:'组件倍数',manual_values:'人工损益数据',remarks:'损益备注',report_metadata:'报表元数据',
            mode:'取数方式',breakdown:'结算分类明细',version:'版本号',calculation_version:'计算口径版本',is_final:'最终成本确认',expense_date:'费用日期',posted_at:'入账时间'},
        breakdown:{commission:'平台及达人佣金',tax:'平台税费',advertising:'广告费用',logistics:'平台物流费用',other:'其他结算差额'},
        enums:{completed:'已完成',rolled_back:'已撤销',draft:'草稿',posted:'已入账',confirmed:'已确认',needs_review:'待核对',cancelled:'已取消',
            reversed:'已冲销',manual:'人工',ledger:'明细',purchase:'采购',head_shipping:'头程',last_mile:'尾程',other:'其他',
            quantity:'按数量',weight:'按重量',volume:'按体积',value:'按金额',true:'是',false:'否',
            order_detail:'订单详情导入',manual_settlement:'手工结算补录',legacy_csv:'旧三列销售导入',stable:'稳健商品',safe_stock:'稳健商品（旧编码）',new:'新品',no_impairment:'不跌价商品',pending_confirmation:'待确认'}
    }};
    const dict=()=>messages[document.documentElement.lang]||messages['zh-CN'];
    const t=key=>dict()[key]||key,fill=(key,values)=>Object.entries(values).reduce((s,[k,v])=>s.replaceAll('{'+k+'}',String(v)),t(key));
    const h=value=>escapeHtml(String(value??'')),root=()=>document.getElementById('operation-logs-root');
    const el=id=>document.getElementById('ol-'+id),owner=()=>typeof CURRENT_USER_ID==='undefined'?0:CURRENT_USER_ID;
    const admin=()=>typeof isAdmin==='function'&&isAdmin();
    let epoch=0,serial=0,optionsSerial=0,detailSerial=0,options=null,page=1,total=0,dialog=null,focus=null;
    const token=()=>({epoch,owner:owner()}),current=tok=>admin()&&tok.epoch===epoch&&tok.owner===owner();
    const sourceLabel=source=>dict().labels[source]||source,actionLabel=action=>dict().actions[action]||action;
    const referenceLabel=row=>row.entity_type&&row.source!=='finance'?[(dict().entities[row.entity_type]||row.entity_type),row.reference].filter(Boolean).join(' · '):(row.source==='finance'?(dict().entities[row.reference]||row.reference):row.reference)||t('none');
    const storeLabel=row=>row.store_name||(row.store_id?fill('missingStore',{id:row.store_id}):t('shared'));
    function summaryLabel(row){
        const summary=row.summary;if(!summary)return '';
        if(summary.changed_fields)return fill('summaryChanges',{count:summary.changed_fields.length,
            fields:summary.changed_fields.slice(0,3).map(key=>dict().fields[key]||key).join('、')+(summary.changed_fields.length>3?'…':'')});
        if(row.source==='stock_count')return fill('summaryCount',{date:summary.count_date,revision:summary.revision,difference:summary.difference});
        return fill('summaryImport',{settlements:summary.settlement_rows,sales:summary.sales_created,pending:summary.pending_confirmation,status:dict().enums[summary.status]||summary.status});
    }
    const timeLabel=row=>String(row.recorded_at||'').replace('T',' ').slice(0,19)+' · '+(row.time_basis==='utc'?'UTC':t('originalTime'));
    const status=(message,error=false)=>{if(el('status')){el('status').textContent=message;el('status').className=error?'msg msg-err':'ol-status';}};
    function initialize(){
        if(!root()||el('filters'))return;
        root().innerHTML=`<section class="card ol-workspace"><div class="flex-between"><h3 id="ol-title">${h(t('title'))}</h3><button type="button" class="btn btn-secondary" id="ol-refresh">${h(t('refresh'))}</button></div>
            <p data-help-tip data-help-anchor="#ol-title">${h(t('help'))}</p>
            <p id="ol-audit-state" class="msg" role="status" hidden></p><form id="ol-filters" class="ol-filters">
            <div class="form-group"><label for="ol-source">${h(t('source'))}</label><select id="ol-source" disabled><option value="">${h(t('all'))}</option></select></div>
            <div class="form-group"><label for="ol-action">${h(t('action'))}</label><select id="ol-action"><option value="">${h(t('all'))}</option>${Object.entries(dict().actions).map(([key,label])=>`<option value="${h(key)}">${h(label)}</option>`).join('')}</select></div>
            <div class="form-group"><label for="ol-start">${h(t('start'))}</label><input type="date" min="1900-01-01" max="9998-12-31" id="ol-start"></div>
            <div class="form-group"><label for="ol-end">${h(t('end'))}</label><input type="date" min="1900-01-01" max="9998-12-31" id="ol-end"></div>
            <div class="form-group"><label id="ol-store-label" for="ol-store">${h(t('store'))}</label><select id="ol-store"><option value="">${h(t('all'))}</option></select></div>
            <p data-help-tip data-help-anchor="#ol-store-label">${h(t('storeHelp'))}</p>
            <div class="form-group"><label for="ol-actor">${h(t('actor'))}</label><input id="ol-actor" maxlength="64" placeholder="${h(t('actorPlaceholder'))}"></div>
            <div class="form-group ol-keyword"><label for="ol-keyword">${h(t('keyword'))}</label><input id="ol-keyword" maxlength="255" placeholder="${h(t('placeholder'))}"></div>
            <div class="form-group ol-keyword"><label id="ol-group-label" for="ol-group">${h(t('group'))}</label><input id="ol-group" minlength="32" maxlength="32" pattern="[0-9a-f]{32}" placeholder="${h(t('groupPlaceholder'))}"></div><p data-help-tip data-help-anchor="#ol-group-label">${h(t('groupHelp'))}</p>
            <div class="ol-filter-actions"><button class="btn btn-primary" type="submit">${h(t('query'))}</button><button class="btn btn-secondary" type="button" id="ol-clear">${h(t('clear'))}</button></div></form>
            <p id="ol-status" class="ol-status" role="status" aria-live="polite"></p><div class="table-scroll ol-table-scroll" tabindex="0" aria-label="${h(t('records'))}"><table class="ol-table"><thead><tr>
            <th><span id="ol-time-label">${h(t('time'))}</span><p data-help-tip data-help-anchor="#ol-time-label">${h(t('timeHelp'))}</p></th><th>${h(t('source'))}</th><th>${h(t('action'))}</th><th>${h(t('reference'))}</th><th>${h(t('store'))}</th><th>${h(t('operator'))}</th><th>${h(t('detail'))}</th></tr></thead><tbody id="ol-body"></tbody></table></div>
            <div class="ol-pagination"><span id="ol-page"></span><label>${h(t('size'))}<select id="ol-size"><option>20</option><option>50</option><option>100</option></select></label><button class="btn btn-secondary" type="button" id="ol-previous">${h(t('previous'))}</button><button class="btn btn-secondary" type="button" id="ol-next">${h(t('next'))}</button></div></section>`;
        el('filters').addEventListener('submit',event=>{event.preventDefault();page=1;load();});
        el('refresh').onclick=()=>load();el('clear').onclick=()=>{el('filters').reset();page=1;load();};
        el('size').onchange=()=>{page=1;load();};el('previous').onclick=()=>{if(page>1){page--;load();}};
        el('next').onclick=()=>{if(page*Number(el('size').value)<total){page++;load();}};
        el('body').onclick=event=>{const button=event.target.closest('[data-ol-detail]');if(button)openDetail(button.dataset.olSource,Number(button.dataset.olDetail));};
        window.HelpTips?.refresh();
    }
    function controls(busy){
        if(!el('previous'))return;
        el('previous').disabled=busy||page<=1;el('next').disabled=busy||page*Number(el('size').value)>=total;
        el('size').disabled=busy;
    }
    async function open(){
        if(!admin()){reset();return;}
        initialize();if(!root())return;
        if(!options){
            const tok=token(),seq=++optionsSerial;status(t('loading'));controls(true);
            try{
                const result=await api('GET','/operation-logs/options',null,{cache:'no-store',isCurrent:()=>current(tok)&&seq===optionsSerial});if(!current(tok)||seq!==optionsSerial)return;
                options=result;el('source').innerHTML=`<option value="">${h(t('all'))}</option>`+result.sources.map(source=>`<option value="${h(source)}">${h(sourceLabel(source))}</option>`).join('');
                el('source').disabled=false;el('store').innerHTML=`<option value="">${h(t('all'))}</option>`+result.stores.map(store=>`<option value="${Number(store.id)}">${h(store.name)}</option>`).join('');
                if(result.audit_enabled===false){el('audit-state').hidden=false;el('audit-state').textContent=t(result.audit_available?'auditPaused':'auditNotReady');}
                else if(result.audit_available===false){el('audit-state').hidden=false;el('audit-state').textContent=t('auditMissing');}
            }catch(error){if(current(tok)&&seq===optionsSerial&&error.name!=='AbortError'){status(error.message||t('failed'),true);controls(false);}return;}
        }
        await load();
    }
    async function load(){
        if(!admin()){reset();return;}
        if(!options){await open();return;}
        if(!options.sources.length){total=0;el('body').innerHTML='';status(t('noSources'));controls(false);return;}
        const tok=token(),seq=++serial;
        const params=new URLSearchParams({page:String(page),page_size:el('size').value});
        for(const key of ['source','action','start','end','store','actor','keyword','group']){const value=el(key).value.trim();if(value)params.set(key==='store'?'store_id':key==='group'?'group_id':key,value);}
        status(t('loading'));el('body').innerHTML='';el('page').textContent='';controls(true);
        try{
            const result=await api('GET','/operation-logs?'+params,null,{cache:'no-store',isCurrent:()=>current(tok)&&seq===serial});
            if(!current(tok)||seq!==serial)return;
            page=result.page;total=result.total;
            el('body').innerHTML=result.items.length?result.items.map(row=>`<tr><td class="ol-time">${h(timeLabel(row))}</td>
                <td><span class="ol-badge ol-${h(row.source)}">${h(sourceLabel(row.source))}</span></td><td>${h(actionLabel(row.action))}</td>
                <td><strong>${h(referenceLabel(row))}</strong>${row.related_id?` <small>#${Number(row.related_id)}</small>`:''}<div class="ol-subject">${h([row.sku,row.product_name].filter(Boolean).join(' · '))}</div><div class="ol-subject">${h(summaryLabel(row))}</div></td>
                <td>${h(storeLabel(row))}</td><td>${h(row.actor_name||t('unknownActor'))}${row.actor_id?` <small>#${Number(row.actor_id)}</small>`:''}</td>
                <td><button type="button" class="btn btn-secondary" data-ol-detail="${Number(row.id)}" data-ol-source="${h(row.source)}">${h(t('detail'))}</button></td></tr>`).join(''):`<tr><td colspan="7" class="empty">${h(t('empty'))}</td></tr>`;
            el('page').textContent=fill('page',{page,total});status('');controls(false);
        }catch(error){if(current(tok)&&seq===serial&&error.name!=='AbortError'){total=0;status(error.message||t('failed'),true);controls(false);}}
    }
    function format(value,key){
        if(value==null)return t('none');if(value==='')return t('blank');
        if(key==='breakdown'&&typeof value==='object'&&!Array.isArray(value))return Object.entries(value).map(([code,amount])=>`${dict().breakdown[code]||code}：${amount}`).join('\n')||t('none');
        if(key==='impairment_store_history'&&Array.isArray(value))return value.map(rule=>`${rule.effective_date}：${rule.store_names?.join(' / ')||t('none')}`).join('\n');
        if(Array.isArray(value))return value.map(item=>typeof item==='object'?JSON.stringify(item):String(item)).join(' / ')||t('none');
        if(typeof value==='object')return JSON.stringify(value,null,2);
        if(key==='role')return dict().roles[value]||String(value);
        return ['status','mapping_status','source_mode','mode','category','allocation_basis','file_type','product_type','is_active','is_final','password_set','password_changed'].includes(key)?(dict().enums[String(value)]||String(value)):String(value);
    }
    function valueTable(values){return `<div class="table-scroll"><table class="ol-detail-table"><thead><tr><th>${h(t('field'))}</th><th>${h(t('value'))}</th></tr></thead><tbody>${Object.entries(values).map(([key,value])=>`<tr><th>${h(dict().fields[key]||key)}</th><td>${h(format(value,key))}</td></tr>`).join('')}</tbody></table></div>`;}
    function changesTable(changes){return `<div class="table-scroll"><table class="ol-detail-table"><thead><tr><th>${h(t('field'))}</th><th>${h(t('before'))}</th><th>${h(t('after'))}</th></tr></thead><tbody>${changes.map(change=>`<tr><th>${h(dict().fields[change.field]||change.field)}</th><td class="ol-before">${h(change.before_known===false?t('unknownBefore'):format(change.before,change.field))}</td><td class="ol-after">${h(format(change.after,change.field))}</td></tr>`).join('')}</tbody></table></div>`;}
    function closeDetail(){detailSerial++;if(dialog?.open)dialog.close();if(el('detail-body'))el('detail-body').replaceChildren();focus?.focus?.();}
    async function openDetail(source,id){
        if(!admin()){reset();return;}
        const tok=token(),seq=++detailSerial;
        if(!dialog){dialog=document.createElement('dialog');dialog.id='ol-dialog';dialog.className='ol-dialog';dialog.setAttribute('aria-labelledby','ol-detail-title');
            dialog.innerHTML=`<div class="flex-between"><h3 id="ol-detail-title">${h(t('detailTitle'))}</h3><button type="button" id="ol-close" class="btn btn-secondary">${h(t('close'))}</button></div><div id="ol-detail-body" role="status"></div>`;
            document.body.appendChild(dialog);el('close').onclick=closeDetail;dialog.addEventListener('cancel',event=>{event.preventDefault();closeDetail();});}
        focus=document.activeElement;el('detail-body').textContent=t('loading');if(!dialog.open)dialog.showModal();
        try{
            const row=await api('GET',`/operation-logs/${encodeURIComponent(source)}/${id}`,null,{cache:'no-store',isCurrent:()=>current(tok)&&seq===detailSerial&&dialog.open});
            if(!current(tok)||seq!==detailSerial||!dialog.open)return;
            const kind=row.record_kind,help=kind==='business_history'?t('historyHelp'):kind==='snapshot'?t('snapshotHelp'):'';
            el('detail-body').innerHTML=`<div class="ol-detail-meta"><span class="ol-badge ol-${h(row.source)}">${h(sourceLabel(row.source))}</span> ${h(actionLabel(row.action))} · ${h(t(kind))}<p><strong>${h(referenceLabel(row))}</strong>${row.related_id?` #${Number(row.related_id)}`:''} · ${h([row.sku,row.product_name].filter(Boolean).join(' · '))}</p>
                <p>${h(timeLabel(row))} · ${h(row.actor_name||t('unknownActor'))}${row.actor_id?` (#${Number(row.actor_id)})`:''}${row.actor_role?` · ${h(dict().roles[row.actor_role]||row.actor_role)}`:''} · ${h(storeLabel(row))}</p>${row.group_id?`<p>${h(t('group'))}：<code>${h(row.group_id)}</code></p>`:''}</div>
                ${help?`<p class="ol-detail-note">${h(help)}</p>`:''}<p class="ol-detail-note"><strong>${h(t('reason'))}：</strong>${h(row.reason||t('noReason'))}</p>
                ${row.unknown_before_fields?.length?`<p class="ol-detail-note">${h(t('unknownBeforeHelp'))}</p>`:''}
                ${kind==='change'?`<h4>${h(fill('changes',{count:row.changes.length}))}</h4>${row.changes.length?changesTable(row.changes):`<p>${h(t('noChanges'))}</p>`}<details class="ol-full"><summary>${h(t('full'))}</summary>${changesTable([...new Set([...Object.keys(row.before_values),...Object.keys(row.after_values)])].map(field=>({field,before:row.before_values[field],after:row.after_values[field],before_known:!row.unknown_before_fields?.includes(field)})))}</details>`:valueTable(row.after_values)}`;
        }catch(error){if(current(tok)&&seq===detailSerial&&dialog.open&&error.name!=='AbortError')el('detail-body').textContent=error.message||t('failed');}
    }
    function reset(){epoch++;serial++;optionsSerial++;detailSerial++;options=null;page=1;total=0;if(dialog?.open)dialog.close();dialog?.remove();dialog=null;focus=null;root()?.replaceChildren();}
    document.querySelectorAll('[data-operation-nav]').forEach(node=>{node.textContent=t('title');});
    window.OperationLogs={open,reset};
})();
