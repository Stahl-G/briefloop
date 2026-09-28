import test from 'node:test';
import assert from 'node:assert/strict';
import {createTemplateOutput} from '../frontend/template-output.js';
import {visibleBuiltinTemplates} from '../frontend/templates.js';

const tick=()=>new Promise(resolve=>setImmediate(resolve));
const deferred=()=>{let resolve,reject;const promise=new Promise((yes,no)=>{resolve=yes;reject=no});return {promise,resolve,reject}};
const brief=(id,run=id)=>({id,run_id:run,created:'2026-09-28T10:00:00Z',detail:JSON.stringify({title:'报告 '+id})});
function fixture(t){
 const nodes=new Map(),requests=[],uploads=[],opened=[],notices=[],timers=new Map();
 let timerId=0,refreshes=0,currentBrief=null,save=async()=>currentBrief?.id;
 const originalTimeout=globalThis.setTimeout,originalClear=globalThis.clearTimeout;
 globalThis.setTimeout=fn=>{const id=++timerId;timers.set(id,fn);return id};globalThis.clearTimeout=id=>timers.delete(id);
 const node=()=>({id:'',value:'',disabled:false,open:false,dataset:{},setAttribute(){},addEventListener(type,fn){this[type]=fn},showModal(){this.open=true},close(){this.open=false},
  set innerHTML(value){
   this.html=value;for(const id of this.childIds||[])nodes.delete(id);this.childIds=[];
   for(const match of value.matchAll(/<(input|select|button|form|a|p|h2)\b([^>]*\bid="([^"]+)"[^>]*)>/g)){
    const child=node(),attrs=match[2];child.id=match[3];child.value=/\bvalue="([^"]*)"/.exec(attrs)?.[1]||'';child.disabled=/\sdisabled(?:\s|$)/.test(attrs);child.href=/\bhref="([^"]*)"/.exec(attrs)?.[1]||'';nodes.set(child.id,child);this.childIds.push(child.id);
   }
  },get innerHTML(){return this.html||''}});
 globalThis.document={createElement:node,getElementById:id=>nodes.get(id),body:{append(element){nodes.set(element.id,element)}}};
 const state={workspace_id:'workspace-A',settings:{default_template_id:'unchanged'},briefs:[brief('unrelated-state-item')],templates:[{id:'style',name:'商业报告·品牌绿',status:'ready'},{id:'pending',name:'准备中模板',status:'pending'}]};
 const ui=createTemplateOutput({api:(path,body)=>{const d=deferred();requests.push({path,body,...d});return d.promise},notice:(message,error)=>notices.push({message,error}),refresh:async()=>{refreshes++},getState:()=>state,getCurrent:()=>currentBrief,savedVersion:()=>save(),openBrief:async version=>{opened.push(version);return true},getUploadLimits:()=>({max_file_bytes:100}),uploadPayload:async(file,limits,extra)=>{uploads.push({file,limits,extra});return {...extra,name:file.name,data:'original-bytes'}}});
 t.after(()=>{ui.reset();globalThis.setTimeout=originalTimeout;globalThis.clearTimeout=originalClear;delete globalThis.document});
 return {ui,state,requests,uploads,opened,notices,timers,$:id=>nodes.get(id),html:()=>nodes.get('template-output-dialog')?.innerHTML||'',setCurrent:value=>currentBrief=value,setSave:fn=>save=fn,get refreshes(){return refreshes},runTimer(){const [id,fn]=timers.entries().next().value||[];assert.ok(fn,'a poll is scheduled');timers.delete(id);return fn()}};
}
function chooseFile(f,name='source.docx'){f.$('template-output-file').onchange({target:{files:[{name,size:10}]}})}
function chooseReport(f,id){f.$('template-output-report').onchange({target:{value:id}})}

