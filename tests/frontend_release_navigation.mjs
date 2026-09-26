import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';
import {deliveryUI} from '../frontend/delivery.js';
import {section as sectionOf} from './source_section.mjs';
import {esc} from '../frontend/dom.js';

const source=fs.readFileSync(new URL('../frontend/app.js',import.meta.url),'utf8').replace(/\r\n/g,'\n');
const line=name=>source.split('\n').find(row=>row.startsWith(`function ${name}(`)||row.startsWith(`async function ${name}(`));
const section=(start,end)=>sectionOf(source,start,end,'frontend/app.js');

function fixture(){
 const nodes=new Map(),calls=[],notices=[],pending=new Map(),saved=[],pages=[];
 const $=id=>{if(!nodes.has(id))nodes.set(id,{value:'',textContent:'',innerHTML:'',dataset:{},open:false,querySelectorAll:()=>[],showModal(){this.open=true}});return nodes.get(id)};
 const brief=id=>({id,run_id:'run-'+id,hash:'hash-'+id,detail:'{}',author:'agent'});
 const a={...brief('A'),markdown:'Report A'},b=brief('B'),c=brief('C');
 const buttons=[b,c].map(brief=>({dataset:{reportRelease:brief.id}}));
 const configurable={configure:()=>({})};
 const context=vm.createContext({$,state:{briefs:[a,b,c],jobs:[],runs:[]},current:a,dirty:false,saving:false,savePromise:null,saveTimer:null,lastSaveError:null,pendingRun:null,editor:null,highlightQuotes:[],
  parse:JSON.parse,esc,notice:message=>notices.push(message),page:name=>pages.push(name),clearTimeout(){},
  Editor:class{destroy(){}},StarterKit:configurable,ReportImage:configurable,TableKit:{},TextStyle:{},Layout:{},Citation:{},Markdown:{},MustFixHighlight:{},ReportTrailingParagraph:{},
  editorDocument:value=>value,toEditor:value=>value,changed(){},updateFormattingTools(){},assessment(){},citations(){},renderBriefLength(){},setReportView(){},renderReportStatus(){},renderAssistantSummary(){},syncPendingReport(){},renderWordExports(){},updateDownloads(){},
  box:{querySelectorAll:()=>buttons},
  api:async route=>{calls.push(route);if(route.startsWith('brief?id='))return new Promise((resolve,reject)=>pending.set(route.slice(9),{resolve,reject}));if(route.startsWith('release-state?'))return {eligibility:{eligible:true},releases:[]};throw Error('Unexpected request: '+route)},
 });
 context.action=async fn=>{try{return await fn()}catch(error){notices.push(error.message)}};
 context.save=async()=>{context.current={...context.current,id:context.current.id+'-saved'};context.dirty=false};
 vm.runInContext([
  section('function renderVersionSelect(){','function showPendingReport('),line('loadBrief'),line('openBrief'),
  section('async function savedVersion(){','const reportExport=reportExportUI('),
 ].join('\n'),context);
 const original=context.savedVersion;
 context.savedVersion=async()=>{saved.push(context.current?.id);return original()};
 // The real delivery module reads the editor state through these accessors.
 const delivery=deliveryUI({api:context.api,notice:context.notice,action:context.action,page:context.page,openBrief:context.openBrief,savedVersion:context.savedVersion,statuses:{},
  getState:()=>context.state,getCurrent:()=>context.current,isDirty:()=>context.dirty,isSaving:()=>context.saving});
 globalThis.document={getElementById:$};
 delivery.init();
 context.delivery=delivery;
 context.rows=context.state.briefs;context.openRelease=b=>context.action(()=>delivery.openReleaseDialog(b));
 const browsing=fs.readFileSync(new URL('../frontend/report-browsing.js',import.meta.url),'utf8');
 vm.runInContext(browsing.split('\n').find(row=>row.trim().startsWith("box.querySelectorAll('[data-report-release]')")),context);
 const resolve=id=>pending.get(id).resolve({...context.state.briefs.find(b=>b.id===id),markdown:'Report '+id});
 return {context,delivery,$,calls,notices,saved,pages,buttons,resolve,reject:(id)=>pending.get(id).reject(Error('Body unavailable'))};
}

test('list delivery waits for the chosen report body before saving or checking eligibility',async()=>{
 const f=fixture(),opened=f.buttons[0].onclick();
 assert.deepEqual(f.calls,['brief?id=B']);assert.deepEqual(f.saved,[]);
 assert.equal(f.context.current.id,'A');assert.equal(f.$('release-dialog').open,false);
 f.resolve('B');await opened;
 assert.equal(f.context.current.id,'B');assert.deepEqual(f.saved,['B']);
 assert.deepEqual(f.calls,['brief?id=B','release-state?version=B']);assert.equal(f.$('release-dialog').open,true);
});

test('a failed chosen body leaves the old report untouched and never opens its delivery panel',async()=>{
 const f=fixture(),opened=f.buttons[0].onclick();f.reject('B');await opened;
 assert.equal(f.context.current.id,'A');assert.deepEqual(f.saved,[]);
 assert.deepEqual(f.calls,['brief?id=B']);assert.equal(f.$('release-dialog').open,false);
 assert.deepEqual(f.notices,['Body unavailable']);
});

test('a later report choice cancels the waiting delivery action and ignores the late body',async()=>{
 const f=fixture(),opened=f.buttons[0].onclick();
 f.context.openBrief(f.context.state.briefs[2]);f.resolve('C');
 await new Promise(resolve=>setImmediate(resolve));assert.equal(f.context.current.id,'C');
 f.resolve('B');await opened;
 assert.equal(f.context.current.id,'C');assert.deepEqual(f.saved,[]);
 assert.equal(f.calls.some(route=>route.startsWith('release-state?')),false);assert.equal(f.$('release-dialog').open,false);
});

test('the report header also waits for a body already loading through normal navigation',async()=>{
 const f=fixture();f.context.openBrief(f.context.state.briefs[1]);
 const opened=f.$('release-open').onclick();assert.deepEqual(f.saved,[]);
 f.resolve('B');await opened;
 assert.deepEqual(f.saved,['B']);assert.equal(f.calls.at(-1),'release-state?version=B');
});

test('unsaved edits block report switching while delivery of the current report still waits for saving',async()=>{
 const f=fixture();f.context.dirty=true;
 await f.buttons[0].onclick();assert.deepEqual(f.calls,[]);assert.deepEqual(f.saved,[]);
 assert.equal(f.context.current.id,'A');assert.equal(f.context.dirty,true);assert.match(f.notices[0],/请先保存/);
 await f.$('release-open').onclick();
 assert.deepEqual(f.saved,['A']);assert.equal(f.context.current.id,'A-saved');
 assert.deepEqual(f.calls,['release-state?version=A-saved']);
});

test('a selection changed while the save barrier settles cannot open a panel for the newer selection',async()=>{
 const f=fixture();f.context.state.briefs[1].markdown='Report B';f.context.state.briefs[2].markdown='Report C';
 const opened=f.buttons[0].onclick();f.context.openBrief(f.context.state.briefs[2]);await opened;
 assert.equal(f.context.current.id,'C');assert.deepEqual(f.saved,['B']);assert.deepEqual(f.calls,[]);
 assert.equal(f.$('release-dialog').open,false);
});
