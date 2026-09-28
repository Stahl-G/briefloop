import assert from 'node:assert/strict';
import {templatesUI} from '../frontend/templates.js';

// The templates page is a gallery: one icon card per genre with color dots,
// a selection bar with explicit output actions. New-report selection applies
// only to that report form, leaving workspace defaults intact. My templates and the
// empty state stay below the gallery.
const handlers = [];
const elements = {'template-select': {value: 'tpl_biz_g'}, 'templates-page-list': {innerHTML: '', querySelectorAll(sel) {
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
const applyElement = {onclick: null, set onclick(fn) { handlers.push(['apply', null, fn]); }};
const state = {
  settings: {default_template_id: ''},
  templates: [
    {id: 'tpl_biz_g', name: '商业报告·品牌绿', revision: 1, status: 'ready', origin: 'builtin'},
    {id: 'tpl_biz_b', name: '商业报告·极简蓝', revision: 1, status: 'ready', origin: 'builtin'},
    {id: 'tpl_gov', name: '政府公文·石墨黑', revision: 1, status: 'ready', origin: 'builtin'},
    {id: 'tpl_stock', name: '券商研报·珊瑚红', revision: 1, status: 'ready', origin: 'builtin'},
    {id: 'tpl_user', name: '公司模板', revision: 2, status: 'ready', origin: 'upload'},
  ],
};
const sectionSelections = [], notices = [], pages = [], calls = [];
const ui = templatesUI({
  renderWorkflowChoices: () => {},
  templateSections: () => sectionSelections.push(elements['template-select'].value),
  action: fn => fn(), notice: message => notices.push(message), page: target => pages.push(target),
  api: async (path, body) => { calls.push([path, body]); return {}; },
  getState: () => state,
  setSettings: next => { state.settings = next; },
});
// dom.js resolves $ through the document global.
globalThis.document = {getElementById: id => id === 'template-new-report' ? applyElement : elements[id]};
ui.render();
let html = elements['templates-page-list'].innerHTML;
assert.ok(html.includes('商业报告') && html.includes('券商研报') && html.includes('政府公文'), 'a card per genre');
assert.ok(html.includes('适用于证券研究、行业分析'), 'genre descriptions render');
assert.equal((html.match(/tpl-card/g) || []).length, 3, 'one card per builtin genre');
assert.equal((html.match(/color-dot/g) || []).length, 4, 'one dot per theme');
assert.ok(html.includes('已选：<strong>商业报告 · 品牌绿</strong>'), 'the preselected genre+theme shows in the bar');
assert.ok(['上传原稿套用','从已有报告套用','按模板新建报告'].every(label=>html.includes(label)), 'all three output paths render');
assert.ok(html.includes('公司模板'), 'my templates stay on the page');

// dot click: switch the pick to the blue theme, bar follows
handlers.filter(h => h[0] === 'dot').find(h => h[1] === 'tpl_biz_b')[2]({stopPropagation() {}});
ui.render();
html = elements['templates-page-list'].innerHTML;
assert.ok(html.includes('已选：<strong>商业报告 · 极简蓝</strong>'), 'dot selection updates the bar');
assert.ok(html.includes('data-id="tpl_biz_b"'), 'the chosen dot stays bound to its template');

// New report: choose this form's layout without silently changing workspace defaults
handlers.find(h => h[0] === 'apply')[2]();
await new Promise(resolve => setImmediate(resolve));
assert.deepEqual(calls, [], 'choosing a template must not write workspace settings');
assert.equal(state.settings.default_template_id, '', 'workspace default remains unchanged');
assert.equal(elements['template-select'].value, 'tpl_biz_b', 'apply selects the template in the form used by generation');
assert.deepEqual(sectionSelections, ['tpl_biz_b'], 'apply rebuilds the selected template sections');
assert.deepEqual(pages, ['setup'], 'apply opens the new-report form');
assert.ok(notices.length >= 1, 'apply confirms with a notice');

const emptyHtml = {};
const empty = templatesUI({api: async () => {}, notice: () => {}, page: () => {}, action: () => {}, renderWorkflowChoices: () => {}, templateSections: () => {}, getState: () => ({templates: []}), setSettings: () => {}});
globalThis.document = {getElementById: () => ({set innerHTML(value) { emptyHtml.value = value; }, querySelectorAll: () => []})};
empty.render();
assert.match(emptyHtml.value, /还没有模板/);
console.log('PASS: templates page renders the gallery with selectable dots and a working apply bar');