test('file conversion binds original upload and ready template, then offers explicit result actions',async t=>{
 const f=fixture(t);assert.equal(f.ui.open('pending'),false);assert.equal(f.requests.length,0);
 f.ui.open('style');chooseFile(f,'原稿.docx');assert.match(f.html(),/template-output-field" hidden/);assert.match(f.html(),/更换文件/);const submit=f.$('template-output-submit').onclick();await tick();
 assert.deepEqual(f.uploads[0].extra,{template_id:'style',workspace_id:'workspace-A'});
 assert.equal(f.requests[0].path,'template-convert');assert.equal(f.requests[0].body.data,'original-bytes');
 const version=brief('converted');f.requests[0].resolve({version,job:{id:'word-1',status:'complete'},notes:['原稿已保存']});await submit;await tick();
 assert.deepEqual(f.opened,[],'completion never changes the visible report');assert.equal(f.refreshes,1);
 assert.match(f.html(),/Word 已生成，转换稿已保存/);assert.match(f.html(),/原稿已保存/);
 assert.match(f.$('template-output-download').href,/job=word-1&workspace_id=workspace-A/);
 assert.equal(f.state.settings.default_template_id,'unchanged');await f.$('template-output-open').onclick();assert.deepEqual(f.opened,[version]);assert.equal(f.$('template-output-dialog').open,false);
});

test('report selection searches and paginates the saved catalog without a current-report fallback',async t=>{
 const f=fixture(t);f.setCurrent(brief('current'));
 f.ui.open('style','report');assert.match(f.requests[0].path,/^reports\?/);assert.match(f.requests[0].path,/workspace_id=workspace-A/);
 f.requests[0].resolve({items:[brief('recent')],next_cursor:'30'});await tick();
 assert.equal(f.$('template-output-submit').disabled,true);assert.doesNotMatch(f.html(),/unrelated-state-item/);
 const more=f.$('template-output-more').onclick();assert.match(f.requests[1].path,/cursor=30/);f.requests[1].resolve({items:[brief('older')],next_cursor:null});await more;
 assert.match(f.html(),/报告 older/);assert.match(f.html(),/2026\/09\/28/);chooseReport(f,'older');const submit=f.$('template-output-submit').onclick();
 assert.deepEqual(f.requests[2].body,{version_id:'older',template_id:'style',workspace_id:'workspace-A'});
 f.requests[2].resolve({id:'selected-export',status:'complete'});await submit;assert.deepEqual(f.opened,[]);
});

test('the selected open report settles edits before export and ignores repeat submissions',async t=>{
 const f=fixture(t),saving=deferred();f.setCurrent(brief('old','run'));f.setSave(()=>saving.promise);
 f.ui.open('style','report');f.requests[0].resolve({items:[brief('old','run')],next_cursor:null});await tick();chooseReport(f,'old');
 const submit=f.$('template-output-submit').onclick();await f.$('template-output-submit').onclick();assert.equal(f.requests.length,1);
 f.setCurrent(brief('just-saved','run'));saving.resolve('just-saved');await tick();assert.equal(f.requests[1].body.version_id,'just-saved');
 f.requests[1].resolve({id:'saved-export',status:'complete'});await submit;
});

test('closing the dialog keeps one accepted conversion and resumes its result on reopening',async t=>{
 const f=fixture(t);f.ui.open('style');chooseFile(f);const submit=f.$('template-output-submit').onclick();await tick();
 f.ui.close();f.ui.open('style');await f.$('template-output-submit').onclick();assert.equal(f.requests.length,1,'no duplicate conversion while the first response is pending');
 f.requests[0].resolve({version:brief('converted'),job:{id:'background',status:'queued'}});await submit;f.ui.close();
 const poll=f.runTimer();assert.match(f.requests[1].path,/^export-status\?job=background/);f.requests[1].resolve({id:'background',status:'complete'});await poll;
 assert.equal(f.$('template-output-dialog').open,false);assert.deepEqual(f.opened,[]);f.ui.open('style');assert.match(f.html(),/下载 Word/);assert.equal(f.requests.length,2);
});

test('poll failures recheck the same job; cancelled jobs retry export without importing another report',async t=>{
 const f=fixture(t);f.ui.open('style');chooseFile(f);const submit=f.$('template-output-submit').onclick();await tick();
 f.requests[0].resolve({version:brief('converted'),job:{id:'first',status:'running'}});await submit;
 const poll=f.runTimer();f.requests[1].reject(Error('网络中断'));await poll;assert.match(f.html(),/重新检查进度/);assert.equal(f.$('template-output-submit'),undefined);
 f.$('template-output-poll').onclick();assert.equal(f.requests.length,3);f.requests[2].resolve({id:'first',status:'cancelled'});await tick();
 assert.equal(f.$('template-output-submit').disabled,false);const retry=f.$('template-output-submit').onclick();assert.equal(f.requests[3].path,'export');assert.equal(f.requests[3].body.version_id,'converted');
 f.requests[3].resolve({id:'retry',status:'complete'});await retry;assert.equal(f.uploads.length,1);
});

test('workspace reset ignores an in-flight conversion and never opens or downloads its old result',async t=>{
 const f=fixture(t);f.ui.open('style');chooseFile(f);const submit=f.$('template-output-submit').onclick();await tick();
 f.state.workspace_id='workspace-B';f.ui.reset();f.ui.open('style');const freshHtml=f.html();
 f.requests[0].resolve({version:brief('old-workspace'),job:{id:'old-job',status:'complete'}});await submit;await tick();
 assert.equal(f.html(),freshHtml);assert.equal(f.refreshes,0);assert.deepEqual(f.opened,[]);assert.equal(f.timers.size,0);
});

test('a superseded report search cannot replace the chosen workspace catalog',async t=>{
 const f=fixture(t);f.ui.open('style','report');f.$('template-output-search').value='annual';f.$('template-output-search-form').onsubmit({preventDefault(){}});
 assert.match(f.requests[1].path,/q=annual/);f.requests[1].resolve({items:[brief('annual')],next_cursor:null});await tick();
 f.requests[0].resolve({items:[brief('stale')],next_cursor:'30'});await tick();assert.match(f.html(),/报告 annual/);assert.doesNotMatch(f.html(),/报告 stale/);
});

test('invalid file selection is rejected locally, and builtin history creates only one swatch per style',t=>{
 const f=fixture(t);f.ui.open('style');chooseFile(f,'image.png');assert.match(f.html(),/请选择 DOCX、Markdown 或 TXT/);assert.equal(f.$('template-output-submit').disabled,true);assert.equal(f.requests.length,0);
 const old={id:'old',origin:'builtin',name:'商业报告·品牌绿',status:'ready',revision:1,created:'2026-01-01'};
 const input=[old,{...old,id:'new',revision:2,created:'2026-09-28'},{...old,id:'pending',revision:3,status:'pending'},{...old,id:'blue',name:'商业报告·极简蓝'}];
 assert.deepEqual(visibleBuiltinTemplates(input).map(t=>t.id),['new','blue']);assert.equal(input.length,4,'saved template history is not deleted');
});
