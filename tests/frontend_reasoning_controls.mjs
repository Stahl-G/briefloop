import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';
import {section} from './source_section.mjs';
import {reasoningControls,settingsEffort,reasoningModel} from '../frontend/reasoning-controls.js';

function dom(t){
 const before={document:globalThis.document,Option:globalThis.Option,Event:globalThis.Event};
 class Node{
  constructor(){this.dataset={};this.children=[];this.value='';this.id='';this.disabled=false}
  querySelector(selector){return this.children.find(n=>selector==='[data-variant-choices]'?Object.hasOwn(n.dataset,'variantChoices'):Object.hasOwn(n.dataset,'reasoningNote'))}
  append(node){this.children.push(node);node.parentElement=this}
  before(node){this.parentElement.append(node)}
  replaceChildren(...children){this.children=children}
  removeAttribute(){}setAttribute(){}getAttribute(){return '推理强度'}focus(){}
  dispatchEvent(){this.onchange?.()}
 }
 globalThis.document={createElement:()=>new Node()};
 globalThis.Option=class{constructor(text,value){this.text=text;this.value=value}};
 globalThis.Event=class{};
 t.after(()=>Object.assign(globalThis,before));
 const parent=new Node(),control=new Node();parent.append(control);control.id='variant';
 return {parent,control};
}

test('a saved high never filters the other advertised variants out of the selector',async t=>{
 const {parent,control}=dom(t);control.value='high';let saves=0;control.onchange=()=>saves++;
 const ui=reasoningControls({api:async()=>({kind:'variant',options:[{id:'low'},{id:'medium'},{id:'high'},{id:'turbo'}]})});
 await ui.configure(control,'opencode','provider/model',{variant:true});
 const select=parent.querySelector('[data-variant-choices]');
 assert.deepEqual(select.children.map(o=>o.value),['','low','medium','high','turbo','__custom__']);
 assert.equal(select.value,'high');assert.equal(control.hidden,true);
 select.value='low';select.onchange();assert.equal(control.value,'low');
 select.value='';select.onchange();assert.equal(control.value,'');assert.equal(saves,2);
 select.value='__custom__';select.onchange();assert.equal(control.hidden,false);
});

test('a delayed old model response cannot replace the new host choices or explicit default',async t=>{
 const {control}=dom(t);control.value='none';const replies=new Map();
 const ui=reasoningControls({api:url=>new Promise(resolve=>replies.set(url.includes('backend=pi')?'pi':'claude',resolve))});
 const old=ui.configure(control,'claude','model'),current=ui.configure(control,'pi','other');
 replies.get('pi')({kind:'levels',options:[{id:'off'},{id:'low'}]});await current;
 replies.get('claude')({kind:'levels',options:[{id:'high'}]});await old;
 assert.deepEqual(control.children.map(o=>o.value),['none','off','low']);assert.equal(control.value,'none');
 assert.equal(settingsEffort({reasoning_effort:'high'},'claude'),'none');
 assert.equal(settingsEffort({},'codex'),'none','an unconfigured workspace does not silently select high');
 assert.equal(settingsEffort({runtime_efforts:{pi:'off'}},'pi'),'off');
});

test('Antigravity Gemini tier aliases cannot contradict an explicit effort',()=>{assert.equal(reasoningModel('antigravity','gemini-3.8-flash-high','low'),'gemini-3.8-flash');assert.equal(reasoningModel('antigravity','gemini-3.8-flash-high','none'),'gemini-3.8-flash-high')});

test('Claude only offers advertised effort levels and unknown saved values fall back visibly to the host',async t=>{
 const {parent,control}=dom(t);control.value='medium';
 const ui=reasoningControls({api:async()=>({kind:'host',options:[],note:'Long host description'})});
 await ui.configure(control,'claude','default');
 assert.deepEqual(control.children.map(option=>option.value),['none']);assert.equal(control.children[0].text,'跟随 Claude Code');assert.equal(control.value,'none');
 assert.match(parent.querySelector('[data-reasoning-note]').textContent,/medium 未获宿主确认/);
 const live=reasoningControls({api:async()=>({kind:'levels',options:[{id:'low'},{id:'high'}]})});control.value='high';await live.configure(control,'claude','real-model');
 assert.deepEqual(control.children.map(option=>option.value),['none','low','high']);assert.equal(control.value,'high');
});

