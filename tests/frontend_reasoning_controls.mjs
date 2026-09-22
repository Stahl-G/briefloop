import test from 'node:test';
import assert from 'node:assert/strict';
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
 assert.equal(settingsEffort({runtime_efforts:{pi:'off'}},'pi'),'off');
});

test('Antigravity Gemini tier aliases cannot contradict an explicit effort',()=>{assert.equal(reasoningModel('antigravity','gemini-3.8-flash-high','low'),'gemini-3.8-flash');assert.equal(reasoningModel('antigravity','gemini-3.8-flash-high','none'),'gemini-3.8-flash-high')});
