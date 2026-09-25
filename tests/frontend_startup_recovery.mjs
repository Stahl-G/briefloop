import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';
import {section as sectionOf} from './source_section.mjs';

const source=fs.readFileSync(new URL('../frontend/app.js',import.meta.url),'utf8').replace(/\r\n/g,'\n');
const section=(start,end)=>sectionOf(source,start,end,'frontend/app.js');
const line=name=>source.split('\n').find(row=>row.startsWith(`function ${name}(`)||row.startsWith(`async function ${name}(`));
const flush=async()=>{for(let i=0;i<30;i++)await Promise.resolve()};

function fixture(read){
 const nodes=new Map(),timers=new Map(),listeners=new Map(),calls=[],renders=[],pages=[],polls=[];let sequence=0;
 const element=()=>({value:'',checked:false,hidden:true,dataset:{},children:[],setAttribute(){},append(...children){this.children.push(...children)},prepend(node){this.children.unshift(node)}});
 const $=id=>{if(!nodes.has(id))nodes.set(id,element());return nodes.get(id)};
 const snapshot={settings:{agent_backend:'briefloop-native',model:'default/model',chat_allow_web:true},sources:[],runs:[{id:'existing-run'}],briefs:[],jobs:[]};
 const chat={id:null,session:null,home:true,sessions:[],messages:[],requests:[],events:new Map(),after:0,drafts:new Map(),attachments:new Set()};
 const storage=new Map([['briefloop-chat-drafts',JSON.stringify([['new',{text:'stored old text',backend:'briefloop-native',model:'default/model'}]])]]);
 const context=vm.createContext({$,chat,state:undefined,AbortController,
  document:{createElement:element,querySelector:()=> $('main')},window:{addEventListener:(name,fn)=>{if(!listeners.has(name))listeners.set(name,[]);listeners.get(name).push(fn)}},
  setTimeout:(fn,ms)=>{const id=++sequence;timers.set(id,{fn,ms});return id},clearTimeout:id=>timers.delete(id),
  sessionStorage:{getItem:key=>storage.get(key),setItem:(key,value)=>storage.set(key,value)},localStorage:{getItem:()=>null,removeItem(){}},
  api:async route=>{calls.push(route);return read?read(route,calls,snapshot):route==='session'?{token:'test-token'}:route==='state'?snapshot:{sessions:[]}},
  setToken(){},scheduledReports:{render(){}},activity:null,renderWordExports(){},render:first=>renders.push(first),refreshProgress:async()=>{},refreshCandidates:async()=>{},refreshReportBudget:async()=>{},refreshWorkspaces:async()=>{},
  notice(){},delivery:{refreshReleaseState:async()=>{}},backgroundActive:()=>false,pollChat:async()=>{},renderMessages:{},renderChat(){},renderSessions(){},renderWelcome(){},openBrief(){},page:name=>pages.push(name),
  settingsEffort:()=> 'medium',effortValue:(value,key)=>value[key],assignEffort:(id,value)=>{$(id).value=value},
  renderAttachments(){},updateComposer(){},autoSizeChatInput(){},refreshInlineModelPickers(){},
  adaptivePoll:(task,options)=>{const poll={task,fast:options.fast,stopped:false};polls.push(poll);return ()=>{poll.stopped=true}},
 });
 vm.runInContext([
  line('refresh'),line('refreshState'),line('chatBackendChoice'),
  section('function rememberDraft(){','const PERMISSION_MODES='),
  section('async function initChat(',"$('chat-allow-web').onchange="),
  section('// BEGIN_WORKSPACE_STARTUP','// END_WORKSPACE_STARTUP'),
 ].join('\n'),context);
 const startup=vm.runInContext('startup',context);
 const nextTimer=async()=>{const [id,timer]=timers.entries().next().value;timers.delete(id);timer.fn();await flush();return timer.ms};
 const input=(text='new input during retry')=>{
  $('chat-input').value=text;chat.nextBackend='codex';$('chat-model').value='chosen-model';$('chat-model-provider').value='chosen-provider';$('chat-effort').value='high';$('chat-permission').value='read-only';
  chat.attachments=new Set(['saved-source']);context.rememberDraft();
 };
 return {context,$,chat,startup,timers,listeners,calls,renders,pages,polls,nextTimer,input,fire:name=>listeners.get(name).forEach(fn=>fn())};
}

