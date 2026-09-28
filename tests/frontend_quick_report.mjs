import test from 'node:test';
import assert from 'node:assert/strict';
import {createQuickReport} from '../frontend/quick-report.js';

function view(){
 const nodes={},calls=[],notices=[];let version='v1',result={mode:'draft_first',state:'deferred'};
 const $=id=>nodes[id]??={value:'',hidden:false,disabled:false,innerHTML:'',listeners:{},
  addEventListener(name,fn){this.listeners[name]=fn},
  querySelector(){return this.innerHTML.includes('data-continue-checks')?(this.button??={}):null}};
 $('completion-mode').value='standard';$('draft-target').value='10';
 const api=async(path,body)=>{calls.push({path,body});return body?{id:'j1',status:'queued'}:result};
 const controls=createQuickReport({$,api,action:fn=>fn(),notice:s=>notices.push(s),
  savedVersion:async()=>version,getCurrent:()=>({id:'v1'}),esc:s=>String(s)});
 controls.init();
 return {$,controls,calls,notices,setVersion:v=>version=v,setResult:v=>result=v};
}

test('draft-first is opt-in, with an editable soft target independent of research tier',()=>{
 const {$,controls}=view();
 assert.deepEqual(controls.read(),{completion_mode:'standard'});
 assert.equal($('draft-target').disabled,true);
 $('completion-mode').value='draft_first';$('completion-mode').listeners.change();
 assert.deepEqual(controls.read(),{completion_mode:'draft_first',target_minutes:10});
 $('draft-target').value='17';assert.equal(controls.read().target_minutes,17);
});

test('continue waits for saved user version and never asks the server to use current defaults',async()=>{
 const {$,controls,calls,setVersion}=view();await controls.render();
 assert.match($('draft-completion').innerHTML,/完整核验尚未开始/);
 assert.match($('draft-completion').innerHTML,/不代表事实已核实/);
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
 assert.doesNotMatch($('draft-completion').innerHTML,/data-continue-checks/);
 setResult({mode:'standard'});await controls.render();assert.equal($('draft-completion').hidden,true);
});
