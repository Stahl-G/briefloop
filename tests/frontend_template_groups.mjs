import fs from 'node:fs';
import vm from 'node:vm';
import assert from 'node:assert/strict';
const source = fs.readFileSync('frontend/app.js', 'utf8');

// The templates page renders built-ins as a matrix: one card per genre, one
// colored chip per theme; clicking a chip sets the workspace default and
// marks it active. The government card has a single fixed-theme chip.
const code = source.slice(source.indexOf('const THEME_COLORS='), source.indexOf("if($('new-report'))"));
const handlers = [];
const box = {innerHTML: '', querySelectorAll(sel) {
  const chips = [];
  for (const match of this.innerHTML.matchAll(/<button class="theme-chip([^"]*)" data-template="([^"]*)"[^>]*>/g))
    chips.push({classes: match[1], id: match[2]});
  if (sel === '.theme-chip:not([disabled])') return chips.map(c => ({dataset: {template: c.id}, set onclick(fn) { handlers.push([c.id, fn]); }}));
  return [];
}};
const context = vm.createContext({
  $: () => box, esc: String, action: fn => fn(), notice: message => context.notices.push(message),
  api: async (path, body) => { context.calls.push([path, body]); return {}; },
  state: {
    settings: {default_template_id: 'tpl_gov'},
    templates: [
      {id: 'tpl_gov', name: '政府公文·政务蓝红', revision: 1, status: 'ready', origin: 'builtin'},
      {id: 'tpl_gen_t1', name: '通用报告·极简蓝', revision: 1, status: 'ready', origin: 'builtin'},
      {id: 'tpl_gen_t2', name: '通用报告·商务蓝', revision: 1, status: 'ready', origin: 'builtin'},
      {id: 'tpl_user', name: '公司模板', revision: 2, status: 'ready', origin: 'upload'},
    ],
  },
});
context.notices = context.notices || []; context.calls = context.calls || []; context.notices = []; context.calls = [];
vm.runInContext(code, context);
vm.runInContext('renderTemplatesPage()', context);
const html = box.innerHTML;
const cards = [...html.matchAll(/<span class="name">([^<]+)<\/span><span class="chips">([\s\S]*?)<\/span><\/div>/g)]
  .map(m => ({genre: m[1], chips: (m[2].match(/theme-chip/g) || []).length, active: m[2].includes('active')}));
assert.deepEqual(cards.map(c => c.genre).sort(), ['政府公文', '通用报告'], 'one card per genre');
const gov = cards.find(c => c.genre === '政府公文');
assert.equal(gov.chips, 1, 'the government card ships its single fixed theme');
assert.ok(gov.active, 'the workspace default renders active');
assert.equal(cards.reduce((n, c) => n + c.chips, 0), 3, 'each theme is one chip');
assert.ok(html.indexOf('极简蓝') < html.indexOf('商务蓝'), 'themes follow the canonical order');
assert.ok(!html.includes('还没有模板'), 'empty-state stays hidden');

handlers[0][1]();
await new Promise(resolve => setImmediate(resolve));
assert.equal(JSON.stringify(context.calls[0]), JSON.stringify(['settings', {default_template_id: 'tpl_gen_t1'}]), 'clicking a chip sets the workspace default');

const empty = vm.createContext({$: () => ({set innerHTML(value) { empty.html = value; }, querySelectorAll: () => []}), esc: String, state: {templates: []}, action: () => {}, notice: () => {}, api: async () => {}});
vm.runInContext(code, empty);
vm.runInContext('renderTemplatesPage()', empty);
assert.match(empty.html, /还没有模板/);
console.log('PASS: built-in templates render as a genre x theme matrix with selectable chips');
