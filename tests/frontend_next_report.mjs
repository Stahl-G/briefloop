import test from 'node:test';
import assert from 'node:assert/strict';
import {nextReportUI} from '../frontend/next-report.js';

function fixture({confirmed=true,currentAfter='v1',template={status:'ready',sections:[]}}={}){
 const elements=Object.fromEntries(['title','template_id','completion_mode','organization','industry','report_date','reader_id','previous_report_version_id','previous_report_hash','allow_web','fact_check'].map(name=>[name,{value:'old',checked:true,type:['allow_web','fact_check'].includes(name)?'checkbox':'text',dispatchEvent(){},focus(){}}]));
 const nodes={requirements:{elements,addEventListener(){}},'template-sections':{querySelectorAll:()=>[]},'next-report-notice':{hidden:true},'next-report-summary':{},'next-report-cancel':{},'next-report':{}};
 const calls=[],applied=[];let cleared=0,requested=false;
 const payload={requirements:{objective:'Prioritize follow-up',template_id:'t1',sections:[],previous_report_version_id:'v1',previous_report_hash:'abc'},previous:{title:'Last period'},notice:'Update time and sources'};
 const ui=nextReportUI({$:id=>nodes[id],getTemplate:()=>template,api:async path=>{calls.push(path);requested=true;return payload},savedVersion:async()=> 'v1',getCurrent:()=>({id:requested?currentAfter:'v1'}),confirm:()=>confirmed,
  applyRequirements:value=>applied.push(JSON.parse(value)),clearSources:()=>{cleared++},notice:()=>{}});
 return {ui,nodes,elements,calls,applied,payload,cleared:()=>cleared};
}

test('next period prepares only a contract and clears stale authorization/source choices',async()=>{
 const f=fixture();await f.ui.start();
 assert.deepEqual(f.calls,['next-report?version_id=v1']);
 assert.equal(f.applied.length,1);assert.equal(f.cleared(),1);
 assert.equal(f.elements.allow_web.checked,false);assert.equal(f.elements.fact_check.checked,false);
 assert.equal(f.elements.completion_mode.value,'standard');
 assert.equal(f.elements.previous_report_version_id.value,'v1');
 assert.equal(f.ui.requirementsForTemplate('t1'),f.payload.requirements);
 assert.equal(f.ui.requirementsForTemplate('another'),null);
 assert.equal(f.nodes['next-report-notice'].hidden,false);
 f.ui.clear();assert.equal(f.elements.previous_report_version_id.value,'');
 assert.equal(f.ui.requirementsForTemplate('t1'),null);
});

test('cancel and navigation races do not overwrite a new request',async()=>{
 const cancelled=fixture({confirmed:false});await cancelled.ui.start();
 assert.equal(cancelled.calls.length,0);assert.equal(cancelled.cleared(),0);
 const moved=fixture({currentAfter:'v2'});
 await assert.rejects(moved.ui.start(),/当前报告已切换/);
 assert.equal(moved.applied.length,0);assert.equal(moved.cleared(),0);
 assert.equal(moved.elements.previous_report_version_id.value,'old');
});


test('missing or changed templates leave the unsent form intact',async()=>{
 for(const template of [null,{status:'pending'}, {status:'ready',sections:[]}]){
  const f=fixture({template});f.payload.requirements.sections=[{section_id:'manual',mode:'manual'}];
  await assert.rejects(f.ui.start(),/模板/);
  assert.equal(f.applied.length,0);assert.equal(f.cleared(),0);
  assert.equal(f.elements.previous_report_version_id.value,'old');
 }
});
