import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';
import {permissionChoices,permissionSelection,createPermissionDirectory} from '../frontend/runtime-permission-modes.js';
import {permissionModeMarkup,createRuntimePermissionPanel} from '../frontend/runtime-permission-panel.js';
import {section} from './source_section.mjs';

const catalog={backend:'claude',kind:'host',source:{kind:'runtime',label:'来自本机 Claude Code'},default_mode:'auto',modes:[
 {id:'default',name:'default',description:'Standard behavior & permissions'},
 {id:'auto',name:'auto'},
 {id:'bypassPermissions',name:'bypassPermissions',disabled:true,disabled_reason:'该接入不支持此模式'}
],inherit_mode:{id:'native',name:'沿用运行端设置',description:'BriefLoop 不覆盖运行端的权限模式。'}};

test('permission rows preserve runtime names, descriptions, order and adapter inheritance without guessed defaults',()=>{
 const html=permissionModeMarkup(catalog,'auto');
 assert.ok(html.indexOf('>default<')<html.indexOf('>auto<'));
 assert.ok(html.indexOf('>auto<')<html.indexOf('>bypassPermissions<'));
 assert.match(html,/Standard behavior &amp; permissions/);
 assert.doesNotMatch(html,/自动处理|操作前询问|只做计划|更多模式/);
 assert.match(html,/value="auto"[^>]*checked[^>]*><span><strong>auto<\/strong><\/span>/);
 assert.match(html,/value="bypassPermissions"[^>]*disabled/);
 assert.match(html,/runtime-permission-inherit.*value="native"/);
 assert.equal(permissionSelection(catalog,'native').unavailable,undefined);
 assert.equal(permissionSelection(catalog,'bypassPermissions').unavailable,true);
 assert.equal(permissionSelection(catalog,'removed').selected,'removed');
 assert.equal(permissionSelection({...catalog,default_mode:'missing'}).selected,'');
 assert.deepEqual(permissionChoices({modes:[],inherit_mode:catalog.inherit_mode}),[catalog.inherit_mode]);
});

test('catalog loading is model-aware and late results cannot replace the current model or invent a failed catalog',async()=>{
 let model='old';const waiting=new Map(),published=[];
 const directory=createPermissionDirectory({getModel:()=>model,api:route=>new Promise((resolve,reject)=>waiting.set(new URL('http://local/'+route).searchParams.get('model'),{resolve,reject})),onChange:(_,value)=>published.push(value)});
 const first=directory.load('claude');await Promise.resolve();
 model='new';assert.equal(directory.get('claude'),undefined);
 const second=directory.load('claude');await Promise.resolve();
 const current={...catalog,modes:[{id:'default',name:'default'}]};waiting.get('new').resolve(current);await second;
 waiting.get('old').resolve(catalog);await first;
 assert.equal(directory.get('claude'),current);assert.deepEqual(published,[current]);
 const reload=directory.load('claude',{refresh:true});await Promise.resolve();waiting.get('new').reject(Error('not available'));await assert.rejects(reload,/not available/);
 assert.equal(directory.get('claude').source.kind,'unavailable');assert.deepEqual(directory.get('claude').modes,[]);
 assert.equal(permissionSelection(directory.get('claude'),'auto').unavailable,true);
});

function fixture(initial=catalog){
 const nodes=new Map(),selected=[],posts=[];let stored='auto',locked=false,current=initial;
 const status={textContent:''};
 const box={radios:[],html:'',textContent:'',setAttribute(){},
  set innerHTML(value){this.html=value;this.radios=[...value.matchAll(/<input\b([^>]+data-permission-mode="(\d+)"[^>]*)>/g)].map(([,attrs,index])=>({dataset:{permissionMode:index},checked:/\bchecked\b/.test(attrs),disabled:/\bdisabled\b/.test(attrs)}))},get innerHTML(){return this.html},
  querySelectorAll(selector){return selector==='[data-permission-mode]'||selector==='button,input,select'?this.radios:[]},
  querySelector(selector){return selector==='.runtime-permission-selection-status'?status:null}};
 const $=id=>{if(!nodes.has(id))nodes.set(id,id==='chat-permissions-options'?box:{textContent:'',hidden:false,disabled:false,setAttribute(){}});return nodes.get(id)};
 const ui=createRuntimePermissionPanel({$,api:async(route,body)=>posts.push({route,body}),directory:{load:async()=>current},getBackend:()=>initial.backend,getModel:()=> 'sonnet',runtimeName:()=> 'Claude Code',getSelection:()=>stored,onSelect:mode=>{selected.push(mode);stored=mode},isLocked:()=>locked});
 return {ui,$,box,status,selected,posts,setStored:value=>stored=value,setCatalog:value=>current=value,setLocked:value=>locked=value};
}

