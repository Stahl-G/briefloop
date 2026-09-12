import fs from 'node:fs';
import vm from 'node:vm';
import assert from 'node:assert/strict';
const source = fs.readFileSync('frontend/app.js', 'utf8');

// The templates page is a gallery: one icon card per genre with color dots,
// a selection bar that names the picked genre+theme, and 使用该模板 which
// persists the default and opens the new-report form. My templates and the
// empty state stay below the gallery.
const code = source.slice(source.indexOf("const GENRE_ORDER="), source.indexOf("if($('new-report'))"));
const handlers = [];
const elements = {'templates-page-list': {innerHTML: '', querySelectorAll(sel) {
  if (sel !== '.tpl-card' && sel !== '.color-dot') return [];
  const out = [];
  for (const match of this.innerHTML.matchAll(/<div class="tpl-card[^"]*" data-genre="([^"]*)"/g)) out.push({dataset: {genre: match[1]}, set onclick(fn) { handlers.push(['card', match[1], fn]); }});
  if (sel === '.color-dot') {
    out.length = 0;
    for (const match of this.innerHTML.matchAll(/<button[^>]*class="color-dot([^"]*)"[^>]*data-genre="([^"]*)" data-theme="([^"]*)" data-id="([^"]*)"/g))
      out.push({dataset: {genre: match[2], theme: match[3], id: match[4]}, set onclick(fn) { handlers.push(['dot', match[4], fn]); }});
  }
  return out;
}}};
const context = vm.createContext({
  $: id => id === 'template-apply' ? {onclick: null, set onclick(fn) { handlers.push(['apply', null, fn]); }} : elements[id],
  esc: String, action: fn => fn(), notice: message => context.notices.push(message), page: target => context.pages.push(target),
  api: async (path, body) => { context.calls.push([path, body]); return {}; },
  state: {
    settings: {default_template_id: ''},
    templates: [
      {id: 'tpl_biz_g', name: '商业报告·品牌绿', revision: 1, status: 'ready', origin: 'builtin'},
      {id: 'tpl_biz_b', name: '商业报告·极简蓝', revision: 1, status: 'ready', origin: 'builtin'},
      {id: 'tpl_gov', name: '政府公文·石墨黑', revision: 1, status: 'ready', origin: 'builtin'},
      {id: 'tpl_stock', name: '券商研报·珊瑚红', revision: 1, status: 'ready', origin: 'builtin'},
      {id: 'tpl_user', name: '公司模板', revision: 2, status: 'ready', origin: 'upload'},
    ],
  },
});
context.notices = []; context.pages = []; context.calls = [];
vm.runInContext(code, context);
vm.runInContext('renderTemplatesPage()', context);
let html = elements['templates-page-list'].innerHTML;
assert.ok(html.includes('商业报告') && html.includes('券商研报') && html.includes('政府公文'), 'a card per genre');
assert.ok(html.includes('适用于证券研究、行业分析'), 'genre descriptions render');
assert.equal((html.match(/tpl-card/g) || []).length, 3, 'one card per builtin genre');
assert.equal((html.match(/color-dot/g) || []).length, 4, 'one dot per theme');
assert.ok(html.includes('已选：<strong>商业报告 · 品牌绿</strong>'), 'the preselected genre+theme shows in the bar');
assert.ok(html.includes('使用该模板'), 'the apply button renders');
assert.ok(html.includes('公司模板'), 'my templates stay on the page');

// dot click: switch the pick to the blue theme, bar follows
handlers.filter(h => h[0] === 'dot').find(h => h[1] === 'tpl_biz_b')[2]({stopPropagation() {}});
vm.runInContext('renderTemplatesPage()', context);
html = elements['templates-page-list'].innerHTML;
assert.ok(html.includes('已选：<strong>商业报告 · 极简蓝</strong>'), 'dot selection updates the bar');
assert.ok(html.includes('data-id="tpl_biz_b"'), 'the chosen dot stays bound to its template');

// apply: persist the default and open the new-report form
handlers.find(h => h[0] === 'apply')[2]();
await new Promise(resolve => setImmediate(resolve));
assert.equal(JSON.stringify(context.calls[0]), JSON.stringify(['settings', {default_template_id: 'tpl_biz_b'}]), 'apply persists the default template');
assert.deepEqual(context.pages, ['setup'], 'apply opens the new-report form');
assert.ok(context.notices.length >= 1, 'apply confirms with a notice');

const empty = vm.createContext({$: () => ({set innerHTML(value) { empty.html = value; }, querySelectorAll: () => []}), esc: String, state: {templates: []}, action: () => {}, notice: () => {}, page: () => {}, api: async () => {}});
vm.runInContext(code, empty);
vm.runInContext('renderTemplatesPage()', empty);
assert.match(empty.html, /还没有模板/);
console.log('PASS: templates page renders the gallery with selectable dots and a working apply bar');
