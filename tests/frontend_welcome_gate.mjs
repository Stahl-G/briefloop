import fs from 'node:fs';
import vm from 'node:vm';
import assert from 'node:assert/strict';
import {welcomeReady,welcomeAgents,welcomeAgentCard} from '../frontend/welcome.js';
const source=fs.readFileSync('frontend/app.js','utf8');
const elements=new Map();const el=id=>{if(!elements.has(id))elements.set(id,{hidden:id!=='welcome'});return elements.get(id)};
// A factory model, an unavailable CLI or an unfinished selection must not start.
const settings={agent_backend:'codex',model:'gpt-5.6-luna',model_selection_required:true};
const runtimes=[{id:'codex',name:'Codex CLI',available:true},{id:'briefloop-native',available:true}];
assert.equal(welcomeReady(settings,runtimes,'cli'),false);
settings.model_selection_required=false;
assert.equal(welcomeReady(settings,runtimes,'cli'),true);
assert.equal(welcomeReady(settings,runtimes,'cli',true),false);
assert.equal(welcomeReady(settings,runtimes,'native'),false);
assert.equal(welcomeReady(settings,[],'cli'),false);
settings.agent_backend='briefloop-native';
assert.equal(welcomeReady(settings,runtimes,'cli'),false);
assert.equal(welcomeReady(settings,runtimes,'native'),true);
// Re-selecting the same Agent preserves the user's selected model.
let backendChanges=0;
const selection=vm.createContext({$:el,state:{settings},runtimeCatalog:runtimes,renderWelcome(){}});
el('agent-backend').onchange=()=>{backendChanges++};
vm.runInContext(source.slice(source.indexOf('let welcomeMode='),source.indexOf('function applyPendingSetupFields')),selection);
await vm.runInContext("chooseWelcomeAgent('briefloop-native')",selection);
assert.equal(backendChanges,0);
assert.equal(settings.model_selection_required,false);
assert.equal(settings.model,'gpt-5.6-luna');
// Compact cards never omit the selection or mix Native into the CLI list.
const many=['antigravity','hermes','kimi','codex','claude','pi','opencode','deepseek-harness'].map(id=>({id,available:true}));
many.push({id:'briefloop-native',available:true},{id:'unavailable',available:false});
const compact=welcomeAgents(many,'kimi');
assert.equal(compact.total,8);assert.equal(compact.visible.length,6);
assert.ok(compact.visible.some(r=>r.id==='kimi'));
assert.ok(!welcomeAgents(many,'',true).visible.some(r=>['briefloop-native','unavailable'].includes(r.id)));
assert.match(welcomeAgentCard({id:'deepseek-harness',name:'DeepSeek Harness'},'deepseek-harness'),/runtime-deepseek.svg/);
assert.match(welcomeAgentCard({id:'codex',name:'Codex <CLI>'},'codex'),/Codex &lt;CLI&gt;/);
console.log('PASS: explicit model choice, selected mode, available Agent and same-card selection');

// The only model entry on welcome must also accept an explicit ID when the
// provider does not offer a catalogue. IME composition must not submit it.
const picked=[];
const custom=vm.createContext({$:el,pickModel:id=>picked.push(id),renderModelPicker(){}});
vm.runInContext(source.split('\n').find(line=>line.startsWith("$('model-picker-search').onkeydown=")),custom);
const input={key:'Enter',target:{value:' vendor/model '},preventDefault(){}};
el('model-picker-search').onkeydown({...input,isComposing:true});
assert.equal(picked.length,0);
el('model-picker-search').onkeydown(input);
assert.deepEqual(picked,['vendor/model']);

// Clicking the sidebar cannot bypass the first-run page.
const pageCode=source.slice(source.indexOf('function page(name){'),source.indexOf("document.querySelectorAll('[data-page]')"));
const notices=[];
const p=vm.createContext({
 chat:{id:null},
 state:{settings:{model:'gpt-5.6-luna',model_selection_required:true}},activity:null,
 $:el,notice:(s)=>notices.push(s),document:{querySelectorAll:()=>[],body:{classList:{contains:()=>false,remove:()=>{}}}},
 renderTasks:()=>{},renderTaskGraph:()=>{},refreshCandidates:()=>{},moveSearchSettings:()=>{},applyPendingSearchInline:()=>{},applyPendingSetupFields:()=>{},
});
el('welcome').hidden=false;
vm.runInContext(pageCode,p);vm.runInContext("page('chat')",p);
assert.equal(el('welcome').hidden,false,'the first-run page stays visible when the sidebar tries to leave');
assert.ok(notices.length>=1,'leaving the first-run page explains why it stays');
vm.runInContext("page('settings-dialog')",p);
assert.equal(el('settings-dialog').hidden,false,'settings is still reachable during first run');
console.log('PASS: the sidebar cannot bypass the first-run page, but settings stays reachable');
el('welcome').hidden=false;
vm.runInContext("chat.id='existing-test';page('chat')",p);
assert.equal(el('chat').hidden,false,'existing test errors remain readable before configuring a new model');
assert.equal(el('welcome').hidden,true);

el('welcome').hidden=false;
vm.runInContext("chat.id=null;page('reports')",p);
assert.equal(el('reports').hidden,false,'saved reports stay accessible without choosing a model');
vm.runInContext("page('report')",p);
assert.equal(el('report').hidden,false,'a saved report can be read, edited and exported locally');
vm.runInContext("page('setup')",p);
assert.equal(el('welcome').hidden,false,'browsing saved reports does not bypass model selection for a new task');

// An empty workspace has no current brief while the welcome page is rendered.
const statusCode=source.slice(source.indexOf('function renderReportStatus(){'),source.indexOf('function renderAssistantSummary(){'));
const empty=vm.createContext({$:el,current:undefined,state:{assessments:[],jobs:[]}});
vm.runInContext(statusCode,empty);
vm.runInContext('renderReportStatus()',empty);
assert.equal(el('report-status').innerHTML,'');
console.log('PASS: cold-start status rendering tolerates an empty workspace');
