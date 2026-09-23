import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';

const source=fs.readFileSync(new URL('../frontend/app.js',import.meta.url),'utf8').replace(/\r\n/g,'\n');
const between=(start,end)=>source.slice(source.indexOf(start),source.indexOf(end));
const code=[
 between('function chatBackendChoice(){','function renderChatBackendChoice(){'),
 between('function rememberDraft(){','const PERMISSION_MODES='),
 between('async function selectChat(id){','async function pollChat('),
 between('async function sendChat(event){',"$('chat-form').onsubmit="),
].join('\n');

function fixture({sendFails=false}={}){
 const nodes=new Map(),stored=new Map(),calls=[];
 const $=id=>{if(!nodes.has(id))nodes.set(id,{value:'',checked:false,focus(){}});return nodes.get(id)};
 const storage={setItem:(key,value)=>stored.set(key,value),removeItem:key=>stored.delete(key)};
 const chat={id:null,home:true,view:'active',sessions:[],session:null,messages:[],requests:[],events:new Map(),after:0,busy:false,uploading:0,drafts:new Map(),attachments:new Set(),request:null};
 const state={settings:{agent_backend:'briefloop-native',model:'default/model',model_variant:'default-effort',chat_allow_web:true}};
 const context=vm.createContext({$,chat,state,sessionStorage:storage,localStorage:storage,
  settingsEffort:()=> 'medium',effortValue:(value,key)=>value[key],assignEffort:(id,value)=>{$(id).value=value},
  renderAttachments(){},updateComposer(){},autoSizeChatInput(){},refreshInlineModelPickers(){},renderMessages:{},chatError(){},renderChat(){},page(){},pollChat:async()=>{},
  chatActive:()=>false,compactReportInstruction:()=>'',crypto:{randomUUID:()=> 'message-id'},
  runtimeChoice:()=>({backend:chat.nextBackend,model:$('chat-model').value,permission:$('chat-permission').value}),
  api:async(route,payload)=>{calls.push({route,payload});if(route==='harness/session')return {id:'created-session',runtime:payload.runtime};if(sendFails)throw Error('send failed');return {}},
 });
 vm.runInContext(code,context);
 const setDraft=(text,attachment)=>{
  $('chat-input').value=text;chat.attachments=new Set([attachment]);chat.nextBackend='codex';
  $('chat-model').value='chosen-model';$('chat-model-provider').value='chosen-provider';$('chat-effort').value='high';$('chat-variant').value='';$('chat-service-tier').value='fast';
  $('chat-allow-web').checked=false;$('chat-permission').value='read-only';chat.hostOptions={mode:'plan'};
 };
 const snapshot=()=>({text:$('chat-input').value,sources:[...chat.attachments],backend:chat.nextBackend,model:$('chat-model').value,provider:$('chat-model-provider').value,effort:$('chat-effort').value,serviceTier:$('chat-service-tier').value,allowWeb:$('chat-allow-web').checked,permission:$('chat-permission').value,hostMode:chat.hostOptions.mode});
 return {context,$,chat,state,stored,calls,setDraft,snapshot};
}

test('returning home preserves text, attachments and the draft runtime instead of applying workspace defaults',async()=>{
 const f=fixture();f.setDraft('待完成的首页需求','home-source');const expected=f.snapshot();
 await f.context.showHome();
 assert.equal(f.chat.id,null);assert.equal(f.chat.home,true);
 assert.deepEqual(f.snapshot(),expected);
 assert.equal(new Map(JSON.parse(f.stored.get('briefloop-chat-drafts'))).get('new').text,expected.text);
 assert.equal(f.calls.length,0,'home navigation does not create a session or send a message');
});

test('home and existing conversation drafts stay separate across navigation',async()=>{
 const f=fixture();f.setDraft('首页待发需求','home-source');const home=f.snapshot();
 f.chat.sessions=[{id:'existing',runtime:{backend:'briefloop-native',model:'session/model'}}];
 await f.context.selectChat('existing');
 f.setDraft('已有会话的下一条消息','session-source');const session=f.snapshot();
 await f.context.showHome();assert.deepEqual(f.snapshot(),home);
 assert.equal(f.chat.drafts.get('existing').text,session.text);
 await f.context.selectChat('existing');assert.deepEqual(f.snapshot(),session);
});

test('explicit new chat clears the home draft durably and uses the workspace defaults',async()=>{
 const f=fixture();f.setDraft('明确新建时可清空的草稿','home-source');
 await f.context.newChat();
 assert.equal(f.$('chat-input').value,'');assert.deepEqual([...f.chat.attachments],[]);
 assert.equal(f.chat.nextBackend,'briefloop-native');assert.equal(f.$('chat-model').value,'default/model');
 const persisted=new Map(JSON.parse(f.stored.get('briefloop-chat-drafts'))).get('new');
 assert.equal(persisted.text,'');assert.deepEqual(persisted.sources,[]);
 await f.context.showHome();assert.equal(f.$('chat-input').value,'','returning home must not revive the cleared draft');
});

test('a first send moves the home draft to its session, preserving failed sends without reviving sent content at home',async()=>{
 for(const sendFails of [false,true]){
  const f=fixture({sendFails});f.setDraft('第一次发送','first-source');f.context.rememberDraft();
  await f.context.sendChat({preventDefault(){}});
  assert.equal(f.chat.id,'created-session');assert.equal(f.chat.drafts.has('new'),false);
  assert.equal(f.chat.drafts.get('created-session').text,sendFails?'第一次发送':'');
  await f.context.showHome();assert.equal(f.$('chat-input').value,'');assert.deepEqual([...f.chat.attachments],[]);
  await f.context.selectChat('created-session');
  assert.equal(f.$('chat-input').value,sendFails?'第一次发送':'');
  assert.deepEqual([...f.chat.attachments],sendFails?['first-source']:[]);
 }
});
