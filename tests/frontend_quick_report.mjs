import test from 'node:test';
import assert from 'node:assert/strict';
import {createQuickReport,prepareNewReportRequirements} from '../frontend/quick-report.js';

function view(){
 const nodes={},calls=[],notices=[];let version='v1',result={mode:'draft_first',state:'deferred'};
 const $=id=>nodes[id]??={value:'',hidden:false,disabled:false,innerHTML:'',listeners:{},
  addEventListener(name,fn){this.listeners[name]=fn},
  querySelector(){return this.innerHTML.includes('data-continue-checks')?(this.button??={}):null}};
 const api=async(path,body)=>{calls.push({path,body});return body?{id:'j1',status:'queued'}:result};
 const controls=createQuickReport({$,api,action:fn=>fn(),notice:s=>notices.push(s),
  savedVersion:async()=>version,getCurrent:()=>({id:'v1'}),esc:s=>String(s)});
 return {$,controls,calls,notices,setVersion:v=>version=v,setResult:v=>result=v};
}

test('new report requests discard saved execution choices while keeping user inputs',()=>{
 const original={title:'Next issue',completion_mode:'fast_web',research_strategy:'goal_driven',target_minutes:17,
  allow_web:false,fact_check:true,research_tier:'deep',max_words:1800,previous_report_version_id:'old-v1'};
 const prepared=prepareNewReportRequirements(original);
 assert.deepEqual(prepared,{title:'Next issue',allow_web:false,fact_check:true,research_tier:'deep',max_words:1800,
  previous_report_version_id:'old-v1'});
 assert.equal(original.completion_mode,'fast_web');
 assert.equal(original.research_strategy,'goal_driven');
 assert.equal(prepareNewReportRequirements({target_minutes:45}).target_minutes,45);
});

test('saved fast reports display their existing background checks without creating a new task',async()=>{
 const {$,controls,calls,setResult}=view();
 setResult({mode:'fast',state:'checking',checked_version:'v0'});await controls.render();
 assert.equal($('draft-completion').hidden,false);
 assert.equal($('draft-completion').querySelector('[data-continue-checks]'),null);
 assert.equal(calls.filter(c=>c.body).length,0);
});

test('continue waits for saved user version and never asks the server to use current defaults',async()=>{
 const {$,controls,calls,setVersion}=view();await controls.render();
 setVersion('v2');await $('draft-completion').button.onclick();
 assert.deepEqual(calls.find(c=>c.body),{path:'continue-checks',body:{version_id:'v2'}});
});

test('failed job resumes its same identity; new edits cannot silently change that binding',async()=>{
 const {$,controls,calls,setVersion,setResult}=view();
 setResult({mode:'draft_first',state:'failed',job_id:'original-check'});await controls.render();
 setVersion('v2');await assert.rejects($('draft-completion').button.onclick(),/新版本/);
 assert.equal(calls.filter(c=>c.body).length,0);
 setVersion('v1');await controls.render();await $('draft-completion').button.onclick();
 assert.deepEqual(calls.find(c=>c.body),{path:'resume',body:{job_id:'original-check'}});
});

test('no duplicate continue button while checking; old reports keep their normal interface',async()=>{
 const {$,controls,setResult}=view();
 setResult({mode:'draft_first',state:'checking',job_id:'j1'});await controls.render();
 assert.equal($('draft-completion').querySelector('[data-continue-checks]'),null);
 setResult({mode:'standard'});await controls.render();assert.equal($('draft-completion').hidden,true);
});
