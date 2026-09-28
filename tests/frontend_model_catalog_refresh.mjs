import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';
import {section} from './source_section.mjs';

test('opening or refreshing a model picker queries its selected host each time',async()=>{
 const app=fs.readFileSync(new URL('../frontend/app.js',import.meta.url),'utf8');
 const elements=new Map(),calls=[];
 const $=id=>{
  if(!elements.has(id))elements.set(id,{value:'',innerHTML:'',showModal(){},querySelectorAll:()=>[]});
  return elements.get(id);
 };
 const context=vm.createContext({$,Date,Map,encodeURIComponent,
  backendValue:()=> 'codex',chatBackendChoice:()=> 'claude',
  api:async url=>{calls.push(url);return {models:[{id:'claude-opus-5-5',name:'Opus 5.5'}],source:'host'}},
  friendlyModel:id=>id,runtimeName:id=>id,refreshRuntimeModelSummaries(){},refreshInlineModelPickers(){},esc:String});
 vm.runInContext(section(app,'let modelCatalog={backend:null','$(\'model-picker-close\').onclick='),context);
 await vm.runInContext("openModelPicker('chat-model')",context);
 await vm.runInContext('refreshCurrentModelPicker()',context);
 assert.deepEqual(calls,['models?backend=claude&refresh=1','models?backend=claude&refresh=1']);
 await vm.runInContext("openModelPicker('model-select')",context);
 assert.equal(calls.at(-1),'models?backend=codex&refresh=1');
});
