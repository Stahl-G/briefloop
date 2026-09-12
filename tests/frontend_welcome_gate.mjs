import fs from 'node:fs';
import vm from 'node:vm';
import assert from 'node:assert/strict';
const source=fs.readFileSync('frontend/app.js','utf8');
const elements=new Map();const el=id=>{if(!elements.has(id))elements.set(id,{hidden:id!=='welcome'});return elements.get(id)};
el('welcome-runtimes');el('welcome-choice');el('welcome-start');el('welcome-purposes');el('welcome');
el('welcome-runtimes').querySelectorAll=()=>[];el('welcome-purposes').querySelectorAll=()=>[];

// Behavior, not string presence: a fresh workspace ships the factory model
// (gpt-5.6-luna) but must NOT enable 开始 until the user actually picks one.
const welcomeCode=source.slice(source.indexOf('let welcomeIndex=0'),source.indexOf("$('welcome-model').onclick"));
const c=vm.createContext({
 $:el,esc:String,runtimeName:id=>id,friendlyModel:m=>m,
 state:{settings:{agent_backend:'codex',model:'gpt-5.6-luna',model_selection_required:true}},
 runtimeCatalog:[{id:'codex',name:'Codex CLI',available:true}],runtimeScanned:true,
 WELCOME_PURPOSES:[{id:'public',label:'公开研究',prompt:'写一份有依据的简报'}],
 openModelPicker:()=>{},Event:class{constructor(t){this.type=t}},notice:()=>{},
 sessionStorage:{setItem(){},getItem(){return null}},
});
vm.runInContext(welcomeCode,c);
vm.runInContext('renderWelcome()',c);
assert.equal(el('welcome-start').disabled,true,'a fresh workspace must not treat the factory default as a choice');
vm.runInContext('state.settings.model_selection_required=false',c);
vm.runInContext('renderWelcome()',c);
assert.equal(el('welcome-start').disabled,false,'a saved model selection enables 开始');
console.log('PASS: the welcome gate requires an explicit model choice, not the factory default');

// Clicking the sidebar cannot bypass the first-run page.
const pageCode=source.slice(source.indexOf('function page(name){'),source.indexOf("document.querySelectorAll('[data-page]')"));
const notices=[];
const p=vm.createContext({
 $:el,notice:(s)=>notices.push(s),document:{querySelectorAll:()=>[],body:{classList:{contains:()=>false,remove:()=>{}}}},
 refreshCandidates:()=>{},moveSearchSettings:()=>{},applyPendingSearchInline:()=>{},applyPendingSetupFields:()=>{},
});
el('welcome').hidden=false;
vm.runInContext(pageCode,p);vm.runInContext("page('chat')",p);
assert.equal(el('welcome').hidden,false,'the first-run page stays visible when the sidebar tries to leave');
assert.ok(notices.length>=1,'leaving the first-run page explains why it stays');
vm.runInContext("page('settings-dialog')",p);
assert.equal(el('settings-dialog').hidden,false,'settings is still reachable during first run');
console.log('PASS: the sidebar cannot bypass the first-run page, but settings stays reachable');