test('pending and failed discovery preserve the selection; settling refreshes summaries without saving',async t=>{
 const {control}=dom(t);control.value='medium';let resolve,saves=0;const summaries=[];
 control.onchange=()=>saves++;
 const ui=reasoningControls({api:()=>new Promise(done=>resolve=done),onUpdate:input=>summaries.push(input.value)});
 const pending=ui.configure(control,'claude','selected-model');
 assert.equal(control.value,'medium');
 assert.ok(control.children.some(option=>option.value==='medium'));
 // A user can explicitly choose the default while discovery is pending.
 control.value='none';resolve({options:[{id:'medium'},{id:'high'}]});await pending;
 assert.equal(control.value,'none');assert.deepEqual(summaries,['none']);assert.equal(saves,0);
 control.value='high';const failed=reasoningControls({api:async()=>{throw Error('offline')}});
 await failed.configure(control,'claude','selected-model');assert.equal(control.value,'high');
 assert.equal(saves,0,'discovery never emits a persistence event');
 const unsupported=reasoningControls({api:async()=>({options:[{id:'medium'}]}),onUpdate:input=>summaries.push(input.value)});
 await unsupported.configure(control,'claude','selected-model');
 assert.equal(control.value,'none');assert.equal(summaries.at(-1),'none');
});

test('settings save waits for discovery and refuses a model switched during the wait',async t=>{
 const {control}=dom(t);control.id='effort-select';control.value='medium';
 const elements={'effort-select':control,'model-select':{value:'first'},'model-provider':{value:''},'model-variant':{value:''}};
 const replies=new Map(),patches=[];let backend='claude';
 const api=(route,patch)=>route==='settings'?patches.push(patch):new Promise((resolve,reject)=>replies.set(route,{resolve,reject}));
 const reasoning=reasoningControls({api});
 const context=vm.createContext({$:id=>elements[id],backendValue:()=>backend,reasoning,reasoningModel,api,
  state:{settings:{runtime_efforts:{pi:'off'}}},updateModelLabel(){}});
 const source=fs.readFileSync('frontend/app.js','utf8');
 vm.runInContext(section(source,'async function saveModel(){','let runtimeCatalog=','frontend/app.js'),context);
 const key=model=>'runtime/reasoning?backend=claude&model='+model;
 const first=vm.runInContext('saveModel()',context);
 assert.equal(patches.length,0);assert.equal(control.value,'medium');
 replies.get(key('first')).resolve({options:[{id:'medium'},{id:'high'}]});await first;
 assert.equal(patches.at(-1).runtime_efforts.claude,'medium');assert.equal(patches.at(-1).runtime_efforts.pi,'off');
 reasoning.refresh();elements['model-select'].value='second';
 const stale=vm.runInContext('saveModel()',context);const rejected=assert.rejects(stale,/模型选择已改变/);
 elements['model-select'].value='third';control.value='high';
 const latest=vm.runInContext('saveModel()',context);
 replies.get(key('third')).resolve({options:[{id:'high'}]});await latest;
 replies.get(key('second')).resolve({options:[{id:'medium'}]});await rejected;
 assert.equal(patches.length,2);assert.equal(patches.at(-1).model,'third');assert.equal(control.value,'high');
 reasoning.refresh();
 const failed=vm.runInContext('saveModel()',context);replies.get(key('third')).reject(Error('offline'));await failed;
 assert.equal(patches.at(-1).runtime_efforts.claude,'high','a failed discovery preserves the explicit saved effort');
});
