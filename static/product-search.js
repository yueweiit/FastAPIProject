/* 商品名称/SKU联想：库存筛选与销售自由输入共用，不建立业务关联。 */
(() => {
    'use strict';
    const messages = {'zh-CN': {
        loading:'正在查找商品…', empty:'没有匹配商品，请修改 SKU 或名称。',
        more:'仅显示前20项，请继续输入以缩小范围。', failed:'读取失败，请重试：',
    }};
    const text = key => (messages[document.documentElement.lang] || messages['zh-CN'])[key] || key;
    const h = value => escapeHtml(String(value ?? ''));
    function bind(config) {
        const {input,popup,list,status} = config;
        let serial=0,timer,controller,items=[],active=-1,composing=false;
        const enabled = () => !input.disabled && (config.enabled?.() ?? true);
        const label = key => config.messages?.[key] ?? text(key);
        function close(restore=false) {
            clearTimeout(timer);controller?.abort();controller=null;serial++;
            items=[];active=-1;composing=false;list.replaceChildren();status.textContent='';popup.hidden=true;
            input.setAttribute('aria-expanded','false');input.removeAttribute('aria-activedescendant');
            config.onClose?.(restore);
        }
        function render(message='') {
            list.innerHTML=items.map((product,index)=>`<button type="button" role="option" tabindex="-1"
                id="${h(input.id)}-option-${index}" data-product-search-option="${index}" ${config.optionAttributes?.(index) || ''}
                aria-selected="${index===active}"><strong>${h(product.sku)}</strong><span>${h(product.name)}</span></button>`).join('');
            status.textContent=message;popup.hidden=false;input.setAttribute('aria-expanded','true');
            if(active>=0)input.setAttribute('aria-activedescendant',`${input.id}-option-${active}`);
            else input.removeAttribute('aria-activedescendant');
        }
        function choose(index) {
            const product=items[index];if(!product || !enabled() || composing)return;
            close();config.onSelect(product);
        }
        function search(event) {
            if(!enabled())return;
            clearTimeout(timer);controller?.abort();
            const request=++serial,query=input.value.trim(),context=config.captureContext();
            const current=()=>request===serial && context() && enabled();
            items=[];active=-1;config.onEdit?.(event);
            if(!query || query.length>255) {
                if(!query && config.initialItems){items=config.initialItems();render();}
                else close();
                return;
            }
            render(label('loading'));
            if(composing || event.isComposing)return;
            timer=setTimeout(async()=>{
                if(!current())return;
                controller=new AbortController();
                try {
                    const params=new URLSearchParams({keyword:query,page:1,page_size:20,prioritize_exact_sku:true});
                    const response=await fetch(`/products?${params}`,{credentials:'same-origin',headers:authHeaders(),signal:controller.signal});
                    if(!current())return;
                    if(response.status===401){handleSessionExpired();return;}
                    const data=await response.json();if(!current())return;
                    if(!response.ok)throw new Error(formatApiError(data?.detail,`${response.status}`));
                    items=data.items;
                    render(!items.length?label('empty'):data.total>items.length?label('more'):'');
                } catch(error) {
                    if(current() && error.name!=='AbortError')render(`${label('failed')}${error.message}`);
                }
            },250);
        }
        input.addEventListener('focus',event=>{
            if(!enabled())return;
            if(config.initialItems){input.select();items=config.initialItems();active=-1;render();}
            else if(input.value.trim())search(event);
        });
        input.addEventListener('compositionstart',()=>{composing=true;clearTimeout(timer);controller?.abort();serial++;});
        input.addEventListener('compositionend',event=>{composing=false;search(event);});
        input.addEventListener('input',search);
        input.addEventListener('keydown',event=>{
            if(composing || event.isComposing || !enabled())return;
            if(event.key==='Escape'){event.preventDefault();close(true);return;}
            if(event.key==='ArrowDown' || event.key==='ArrowUp') {
                event.preventDefault();
                if(popup.hidden) {
                    if(!config.initialItems){search(event);return;}
                    items=config.initialItems();
                }
                if(!items.length)return;
                active=active<0?(event.key==='ArrowDown'?0:items.length-1):(active+(event.key==='ArrowDown'?1:-1)+items.length)%items.length;
                render();document.getElementById(`${input.id}-option-${active}`).scrollIntoView({block:'nearest'});
            }
            if(event.key==='Enter' && (config.selectFirstOnEnter || active>=0)) {event.preventDefault();choose(Math.max(0,active));}
        });
        input.addEventListener('blur',()=>close(true));
        list.addEventListener('mousedown',event=>event.preventDefault());
        list.addEventListener('click',event=>{
            const option=event.target.closest('[data-product-search-option]');
            if(option && list.contains(option))choose(Number(option.dataset.productSearchOption));
        });
        return {close};
    }
    window.ProductSearch={bind};
})();
