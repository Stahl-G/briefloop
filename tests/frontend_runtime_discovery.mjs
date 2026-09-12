// Runtime discovery is not a successful authentication or inference test.
import fs from 'node:fs';
import vm from 'node:vm';
import assert from 'node:assert/strict';
const source=fs.readFileSync(new URL('../frontend/app.js',import.meta.url),'utf8');
const code=source.slice(source.indexOf('function renderRuntimeDiscovery()'),source.indexOf("$('agent-backend').onchange="));
const nodes=new Map(),calls=[];
const node=id=>{if(!nodes.has(id))nodes.set(id,{value:'',innerHTML:'',textContent:'',hidden:true,options:[],querySelectorAll:()=>[],append:()=>{},replaceChildren(){this.options=[]},add(option){this.options.push(option)}});return nodes.get(id)};
const runtimes=[
 {id:'hermes',name:'Hermes',installed:true,available:true,version:null,diagnostic:'Version probe failed'},
 {id:'kilo',name:'Kilo',installed:true,available:false,version:'1.0',diagnostic:'本机 CLI 已找到；执行协议尚未接入'},
 {id:'codex',name:'Codex',installed:false,available:false,version:null},
];
node('agent-backend').value='hermes';
const view=vm.createContext({$:node,esc:String,runtimeCatalog:[],runtimeScanned:false,
 state:{settings:{agent_backend:'hermes',model:'default'}},
 Option:class{constructor(text,value){this.text=text;this.value=value}},
 api:async route=>{calls.push(route);return {runtimes}},
 refreshModelSuggestions:async()=>{},renderSettingsSessionNote:()=>{}});
vm.runInContext(code,view);
await view.refreshRuntimeDiscovery();
assert.deepEqual(calls,['runtimes']);
assert.equal(node('runtime-discovery-status').textContent,'检测到 2 个本机 CLI，其中 1 个可选择；检测未验证账号与模型调用，需另行短测试。');
assert.doesNotMatch(node('runtime-discovery-status').textContent,/已接入|调用通过/);
const cards=node('runtime-discovery-details').innerHTML;
assert.match(cards,/版本未确认 · 已检测到，可选择/);
assert.match(cards,/仅发现，尚未支持/);
assert.match(cards,/当前选择/);
assert.doesNotMatch(cards,/当前使用/);
assert.match(cards,/Version probe failed/);
assert.equal(node('agent-backend').options.find(option=>option.value==='hermes').disabled,false);
assert.equal(node('agent-backend').options.find(option=>option.value==='kilo').disabled,true);
assert.equal(node('agent-backend').options.find(option=>option.value==='codex').disabled,true);
runtimes[0].diagnostic=null;view.renderRuntimeDiscovery();
assert.match(node('runtime-discovery-details').innerHTML,/可选择不代表调用通过/);
console.log('PASS: detected, selectable and unintegrated runtimes remain distinct; no inference is claimed by discovery');
