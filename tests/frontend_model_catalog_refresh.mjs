import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';
import {section} from './source_section.mjs';
import {createModelCatalog,createProviderCatalog,catalogDescription} from '../frontend/model-catalog.js';

function harness(api){
 const elements=new Map(),selects=[],inputs=[];
 class Node {
  constructor(){this.value='';this.innerHTML='';this.dataset={};this.children=[];this.options=[];this.disabled=false}
  setAttribute(){} removeAttribute(){} getAttribute(){return ''}
  showModal(){this.open=true} close(){this.open=false}
  querySelectorAll(){return []} querySelector(){return this.children.find(c=>c.id)}
  before(){} append(node){this.children.push(node);node.parentElement=this;if(node.className==='model-picker-select')selects.push(node)}
  replaceChildren(...nodes){this.options=nodes} add(node){this.options.push(node)}
  dispatchEvent(){} focus(){} select(){}
 }
 const $=id=>{if(!elements.has(id)){const node=new Node();node.id=id;elements.set(id,node)}return elements.get(id)};
 const document={querySelectorAll:query=>query.startsWith('input[')?inputs:selects,createElement:()=>new Node()};
 class Option{constructor(text,value){this.text=text;this.value=value}}
 let backend='codex',chatBackend='claude';
 const directory=createModelCatalog({api,$,getBackend:()=>backend,getChatBackend:()=>chatBackend,document,Option,MutationObserver:null});
 return {directory,$,selects,addInput(id){const input=$(id);inputs.push(input);return input},setBackend(value){backend=value},setChatBackend(value){chatBackend=value}};
}
const payload=(id,extra={})=>({models:[{id,name:id,provider:'provider'}],source:'host_catalog',status:'ok',checked_at:'2026-09-29T10:00:00Z',...extra});
const tick=()=>new Promise(resolve=>setImmediate(resolve));

test('settings, onboarding and chat picker opens query fresh directories without changing the choice',async()=>{
 const calls=[];let number=0;
 const h=harness(async route=>{calls.push(route);return payload('model-'+(++number))});
 h.$('model-select').value='saved-model';h.$('chat-model').value='saved-chat-model';
 await h.directory.openModelPicker('model-select');
 assert.match(h.$('model-picker-list').innerHTML,/model-1/);
 await h.directory.openModelPicker('model-select'); // onboarding reuses the same target
 assert.match(h.$('model-picker-list').innerHTML,/model-2/);
 await h.directory.openModelPicker('chat-model');
 assert.match(h.$('model-picker-list').innerHTML,/model-3/);
 await h.directory.refreshCurrentModelPicker();
 assert.match(h.$('model-picker-list').innerHTML,/model-4/);
 assert.deepEqual(calls,['models?backend=codex&refresh=1','models?backend=codex&refresh=1','models?backend=claude&refresh=1','models?backend=claude&refresh=1']);
 h.$('model-picker-search').value='manual/model';h.directory.renderModelPicker();
 assert.equal(calls.length,4,'typing only filters the fetched directory');
 assert.match(h.$('model-picker-list').innerHTML,/使用此模型 ID/);
 assert.equal(h.$('model-select').value,'saved-model');assert.equal(h.$('chat-model').value,'saved-chat-model');
});

test('inline settings, roles and chat share live requests, including repeated keyboard opens',async()=>{
 const calls=[];let resolve;
 const h=harness(route=>{calls.push(route);return new Promise(done=>{resolve=done})});
 const main=h.addInput('model-select'),role=h.addInput('role-evaluator-model'),chat=h.addInput('chat-model');
 role.dataset.roleModel='evaluator';main.value='saved';role.value='';chat.value='saved-chat';h.setChatBackend('codex');h.directory.setupModelPickers();
 h.selects[0].onpointerdown();h.selects[1].onkeydown({key:'ArrowDown'});h.selects[2].onpointerdown();await tick();
 assert.equal(calls.length,1);resolve(payload('latest'));await tick();
 for(const select of h.selects)assert.ok(select.options.some(option=>option.value==='latest'));
 assert.equal(main.value,'saved');assert.equal(role.value,'');assert.equal(chat.value,'saved-chat');
 h.selects[2].onkeydown({key:'ArrowDown'});await tick();assert.equal(calls.length,2);resolve(payload('newer'));await tick();
 for(const select of h.selects)assert.ok(select.options.some(option=>option.value==='newer'));
});

