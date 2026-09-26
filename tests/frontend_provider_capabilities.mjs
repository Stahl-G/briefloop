import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';
import {createProviderCapabilities} from '../frontend/provider-capabilities.js';
import {section} from './source_section.mjs';
const elements=new Map();
const $=id=>{if(!elements.has(id))elements.set(id,{value:'',hidden:false});return elements.get(id)};
const capabilities=createProviderCapabilities({$});
for(const value of [null,true,false]){
  capabilities.load({supports_reasoning:value},'native');
  assert.equal($('custom-reasoning-capability').hidden,false);
  assert.deepEqual(capabilities.read('native'),{supports_reasoning:value});
}
capabilities.load({supports_reasoning:true},'native');
capabilities.reset('native');
assert.deepEqual(capabilities.read('native'),{supports_reasoning:null});
capabilities.load({supports_reasoning:true},'opencode');
assert.equal($('custom-reasoning-capability').hidden,true);
assert.equal($('custom-supports-reasoning').value,'');
assert.deepEqual(capabilities.read('opencode'),{});
capabilities.load({supports_reasoning:'true'},'native');
assert.deepEqual(capabilities.read('native'),{supports_reasoning:null});
console.log('PASS: reasoning capability is model-local tristate and excluded from OpenCode saves');

// Exercise the real open/close/select/save handlers, not only the field helper.
const source=fs.readFileSync('frontend/app.js','utf8');
for(const declared of [true,false]){
  const fields=new Map();
  const el=id=>{
    if(!fields.has(id))fields.set(id,{value:'',hidden:false,dataset:{},addEventListener(){},setAttribute(){},
      dispatchEvent(event){return this['on'+event.type]?.(event)},click(){return this.onclick?.()}});
    return fields.get(id);
  };
  const configs=[
    {provider:'shared',model:'other',name:'Other',supports_reasoning:!declared},
    {provider:'shared',model:'target',name:'Target',supports_reasoning:declared,
      protocol:'chat-completions',base_url:'https://example.invalid/v1',supports_images:false},
  ];
  const bodies=[];const storage=new Map();
  const context=vm.createContext({$:el,Event,esc:value=>value,settingsView(){},settingsModelTab(){},
    welcomeFromProvider:false,providerCapabilities:createProviderCapabilities({$:el}),
    localStorage:{getItem:key=>storage.get(key),setItem:(key,value)=>storage.set(key,value)},
    api:async(route,body)=>{
      if(route==='native/providers')return {configurations:configs};
      if(route==='native/provider-catalog')return {models:['target','other'],status:'reachable'};
      assert.equal(route,'native/provider');bodies.push({...body});return {model:'shared/target'};
    }});
  vm.runInContext(section(source,'let savedProviderConfigurations=[];',"$('timeout-minutes').onchange=",'frontend/app.js'),context);
  el('provider-engine').value='briefloop-native';
  el('custom-provider').value='shared';el('custom-model').value='target';
  await el('provider-open').onclick();
  assert.equal(el('custom-saved').value,'1');
  assert.equal(el('custom-supports-reasoning').value,String(declared));
  el('provider-close').onclick();
  await el('provider-open').onclick();
  assert.equal(el('custom-saved').value,'1');
  assert.equal(el('custom-supports-reasoning').value,String(declared));
  el('custom-name').value='Renamed only';
  await el('provider-form').onsubmit({preventDefault(){}});
  assert.equal(bodies.length,1);
  assert.equal(bodies[0].model,'target');
  assert.equal(bodies[0].name,'Renamed only');
  assert.equal(bodies[0].supports_reasoning,declared);
  // Another model under the same provider must not borrow this declaration.
  el('custom-model').value='not-saved';await el('provider-open').onclick();
  assert.equal(el('custom-saved').value,'');
  assert.equal(el('custom-supports-reasoning').value,'');
}
console.log('PASS: closing and reopening an exact saved provider/model preserves true and false through rename/save');
