import fs from 'node:fs';
import vm from 'node:vm';
import assert from 'node:assert/strict';
const source=fs.readFileSync('frontend/app.js','utf8');
const code=source.slice(source.indexOf('function restoreDraft(){'),source.indexOf('function renderChatRuntimePermissions()'));
const elements=new Map();const el=id=>{if(!elements.has(id))elements.set(id,{});return elements.get(id)};
const c=vm.createContext({$:el,chat:{id:'existing',drafts:new Map(),session:{runtime:{backend:'claude',model:'default'}}},state:{settings:{agent_backend:'claude',model:'default',model_selection_required:true}},effortValue:()=>null,assignEffort:()=>{},refreshInlineModelPickers:()=>{},renderAttachments:()=>{},updateComposer:()=>{},autoSizeChatInput:()=>{}});
vm.runInContext(code,c);vm.runInContext('restoreDraft()',c);assert.equal(el('chat-model').value,'default');
c.chat.id=null;c.chat.session=null;c.chat.drafts.set('new',{text:'unsent message',backend:'claude',model:'default'});
vm.runInContext('restoreDraft()',c);assert.equal(el('chat-model').value,'default');assert.equal(el('chat-input').value,'unsent message');
c.chat.drafts.set('new',{text:'keep this text',backend:'codex',model:'old-codex-model'});
vm.runInContext('restoreDraft()',c);assert.equal(el('chat-model').value,'');assert.equal(el('chat-input').value,'keep this text');
console.log('PASS: explicit chat choice survives pending workspace selection; model does not cross runtime boundaries');

// Failed first send binds the unsent draft to the newly created session.
c.chat={id:null,session:null,busy:false,uploading:0,attachments:new Set(),drafts:new Map(),request:null};
c.localStorage={setItem(){}};c.sessionStorage={setItem(){}};c.crypto={randomUUID:()=> 'fixed-request'};
c.runtimeChoice=()=>({backend:'claude',model:'default',permission:'runtime-native'});
c.api=async route=>{if(route==='harness/session')return {id:'new-session',runtime:{backend:'claude',model:'default'}};throw Error('send failed')};
c.chatActive=()=>false;c.chatError=()=>{};c.pollChat=async()=>{};
el('chat-input').value='你是谁';el('chat-input').focus=()=>{};el('chat-model').value='default';el('chat-model-provider').value='';
vm.runInContext(source.slice(source.indexOf('function rememberDraft(){'),source.indexOf('function restoreDraft(){')),c);
vm.runInContext(source.slice(source.indexOf('async function sendChat(event){'),source.indexOf("$('chat-form').onsubmit=")),c);
await vm.runInContext('sendChat({preventDefault(){}})',c);
assert.equal(c.chat.drafts.get('new-session').text,'你是谁');
assert.equal(c.chat.drafts.get('new-session').model,'default');
vm.runInContext('restoreDraft()',c);assert.equal(el('chat-input').value,'你是谁');
console.log('PASS: failed initial send preserves text and model on the created session');
