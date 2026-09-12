import fs from 'node:fs';
import vm from 'node:vm';
import assert from 'node:assert/strict';
const source = fs.readFileSync('frontend/app.js', 'utf8');

// The templates page groups built-in profiles apart from user uploads, and the
// setup picker marks built-ins so users can tell bundled layouts from their own.
const code = source.slice(source.indexOf('function renderTemplatesPage(){'), source.indexOf("if($('new-report'))"));
const captured = [];
const el = id => ({set innerHTML(value){ captured.push(value); }, get innerHTML(){ return captured[captured.length - 1]; }});
const context = vm.createContext({
  $: () => el('templates-page-list'), esc: String, state: {templates: [
    {id: 'tpl_user', name: '公司模板', revision: 2, status: 'ready', origin: 'upload'},
    {id: 'tpl_builtin', name: '研报版式', revision: 1, status: 'ready', origin: 'builtin'},
  ]},
});
vm.runInContext(code, context);
vm.runInContext('renderTemplatesPage()', context);
assert.match(captured[0], /内置版式/);
assert.ok(captured[0].indexOf('研报版式') < captured[0].indexOf('我的模板'), 'builtin group renders before user templates');
assert.ok(!captured[0].includes('还没有模板'), 'the empty-state hint stays hidden when templates exist');

const empty = vm.createContext({$: () => ({set innerHTML(value){ captured.push(value); }}), esc: String, state: {templates: []}});
vm.runInContext(code, empty);
vm.runInContext('renderTemplatesPage()', empty);
assert.match(captured[captured.length - 1], /还没有模板/);

const pickerCode = source.slice(source.indexOf('function renderTemplates(first=false){'), source.indexOf("$('template-select').onchange"));
const select = {set innerHTML(value){ select.options = value; }, set value(v){ select.picked = v; }, get value(){ return select.picked; }, options: ''};
const picker = vm.createContext({
  $: () => select, esc: String, parse: () => ({}), state: {
    templates: [
      {id: 'tpl_user', name: '公司模板', revision: 2, status: 'ready', origin: 'upload'},
      {id: 'tpl_builtin', name: '研报版式', revision: 1, status: 'ready', origin: 'builtin'},
    ],
    requirements: {}, settings: {},
  },
  templateSections: () => {}, applyTemplateSectionEdits: () => {}, unreadyTemplate: () => null,
});
vm.runInContext(pickerCode, picker);
vm.runInContext('renderTemplates(true)', picker);
assert.ok(select.options.indexOf('研报版式（内置）') < select.options.indexOf('公司模板 ·'), 'the setup picker lists the built-in first and labels it');
console.log('PASS: the templates page groups built-ins and the picker marks them');
