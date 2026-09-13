// Delay real refreshProgress fetches across stop, retry and report transitions.
import fs from 'node:fs';
import vm from 'node:vm';
import assert from 'node:assert/strict';
import {withoutSupersededRetries} from '../frontend/review-status.js';
const source=fs.readFileSync(new URL('../frontend/app.js',import.meta.url),'utf8');
const code=source.slice(source.indexOf('function effectiveReportJobs'),source.indexOf('function friendlyModel'));
function fixture(status='running'){
 const nodes=new Map(),pending=[];
 const node=id=>{if(!nodes.has(id))nodes.set(id,{hidden:false,innerHTML:'',textContent:'',insertAdjacentHTML(_position,html){this.innerHTML+=html}});return nodes.get(id)};
 const job={id:'job',kind:'generate',status,payload:'{"run_id":"report"}',created:new Date().toISOString()};
 const context=vm.createContext({withoutSupersededRetries,$:node,parse:s=>JSON.parse(s||'{}'),esc:String,modelLabel:()=> 'test/model',
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

{
 const {context,node,pending}=fixture();
 let opened=null;context.selectChat=id=>{opened=id};
 const refresh=context.refreshProgress();
 pending.shift().resolve([{kind:'runtime_started',data:'{"session_id":"bound-report-session"}'}]);
 pending.shift().resolve({session_id:'unrelated-chat',worker_alive:true,pid:1,returncode:null});
 await new Promise(resolve=>setImmediate(resolve));
 assert.equal(pending[0].route,'harness/session?id=bound-report-session');
 pending.shift().resolve({requests:[{id:'old',status:'answered'},{id:'permission',status:'pending'}]});
 await refresh;
 assert.match(node('run-progress').innerHTML,/等待你的确认/);
 assert.match(node('run-progress').innerHTML,/有 1 项操作等待确认/);
 node('progress-requests').onclick();assert.equal(opened,'bound-report-session');
}
{
 const {context,node,job,pending}=fixture();
 const old=context.refreshProgress();
 pending.shift().resolve([{kind:'runtime_started',data:'{"session_id":"old-session"}'}]);
 pending.shift().resolve({});
 await new Promise(resolve=>setImmediate(resolve));
 const stale=pending.shift();job.status='cancelled';
 const paused=context.refreshProgress();pending.shift().resolve({});await paused;
 stale.resolve({requests:[{status:'pending'}]});await old;
 assert.match(node('run-progress').innerHTML,/任务已暂停/);
 assert.doesNotMatch(node('run-progress').innerHTML,/查看并处理/);
}
