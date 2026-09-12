// Delay real refreshProgress fetches across stop, retry and report transitions.
import fs from 'node:fs';
import vm from 'node:vm';
import assert from 'node:assert/strict';
const source=fs.readFileSync(new URL('../frontend/app.js',import.meta.url),'utf8');
const code=source.slice(source.indexOf('function effectiveReportJobs'),source.indexOf('function friendlyModel'));
function fixture(status='running'){
 const nodes=new Map(),pending=[];
 const node=id=>{if(!nodes.has(id))nodes.set(id,{hidden:false,innerHTML:'',textContent:''});return nodes.get(id)};
 const job={id:'job',kind:'generate',status,payload:'{"run_id":"report"}',created:new Date().toISOString()};
 const context=vm.createContext({$:node,parse:s=>JSON.parse(s||'{}'),esc:String,modelLabel:()=> 'test/model',
  current:null,pendingRun:'report',
  state:{jobs:[job],briefs:[],runs:[],sources:[],settings:{timeout_minutes:30}},
  showSettings:()=>{},page:()=>{},action:async fn=>fn(),
  api:route=>new Promise((resolve,reject)=>pending.push({route,resolve,reject}))});
 vm.runInContext(code,context);
 return {context,node,job,pending};
}
for(const failOld of [false,true]){
 const {context,node,job,pending}=fixture();
 const old=context.refreshProgress();assert.equal(pending.length,2);
 const obsolete=pending.splice(0);
 job.status='cancelled';
 const paused=context.refreshProgress();assert.equal(pending.length,1);
 pending.shift().resolve({});await paused;
 assert.match(node('run-progress').innerHTML,/任务已暂停/);
 if(failOld)obsolete[0].reject(Error('provider credential: secret-test-key'));
 else obsolete[0].resolve([{kind:'runtime_progress',data:'{"stage":"obsolete active stage"}'}]);
 obsolete[1].resolve({worker_alive:true,pid:1,returncode:null});await old;
 assert.match(node('run-progress').innerHTML,/任务已暂停/);
 assert.doesNotMatch(node('run-progress').innerHTML,/obsolete active stage/);
 assert.equal(node('run-progress').textContent,'');
}
{
 const {context,node,job,pending}=fixture('cancelled');
 const old=context.refreshProgress(),obsolete=pending.shift();
 job.status='running';const resumed=context.refreshProgress();
 pending.shift().resolve([{kind:'runtime_progress',data:'{"stage":"resumed stage"}'}]);
 pending.shift().resolve({worker_alive:true,pid:1,returncode:null});await resumed;
 obsolete.resolve({});await old;
 assert.match(node('run-progress').innerHTML,/resumed stage/);
 assert.doesNotMatch(node('run-progress').innerHTML,/任务已暂停/);
}
{
 const {context,node,job,pending}=fixture();
 const old=context.refreshProgress(),obsolete=pending.splice(0);
 job.status='cancelled';const paused=context.refreshProgress();
 // Finishing an obsolete request cannot unlock the newer in-flight request.
 obsolete[0].resolve([]);obsolete[1].resolve({});await old;
 await context.refreshProgress();assert.equal(pending.length,1);
 pending.shift().resolve({});await paused;
 assert.match(node('run-progress').innerHTML,/任务已暂停/);
}
{
 const {context,node,pending}=fixture();
 const old=context.refreshProgress(),obsolete=pending.splice(0);
 context.current={id:'another-draft',run_id:'another-report'};
 context.pendingRun=null;
 await context.refreshProgress();assert.equal(node('run-progress').hidden,true);
 obsolete[0].resolve([]);obsolete[1].resolve({});await old;
 assert.equal(node('run-progress').hidden,true);
}
{
 const {context,node,pending}=fixture('failed');
 const refresh=context.refreshProgress();pending.shift().reject(Error('api_key=secret-test-key'));await refresh;
 assert.equal(node('run-progress').textContent,'进度连接暂时中断，任务没有重新提交。');
}
console.log('PASS: stopped, resumed and switched reports reject stale progress; errors use fixed public text');
