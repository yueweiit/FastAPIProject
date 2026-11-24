/* 销售扣库只读预览；提交时服务端重新核对签名、来源、映射和库存。 */
(() => {
    'use strict';
    const messages={'zh-CN':{
        title:'核对预计扣库',help:'请核对本地商品、规格和倍数。优先按SKU编码识别，没有本地编码时按产品名唯一匹配。待确认及仅结算记录本次扣库为0；待确认记录可在导入后选择商品，店铺映射可按需保存。',
        row:'来源行 / 订单',source:'来源商品 / 编码',quantity:'销售数量',local:'本地 SKU / 商品 / 规格',
        multiplier:'每份库存数量',required:'所需库存数量',available:'销售日可用',deduct:'本次扣库数量',status:'处理结果',
        method:'匹配依据',store_mapping:'已确认店铺映射',local_sku:'本地 SKU 编码',product_name:'产品名唯一匹配',legacy_sku:'旧 CSV 编码',manual:'本次人工确认',
        pending:'待确认，不扣库',settlement:'仅结算，不扣库',ready:'预计出库',na:'—',
        cancel:'返回',confirm:'确认此计划并提交',summary:'本次预计扣减 {units} 个库存单位；待确认 {pending} 行；重复跳过 {duplicates} 行。',
        invalid:'预览数据不可用，请更新后端后重新预览。',version:'映射版本',
        introduction:'请核对商品、倍数和本次扣库数量，确认后提交。',rules:'识别与扣库规则',
        planned:'本次预计扣库',inventoryUnits:'个库存单位',pendingRows:'待确认',duplicates:'重复跳过',rows:'行',
        detail:'扣库明细',sourceLine:'来源行',submitHelp:'确认提交后才会扣减库存。',
        scrollHelp:'左右滑动可查看全部明细。',
    }};
    const t=key=>(messages[document.documentElement.lang] || messages['zh-CN'])[key] || key;
    const h=value=>escapeHtml(String(value??''));
    let dialog,finish;
    function reset(){if(dialog?.open)dialog.close();const done=finish;finish=null;done?.(null);}
    function initialize(){
        if(dialog)return;
        dialog=document.createElement('dialog');dialog.id='sales-deduction-preview';dialog.className='sales-review-dialog';
        const columns=['row','source','quantity','local','method','multiplier','required','available','deduct','status'];
        dialog.innerHTML=`<header class="sales-review-head"><h3 id="sales-deduction-title">${h(t('title'))}</h3><p>${h(t('introduction'))}</p></header>
            <div class="sales-review-body"><div id="sales-deduction-summary" class="sales-deduction-summary" role="status"></div>
            <details class="sales-review-guidance"><summary>${h(t('rules'))}</summary><p>${h(t('help'))}</p></details>
            <p class="sales-review-scroll-hint">${h(t('scrollHelp'))}</p>
            <div class="sales-deduction-scroll" role="region" aria-label="${h(t('detail'))}" tabindex="0"><table class="sales-deduction-table">
            <colgroup>${[180,185,70,210,100,90,90,95,100,180].map(width=>`<col style="width:${width}px">`).join('')}</colgroup>
            <thead><tr>${columns.map((key,index)=>`<th scope="col"${index===2 || index>=5 && index<=8?' class="sales-review-number"':''}>${h(t(key))}</th>`).join('')}</tr></thead><tbody id="sales-deduction-rows"></tbody></table></div></div>
            <footer class="sales-review-footer"><p>${h(t('submitHelp'))}</p><div class="page-action-buttons"><button type="button" class="btn btn-secondary" id="sales-deduction-cancel">${h(t('cancel'))}</button><button type="button" class="btn btn-primary" id="sales-deduction-confirm">${h(t('confirm'))}</button></div></footer>`;
        dialog.setAttribute('aria-labelledby','sales-deduction-title');document.body.appendChild(dialog);
        document.getElementById('sales-deduction-cancel').onclick=reset;
        dialog.addEventListener('cancel',event=>{event.preventDefault();reset();});
    }
    async function confirm(report,{isCurrent=()=>true}={}){
        reset();if(!isCurrent())return null;
        if(!report?.preview_token || !Array.isArray(report.rows))throw new Error(t('invalid'));
        initialize();let units=0,pending=0;
        document.getElementById('sales-deduction-rows').innerHTML=report.rows.map((row,rowIndex)=>{
            if(row.status==='pending')pending++;
            return (row.components?.length?row.components:[null]).map((part,partIndex)=>{
                units+=Number(part?.deduct_quantity || 0);
                const local=part?`<span class="sales-review-code">${h(part.sku)}</span><span class="sales-review-primary">${h(part.name)}</span>${part.specification?`<span class="sales-review-secondary">${h(part.specification)}</span>`:''}`:h(t('na'));
                const method=row.resolution?t(row.resolution.method)+(row.resolution.version_id?` / ${t('version')} ${row.resolution.version_id}`:''):t('na');
                const quantity=`${row.source_quantity}${row.resolution?.source_unit?' '+row.resolution.source_unit:''}`;
                const status=['deduct','pending','settlement'].includes(row.status)?row.status:'pending';
                const values=[`<span class="sales-review-secondary">${h(t('sourceLine'))} ${h(row.row)}</span><span class="sales-review-code">${h(row.order_no)}</span>`,
                    `<span class="sales-review-primary">${h(row.product_name || t('na'))}</span><span class="sales-review-secondary sales-review-code">${h(row.source_sku)}</span>`,
                    h(quantity),local,h(method),h(part?.multiplier??t('na')),h(part?.required_quantity??0),h(part?.available_quantity??t('na')),h(part?.deduct_quantity??0),
                    `<span class="sales-review-badge" data-status="${status}">${h(t(status==='deduct'?'ready':status))}</span>${row.reason?`<span class="sales-review-secondary">${h(row.reason)}</span>`:''}`];
                return `<tr data-status="${status}" data-group-start="${rowIndex>0 && partIndex===0}">${values.map((value,index)=>`<td${index===2 || index>=5 && index<=8?` class="sales-review-number${index===8?' sales-review-deduct':''}"`:''}>${value}</td>`).join('')}</tr>`;
            }).join('');
        }).join('');
        document.getElementById('sales-deduction-summary').innerHTML=[['planned',units,'inventoryUnits'],['pendingRows',pending,'rows'],['duplicates',report.skipped_duplicates??report.skipped?.length??0,'rows']]
            .map(([label,count,unit])=>`<div class="sales-deduction-metric" data-attention="${label==='pendingRows' && count>0}"><span>${h(t(label))}</span><strong>${h(count)}</strong><span>${h(t(unit))}</span></div>`).join('');
        return new Promise(resolve=>{
            finish=resolve;
            document.getElementById('sales-deduction-confirm').onclick=()=>{
                const done=finish;finish=null;dialog.close();done?.(isCurrent()?report.preview_token:null);
            };
            dialog.showModal();document.getElementById('sales-deduction-cancel').focus();
        });
    }
    window.SalesDeductionPreview={confirm,reset};
})();
