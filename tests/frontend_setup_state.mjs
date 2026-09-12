// The intake page must not silently change the template or the material scope.
import fs from 'node:fs';
import vm from 'node:vm';
import assert from 'node:assert/strict';
const source=fs.readFileSync(process.env.BRIEFLOOP_APP_JS||new URL('../frontend/app.js',import.meta.url),'utf8');
const elements=new Map();
const el=id=>{if(!elements.has(id))elements.set(id,{value:'',checked:false,hidden:false,disabled:false,textContent:'',innerHTML:'',dataset:{},classList:{toggle(){}},closest:()=>({hidden:false}),querySelector:()=>null,append(){}});return elements.get(id)};

// Template rows keep the edits saved with the last run, unrelated refreshes do not rebuild
// them, and a template that is not ready blocks the run instead of using the general layout.
const templateCode=source.slice(source.indexOf('function readTemplateSections()'),source.indexOf("$('template-import-button').onclick"));
let sectionWrites=0;
const rows=[];
const chapterTitle={value:''},chapterMode={value:''};
const renderRow=(title,mode)=>{chapterTitle.value=title;chapterMode.value=mode};
el('template-sections').querySelectorAll=selector=>selector==='[data-section-id]'?rows:[];
Object.defineProperty(el('template-sections'),'innerHTML',{get:()=>'',set:html=>{sectionWrites++;renderRow((/value="([^"]*)"/.exec(html)||[,''])[1],'required')}});
const templates=vm.createContext({$:el,console,JSON,state:null,CSS:{escape:s=>s},esc:value=>String(value),
 parse:value=>{try{return JSON.parse(value||'{}')}catch{return {}}}});
vm.runInContext(templateCode,templates);
templates.state={templates:[{id:'t1',name:'我的模板',status:'ready',revision:1,spec:JSON.stringify({sections:[{section_id:'summary',title:'核心摘要',purpose:'模板用途'}]})}],
 requirements:{template_id:'t1',sections:[{section_id:'summary',title:'本期重点',mode:'manual',purpose:'保存的用途'}]},settings:{default_template_id:null}};
el('template-select').value='t1';
rows.push({dataset:{sectionId:'summary'},querySelector:selector=>selector==='[data-title]'?chapterTitle:selector==='select'?chapterMode:null});
vm.runInContext('renderTemplates(true)',templates);
assert.equal(chapterTitle.value,'本期重点');
assert.equal(chapterMode.value,'manual');
vm.runInContext('renderTemplates(false)',templates);
assert.equal(sectionWrites,1,'unchanged templates must not rebuild the section rows');
// Another template becoming ready changes the list; the saved edits must still be re-applied.
templates.state.templates=[...templates.state.templates,{id:'t3',name:'新模板',status:'ready',revision:1,spec:'{}'}];
vm.runInContext('renderTemplates(false)',templates);
assert.equal(sectionWrites,2);
assert.equal(chapterTitle.value,'本期重点');
assert.equal(chapterMode.value,'manual');
templates.state.templates=[{id:'t2',name:'待准备模板',status:'preparing',revision:1,spec:'{}'}];
el('template-select').value='t2';
vm.runInContext('renderTemplates(false)',templates);
assert.match(el('template-status').textContent,/尚未就绪/);
assert.throws(()=>vm.runInContext('readTemplateSections()',templates),/尚未就绪/);
console.log('PASS: template choice survives refreshes and an unready template blocks the run');

// Reference reports stay references when changing content methods; uploaded old
// facts must not silently become current evidence.
const evidenceB={checked:false};
el('source-list').querySelector=selector=>selector.includes('"b"')?evidenceB:null;
const profileCode=source.slice(source.indexOf('const INDUSTRY_TASK_OUTLINE='),source.indexOf("$('industry-task-outline').onclick"));
const c=vm.createContext({$:el,console,JSON,CSS:{escape:s=>s},validateLengthInputs:()=>{},
 LENGTH_PRESETS:{compact:[800,1000],balanced:[1500,2000],detailed:[2000,2500]},
 selected:new Set(['a','b']),referenceSelected:new Set(['b']),
 state:{sources:[{id:'a',status:'ready'},{id:'b',status:'ready'}]}});
vm.runInContext(profileCode,c);
el('report-profile').value='industry_periodic';el('report-profile').onchange();
assert.equal(c.selected.has('b'),false,'reference material leaves this period');
el('report-profile').value='brief';el('report-profile').onchange();
assert.equal(c.selected.has('b'),false,'switching purpose must not promote old reports to current evidence');
assert.equal(c.referenceSelected.has('b'),true);
assert.equal(evidenceB.checked,false);
console.log('PASS: changing purpose preserves the reference/evidence separation');

// A template suggestion is visible; explicit method selection survives style changes.
const workflowCode=source.slice(source.indexOf('function readWorkflowChoice()'),source.indexOf('// Reference reports are explicitly'));
const workflows=JSON.parse(fs.readFileSync(new URL('../src/briefloop/workflow_assets/business_report/manifest.json',import.meta.url)));
c.esc=String;c.state.workflows=[workflows];c.state.templates=[{id:'business',workflow_hint:'business_report'}];
vm.runInContext(workflowCode,c);
el('template-select').value='business';vm.runInContext('renderWorkflowChoices(true)',c);
assert.match(el('workflow-hint').textContent,/本轮建议：商业报告 · 决策分析/);
el('workflow-choice').value='business_report/work_progress';vm.runInContext('renderWorkflowChoices()',c);
el('template-select').value='another-style';vm.runInContext('renderWorkflowChoices()',c);
assert.equal(el('workflow-choice').value,'business_report/work_progress');
assert.match(el('workflow-hint').textContent,/已选：商业报告 · 工作进展周报/);
assert.equal(vm.runInContext('readWorkflowChoice().workflow_variant',c),'work_progress');
console.log('PASS: explicit document purpose survives template changes');
