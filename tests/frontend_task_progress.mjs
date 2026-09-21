import assert from 'node:assert/strict';
import {taskProgressCard,elapsedText} from '../frontend/task-progress.js';
const job={id:'j',kind:'generate',created:'2026-09-14T00:00:00Z',status:'running'};
const p={run_id:'r',title:'日报 <img>',stage:'研究中',source_count:2,search_metered:true,search_counts:{completed:1,failed:2},sources:[],agents:[],timeline:[],stages:[]};
let html=taskProgressCard(job,p);
assert.match(html,/日报 &lt;img&gt;/);assert.match(html,/成功搜索 1 次/);assert.match(html,/失败 2 次/);
assert.doesNotMatch(html,/data-progress-version/);
html=taskProgressCard({...job,status:'cancelled'},{...p,version_id:'saved'},{expanded:true,error:true});
assert.match(html,/恢复任务/);assert.match(html,/data-progress-version="saved"/);assert.match(html,/稍后自动重试/);assert.match(html,/data-task-detail="j" open/);
assert.doesNotMatch(html,/data-task-stop/);
assert.doesNotMatch(html,/研究中/);assert.match(html,/已停止/);
assert.equal(elapsedText(job.created,Date.parse(job.created)+84000),'1 分 24 秒');

// Stopped/replaced requests cannot overwrite the newer card snapshot.
const {readFileSync}=await import('node:fs');
const vm=await import('node:vm');
const source=readFileSync(new URL('../frontend/app.js',import.meta.url),'utf8');
const controller=source.slice(source.indexOf('const taskSnapshots='),source.indexOf('function renderTasks(){'));
const pending=[],renders=[];
const c=vm.createContext({state:{jobs:[{...job,kind:'generate',payload:'{}'}],task_labels:{generate:'报告'}},taskLabel:k=>({generate:'报告'})[k],parse:JSON.parse,
 $:()=>({hidden:false}),api:route=>new Promise((resolve,reject)=>pending.push({route,resolve,reject})),
 renderTasks:()=>renders.push(true)});
vm.runInContext(controller,c);
const old=c.renderTaskGraph();const first=pending.shift();
c.state.jobs[0].status='cancelled';const newer=c.renderTaskGraph();const second=pending.shift();
first.resolve({...p,stage:'stale active'});await old;
await c.renderTaskGraph();assert.equal(pending.length,0,'old request must not unlock newer fetch');
second.resolve({...p,stage:'stopped'});await newer;
assert.equal(vm.runInContext("taskSnapshots.get('j').data.stage",c),'stopped');
const failed=c.renderTaskGraph();pending.shift().reject(Error('private provider diagnostic'));await failed;
assert.equal(vm.runInContext("taskSnapshots.get('j').error",c),true);
assert.doesNotMatch(vm.runInContext("JSON.stringify(taskSnapshots.get('j'))",c),/private provider/);

// Empty list refreshes when the first report starts, despite unchanged draft IDs.
const nodes=new Map();const el=id=>{if(!nodes.has(id))nodes.set(id,{value:'',innerHTML:'',querySelectorAll:()=>[]});return nodes.get(id)};
const emptyContext=vm.createContext({$:el,state:{briefs:[],jobs:[]}});
vm.runInContext(source.slice(source.indexOf('function renderReports(){'),source.indexOf('function sourceState(')),emptyContext);
emptyContext.renderReports();assert.match(el('reports-list').innerHTML,/还没有报告/);
emptyContext.state.jobs.push({kind:'generate',status:'running'});
emptyContext.renderReports();assert.match(el('reports-list').innerHTML,/首份报告正在制作/);
emptyContext.state.jobs[0].status='cancelled';
emptyContext.renderReports();assert.doesNotMatch(el('reports-list').innerHTML,/首份报告正在制作/);

// The module used to alias its own copy as `const e=escape`. With the copy
// gone that name resolves to the legacy global escape(), which percent-encodes
// instead of escaping markup — and every card silently renders %u65E5%u62A5.
assert.doesNotMatch(taskProgressCard({...job,status:'running'},{...p,title:'<b>x</b>'}),/%u|%3C/);
assert.match(taskProgressCard({...job,status:'running'},{...p,title:'<b>x</b>'}),/&lt;b&gt;/);
