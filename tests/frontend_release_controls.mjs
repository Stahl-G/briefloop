import {settingsEffort} from '../frontend/reasoning-controls.js';
import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';
import {scheduleUI} from '../frontend/schedules.js';

const app=fs.readFileSync(new URL('../frontend/app.js',import.meta.url),'utf8');
const section=(start,end)=>app.slice(app.indexOf(start),app.indexOf(end));
function elements(){
 const nodes=new Map();
 return id=>{
  if(!nodes.has(id))nodes.set(id,{value:'',hidden:false,innerHTML:'',querySelectorAll:()=>[],
   classList:{values:new Set(),toggle(key,on){if(on)this.values.add(key);else this.values.delete(key)}},
   showModal(){this.open=true}});
  return nodes.get(id);
 };
}

test('an empty schedule list still lets the user create the first schedule',()=>{
 const $=elements(),previous=globalThis.document;
 globalThis.document={getElementById:$};
 try{
  const ui=scheduleUI({api(){throw Error('No requests expected')},getState:()=>({schedules:[],sources:[],requirements:{}}),refresh(){},notice(){},openReport(){}});
  ui.render();
  assert.equal($('home-block-schedule').hidden,false);
  assert.equal($('schedule-list').hidden,true);
  $('schedule-new').onclick();
  assert.equal($('schedule-dialog').open,true);
 }finally{globalThis.document=previous}
});

test('applying settings updates the next-turn effort, and clearing it selects model default',()=>{
 const $=elements();
 for(const [id,value] of Object.entries({'chat-model':'fixture/model','chat-variant':'low','chat-permission':'workspace-write','model-select':'fixture/model','model-variant':'high'}))$(id).value=value;
 const context=vm.createContext({settingsEffort,$,state:{settings:{model_variant:'high'}},chatBackendChoice:()=> 'opencode',backendValue:()=> 'opencode',
  chat:{session:{lifecycle:'active'},events:new Map()},chatActive:()=>false,assignEffort:(id,value)=>$(id).value=value,
  rememberDraft(){},updateComposer(){},renderSettingsSessionNote(){},notice(){}});
 vm.runInContext(section('function runtimeChoice()','\nfunction messageTime('),context);
 vm.runInContext(section("$('model-apply-session').onclick=","$('version-history').onclick="),context);
 assert.equal(vm.runInContext('runtimeChoice().variant',context),'low');
 $('model-apply-session').onclick();
 assert.equal(vm.runInContext('runtimeChoice().variant',context),'high');
 $('chat-variant').value='';
 assert.equal(vm.runInContext('runtimeChoice().variant',context),null);
});

test('task completion clears the rail even while an existing conversation is open',()=>{
 const $=elements(),state={jobs:[{id:'job1',kind:'generate',status:'running',created:'2026-09-21T00:00:00Z'}]};
 const context=vm.createContext({settingsEffort,$,state,chat:{home:false,messages:[{role:'user'}],session:null},chatActive:()=>false,
  taskLabel:()=> '生成简报',bannerTitle:()=> 'Test report',esc:String,dayTime:String,chatStates:{},
  renderMessages(){},renderActivities(){},autoOpenActivity(){},renderRequests(){},renderContext(){},renderSessions(){},renderSessionLifecycle(){},updateComposer(){},modelLabel(){}});
 vm.runInContext(section('function renderHomeTasks()','function homeReportRowHTML('),context);
 vm.runInContext(section('function renderChat()','async function selectChat('),context);
 vm.runInContext('renderChat()',context);
 assert.equal($('home-rail').hidden,false);
 assert.match($('home-rail-job-list').innerHTML,/执行中/);
 state.jobs[0].status='complete';
 vm.runInContext('renderChat()',context);
 assert.equal($('home-rail').hidden,true);
 assert.equal($('home-rail-job-list').innerHTML,'');
 assert.equal($('chat').classList.values.has('has-home-rail'),false);
});


test('welcome applies the selected host together with its model and effort',()=>{
 const $=elements();$('chat-input').focus=()=>{};
 const context=vm.createContext({settingsEffort,$,state:{settings:{agent_backend:'zcode',model:'default',model_variant:'',reasoning_effort:'high'}},
  chat:{nextBackend:'codex',hostOptions:{mode:'plan'}},notice(){},page(){},updateComposer(){},refreshInlineModelPickers(){},rememberDraft(){},
  assignEffort:(id,value)=>$(id).value=value,effortValue:(s,k)=>s[k]});
 vm.runInContext(section("$('welcome-start').onclick=",'\nfunction render(first)'),context);
 $('welcome-start').onclick();
 assert.equal(context.chat.nextBackend,'zcode');
 assert.equal($('chat-model').value,'default');
 assert.equal(Object.keys(context.chat.hostOptions).length,0);
});
