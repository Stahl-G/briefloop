import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';
import {adaptivePoll} from '../frontend/polling.js';
import {homeUI} from '../frontend/home.js';

test('polling backs off idle/hidden, refreshes on return and never overlaps a slow request',async()=>{
 const timers=new Map();let sequence=0,listener,active=false,release,calls=0;
 const doc={hidden:false,addEventListener:(_,fn)=>listener=fn,removeEventListener:()=>listener=null};
 const stop=adaptivePoll(()=>{calls++;return new Promise(r=>release=r)},{active:()=>active,fast:1300,doc,
  setTimer:(fn,ms)=>{const id=++sequence;timers.set(id,{fn,ms});return id},clearTimer:id=>timers.delete(id)});
 const next=()=>[...timers.values()][0];
 assert.equal(next().ms,15000);
 const initial=next();timers.clear();const pending=initial.fn();
 assert.equal(calls,1);assert.equal(timers.size,0);
 doc.hidden=true;listener();assert.equal(next().ms,60000);
 doc.hidden=false;listener();assert.equal(calls,1);
 active=true;release();await pending;
 assert.equal(next().ms,0);
 const immediate=next();timers.clear();const second=immediate.fn();release();await second;
 assert.equal(next().ms,1300);
 doc.hidden=true;listener();assert.equal(next().ms,60000);
 stop();assert.equal(timers.size,0);assert.equal(listener,null);
});

const source=fs.readFileSync(new URL('../frontend/app.js',import.meta.url),'utf8').replace(/\r\n/g,'\n');
const line=name=>source.split('\n').find(row=>row.startsWith(`function ${name}(`)||row.startsWith(`async function ${name}(`));

test('a snapshot that differs only by the server clock keeps the page; timed task views still refresh',async()=>{
 let clock='2026-09-25T10:00:00+00:00',jobs=[],renders=0,workspace='one',resets=0;const timed=[];
 const node={textContent:'',hidden:true,open:false};
 const context=vm.createContext({$:()=>node,state:null,templateOutput:{reset(){resets++}},api:async()=>({workspace_id:workspace,system_clock:{now:clock},jobs,briefs:[]}),
  scheduledReports:{render(){}},activity:null,renderWordExports(){},notice(){},render:()=>renders++,
  renderTasks:()=>timed.push('tasks'),renderTaskBanner:()=>timed.push('banner'),
  refreshProgress:async()=>{},refreshCandidates:async()=>{},refreshReportBudget:async()=>{},refreshReleaseState:async()=>{}});
 vm.runInContext(line('refresh')+'\n'+line('refreshState'),context);
 await vm.runInContext('refresh(true)',context);assert.equal(renders,1);
 clock='2026-09-25T10:00:03+00:00';await vm.runInContext('refresh()',context);
 assert.equal(renders,1,'a new clock stamp alone does not rebuild the page');
 assert.deepEqual(timed,['tasks','banner']);
 assert.equal(vm.runInContext('state.system_clock.now',context),clock,'readers still see the latest clock');
 jobs=[{id:'job',status:'running'}];await vm.runInContext('refresh()',context);
 assert.equal(renders,2,'a changed record still renders');
 assert.equal(resets,0,'normal polling preserves template output selection');
 workspace='two';await vm.runInContext('refresh()',context);
 assert.equal(resets,1,'switching workspaces invalidates template output operations');
});

test('an unchanged task banner keeps its buttons across polls and a closed one stays consistent',()=>{
 let writes=0,stored=null;const buttons=new Map();
 const box={hidden:true,className:'',html:'',get innerHTML(){return this.html},set innerHTML(value){writes++;this.html=value},
  querySelector:selector=>{if(!buttons.has(selector))buttons.set(selector,{});return buttons.get(selector)}};
 const job={id:'job',kind:'generate',status:'running',payload:'{}',created:new Date().toISOString()};
 Object.defineProperty(globalThis,'localStorage',{configurable:true,value:{getItem:()=>stored,setItem:(_,value)=>{stored=value}}});
 const state={jobs:[job],briefs:[],runs:[]};
 const {renderTaskBanner}=homeUI({$:()=>box,getState:()=>state,parse:value=>JSON.parse(value||'{}'),
  statuses:{running:'运行中'},taskLabel:()=>'生成报告',page(){},action(){},api(){},openBrief(){}});
 renderTaskBanner();renderTaskBanner();
 assert.equal(writes,1,'polling with the same task does not replace the announced banner');
 assert.equal(box.hidden,false);assert.match(box.html,/正在生成报告/);
 buttons.get('[data-banner-close]').onclick();
 assert.equal(box.hidden,true);assert.equal(box.html,'');
 job.status='complete';job.updated=new Date().toISOString();renderTaskBanner();
 assert.equal(box.hidden,false);assert.match(box.html,/新报告已生成/);
});
