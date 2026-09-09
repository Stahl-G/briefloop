// Exercise actual application functions with controlled save completion order.
import fs from 'node:fs';
import vm from 'node:vm';
import assert from 'node:assert/strict';
const source=fs.readFileSync(new URL('../frontend/app.js',import.meta.url),'utf8');
const saveCode=source.slice(source.indexOf('let savePromise='),source.indexOf('\nfunction scheduleLearning'));
const commentCode=source.split('\n').find(l=>l.startsWith("$('comment-submit').onclick="));
const progressCode=source.slice(source.indexOf('function effectiveReportJobs'),source.indexOf('let progressRequest='));
const elements=new Map();
const el=id=>{if(!elements.has(id))elements.set(id,{value:'',href:'',textContent:''});return elements.get(id)};
let pending=[],calls=[],downloads=[],timers=[];
const c=vm.createContext({console,Promise,setTimeout:fn=>{timers.push(fn);return timers.length},clearTimeout:()=>{},$:el,
 dirty:true,saving:false,current:{id:'old',run_id:'r'},markdownMode:true,saveTimer:null,
 updateDownloads:()=>{},refresh:async()=>{},scheduleLearning:()=>{},notice:()=>{},
 window:{location:{assign:url=>downloads.push(url)}},
 api:(route,payload)=>{calls.push({route,payload});if(route==='save')return new Promise((resolve,reject)=>pending.push({resolve,reject}));return Promise.resolve({})},
 action:async fn=>fn(),parse:s=>JSON.parse(s||'{}'),pendingRun:null,tryOpenPending:()=>{}});
vm.runInContext(saveCode+'\n'+commentCode+'\n'+progressCode,c);
el('markdown-source').value='Revenue 12';el('comment').value='Comment about 12';
const comment=el('comment-submit').onclick();
assert.equal(calls.length,1);assert.equal(calls[0].route,'save');
// A second edit while first save is running must also be persisted.
el('markdown-source').value='Revenue 14';c.dirty=true;
pending.shift().resolve({id:'v12',run_id:'r'});
for(let n=0;n<10;n++)await Promise.resolve();
assert.equal(calls.length,2);assert.equal(calls[1].payload.base_version,'v12');
pending.shift().resolve({id:'v14',run_id:'r'});await comment;
assert.equal(calls.at(-1).route,'comment');assert.equal(calls.at(-1).payload.version_id,'v14');
// Downloads wait for save and use the resulting version.
c.dirty=true;el('markdown-source').value='Revenue 16';
let prevented=false;const download=el('download-docx').onclick({preventDefault(){prevented=true}});
assert.equal(downloads.length,0);pending.shift().resolve({id:'v16',run_id:'r'});await download;
assert.ok(prevented);assert.equal(downloads.length,0);
assert.equal(calls.at(-1).route,'export');assert.equal(calls.at(-1).payload.version_id,'v16');
// Save conflict leaves comment input intact and never submits it.
c.dirty=true;el('comment').value='Keep this';
const failure=el('comment-submit').onclick();pending.shift().reject(Error('conflict'));
await assert.rejects(failure,/conflict/);assert.equal(el('comment').value,'Keep this');
assert.equal(calls.at(-1).route,'save');
c.state={jobs:[{id:'new',status:'complete',payload:JSON.stringify({run_id:'r',previous_job_id:'old'})},{id:'old',status:'failed',payload:JSON.stringify({run_id:'r'})},{id:'other',status:'failed',payload:JSON.stringify({run_id:'other'})}],briefs:[]};
assert.equal(vm.runInContext('effectiveReportJobs().map(j=>j.id).join(",")',c),'new');
console.log('PASS: concurrent edits, comment binding, download version, save conflict, retry progress scope');

// Learning jobs are workspace-scoped even without run_id/version_id.
c.state.jobs.unshift({id:'learn',kind:'learn',status:'queued',payload:'{"feedback_ids":["f"]}'});
assert.ok(vm.runInContext('effectiveReportJobs().some(j=>j.id==="learn")',c));
// Use the real openBrief and pending logic with minimal editor/DOM fixtures.
const opening=source.slice(source.indexOf('function tryOpenPending'),source.indexOf('function changed()'));
let editorContent='';
c.changed=()=>{};
c.updateFormattingTools=()=>{};
c.Editor=class {constructor(options){editorContent=options.content}destroy(){}};
c.StarterKit={configure:()=>({})};c.TableKit={};c.ReportImage={configure:()=>({})};c.Markdown={};c.TextStyle={};c.Layout={};c.Citation={};
c.toEditor=x=>x;c.editor=null;c.assessment=()=>{};c.citations=()=>{};c.renderBriefLength=()=>{};
el('toolbar').querySelectorAll=()=>[];
vm.runInContext(opening,c);
c.current={id:'saved-old',run_id:'r',detail:'{}',markdown:'Revenue 16'};
c.state.briefs=[{id:'new-report',run_id:'new-run',detail:'{}',markdown:'New report',author:'agent'},c.current];
c.pendingRun='new-run';c.saving=true;c.dirty=false;
assert.equal(vm.runInContext('tryOpenPending()',c),false);
assert.equal(c.pendingRun,'new-run');assert.equal(c.current.id,'saved-old');
// Reproduce arrival during the real save refresh and let waiting action finish first.
c.saving=false;c.dirty=true;el('markdown-source').value='Revenue 18';el('comment').value='About old report edit';
c.refresh=async()=>{vm.runInContext('tryOpenPending()',c)};
const withArrival=el('comment-submit').onclick();
pending.shift().resolve({id:'saved-18',run_id:'r',detail:'{}',markdown:'Revenue 18'});
await withArrival;
assert.equal(calls.at(-1).payload.version_id,'saved-18');
assert.equal(c.pendingRun,'new-run');
for(const timer of timers.splice(0))timer();
assert.equal(c.current.id,'new-report');assert.equal(c.pendingRun,null);assert.equal(editorContent,'New report');
console.log('PASS: workspace learning progress and pending report survives save; comment stays on edited report');
