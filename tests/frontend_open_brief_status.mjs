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
 const state={briefs,jobs:[{id:'export',kind:'export_docx',status:'complete',payload:JSON.stringify({version_id:'approved',run_id:'run'}),result:JSON.stringify({download_url:'/saved.docx'})}],runs:[],assessments:[
  {version_id:'revise',data:JSON.stringify({status:'complete',overall:'建议修改'})},
  {version_id:'approved',data:JSON.stringify({status:'complete',overall:'达到要求'})}
 ]};
 const nodes=new Map();
 const $=id=>{if(!nodes.has(id))nodes.set(id,{dataset:{},innerHTML:'',value:'',hidden:true,open:false,querySelectorAll:()=>[]});return nodes.get(id)};
 const configurable={configure:()=>({})};
 let renders=0;
 const context=vm.createContext({state,current:briefs[0],dirty:false,saving:false,followUpdates:false,editor:null,highlightQuotes:[],
  $,parse:JSON.parse,esc:String,runConflicts:()=>[],runSourceCount:()=>0,reviewPending,notice:()=>{},updateDownloads:()=>{},
  Editor:class{destroy(){}},StarterKit:configurable,TableKit:{},ReportImage:configurable,TextStyle:{},Layout:{},Citation:{},ReportTrailingParagraph:{},Markdown:{},MustFixHighlight:{},
  editorDocument:x=>x,toEditor:x=>x,changed:()=>{},updateFormattingTools:()=>{},assessment:()=>{},citations:()=>{},renderBriefLength:()=>{},setReportView:()=>{},
  api:async()=>state,syncPendingReport:()=>{},renderWordExports:()=>{},render:()=>renders++,refreshProgress:async()=>{},refreshCandidates:async()=>{},refreshReportBudget:async()=>{},refreshReleaseState:async()=>{}});
 // Run the real app entry point and renderers; only editor/DOM plumbing is stubbed.
 vm.runInContext([
  source.slice(source.indexOf('function renderWordExports(){'),source.indexOf('function renderWordExports(){')+source.slice(source.indexOf('function renderWordExports(){')).indexOf('\n}')+2),
  functionBefore('renderReportStatus','renderAssistantSummary'),
  functionBefore('renderAssistantSummary','sendReportQuestion'),
  oneLine('openBrief'),oneLine('refresh'),oneLine('refreshState')
 ].join('\n'),context);
 vm.runInContext('refresh.signature=JSON.stringify(state);renderReportStatus()',context);
 const signature=JSON.stringify(state);
 assert.match($('report-status').innerHTML,/已评分 · 建议修改/);
 for(const [id,label] of [['approved','已评分 · 达到要求'],['unscored','未评分'],['revise','已评分 · 建议修改']]){
  vm.runInContext(`openBrief(state.briefs.find(b=>b.id==='${id}'))`,context);
  assert.equal($('version-select').value,id);
  assert.ok($('word-exports').innerHTML.includes(id==='approved'?'当前稿件版本':'历史稿件版本'),'export label updates before polling');
  assert.ok($('report-status').innerHTML.includes(label),'header updates before polling');
  await vm.runInContext('refresh()',context);
  assert.ok($('report-status').innerHTML.includes(label));
  assert.equal(renders,0,'unchanged API state must not be needed to repair the header');
  assert.equal(JSON.stringify(state),signature);
 }
});

test('polled version summaries load their body once and a later choice wins',async()=>{
 const summary={id:'old',run_id:'run',detail:'{}',hash:'h1',author:'agent'};
 const other={id:'new',run_id:'run',detail:'{}',hash:'h2',author:'user',markdown:'Loaded new'};
 const state={briefs:[other,summary],jobs:[],runs:[],assessments:[]};
 const nodes=new Map();
 const $=id=>{if(!nodes.has(id))nodes.set(id,{dataset:{},innerHTML:'',value:'',hidden:true,textContent:'',querySelectorAll:()=>[]});return nodes.get(id)};
 const configurable={configure:()=>({})};
 const requests=[];let resolveBody;
 const context=vm.createContext({state,current:null,dirty:false,saving:false,followUpdates:false,editor:null,highlightQuotes:[],pendingRun:null,
  $,parse:JSON.parse,notice:()=>{},updateDownloads:()=>{},syncPendingReport:()=>{},renderWordExports:()=>{},renderReportStatus:()=>{},renderAssistantSummary:()=>{},
  Editor:class{destroy(){}},StarterKit:configurable,TableKit:{},ReportImage:configurable,TextStyle:{},Layout:{},Citation:{},ReportTrailingParagraph:{},Markdown:{},MustFixHighlight:{},
  editorDocument:x=>x,toEditor:x=>x,changed:()=>{},updateFormattingTools:()=>{},assessment:()=>{},citations:()=>{},renderBriefLength:()=>{},setReportView:()=>{},
  api:route=>{requests.push(route);return new Promise(resolve=>{resolveBody=resolve})},encodeURIComponent});
 vm.runInContext(oneLine('openBrief'),context);
 assert.equal(vm.runInContext("openBrief(state.briefs[1])",context),true);
 vm.runInContext("openBrief(state.briefs[1])",context);
 assert.deepEqual(requests,['brief?id=old'],'repeated polling opens share one request');
 assert.equal(context.current,null);
 resolveBody({...summary,markdown:'Loaded old',length_stats:{count:2}});
 await new Promise(resolve=>setImmediate(resolve));
 assert.equal(vm.runInContext('current.markdown',context),'Loaded old');
 assert.equal($('version-select').value,'old');
 vm.runInContext("openBrief(state.briefs[1])",context);
 assert.equal(requests.length,1,'an unchanged hash reuses the loaded body');
 // A newer choice made while a body is loading must not be replaced by it.
 vm.runInContext("state.briefs[1]={...state.briefs[1],hash:'h3'};openBrief(state.briefs[1]);openBrief(state.briefs[0])",context);
 resolveBody({...summary,hash:'h3',markdown:'Late old'});
 await new Promise(resolve=>setImmediate(resolve));
 assert.equal(vm.runInContext('current.id',context),'new');
});
