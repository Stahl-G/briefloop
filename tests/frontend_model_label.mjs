// Settings and frozen job payloads use different backend field names.
import fs from 'node:fs';
import vm from 'node:vm';
import assert from 'node:assert/strict';
import {withoutSupersededRetries} from '../frontend/review-status.js';
const source=fs.readFileSync(new URL('../frontend/app.js',import.meta.url),'utf8');
const code=['friendlyModel','runtimeName','modelLabel','jobModelLabel'].map(name=>source.split('\n').find(line=>line.startsWith('function '+name+'('))).join('\n');
const view=vm.createContext({withoutSupersededRetries,parse:s=>JSON.parse(s||'{}'),runtimeCatalog:[{id:'opencode',name:'OpenCode'},{id:'codex',name:'Codex'},{id:'other',name:'Other'}]});
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
assert.equal(view.modelLabel({backend:'codex',model:'gpt-5.6-luna',reasoning_effort:'medium',service_tier:'fast'}),'Codex · Luna / medium · Fast（请求）');
console.log('PASS: settings and frozen runtime backend fields display the same model and variant');

// Exercise both rendering call sites using the actual persisted outer backend shape.
const nodes=new Map();
view.$=id=>{if(!nodes.has(id))nodes.set(id,{innerHTML:'',hidden:false});return nodes.get(id)};
view.esc=String;view.statuses={running:'运行中'};
const job={id:'job_display',kind:'generate',status:'running',created:new Date().toISOString(),payload:JSON.stringify({agent_backend:'opencode',runtime:{model},run_id:'report'})};
view.state={jobs:[job],briefs:[],runs:[],sources:[],settings:{timeout_minutes:30,agent_backend:'codex'}};
const historyLine=source.split('\n').find(line=>line.trimStart().startsWith("$('jobs').innerHTML=state.jobs"));
vm.runInContext(historyLine,view);
assert.match(view.$('jobs').innerHTML,/OpenCode · opencode\/muse-spark-1.3-contributor-free \/ 模型默认/);
view.current=null;view.pendingRun='report';view.showSettings=()=>{};view.action=async fn=>fn();
view.api=async route=>route.startsWith('events?')?[]:{worker_alive:true,pid:1,returncode:null};
vm.runInContext(source.slice(source.indexOf('function effectiveReportJobs'),source.indexOf('function friendlyModel')),view);
await view.refreshProgress();
assert.match(view.$('run-progress').innerHTML,/OpenCode · opencode\/muse-spark-1.3-contributor-free \/ 模型默认/);
// Event backend is a fallback for older jobs; a frozen nested backend takes precedence.
assert.equal(view.jobModelLabel({payload:'{}'},{backend:'opencode',runtime:{model}}),'OpenCode · '+model+' / 模型默认');
assert.equal(view.jobModelLabel({payload:JSON.stringify({agent_backend:'other',runtime:{backend:'codex',model:'gpt-5.6-luna',reasoning_effort:'medium',service_tier:'fast'}})}),'Codex · Luna / medium · Fast（请求）');
assert.equal(view.jobModelLabel({payload:JSON.stringify({runtime:{model}})}),model+' / 未记录');
console.log('PASS: job history and live progress retain the frozen outer backend without guessing from model IDs');
