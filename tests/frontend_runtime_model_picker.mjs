import test from 'node:test';
import assert from 'node:assert/strict';
import {createRuntimeModelPicker} from '../frontend/runtime-model-picker.js';
import {createRuntimePickerBindings} from '../frontend/runtime-picker-bindings.js';
const tick=()=>new Promise(r=>setImmediate(r));
function harness({api=async()=>({options:[{id:'high'}]}),fetchModels,commit=async()=>{},canEdit=()=>true}={}){
 const nodes=new Map();let focused=null;
 class Node{
  constructor(){this.value='';this.options=[];this.disabled=false;this.hidden=false;this.dataset={};this.innerHTML='';this.textContent=''}
  replaceChildren(...options){this.options=options}add(option){this.options.push(option)}
  querySelectorAll(){return []}querySelector(){return {focus(){focused='model'}}}
  showModal(){this.open=true}close(){this.open=false;this.onclose?.()}focus(){focused=this.id}
 }
 class Option{constructor(text,value){this.text=text;this.value=value}}
 const $=id=>{if(!nodes.has(id)){const n=new Node();n.id=id;nodes.set(id,n)}return nodes.get(id)};
 const directory={catalogs:new Map(),async fetchModelCatalog(_,backend){const models=fetchModels?await fetchModels(backend):[{id:backend+'/one'},{id:'unavailable',available:false}];this.catalogs.set(backend,{models,status:'ok'});return models}};
 const current={backend:'codex',model:'old',effort:'high'},commits=[];
 const picker=createRuntimeModelPicker({api,$,directory,getRuntimes:()=>[{id:'codex',name:'Codex',available:true},{id:'claude',name:'Claude',available:true},{id:'briefloop-native',name:'Native',available:true},{id:'missing',name:'Missing',available:false}],read:()=>current,commit:async(...args)=>{commits.push(args);await commit(...args)},canEdit,document:{activeElement:{focus(){focused='opener'}}},Option});
 const field=id=>$('runtime-picker-'+id);
 const selectBackend=backend=>{field('backend').value=backend;field('backend').onchange()};
 const custom=model=>{field('custom').value=model;field('custom-form').onsubmit({preventDefault(){}})};
 return {$,field,current,commits,picker,selectBackend,custom,directory,focused:()=>focused};
}
test('engine/model/effort are staged; Escape cancels and keyboard search focuses candidates',async()=>{
 const h=harness();await h.picker.open('chat-model');assert.equal(h.focused(),'runtime-picker-backend');
 h.selectBackend('claude');await tick();assert.equal(h.field('apply').disabled,true);h.custom('default');await tick();
 h.field('effort').value='high';h.field('effort').onchange();
 let prevented=0;h.field('search').onkeydown({key:'Enter',isComposing:true,preventDefault(){prevented++}});assert.equal(prevented,0);
 h.field('search').onkeydown({key:'Enter',preventDefault(){prevented++}});assert.equal(h.focused(),'model');
 h.$('runtime-model-picker').oncancel({preventDefault(){}});assert.equal(h.commits.length,0);assert.equal(h.current.backend,'codex');assert.equal(h.current.model,'old');assert.equal(h.focused(),'opener');
 await h.picker.open('chat-model');assert.equal(h.field('backend').value,'codex');h.selectBackend('missing');assert.equal(h.field('backend').value,'codex');h.picker.close();
});
test('late directories and reasoning never overwrite the new engine/model; closed opens stay closed',async()=>{
 const catalogs=new Map(),reasoning=[];const h=harness({fetchModels:b=>new Promise(r=>catalogs.set(b,r)),api:route=>new Promise(r=>reasoning.push({route,r}))});
 const first=h.picker.open('chat-model');await tick();h.selectBackend('claude');catalogs.get('claude')([{id:'default'}]);await tick();
 h.custom('default');await tick();reasoning[1].r({options:[{id:'low'}]});await tick();
 catalogs.get('codex')([{id:'obsolete'}]);reasoning[0].r({options:[{id:'obsolete'}]});await first;
 assert.match(h.field('list').innerHTML,/default/);assert.doesNotMatch(h.field('list').innerHTML,/obsolete/);assert.equal(h.field('effort').options.some(o=>o.value==='obsolete'),false);
 await h.field('apply').onclick();assert.deepEqual(h.commits[0][1],{backend:'claude',model:'default',effort:'none'});assert.equal(h.$('runtime-model-picker').open,false);
});
test('failed Apply remains editable, current lock blocks commit, custom provider/model and variant are retained',async()=>{
 let locked=false,fail=true;const h=harness({canEdit:()=>!locked,commit:async()=>{if(fail)throw Error('save failed')}});
 await h.picker.open('model-select');h.selectBackend('briefloop-native');await tick();h.custom('provider/model');await tick();
 h.field('effort').value='__custom__';h.field('effort').onchange();h.field('custom-effort').value='deep';h.field('custom-effort').oninput();
 locked=true;await h.field('apply').onclick();assert.equal(h.commits.length,0);locked=false;h.field('search').oninput();
 await h.field('apply').onclick();assert.equal(h.$('runtime-model-picker').open,true);assert.match(h.field('error').textContent,/save failed/);assert.equal(h.field('apply').disabled,false);
 fail=false;await h.field('apply').onclick();assert.equal(h.commits[1][1].effort,'deep');assert.equal(h.$('runtime-model-picker').open,false);
});
test('Apply waits for effort validation and displays unavailable models without selecting them',async()=>{
 let resolve;const h=harness({api:()=>new Promise(r=>resolve=r)});const opened=h.picker.open('chat-model');await tick();assert.equal(h.field('apply').disabled,true);
 resolve({options:[]});await opened;assert.equal(h.field('effort').value,'none');assert.match(h.field('effort-note').textContent,/未获/);assert.match(h.field('list').innerHTML,/unavailable[^]*不可用/);
 h.custom('unavailable');resolve({options:[]});await tick();assert.equal(h.field('apply').disabled,true);
});
function bindingsHarness(){
 const nodes=new Map(),$=id=>{if(!nodes.has(id))nodes.set(id,{value:'',disabled:false});return nodes.get(id)};
 const state={workspace_id:'workspace',settings:{agent_backend:'codex',model:'old',role_models:{evaluator:{model:'role'}},runtime_efforts:{}}};
 const chat={id:'session',session:{turn_id:'turn'},events:new Map(),nextBackend:'codex',hostOptions:{mode:'full'},busy:false,request:'request'};
 let posts=[],fail=false,saved=0,chatRefresh=0,settingsRefresh=0;
 $('chat-model').value=$('model-select').value='old';$('agent-backend').value='codex';$('chat-effort').value=$('effort-select').value='high';$('chat-permission').value='read-only';$('chat-model-provider').value='provider';$('chat-service-tier').value='fast';
 const bindings=createRuntimePickerBindings({$,api:async(route,patch)=>{if(fail)throw Error('offline');posts.push({route,patch});return {...state.settings,...patch}},getState:()=>state,getChat:()=>chat,getBackend:()=>$('agent-backend').value,getChatBackend:()=>chat.nextBackend,assignEffort:(id,value)=>$(id).value=value,setChatPermission:value=>$('chat-permission').value=value,rememberDraft:()=>saved++,refreshChat:()=>chatRefresh++,refreshSettings:()=>settingsRefresh++});
 return {$,state,chat,bindings,posts,fail:()=>fail=true,counts:()=>({saved,chatRefresh,settingsRefresh})};
}
test('chat commit is draft-only and resets permissions only when changing engine',async()=>{
 const h=bindingsHarness();await h.bindings.commit('chat-model',{backend:'codex',model:'new',effort:'low'},h.bindings.read('chat-model'));
 assert.equal(h.$('chat-permission').value,'read-only');assert.deepEqual(h.chat.hostOptions,{mode:'full'});assert.equal(h.posts.length,0);
 await h.bindings.commit('chat-model',{backend:'claude',model:'default',effort:'none'},h.bindings.read('chat-model'));
 assert.equal(h.chat.nextBackend,'claude');assert.equal(h.$('chat-model').value,'default');assert.equal(h.$('chat-permission').value,'');assert.deepEqual(h.chat.hostOptions,{});assert.equal(h.$('chat-model-provider').value,'');assert.equal(h.$('chat-service-tier').value,'');assert.equal(h.posts.length,0);
});
test('settings Apply posts one tuple, saves only after success, preserves chat and same-engine roles',async()=>{
 const h=bindingsHarness();await h.bindings.commit('model-select',{backend:'codex',model:'new',effort:'low'},h.bindings.read('model-select'));
 assert.equal(h.posts.length,1);assert.equal(h.posts[0].patch.role_models,undefined);assert.equal(h.state.settings.role_models.evaluator.model,'role');
 await h.bindings.commit('model-select',{backend:'briefloop-native',model:'provider/model',effort:'deep'},h.bindings.read('model-select'));
 assert.deepEqual(h.posts[1].patch,{agent_backend:'briefloop-native',model:'provider/model',model_selection_required:false,role_models:{},model_provider:null,model_variant:'deep',service_tier:null});
 assert.equal(h.$('chat-model').value,'old');assert.equal(h.chat.nextBackend,'codex');
 h.fail();const before=JSON.stringify(h.state);await assert.rejects(h.bindings.commit('model-select',{backend:'claude',model:'default',effort:'none'},h.bindings.read('model-select')),/offline/);assert.equal(JSON.stringify(h.state),before);assert.equal(h.$('model-select').value,'provider/model');
});
test('stale session, internal task, and steer state cannot apply an old draft',async()=>{
 const h=bindingsHarness(),original=h.bindings.read('chat-model'),choice={backend:'claude',model:'default',effort:'none'};h.chat.id='another';await assert.rejects(h.bindings.commit('chat-model',choice,original),/状态已改变/);
 h.chat.id='session';h.chat.events.set('event',{kind:'session/internal'});assert.equal(h.bindings.canEdit('chat-model'),true);assert.equal(h.bindings.canSwitchBackend('chat-model'),false);await assert.rejects(h.bindings.commit('chat-model',choice,original),/冻结运行时/);await h.bindings.commit('chat-model',{backend:'codex',model:'old',effort:'low'},original);h.chat.events.clear();h.chat.session.status='running';h.$('chat-mode').value='steer';assert.equal(h.bindings.canEdit('chat-model'),false);
 assert.equal(h.posts.length,0);assert.equal(h.$('chat-model').value,'old');
});


test('same-engine model races and late close events cannot damage a reopened chooser',async()=>{
 const requests=[];const h=harness({api:()=>new Promise(resolve=>requests.push(resolve))});
 const opened=h.picker.open('chat-model');await tick();h.custom('second');h.custom('third');
 requests[2]({options:[{id:'low'}]});await tick();requests[1]({options:[{id:'obsolete'}]});requests[0]({options:[]});await opened;
 assert.equal(h.field('effort').options.some(o=>o.value==='obsolete'),false);assert.match(h.field('selected').textContent,/third/);
 h.picker.close();const reopened=h.picker.open('chat-model');await tick();h.$('runtime-model-picker').onclose();requests[3]({options:[{id:'high'}]});await reopened;
 assert.equal(h.$('runtime-model-picker').open,true);assert.match(h.field('selected').textContent,/old/);h.picker.close();
});
