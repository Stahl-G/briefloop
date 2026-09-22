import fs from 'node:fs';
import vm from 'node:vm';
import assert from 'node:assert/strict';
import {runtimeCard} from '../frontend/runtime-cards.js';
const source=fs.readFileSync('frontend/app.js','utf8');
const elements=new Map();const el=id=>{if(!elements.has(id))elements.set(id,{value:'',dataset:{}});return elements.get(id)};
const calls=[];
const ctx=vm.createContext({$:el,state:{settings:{model_variant:'low'}},chat:{},chatBackendChoice:()=> 'briefloop-native',
 api:async(route,body)=>{calls.push({route,body:{...body}});return {model:'custom/model',models:[],status:'reachable'}},
 esc:x=>x,action:fn=>fn(),selectChat:async()=>{},saveModel:async()=>{},refresh:async()=>{},renderBackend:()=>{},refreshModelSuggestions:async()=>{},backendValue:()=> 'opencode',chatActive:()=>false,renderChatRuntimePermissions:()=>{},rememberDraft:()=>{},updateComposer:()=>{}});
vm.runInContext(source.slice(source.indexOf('function runtimeChoice(){'),source.indexOf('function messageTime(')),ctx);
el('chat-variant').value='low';el('chat-model').value='custom/model';el('chat-permission').value='read-only';
assert.deepEqual(JSON.parse(JSON.stringify(vm.runInContext('runtimeChoice()',ctx))),{backend:'briefloop-native',model:'custom/model',variant:'low',permission:'read-only'});
el('chat-model').value='default';assert.throws(()=>vm.runInContext('runtimeChoice()',ctx),/provider\/model/);
vm.runInContext(source.slice(source.indexOf('function providerEndpoint(){'),source.indexOf("$('provider-engine').onchange=")),ctx);
vm.runInContext(source.slice(source.indexOf("$('provider-form').onsubmit="),source.indexOf("$('timeout-minutes').onchange=")),ctx);
el('provider-engine').value='briefloop-native';el('custom-provider').value='custom';el('custom-model').value='model';
el('custom-base-url').value='https://example.test/v1';el('custom-protocol').value='chat-completions';
el('custom-api-key').value='test-only-secret';el('custom-supports-images').value='';
await el('provider-form').onsubmit({preventDefault(){}});
assert.equal(calls[0].route,'native/provider');assert.equal(calls[0].body.api_key,'test-only-secret');
assert.equal(el('custom-api-key').value,'');assert.equal(calls[1].route,'native/provider-catalog');
await el('provider-use').onclick();assert.equal(ctx.chat.nextBackend,'briefloop-native');assert.equal(el('agent-backend').value,'briefloop-native');
await el('provider-test-model').onclick();assert.equal(calls.at(-1).route,'runtime-test');
assert.equal(calls.at(-1).body.backend,'briefloop-native');assert.equal(calls.at(-1).body.model,'custom/model');
const html=runtimeCard({id:'briefloop-native',name:'BriefLoop 内置引擎',installed:true,available:true,version:'test'},{chosen:'briefloop-native',model:'custom/model',esc:x=>x});
assert.ok(html.includes('BriefLoop 内置引擎')&&html.includes('data-runtime-select="briefloop-native"')&&html.includes('<svg'));
// Opening the Native tab changes the configuration form, not the saved host.
ctx.state.settings.agent_backend='opencode';
el('provider-engine').value='opencode';el('custom-api-key').value='old-test-key';
let opened=0;el('provider-open').click=()=>{opened++};
vm.runInContext(source.slice(source.indexOf("$('provider-engine').onchange="),source.indexOf("$('provider-open').onclick=")),ctx);
const nativeTab=source.split('\n').find(line=>line.includes("$('settings-tab-api').onclick="));
vm.runInContext(nativeTab,ctx);
el('settings-tab-api').onclick();
assert.equal(el('provider-engine').value,'briefloop-native');assert.equal(opened,1);
assert.equal(el('custom-api-key').value,'');assert.equal(ctx.state.settings.agent_backend,'opencode');
console.log('PASS: Native model/permission selection, local provider save and test route use the chosen engine');
