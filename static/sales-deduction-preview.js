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
    }};
    const t=key=>(messages[document.documentElement.lang] || messages['zh-CN'])[key] || key;
    const h=value=>escapeHtml(String(value??''));
    let dialog,finish;
    function reset(){if(dialog?.open)dialog.close();const done=finish;finish=null;done?.(null);}
    function initialize(){
        if(dialog)return;
        dialog=document.createElement('dialog');dialog.id='sales-deduction-preview';
        dialog.style.cssText='width:min(1400px,96vw);max-width:96vw;max-height:94vh;border:1px solid var(--line);border-radius:12px;padding:20px;color:var(--text);';
        dialog.innerHTML=`<h3 id="sales-deduction-title">${h(t('title'))}</h3><p>${h(t('help'))}</p><p id="sales-deduction-summary" role="status"></p>
            <div style="max-height:65vh;overflow:auto;border:1px solid var(--line)"><table style="min-width:1100px;width:100%"><thead><tr>${['row','source','quantity','local','method','multiplier','required','available','deduct','status'].map(k=>`<th>${h(t(k))}</th>`).join('')}</tr></thead><tbody id="sales-deduction-rows"></tbody></table></div>
            <div class="page-action-buttons" style="margin-top:16px"><button type="button" class="btn btn-secondary" id="sales-deduction-cancel">${h(t('cancel'))}</button><button type="button" class="btn btn-primary" id="sales-deduction-confirm">${h(t('confirm'))}</button></div>`;
        dialog.setAttribute('aria-labelledby','sales-deduction-title');document.body.appendChild(dialog);
        document.getElementById('sales-deduction-cancel').onclick=reset;
        dialog.addEventListener('cancel',event=>{event.preventDefault();reset();});
    }
    async function confirm(report,{isCurrent=()=>true}={}){
        reset();if(!isCurrent())return null;
        if(!report?.preview_token || !Array.isArray(report.rows))throw new Error(t('invalid'));
        initialize();let units=0,pending=0;
        document.getElementById('sales-deduction-rows').innerHTML=report.rows.map(row=>{
            if(row.status==='pending')pending++;
            return (row.components?.length?row.components:[null]).map(part=>{
                units+=Number(part?.deduct_quantity || 0);
                const local=part?[part.sku,part.name,part.specification].filter(Boolean).join(' · '):t('na');
                const method=row.resolution?t(row.resolution.method)+(row.resolution.version_id?` / ${t('version')} ${row.resolution.version_id}`:''):t('na');
                const quantity=`${row.source_quantity}${row.resolution?.source_unit?' '+row.resolution.source_unit:''}`;
                const values=[`${row.row} / ${row.order_no}`,`${row.source_sku || ''} · ${row.product_name || ''}`,quantity,local,method,
                    part?.multiplier??t('na'),part?.required_quantity??0,part?.available_quantity??t('na'),part?.deduct_quantity??0,
                    row.reason || t(row.status==='deduct'?'ready':row.status)];
                return `<tr>${values.map(value=>`<td style="white-space:normal;overflow-wrap:anywhere;padding:8px">${h(value)}</td>`).join('')}</tr>`;
            }).join('');
        }).join('');
        document.getElementById('sales-deduction-summary').textContent=t('summary').replace('{units}',units).replace('{pending}',pending).replace('{duplicates}',report.skipped_duplicates??report.skipped?.length??0);
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