test('opening and refresh are read-only; explicit available selection changes only the chosen mode',async()=>{
 const f=fixture();await f.ui.load();assert.deepEqual(f.selected,[]);assert.deepEqual(f.posts,[]);
 assert.equal(f.$('chat-permissions-note').textContent,catalog.source.label);
 assert.equal(f.$('chat-permissions-native').hidden,true);
 assert.equal(f.box.radios[1].checked,true);
 f.box.radios[0].checked=true;f.box.radios[0].onchange();assert.deepEqual(f.selected,['default']);
 assert.match(f.status.textContent,/下一回合/);
 f.setStored('removed');await f.ui.load();assert.match(f.$('chat-permissions-error').textContent,/removed.*重新选择/);
 assert.ok(f.box.radios.every(input=>!input.checked));assert.deepEqual(f.selected,['default']);
 f.box.radios[2].checked=true;f.box.radios[2].onchange();assert.deepEqual(f.selected,['default'],'disabled runtime entries cannot be selected');
 f.setLocked(true);f.box.radios[1].checked=true;f.box.radios[1].onchange();assert.deepEqual(f.selected,['default']);
});

test('Antigravity global rule edits retain explicit scope and revision while handcrafted presets stay absent',async()=>{
 const f=fixture({backend:'antigravity',kind:'host',modes:[{id:'plan',name:'plan'}],source:{kind:'runtime',label:'Antigravity'},rules:[{decision:'allow',rule:'read_file(/workspace/file)'}],revision:'revision-1'});
 await f.ui.load();assert.match(f.box.html,/也会影响其他会话/);assert.match(f.box.html,/allow · read_file/);assert.doesNotMatch(f.box.html,/full-machine|turbo|antigravity-preset/);assert.deepEqual(f.posts,[]);
 await f.ui.saveRule({operation:'remove',decision:'allow',rule:'read_file(/workspace/file)'});
 assert.deepEqual(f.posts,[{route:'runtime/permissions',body:{backend:'antigravity',revision:'revision-1',operation:'remove',decision:'allow',rule:'read_file(/workspace/file)'}}]);
});

test('send validation accepts inheritance and blocks missing or disabled saved choices without changing them',()=>{
 const source=fs.readFileSync(new URL('../frontend/app.js',import.meta.url),'utf8');
 const values={'chat-model':{value:'sonnet'},'chat-effort':{value:'none'}},chat={hostOptions:{mode:'native'}};
 const context=vm.createContext({$:id=>values[id],chat,chatBackendChoice:()=> 'claude',permissionDirectory:{get:()=>catalog},permissionSelection,reasoningModel:(_backend,model)=>model});
 vm.runInContext(section(source,'function runtimeChoice(){','function messageTime(','frontend/app.js'),context);
 assert.equal(context.runtimeChoice().host_options.mode,'native');
 for(const invalid of ['removed','bypassPermissions']){chat.hostOptions.mode=invalid;assert.throws(()=>context.runtimeChoice(),/重新选择/);assert.equal(chat.hostOptions.mode,invalid)}
});

test('switching from a host runtime clears its scope marker and resolves only the destination catalog default',()=>{
 const source=fs.readFileSync(new URL('../frontend/app.js',import.meta.url),'utf8'),nodes=new Map();
 const $=id=>{if(!nodes.has(id))nodes.set(id,{value:''});return nodes.get(id)};
 const chat={nextBackend:'kimi',messages:[],hostOptions:{mode:'auto'}},state={settings:{}};
 let current={backend:'briefloop-native',kind:'native',default_mode:'read-only',modes:[{id:'read-only',name:'read-only'},{id:'workspace-write',name:'workspace-write'}]};
 const context=vm.createContext({$,chat,state,permissionSelection,permissionDirectory:{get:()=>current},
  reasoning:{refresh(){}},assignEffort(){},effortValue:()=> 'none',settingsEffort:()=> 'none',
  renderChatRuntimePermissions(){},rememberDraft(){},updateComposer(){},refreshInlineModelPickers(){}});
 vm.runInContext([
  section(source,'function chatBackendChoice(){','function renderChatBackendChoice(){','frontend/app.js'),
  section(source,"$('chat-backend').onchange=()=>{",'function rememberDraft(){','frontend/app.js'),
  section(source,'function setChatPermission(','function renderChatRuntimePermissions(){','frontend/app.js'),
  section(source,'function runtimeChoice(){','function messageTime(','frontend/app.js')
 ].join('\n'),context);
 $('chat-permission').value='runtime-native';$('chat-backend').value='briefloop-native';$('chat-backend').onchange();
 assert.equal($('chat-permission').value,'','the previous host marker is not a destination choice');
 $('chat-model').value='provider/model';
 assert.equal(context.runtimeChoice().permission,'read-only','uses the verified destination default, not a hardcoded scope');
 assert.equal($('chat-permission').value,'','resolving the effective default does not alter the stored selection');
 assert.match(permissionModeMarkup(current,$('chat-permission').value),/value="read-only"[^>]*checked/);
 $('chat-permission').value='workspace-write';assert.equal(context.runtimeChoice().permission,'workspace-write');
 current={...current,modes:[{id:'read-only',name:'read-only'}]};assert.throws(()=>context.runtimeChoice(),/重新选择/);
 assert.equal($('chat-permission').value,'workspace-write','unavailable explicit selections must remain visible for recovery');
 chat.messages=[{role:'user',runtime:{backend:'briefloop-native',permission:'read-only',model:'provider/previous'}}];
 $('chat-backend').onchange();assert.equal($('chat-permission').value,'read-only','a previous destination choice is restored');
 $('chat-permission').value='';current={...current,default_mode:'missing'};assert.throws(()=>context.runtimeChoice(),/默认权限/);
});
