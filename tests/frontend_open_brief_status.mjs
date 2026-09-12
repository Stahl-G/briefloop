import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';
import {reviewPending} from '../frontend/review-status.js';

const source=fs.readFileSync(new URL('../frontend/app.js',import.meta.url),'utf8');
const oneLine=name=>source.split('\n').find(line=>line.startsWith(`function ${name}(`)||line.startsWith(`async function ${name}(`));
const functionBefore=(name,next)=>source.slice(source.indexOf(`function ${name}(){`),source.indexOf(`\nfunction ${next}(`));

test('opening another version immediately refreshes scored and unscored headers with unchanged API state',async()=>{
 const briefs=['revise','approved','unscored'].map(id=>({id,run_id:'run',detail:'{}',markdown:'Saved report',author:'user'}));
 const state={briefs,jobs:[],runs:[],assessments:[
  {version_id:'revise',data:JSON.stringify({status:'complete',overall:'建议修改'})},
  {version_id:'approved',data:JSON.stringify({status:'complete',overall:'达到要求'})}
 ]};
 const nodes=new Map();
 const $=id=>{if(!nodes.has(id))nodes.set(id,{innerHTML:'',value:'',hidden:true,open:false,querySelectorAll:()=>[]});return nodes.get(id)};
 const configurable={configure:()=>({})};
 let renders=0;
 const context=vm.createContext({state,current:briefs[0],dirty:false,saving:false,followUpdates:false,editor:null,highlightQuotes:[],
  $,parse:JSON.parse,esc:String,runConflicts:()=>[],runSourceCount:()=>0,reviewPending,notice:()=>{},updateDownloads:()=>{},
  Editor:class{destroy(){}},StarterKit:configurable,TableKit:{},ReportImage:configurable,TextStyle:{},Layout:{},Citation:{},Markdown:{},MustFixHighlight:{},
  editorDocument:x=>x,toEditor:x=>x,changed:()=>{},updateFormattingTools:()=>{},assessment:()=>{},citations:()=>{},renderBriefLength:()=>{},setReportView:()=>{},
  api:async()=>state,renderWordExports:()=>{},render:()=>renders++,refreshProgress:async()=>{},refreshCandidates:async()=>{},refreshReportBudget:async()=>{},refreshReleaseState:async()=>{}});
 // Run the real app entry point and renderers; only editor/DOM plumbing is stubbed.
 vm.runInContext([
  functionBefore('renderReportStatus','renderAssistantSummary'),
  functionBefore('renderAssistantSummary','sendReportQuestion'),
  oneLine('openBrief'),oneLine('refresh')
 ].join('\n'),context);
 vm.runInContext('refresh.signature=JSON.stringify(state);renderReportStatus()',context);
 const signature=JSON.stringify(state);
 assert.match($('report-status').innerHTML,/已评分 · 建议修改/);
 for(const [id,label] of [['approved','已评分 · 达到要求'],['unscored','未评分'],['revise','已评分 · 建议修改']]){
  vm.runInContext(`openBrief(state.briefs.find(b=>b.id==='${id}'))`,context);
  assert.equal($('version-select').value,id);
  assert.ok($('report-status').innerHTML.includes(label),'header updates before polling');
  await vm.runInContext('refresh()',context);
  assert.ok($('report-status').innerHTML.includes(label));
  assert.equal(renders,0,'unchanged API state must not be needed to repair the header');
  assert.equal(JSON.stringify(state),signature);
 }
});