test('session and first state failures recover with one initialization and one set of polls',async()=>{
 const f=fixture((route,calls,snapshot)=>{
  if(['session','state'].includes(route)&&calls.filter(value=>value===route).length===1)throw Error('temporarily unavailable');
  return route==='session'?{token:'test-token'}:route==='state'?snapshot:{sessions:[]};
 });
 await flush();assert.equal(f.timers.size,1);f.input();
 const pending=f.context.startWorkspace();assert.equal(pending,f.startup.running);f.fire('online');
 assert.equal(await f.nextTimer(),1000);assert.equal(await f.nextTimer(),2000);await pending;
 assert.equal(f.startup.ready,true);assert.deepEqual(f.renders,[true]);assert.deepEqual(f.pages,['chat']);
 assert.equal(f.$('chat-input').value,'new input during retry');assert.equal(f.$('chat-model').value,'chosen-model');assert.equal(f.$('chat-permission').value,'read-only');assert.deepEqual([...f.chat.attachments],['saved-source']);
 assert.deepEqual(f.polls.map(p=>p.fast),[3000,1300]);assert.equal(f.timers.size,0);
 f.fire('online');f.fire('pageshow');await flush();assert.equal(f.polls.length,2);
 assert.deepEqual([...f.listeners].map(([name,items])=>[name,items.length]),[['online',1],['pagehide',1],['pageshow',1]]);
 f.fire('pagehide');assert.equal(f.polls.filter(p=>!p.stopped).length,0);
 f.fire('online');await flush();assert.equal(f.polls.filter(p=>!p.stopped).length,0);
 f.fire('pageshow');await flush();assert.equal(f.polls.filter(p=>!p.stopped).length,2);assert.deepEqual(f.renders,[true]);
});

test('a later chat-initialization failure retries without reapplying state fields or replacing live drafts',async()=>{
 const f=fixture((route,calls,snapshot)=>{
  if(route==='harness/sessions?view=active'&&calls.filter(value=>value===route).length===1)throw Error('sessions unavailable');
  return route==='session'?{token:'test-token'}:route==='state'?snapshot:{sessions:[]};
 });
 await flush();assert.deepEqual(f.renders,[true]);f.input('typed after state arrived');
 await f.nextTimer();
 assert.equal(f.startup.ready,true);assert.equal(f.calls.filter(route=>route==='state').length,1);
 assert.deepEqual(f.renders,[true]);assert.deepEqual(f.pages,['chat']);assert.equal(f.$('chat-input').value,'typed after state arrived');
});

test('persistent failure has bounded retries and can resume manually without background busy looping',async()=>{
 let failing=true;
 const f=fixture((route,_calls,snapshot)=>{if(failing)throw Error('offline');return route==='session'?{token:'test-token'}:route==='state'?snapshot:{sessions:[]}});
 await flush();f.startup.panel.pause.onclick();await flush();
 assert.equal(f.timers.size,0);f.fire('online');await flush();assert.equal(f.calls.length,1);
 f.startup.panel.again.onclick();await flush();
 const delays=[];while(f.timers.size)delays.push(await f.nextTimer());
 assert.deepEqual(delays,[1000,2000,5000,10000,30000]);assert.equal(f.calls.length,7);assert.equal(f.polls.length,0);
 assert.match(f.startup.panel.text.textContent,/自动重试已暂停/);assert.equal(f.startup.panel.again.disabled,false);
 failing=false;f.startup.panel.again.onclick();await flush();
 assert.equal(f.startup.ready,true);assert.equal(f.polls.length,2);assert.equal(f.timers.size,0);
});

test('pausing ignores a late state response and online events until the user resumes',async()=>{
 let release,hold=true;
 const f=fixture((route,_calls,snapshot)=>{
  if(route==='state'&&hold)return new Promise(resolve=>{release=()=>resolve(snapshot)});
  return route==='session'?{token:'test-token'}:route==='state'?snapshot:{sessions:[]};
 });
 await flush();f.input('keep after cancellation');f.startup.panel.pause.onclick();f.fire('online');
 assert.equal(f.calls.filter(route=>route==='state').length,1);release();await flush();
 assert.equal(f.startup.ready,false);assert.deepEqual(f.renders,[]);assert.equal(f.polls.length,0);assert.equal(f.timers.size,0);
 assert.equal(f.$('chat-input').value,'keep after cancellation');assert.match(f.startup.panel.text.textContent,/重试已暂停/);
 hold=false;f.startup.panel.again.onclick();await flush();assert.equal(f.startup.ready,true);
 assert.equal(f.$('chat-input').value,'keep after cancellation');assert.equal(f.polls.length,2);
});
