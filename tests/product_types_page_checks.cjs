// 两份实际页面：三种类型、旧资料回显、表单提交及导入预览。DOM模拟，不作视觉验收。
const fs = require('fs');
const vm = require('vm');
const assert = require('assert/strict');
let checks = 0;

async function verify(path) {
    const html = fs.readFileSync(path, 'utf8');
    const scripts = [...html.matchAll(/<script\b[^>]*>([\s\S]*?)<\/script>/gi)].map(match => match[1]);
    scripts.forEach(script => new vm.Script(script));
    const source = scripts.join('\n');
    const names = ['productTypeText', 'productTypeValue', 'productTypeLabel', 'productTypeTagClass',
        'initializeProductTypes', 'toggleSafeStockField', 'escapeHtml', 'renderProductPage',
        'openEditProduct', 'renderProductImportReport', 'readProductSpecification', 'formatApiError', 'createProduct', 'saveEditProduct',
        'productImageUrl', 'productRowActions', 'setProductView', 'openProductEditor', 'openNewProduct',
        'setProductSaving', 'updateProductImagePreview', 'clearImage', 'closeEditProduct', 'saveProduct'];
    const functions = names.map(name => {
        const start = source.search(new RegExp(`^(?:async )?function ${name}\\(`, 'm'));
        assert(start >= 0, name);
        const remaining = source.slice(start);
        const next = remaining.slice(1).search(/\n(?:async )?function /);
        return next < 0 ? remaining : remaining.slice(0, next + 1);
    }).join('\n');
    const elements = new Map();
    const element = id => {
        if (!elements.has(id)) elements.set(id, {value: '', files: [], style: {}, dataset: {}, innerHTML: '', textContent: '', addEventListener() {}, setAttribute() {}, focus() {}, reset() {if(id==='product-form'){['sku','name','specification','image'].forEach(key=>element('p-'+key).value='');element('p-product-type').value='stable';element('p-safe-stock-quantity').value=0;}}, showModal(){this.open=true;}, close(){this.open=false;}, checkValidity(){return true;}, setCustomValidity(message) {this.validationMessage=message;}, reportValidity() {return !this.validationMessage;}});
        return elements.get(id);
    };
    const textNodes = [...html.matchAll(/data-product-type-text="([^"]+)"/g)]
        .map(match => ({dataset: {productTypeText: match[1]}, textContent: ''}));
    const labels = {stable: '稳健商品', new: '新品', no_impairment: '不跌价商品'};
    const products = Object.keys(labels).map((kind, i) => ({id: i + 1, sku: kind, name: `<${kind}>`, specification: `<${kind}> / 标准款`,
        product_type: kind, safe_stock_quantity: kind === 'stable' ? 40 : 0, stock_quantity: 100, stock_value: 1000}));
    products.push({...products[0], id:4, sku:'old-safe', product_type:'safe_stock', safe_stock_quantity:40});
    products.push({...products[0], id:5, sku:'legacy', product_type:'stable', safe_stock_quantity:25, specification:null});
    const requests = [], messages = [];
    const document = {documentElement: {lang: 'es'}, getElementById: element, querySelectorAll: selector => selector === '[data-product-type-text]' ? textNodes : []};
    const context = vm.createContext({document, console, FormData,
        renderPagination() {}, updateProductSelectionState() {}, authHeaders: () => ({}),
        showMsg: (...args) => messages.push(args), invalidateProductOptions() {}, loadProducts() {},
        initializeProductWorkspace() {}, URL, fetch: async (url, options) => {
            requests.push({url, options});
            return {ok: true, json: async () => ({id: 1, sku: 'test'})};
        }});
    vm.runInContext(functions + `\ncurrentProductList = ${JSON.stringify(products)};
        selectedProductIds = new Set(); productPage = 1; productTotal = 5; productPageSize = 20;
        editingProductId = null; productImportReport = null; productView='list'; productSessionId=0;
        productSaving=false; productEditorSerial=0; productPreviewUrl=''; productOriginalImage=''; CURRENT_USER_ID=1;`, context);
    for (const prefix of ['p']) {
        const specificationInput = html.match(new RegExp(`<textarea id="${prefix}-specification"[^>]*>`))[0];
        assert(!specificationInput.includes('required') && specificationInput.includes('maxlength="255"'));
        assert(html.indexOf(`id="${prefix}-name"`) < html.indexOf(`id="${prefix}-specification"`));
        assert(html.indexOf(`id="${prefix}-specification"`) < html.indexOf(`id="${prefix}-product-type"`));
        const select = html.match(new RegExp(`<select id="${prefix}-product-type"[^>]*>(.*?)<\\/select>`))[1];
        assert.deepEqual([...select.matchAll(/value="([^"]+)"/g)].map(match => match[1]), Object.keys(labels));
        for (const type of Object.keys(labels)) {
            element(`${prefix}-product-type`).value = type;
            element(`${prefix}-safe-stock-quantity`).value = 40;
            vm.runInContext(`toggleSafeStockField('${prefix}')`, context);
            assert.equal(element(`${prefix}-safe-stock-quantity`).disabled, type !== 'stable');
            assert.equal(element(`${prefix}-safe-stock-quantity`).value, type === 'stable' ? 40 : 0);
            checks += 2;
        }
        checks++;
    }
    vm.runInContext('initializeProductTypes(); renderProductPage()', context);
    assert(textNodes.every(node => node.textContent && !node.textContent.includes('undefined')));
    const body = element('products-tbody').innerHTML;
    assert(!body.includes('40件') && !body.includes('25件') && body.includes('&lt;no_impairment&gt;'));
    assert(body.includes('查看库存'));
    await vm.runInContext('openEditProduct(5)', context);
    assert.equal(element('p-product-type').value, 'stable');
    assert.equal(element('p-safe-stock-quantity').value, 25);
    assert.equal(element('p-specification').value, '');
    assert.equal(element('p-specification-help').hidden, false);
    assert(!body.includes('待补充') && body.includes('&lt;stable&gt; / 标准款'));
    assert(body.includes('<td></td>'));
    for (const product of products.slice(0, 3)) {
        await vm.runInContext(`openEditProduct(${product.id})`, context);
        assert.equal(element('p-product-type').value, product.product_type);
        await vm.runInContext('saveEditProduct()', context);
        const saved = requests.at(-1).options.body;
        assert.equal(saved.get('product_type'), product.product_type);
        assert.equal(saved.get('specification'), product.specification);
        assert.equal(saved.get('safe_stock_quantity'), String(product.safe_stock_quantity));
        vm.runInContext('openNewProduct()', context);
        element('p-sku').value = 'NEW'; element('p-name').value = '新增示例';
        element('p-product-type').value = product.product_type;
        element('p-safe-stock-quantity').value = product.safe_stock_quantity;
        element('p-specification').value = '  黑色 / 500 mL  ';
        await vm.runInContext('createProduct()', context);
        assert.equal(requests.at(-1).options.body.get('specification'), '黑色 / 500 mL');
        assert.equal(element('p-specification').value, '');
        assert.equal(requests.at(-1).options.body.get('product_type'), product.product_type);
        assert.equal(element('p-product-type').value, 'stable');
        assert.equal(element('p-safe-stock-quantity').disabled, false);
        checks += 6;
    }
    context.report = {total_rows: 5, new_count: 5, skipped_count: 0, error_count: 0, created_count: 0,
        can_import: true, rows: products.map((product, index) => ({...product, row_number: index + 2,
            errors: [], status: 'new', message: '待新增'}))};
    vm.runInContext('renderProductImportReport(report)', context);
    Object.values(labels).forEach(label => assert(element('product-import-tbody').innerHTML.includes(label)));
    context.report.rows = [{row_number: 2, sku: 'INVALID', name: '缺失类型', product_type: null,
        safe_stock_quantity: 0, status: 'error', errors: [], message: '类型必填'}];
    vm.runInContext('renderProductImportReport(report)', context);
    assert(element('product-import-tbody').innerHTML.includes('<td>-</td>'));
    assert(!element('product-import-tbody').innerHTML.includes('稳健商品'));
    assert(messages.every(([, ok]) => ok !== false), JSON.stringify(messages));
    context.currentProductList[0].sku = ' RAW-SKU ';
    context.currentProductList[0].name = ' 原始商品名称 ';
    await vm.runInContext('openEditProduct(1); saveEditProduct()', context);
    assert.equal(requests.at(-1).options.body.get('sku'), ' RAW-SKU ');
    assert.equal(requests.at(-1).options.body.get('name'), ' 原始商品名称 ');
    checks += 2;
    context.currentProductList[0].specification = '黑色\r\n标准版';
    await vm.runInContext('openEditProduct(1)', context);
    assert.equal(element('p-specification').value, '黑色\r\n标准版');
    await vm.runInContext('saveEditProduct()', context);
    assert.equal(requests.at(-1).options.body.get('specification'), '黑色 标准版');
    vm.runInContext('openNewProduct()', context);
    element('p-sku').value = 'NEW-LINE'; element('p-name').value = '换行示例';
    element('p-specification').value = ' 蓝色\n大号\rUSB\u2028套装 ';
    await vm.runInContext('createProduct()', context);
    assert.equal(requests.at(-1).options.body.get('specification'), '蓝色 大号 USB 套装');
    element('p-specification').value = '🔵';
    assert.equal(vm.runInContext("readProductSpecification('p')", context), '🔵');
    checks += 4;
    for (const editing of [false, true]) {
        for (const value of ['', ' \t\n ']) {
            await vm.runInContext(editing ? 'openEditProduct(1)' : 'openNewProduct()', context);
            element('p-sku').value = 'OPTIONAL'; element('p-name').value = '选填规格';
            element('p-specification').value = value;
            const before = requests.length;
            await vm.runInContext(editing ? 'saveEditProduct()' : 'createProduct()', context);
            assert.equal(requests.length, before + 1);
            assert.equal(requests.at(-1).options.body.get('specification'), '');
            checks += 2;
        }
    }
    const before = requests.length;
    for (const editing of [false, true]) {
        vm.runInContext(editing ? 'openEditProduct(1)' : 'openNewProduct()', context);
        for (const value of ['x'.repeat(256), '黑色\x0b标准版', '\x0c标准版', '标准版\x1f', '\ufffe', '\ud800']) {
            element('p-specification').value = value;
            await vm.runInContext(editing ? 'saveEditProduct()' : 'createProduct()', context);
            assert.equal(requests.length, before);
            assert(element('product-editor-status').textContent.includes('规格/型号'));
            checks += 2;
        }
    }
    assert.equal(vm.runInContext("formatApiError({code:'product_specification_invalid_type'}, '')", context), '规格/型号请按文本填写');
    assert.equal(vm.runInContext("formatApiError({code:'product_specification_invalid_characters'}, '')", context), '规格/型号含无法保存到 Excel 的字符，请删除后重试');
    vm.runInContext('currentProductList = []; renderProductPage()', context);
    assert(element('products-tbody').innerHTML.includes('colspan="6"'));
    checks += 12;
}

(async () => {
    await verify('templates/index.html');
    await verify('index.html');
    console.log(`${checks} product type page checks passed`);
})().catch(error => {console.error(error); process.exitCode = 1;});