test('late host responses cannot replace a newly opened backend or its saved selection',async()=>{
 const pending=new Map();const h=harness(route=>new Promise(resolve=>pending.set(route,resolve)));
 h.$('model-select').value='saved-native';
 const old=h.directory.openModelPicker('model-select');await tick();
 h.setBackend('briefloop-native');const current=h.directory.openModelPicker('model-select');await tick();
 pending.get('models?backend=briefloop-native&refresh=1')(payload('provider/new',{source:'provider_api'}));await current;
 pending.get('models?backend=codex&refresh=1')(payload('old-host'));await old;
 assert.match(h.$('model-picker-list').innerHTML,/provider\/new/);assert.doesNotMatch(h.$('model-picker-list').innerHTML,/old-host/);
 assert.match(h.$('model-picker-status').textContent,/提供方 API/);assert.equal(h.$('model-select').value,'saved-native');
});

test('failed refresh removes old candidates and static hints but preserves manual choices',async()=>{
 let mode='good';const h=harness(async()=>{if(mode==='error')throw Error('credential unavailable');return mode==='hints'?payload('factory-preset',{source:'builtin_hints'}):payload('old-model')});
 h.$('model-select').value='manual/persisted';await h.directory.openModelPicker('model-select');mode='error';await h.directory.refreshCurrentModelPicker();
 assert.equal(h.directory.catalogs.get('codex').models.length,0);assert.doesNotMatch(h.$('model-picker-list').innerHTML,/old-model/);
 assert.match(h.$('model-picker-status').textContent,/目录读取失败.*credential unavailable/);
 mode='hints';await h.directory.refreshCurrentModelPicker();assert.equal(h.directory.catalogs.get('codex').models.length,0);
 assert.equal(h.$('model-select').value,'manual/persisted');
 const html=fs.readFileSync(new URL('../src/briefloop/static/index.html',import.meta.url),'utf8');
 assert.match(html,/<datalist id="model-suggestions"><\/datalist>/);
});

test('Native provider field and execution picker consume the same API result with partial diagnostics',async()=>{
 let calls=0;const h=harness(async()=>{calls++;return payload('vendor/live',{source:'provider_api',status:'partial',refreshed_at:'2026-09-29T11:00:00Z',providers:[{provider:'vendor',status:'reachable',source:'provider_api',models:['live']},{provider:'other',status:'auth_failed',diagnostic:'missing key'}]})});
 h.setBackend('briefloop-native');h.$('custom-provider').value='vendor';h.$('custom-model').value='saved-id';
 const provider=createProviderCatalog({api:()=>assert.fail('Native must use shared catalog'),$:h.$,getEndpoint:()=> 'native',modelDirectory:h.directory});
 await Promise.all([h.directory.openModelPicker('model-select'),provider.load()]);
 assert.equal(calls,1);assert.match(h.$('provider-model-options').innerHTML,/value="live"/);assert.equal(h.$('custom-model').value,'saved-id');
 assert.match(h.$('model-picker-status').textContent,/部分目录读取失败/);assert.match(h.$('model-picker-status').textContent,/missing key/);
 assert.match(catalogDescription(h.directory.catalogs.get('briefloop-native')),/不代表模型调用成功/);
});

test('independent Reviewer uses its explicit backend without inheriting the main directory',async()=>{
 const calls=[];const h=harness(async route=>{calls.push(route);return payload('review-model')});
 const input=h.addInput('review-model');input.dataset.modelBackendInput='review-backend';input.value='saved-review';h.$('review-backend').value='briefloop-native';
 h.directory.setupModelPickers();h.selects[0].onpointerdown();await tick();
 assert.deepEqual(calls,['models?backend=briefloop-native&refresh=1']);assert.equal(input.value,'saved-review');
});

