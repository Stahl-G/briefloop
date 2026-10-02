import test from 'node:test';
import assert from 'node:assert/strict';
import {processSteps,researchProcessHTML,processDisclosureKey} from '../frontend/research-process.js';
const job={id:'j',status:'running',kind:'generate',created:'2026-10-01T10:00:00Z',updated:'2026-10-01T10:02:00Z'};
const progress={stage:'检索年度公告',started:job.created,timeline:[{label:'检索年度公告',status:'active',started:job.created}],message:'RAW AGENT RESPONSE',thinking:'PRIVATE REASONING',agents:[{role:'来源核查',activity:'RAW WORKER OUTPUT',status:'running'}]};
test('process collapses to the current action, not percent or raw model output',()=>{
 const html=researchProcessHTML(job,progress,{now:Date.parse(job.created)+90000});
 assert.match(html,/检索年度公告 · 2 步 · 用时 1 分 30 秒/);
 assert.doesNotMatch(html,/RAW|PRIVATE|%| open>/);
 assert.match(html,/process-step-time">1 分 30 秒/);
 assert.match(html,/用时未记录/);
 assert.equal(processSteps(job,progress)[1].detail,'');
});
test('completed work defaults closed; gaps and conflicts default open and are readable in the same block',()=>{
 const finished={...job,status:'complete'};
 assert.match(researchProcessHTML(finished,{...progress,timeline:[]}),/研究完成/);
 assert.doesNotMatch(researchProcessHTML(finished,{...progress,timeline:[]}),/ open>/);
 const problem={...progress,gaps:[{text:'缺少本期公告'}],conflicts:[{text:'收入口径不同'}]};
 const html=researchProcessHTML(finished,problem);
 assert.match(html,/ open>/);assert.match(html,/is-warn/);assert.match(html,/is-error/);
 assert.match(html,/缺少本期公告/);assert.match(html,/收入口径不同/);
 assert.doesNotMatch(researchProcessHTML(finished,problem,{expanded:false}),/ open>/);
 assert.notEqual(processDisclosureKey(job,progress),processDisclosureKey(finished,progress),'completion does not inherit a running disclosure preference');
 assert.notEqual(processDisclosureKey(finished,progress),processDisclosureKey(finished,problem),'new issues reopen a previously clean process');
});
test('unknown step timing is not estimated from queue age, percentages, or neighboring parallel actions',()=>{
 const html=researchProcessHTML({...job,status:'queued'},{timeline:[{label:'读取',status:'pending',time:job.created}],percent:93});
 assert.match(html,/尚未执行/);assert.match(html,/用时未记录/);assert.doesNotMatch(html,/93|%/);
});

test('planned Scouts show not started; real child timestamps provide elapsed time',()=>{
 const html=researchProcessHTML(job,{started:job.created,timeline:[{label:'第 2 轮 scout-1：未派发',status:'warn',not_started:true}],agents:[{role:'Scout',status:'completed',started:job.created,ended:'2026-10-01T10:01:00Z'}]});
 assert.match(html,/尚未开始/);assert.match(html,/1 分 0 秒/);assert.doesNotMatch(html,/用时未记录/);
});
