import fs from 'node:fs';
import vm from 'node:vm';
import assert from 'node:assert/strict';
import {welcomeReady,welcomeAgents,welcomeAgentCard} from '../frontend/welcome.js';
import {section} from './source_section.mjs';
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
vm.runInContext(section(source,'let welcomeMode=','function applyPendingSetupFields','frontend/app.js'),selection);
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
const pageCode=section(source,'function page(name){',"document.querySelectorAll('[data-page]')",'frontend/app.js');
const notices=[];
const p=vm.createContext({
 chat:{id:null},
 state:{settings:{model:'gpt-5.6-luna',model_selection_required:true}},activity:null,
 $:el,notice:(s)=>notices.push(s),document:{querySelectorAll:()=>[],body:{classList:{contains:()=>false,remove:()=>{}}}},
 renderTasks:()=>{},renderTaskGraph:()=>{},renderReports:()=>{},refreshCandidates:()=>{},moveSearchSettings:()=>{},applyPendingSearchInline:()=>{},applyPendingSetupFields:()=>{},
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
const statusCode=section(source,'function renderReportStatus(){','function renderAssistantSummary(){','frontend/app.js');
const empty=vm.createContext({$:el,current:undefined,state:{assessments:[],jobs:[]}});
vm.runInContext(statusCode,empty);
vm.runInContext('renderReportStatus()',empty);
assert.equal(el('report-status').innerHTML,'');
console.log('PASS: cold-start status rendering tolerates an empty workspace');

// Restoring the locally edited demo does not enter renderChat. The session list
// still needs to leave its HTML loading placeholder before the first idle poll.
const startupElements=new Map();
const startupEl=id=>{if(!startupElements.has(id))startupElements.set(id,{hidden:true,innerHTML:'',querySelectorAll:()=>[]});return startupElements.get(id)};
startupEl('session-list').innerHTML='<p>正在读取会话…</p>';
const startupPages=[],startupOpened=[],startupPolls=[];
const savedDemo={id:'edited-demo',run_id:'demo-run',author:'user'};
const startup=vm.createContext({
 $:startupEl,chat:{id:null,sessions:[],view:'active',drafts:new Map()},
 state:{settings:{model_selection_required:true},demo:{run_id:'demo-run'},briefs:[savedDemo],runs:[{id:'demo-run'}],jobs:[]},
 sessionStorage:{getItem:()=>null},localStorage:{getItem:()=>null,removeItem(){}},
 api:async route=>{assert.equal(route,'harness/sessions?view=active');return {sessions:[]}},
 page:name=>startupPages.push(name),openBrief:brief=>startupOpened.push(brief),
 adaptivePoll:(task,options)=>startupPolls.push({task,options}),backgroundActive:()=>false,
 renderWelcome(){throw Error('a saved demo should open locally')},renderChat(){throw Error('demo restoration does not enter chat')},
});
for(const name of ['renderSessions','initChat']){
 vm.runInContext(section(source,`${name==='initChat'?'async ':''}function ${name}(`,'\n}','frontend/app.js')+'\n}',startup);
}
await vm.runInContext('initChat()',startup);
assert.deepEqual(startupPages,['report']);
assert.equal(startupOpened[0],savedDemo);
assert.match(startupEl('session-list').innerHTML,/对话会保存在这里/);
assert.doesNotMatch(startupEl('session-list').innerHTML,/正在读取会话/);
assert.equal(startupPolls.length,0,'the startup coordinator alone schedules polling after successful initialization');
console.log('PASS: demo restoration renders the loaded session list before background polling');