// Use the real save handler: an explicit connection save must break both the
// shared execution request and the configuration form's request, even when the
// engine and provider IDs remain unchanged.
for(const engine of ['native','opencode'])test(`saving a same-name ${engine} provider starts fresh requests and retains their in-flight deduplication`,async()=>{
 const backend=engine==='native'?'briefloop-native':'opencode',requests=[];
 const h=harness(route=>new Promise((resolve,reject)=>requests.push({route,resolve,reject})));
 h.setBackend(backend);h.$('custom-provider').value='vendor';h.$('custom-model').value='manual-saved';h.$('model-select').value='vendor/manual-saved';
 h.$('provider-engine').value=backend;
 const provider=createProviderCatalog({api:route=>new Promise((resolve,reject)=>requests.push({route,resolve,reject})),$:h.$,getEndpoint:()=>engine,modelDirectory:h.directory});
 const oldMain=h.directory.fetchModelCatalog(true,backend),oldProvider=provider.load();await tick();
 const beforeSave=requests.length;assert.equal(beforeSave,engine==='native'?1:2);
 const app=fs.readFileSync(new URL('../frontend/app.js',import.meta.url),'utf8');
 const context=vm.createContext({$:h.$,providerEndpoint:()=>engine,modelDirectory:h.directory,providerDirectory:provider,
  providerCapabilities:{read:()=>({})},loadProviderCatalog:()=>provider.load(),
  api:async route=>{assert.equal(route,engine+'/provider');return {model:'vendor/manual-saved'}}});
 vm.runInContext(section(app,"$('provider-form').onsubmit=",'const providerDirectory=', 'frontend/app.js'),context);
 const saved=h.$('provider-form').onsubmit({preventDefault(){}});await tick();
 assert.equal(requests.length,beforeSave*2,'saving must issue new requests before the old ones settle');
 // An old completion cannot remove the current pending entry. Native exercises
 // old success, OpenCode old failure; neither may replace the refreshed UI.
 for(const request of requests.slice(0,beforeSave)){
  if(engine==='opencode')request.reject(Error('obsolete connection failed'));
  else request.resolve(payload('vendor/old',{source:'provider_api',providers:[{provider:'vendor',status:'reachable',source:'provider_api',models:['old']}]}));
 }
 await Promise.all([oldMain,oldProvider]);
 assert.equal(h.directory.catalogs.get(backend).loading,true);
 assert.doesNotMatch(h.$('provider-result').textContent,/obsolete|目录已读取/);
 const duplicateMain=h.directory.fetchModelCatalog(true,backend),duplicateProvider=provider.load();await tick();
 assert.equal(requests.length,beforeSave*2,'old finally must not delete the new in-flight requests');
 for(const request of requests.slice(beforeSave))request.resolve(request.route.startsWith('models?')
  ?payload('vendor/new',{source:'provider_api',providers:[{provider:'vendor',status:'reachable',source:'provider_api',models:['new']}]})
  :{models:['new'],source:'provider_api',status:'reachable'});
 await Promise.all([saved,duplicateMain,duplicateProvider]);
 assert.deepEqual(h.directory.catalogs.get(backend).models.map(m=>m.id),['vendor/new']);
 assert.match(h.$('provider-model-options').innerHTML,/value="new"/);assert.doesNotMatch(h.$('provider-model-options').innerHTML,/old/);
 assert.equal(h.$('model-select').value,'vendor/manual-saved');assert.equal(h.$('custom-model').value,'manual-saved');
});

test('an obsolete model request resolving after its replacement cannot overwrite the new catalog',async()=>{
 const requests=[];const h=harness(()=>new Promise(resolve=>requests.push(resolve)));
 const old=h.directory.fetchModelCatalog(true,'codex');await tick();h.directory.invalidate('codex');
 const current=h.directory.fetchModelCatalog(true,'codex');await tick();requests[1](payload('new'));await current;
 requests[0](payload('old'));await old;
 assert.deepEqual(h.directory.catalogs.get('codex').models.map(m=>m.id),['new']);
});
