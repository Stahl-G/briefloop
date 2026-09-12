// Settings and frozen job payloads use different backend field names.
import fs from 'node:fs';
import vm from 'node:vm';
import assert from 'node:assert/strict';
const source=fs.readFileSync(new URL('../frontend/app.js',import.meta.url),'utf8');
const code=['friendlyModel','runtimeName','modelLabel'].map(name=>source.split('\n').find(line=>line.startsWith('function '+name+'('))).join('\n');
const view=vm.createContext({runtimeCatalog:[{id:'opencode',name:'OpenCode'},{id:'codex',name:'Codex'},{id:'other',name:'Other'}]});
vm.runInContext(code,view);
const model='opencode/muse-spark-1.3-contributor-free';
for(const variant of [undefined,null,'','high']){
 const settings={agent_backend:'opencode',model,model_variant:variant};
 const frozen={backend:'opencode',model,model_variant:variant};
 assert.equal(view.modelLabel(frozen),view.modelLabel(settings));
 assert.equal(view.modelLabel(frozen),'OpenCode · '+model+' / '+(variant||'模型默认'));
}
assert.equal(view.modelLabel({backend:'codex',model:'gpt-5.6-luna',reasoning_effort:'medium'}),'Codex · Luna / medium');
assert.equal(view.modelLabel({backend:'other',model:'test-model'}),'Other · test-model');
// Preserve the existing setting field's precedence and legacy unknown effort.
assert.equal(view.modelLabel({agent_backend:'opencode',backend:'codex',model}),'OpenCode · '+model+' / 模型默认');
assert.equal(view.modelLabel({model:'gpt-5.6-luna'}),'Luna / 未记录');
assert.equal(view.modelLabel(null),'未指定模型');
console.log('PASS: settings and frozen runtime backend fields display the same model and variant');
