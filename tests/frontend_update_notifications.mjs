import test from 'node:test';
import assert from 'node:assert/strict';
import {activityCenter} from '../frontend/notifications.js';

test('rendering the activity center never makes an update network request',async()=>{
 const originalDocument=globalThis.document,originalWindow=globalThis.window;
 const elements=new Map(),calls=[];
 const element=id=>{
  if(!elements.has(id))elements.set(id,{id,innerHTML:'',hidden:false,disabled:false,
   querySelector:()=>({textContent:''}),querySelectorAll:()=>[],setAttribute(){},
   classList:{toggle(){}},append(){},close(){},showModal(){}});
  return elements.get(id);
 };
 let updateEvent;
 globalThis.document={getElementById:element,querySelector:()=>null,createElement:()=>element('dot')};
 globalThis.window={briefloopDesktop:{updateStatus:()=>{calls.push('status');},checkForUpdates:()=>{calls.push('check');},
  onUpdateStatus:callback=>{updateEvent=callback;}}};
 const state={workspace_id:'fixture',notifications:{items:[],counts:{},unread:0,through:0}};
 try{
  const ui=activityCenter({api:async(endpoint,body)=>{calls.push(endpoint);if(endpoint==='notifications/version')return state.notifications;throw Error(endpoint);},
   getState:()=>state,page(){},openBrief(){},showSettings(){},settingsView(){}});
  ui.render();ui.render();
  assert.deepEqual(calls,[]);
  updateEvent({state:'available',currentAppVersion:'0.25.0',releaseVersion:'0.25.1'});
  await new Promise(resolve=>setImmediate(resolve));
  assert.deepEqual(calls,['notifications/version'],'an explicit updater result still creates the unread notice');
 }finally{globalThis.document=originalDocument;globalThis.window=originalWindow;}
});

test('permission activity opens the exact task conversation without granting permission',async()=>{
 const originalDocument=globalThis.document,originalWindow=globalThis.window;
 const elements=new Map(),calls=[],button={dataset:{activityOpen:'7'}};
 const element=id=>{
  if(!elements.has(id))elements.set(id,{innerHTML:'',querySelector:()=>({}),
   querySelectorAll:()=>id==='notifications-list'?[button]:[],setAttribute(){},classList:{toggle(){}},close(){calls.push('close')},showModal(){}});
  return elements.get(id);
 };
 globalThis.document={getElementById:element,querySelector:()=>null};globalThis.window={};
 const item={seq:7,category:'reports',title:'生成简报 等待授权',target:{job_id:'job-1',session_id:'task-chat',request_id:'permission-1'}};
 const state={notifications:{items:[item],counts:{reports:1},unread:1,through:7}};
 try{
  const ui=activityCenter({getState:()=>state,selectChat:async id=>calls.push(id),
   api:async(endpoint,body)=>{calls.push({endpoint,body});return state.notifications;},
   page(){throw Error('Must open the task conversation directly')}});
  ui.render();await button.onclick();
  assert.deepEqual(calls,['task-chat','close',{endpoint:'notifications/read',body:{through:7,seq:7}}]);
 }finally{globalThis.document=originalDocument;globalThis.window=originalWindow;}
});
