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

// The permission control follows what the runtime advertises, and a single mode is
// not presented as a dropdown the user could choose from.
const permissionCode=source.slice(source.indexOf('const PERMISSION_MODES='),source.indexOf('function messageTime('));
const select={value:'',hidden:false,title:'',replaceChildren(...options){this.options=options}};
const mode={value:'queue',options:[{value:'queue'},{value:'steer',hidden:false,disabled:false}]};
const p=vm.createContext({$:id=>id==='chat-permission'?select:id==='chat-mode'?mode:{hidden:false},
 chat:{session:{runtime:{backend:'claude'}}},state:{settings:{agent_backend:'claude'}},runtimeCatalog:[],
 document:{querySelector:()=>({hidden:false})},JSON,console,
 Option:class{constructor(text,value){this.text=text;this.value=value}}});
vm.runInContext(permissionCode,p);
vm.runInContext('renderChatRuntimePermissions()',p);
assert.equal(select.options.length,1);assert.equal(select.hidden,true,'a single permission mode is not a choice');
assert.equal(select.value,'runtime-native');
assert.equal(mode.options[1].hidden,true,'a host without in-flight steering must not offer it');
p.chat.session.runtime.backend='codex';p.state.settings.agent_backend='codex';
p.runtimeCatalog=[{id:'codex',capabilities:{permission_modes:['workspace-write','read-only'],steer:true}}];
vm.runInContext('renderChatRuntimePermissions()',p);
assert.equal(select.hidden,false);assert.deepEqual(select.options.map(option=>option.text),['读写工作区','只读']);
assert.match(select.title,/只读取和解释资料/);
assert.equal(mode.options[1].hidden,false);
// Before discovery finishes, a CLI host must not be offered codex-style sandboxes.
p.chat.session.runtime.backend='kimi';p.runtimeCatalog=[];p.chat.session.runtime.permission='runtime-native';
vm.runInContext('renderChatRuntimePermissions()',p);
assert.equal(select.options.length,1);assert.equal(select.hidden,true);assert.equal(select.value,'runtime-native');
console.log('PASS: chat permission options come from the runtime and hide when there is no choice');
