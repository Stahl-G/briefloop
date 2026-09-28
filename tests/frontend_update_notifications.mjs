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
