// 两份实际页面：四种类型、旧资料回显、表单提交及导入预览。DOM模拟，不作视觉验收。
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
        'openEditProduct', 'renderProductImportReport', 'createProduct', 'saveEditProduct'];
    const functions = names.map(name => {
        const start = source.search(new RegExp(`^(?:async )?function ${name}\\(`, 'm'));
        assert(start >= 0, name);
        const remaining = source.slice(start);
        const next = remaining.slice(1).search(/\n(?:async )?function /);
        return next < 0 ? remaining : remaining.slice(0, next + 1);
    }).join('\n');
    const elements = new Map();
    const element = id => {
        if (!elements.has(id)) elements.set(id, {value: '', files: [], style: {}, dataset: {}, innerHTML: '', textContent: '', addEventListener() {}});
        return elements.get(id);
    };
    const textNodes = [...html.matchAll(/data-product-type-text="([^"]+)"/g)]
        .map(match => ({dataset: {productTypeText: match[1]}, textContent: ''}));
    const labels = {stable: '稳健商品', new: '新品', safe_stock: '安全库存商品', no_impairment: '不计跌价商品'};
    const products = Object.keys(labels).map((kind, i) => ({id: i + 1, sku: kind, name: `<${kind}>`,
        product_type: kind, safe_stock_quantity: kind === 'safe_stock' ? 40 : 0, stock_quantity: 100, stock_value: 1000}));
    products.push({...products[2], id: 5, sku: 'legacy', product_type: 'stable', safe_stock_quantity: 25});
    const requests = [], messages = [];
    const document = {documentElement: {lang: 'es'}, getElementById: element, querySelectorAll: () => textNodes};
    const context = vm.createContext({document, console, FormData,
        renderPagination() {}, updateProductSelectionState() {}, authHeaders: () => ({}),
        showMsg: (...args) => messages.push(args), invalidateProductOptions() {}, loadProducts() {},
        clearImage() {}, closeEditProduct() {}, fetch: async (url, options) => {
            requests.push({url, options});
            return {ok: true, json: async () => ({id: 1, sku: 'test'})};
        }});
    vm.runInContext(functions + `\ncurrentProductList = ${JSON.stringify(products)};
        selectedProductIds = new Set(); productPage = 1; productTotal = 5; productPageSize = 20;
        editingProductId = null; productImportReport = null;`, context);
    for (const prefix of ['p', 'ep']) {
        const select = html.match(new RegExp(`<select id="${prefix}-product-type"[^>]*>(.*?)<\\/select>`))[1];
        assert.deepEqual([...select.matchAll(/value="([^"]+)"/g)].map(match => match[1]), Object.keys(labels));
        for (const type of Object.keys(labels)) {
            element(`${prefix}-product-type`).value = type;
            element(`${prefix}-safe-stock-quantity`).value = 40;
            vm.runInContext(`toggleSafeStockField('${prefix}')`, context);
            assert.equal(element(`${prefix}-safe-stock-quantity`).disabled, type !== 'safe_stock');
            assert.equal(element(`${prefix}-safe-stock-quantity`).value, type === 'safe_stock' ? 40 : 0);
            checks += 2;
        }
        checks++;
    }
    vm.runInContext('initializeProductTypes(); renderProductPage()', context);
    assert(textNodes.every(node => node.textContent && !node.textContent.includes('undefined')));
    const body = element('products-tbody').innerHTML;
    Object.values(labels).forEach(label => assert(body.includes(label)));
    assert(body.includes('40件') && body.includes('25件') && body.includes('&lt;no_impairment&gt;'));
    await vm.runInContext('openEditProduct(5)', context);
    assert.equal(element('ep-product-type').value, 'safe_stock');
    assert.equal(element('ep-safe-stock-quantity').value, 25);
    for (const product of products.slice(0, 4)) {
        await vm.runInContext(`openEditProduct(${product.id})`, context);
        assert.equal(element('ep-product-type').value, product.product_type);
        await vm.runInContext('saveEditProduct()', context);
        const saved = requests.at(-1).options.body;
        assert.equal(saved.get('product_type'), product.product_type);
        assert.equal(saved.get('safe_stock_quantity'), String(product.safe_stock_quantity));
        element('p-product-type').value = product.product_type;
        element('p-safe-stock-quantity').value = product.safe_stock_quantity;
        await vm.runInContext('createProduct()', context);
        assert.equal(requests.at(-1).options.body.get('product_type'), product.product_type);
        assert.equal(element('p-product-type').value, 'stable');
        assert.equal(element('p-safe-stock-quantity').disabled, true);
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
    checks += 12;
}

(async () => {
    await verify('templates/index.html');
    await verify('index.html');
    console.log(`${checks} product type page checks passed`);
})().catch(error => {console.error(error); process.exitCode = 1;});
