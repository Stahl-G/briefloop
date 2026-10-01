import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';
import {createReportFormDefaults} from '../frontend/report-form-defaults.js';
import {section} from './source_section.mjs';

const source=fs.readFileSync(new URL('../frontend/app.js',import.meta.url),'utf8');
const budgetIds=['budget-search-requests','budget-candidate-urls','budget-source-pages'];
function view(){
 const nodes={};
 const $=id=>nodes[id]??=({value:'',listeners:{},addEventListener(event,fn){(this.listeners[event]??=[]).push(fn)},
  emit(event){for(const fn of this.listeners[event]||[])fn({target:this});this['on'+event]?.({target:this})}});
 $('requirements').elements=Object.fromEntries(['title','period','period_start','period_end','report_timezone'].map(key=>[key,$(key)]));
 $('report-language').value='zh';$('report-profile').value='industry_periodic';$('research-tier').value='standard';$('length-preset').value='balanced';
 const defaults=createReportFormDefaults({$});defaults.init();defaults.restore();defaults.sync();
 const input=(id,value)=>{$(id).value=value;$(id).emit('input')};
 const lengths=()=>['target-words','max-words'].map(id=>Number($(id).value));
 const budget=()=>budgetIds.map(id=>Number($(id).value));
 return {$,defaults,input,lengths,budget};
}
const period=(start,end)=>({start,end_exclusive:end});

test('untouched industry form follows title and the normalized inclusive 25-day boundary',()=>{
 const {$,defaults,input,lengths,budget}=view();
 assert.deepEqual(lengths(),[5000,5500]);assert.deepEqual(budget(),[30,150,60]);
 input('title','AI MONTHLY');assert.deepEqual(lengths(),[9000,10000]);assert.deepEqual(budget(),[80,400,150]);
 input('title','行业动态');
 input('period_start','2026-09-01');input('period_end','2026-09-24');
 defaults.setWindow(period('2026-09-01T00:00:00+08:00','2026-09-25T00:00:00+08:00'));
 assert.deepEqual(lengths(),[5000,5500]);
 input('period_end','2026-09-25');
 defaults.setWindow(period('2026-09-01T00:00:00+08:00','2026-09-26T00:00:00+08:00'));
 assert.deepEqual(lengths(),[9000,10000]);assert.deepEqual(budget(),[80,400,150]);
 // A stale preview cannot apply a previous month's defaults to a new period.
 const old={period_start:'2026-09-01',period_end:'2026-09-25'};
 input('period_end','2026-09-07');defaults.setWindow(period('2026-09-01T00:00:00Z','2026-10-01T00:00:00Z'),old);
 assert.deepEqual(lengths(),[5000,5500]);assert.deepEqual(budget(),[30,150,60]);
});

test('monthly boundary uses calendar days through a spring DST transition',()=>{
 const {defaults,input,lengths,budget}=view();
 input('period_start','2026-03-01');input('period_end','2026-03-25');input('report_timezone','America/New_York');
 defaults.setWindow(period('2026-03-01T00:00:00-05:00','2026-03-26T00:00:00-04:00'));
 assert.deepEqual(lengths(),[9000,10000]);assert.deepEqual(budget(),[80,400,150]);
 input('period_end','2026-03-24');
 defaults.setWindow(period('2026-03-01T00:00:00-05:00','2026-03-25T00:00:00-04:00'));
 assert.deepEqual(lengths(),[5000,5500]);assert.deepEqual(budget(),[30,150,60]);
});

test('actual tier handler updates automatic budget but preserves numeric, preset and saved overrides',()=>{
 const {$,defaults,input,budget,lengths}=view();
 const context=vm.createContext({$,reportFormDefaults:defaults,renderBudgetProviderScope(){}});
 vm.runInContext(section(source,'const RESEARCH_TIERS=','const BUDGET_FIELDS=','frontend/app.js'),context);
 vm.runInContext(section(source,"$('research-tier').onchange=",'let budgetPolling=false;','frontend/app.js'),context);
 const tier=value=>{$('research-tier').value=value;$('research-tier').emit('change')};
 tier('quick');assert.deepEqual(budget(),[6,30,12]);
 const req={research_budget:{search_requests:6,candidate_urls:30,source_pages:12}};
 assert.deepEqual(defaults.prepare({...req}),req,'a selected tier budget is submitted');
 tier('deep');assert.deepEqual(budget(),[80,400,150]);
 tier('standard');assert.deepEqual(budget(),[30,150,60]);
 input('budget-search-requests','17');tier('deep');
 assert.deepEqual(budget(),[17,150,60]);assert.deepEqual(lengths(),[10000,12000]);
 input('title','月报');assert.deepEqual(budget(),[17,150,60]);
 defaults.restore();defaults.sync();$('budget-preset').value='weekly';$('budget-preset').emit('change');
 for(const [index,id] of budgetIds.entries())$(id).value=String([30,150,60][index]);
 tier('quick');assert.deepEqual(budget(),[30,150,60],'explicit weekly preset survives tier changes');
 defaults.restore({research_budget:{search_requests:30,candidate_urls:150,source_pages:60}});
 tier('deep');assert.deepEqual(budget(),[30,150,60],'saved explicit budget survives tier changes');
});

test('manual numbers and presets are explicit even when equal to weekly defaults',()=>{
 const {$,defaults,input,lengths,budget}=view();
 input('target-words','5000');input('max-words','5500');input('budget-search-requests','30');
 input('title','月报');assert.deepEqual(lengths(),[5000,5500]);assert.deepEqual(budget(),[30,150,60]);
 const req={target_words:5000,max_words:5500,research_budget:{search_requests:30,candidate_urls:150,source_pages:60}};
 assert.deepEqual(defaults.prepare({...req}),req);
 const selected=view();selected.$('budget-preset').value='weekly';selected.$('budget-preset').emit('change');
 selected.$('length-preset').emit('change');selected.input('title','月度报告');
 assert.deepEqual(selected.budget(),[30,150,60]);assert.deepEqual(selected.lengths(),[5000,5500]);
});

test('automatic lengths respect language, report purpose, deep research and explicit bounds',()=>{
 const {$,defaults,input,lengths,budget}=view();input('title','月报');
 $('report-language').value='en';defaults.sync();assert.deepEqual(lengths(),[5800,6500]);
 $('research-tier').value='deep';defaults.sync();assert.deepEqual(lengths(),[6500,8000]);
 $('research-tier').value='standard';$('report-profile').value='brief';defaults.sync();
 assert.deepEqual(lengths(),[1000,1300]);assert.deepEqual(budget(),[80,400,150]);
 $('max-words').value='800';defaults.restore({max_words:800});defaults.sync();assert.deepEqual(lengths(),[800,800]);
 assert.deepEqual(defaults.prepare({target_words:800,max_words:800,research_budget:{}}),{max_words:800});
 $('target-words').value='7000';defaults.restore({target_words:7000});defaults.sync();assert.deepEqual(lengths(),[7000,7000]);
});

test('saved explicit requirements and a chosen strict maximum survive period changes',()=>{
 const {$,defaults,input,lengths,budget}=view();
 defaults.restore({target_words:5000,max_words:5500,research_budget:{search_requests:30}});
 input('title','月报');assert.deepEqual(lengths(),[5000,5500]);assert.deepEqual(budget(),[30,150,60]);
 const fresh=view();fresh.$('length-mode').value='strict';fresh.$('length-mode').emit('change');fresh.input('title','月报');
 assert.deepEqual(fresh.lengths(),[5500,5500]);
 assert.deepEqual(fresh.defaults.prepare({target_words:5500,max_words:5500,research_budget:{}}),{max_words:5500});
});

test('actual preview handler uses normalized text-period response; submit omits untouched defaults before it resolves',async()=>{
 const {$,defaults,input,lengths}=view();
 $('requirements').elements.objective=$('objective');input('period','2026-09');
 let resolvePreview,submitted,work;
 const context=vm.createContext({$,reportFormDefaults:defaults,
  api:async(path,payload)=>path==='report-time-preview'?new Promise(resolve=>{resolvePreview=resolve}):(submitted=payload,{payload:'{}'}),
  FormData:class{constructor(){this.values={title:'行业动态',objective:'研究',period:'2026-09',report_profile:'industry_periodic',target_words:'5000',max_words:'5500'}}entries(){return Object.entries(this.values)}has(){return false}},
  action:fn=>{work=fn()},state:{settings:{company_context_enabled:true}},lengthControls:{read:()=>({length_mode:'soft'})},quickReport:{read:()=>({})},
  readResearchBudget:()=>({search_requests:30,candidate_urls:150,source_pages:60}),readSearchPolicy:()=>({}),readWorkflowChoice:()=>({}),
  referenceSelected:new Set(),readTemplateSections:()=>[],preserveWritingPreferences:()=>{},writingPreferencesOverride:null,
  saveModel:async()=>{},current:null,reportMcpSelection:{selection:()=>null},chat:{},selected:new Set(),parse:JSON.parse,showPendingReport:()=>{},page:()=>{},notice:()=>{}});
 vm.runInContext(section(source,'let reportTimePreviewTicket=0;','\nfor(const key of [\'period\',\'period_start\',\'period_end\',\'report_timezone\'])', 'frontend/app.js'),context);
 const pending=vm.runInContext('previewReportTime()',context);
 vm.runInContext(section(source,"$('requirements').onsubmit=","$('upload').onchange=",'frontend/app.js'),context);
 $('requirements').onsubmit({preventDefault(){},target:$('requirements')});await work;
 for(const key of ['target_words','max_words','research_budget','scout_limit'])assert.equal(key in submitted.requirements,false,`${key} must let admission choose the monthly default`);
 resolvePreview({...period('2026-09-01T00:00:00Z','2026-10-01T00:00:00Z'),today:'2026-10-01',timezone:'UTC'});await pending;
 assert.deepEqual(lengths(),[9000,10000]);assert.match($('report-system-clock').textContent,/2026-10-01/);
 input('target-words','5000');input('max-words','5500');input('budget-search-requests','30');
 $('requirements').onsubmit({preventDefault(){},target:$('requirements')});await work;
 assert.equal(submitted.requirements.target_words,5000);assert.equal(submitted.requirements.max_words,5500);assert.equal(submitted.requirements.research_budget.search_requests,30);
});
