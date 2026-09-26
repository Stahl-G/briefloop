import test from 'node:test';
import assert from 'node:assert/strict';
import {createReportBrowsing} from '../frontend/report-browsing.js';

function fixture(){
 const nodes=new Map(),reads=[],errors=[],opened=[],changes=[];
 const element=()=>({value:'',hidden:false,open:false,textContent:'',innerHTML:'',dataset:{},classList:{toggle(){}},querySelectorAll:()=>[],querySelector:()=>null,replaceChildren(){this.children=[]},append(child){(this.children||=[]).push(child)},showModal(){this.open=true},close(){this.open=false}});
 const $=id=>{if(!nodes.has(id))nodes.set(id,element());return nodes.get(id)};
 globalThis.document={getElementById:$,createElement:element,querySelectorAll:()=>[]};
 const state={briefs:[],runs:[],assessments:[],jobs:[],report_catalog:{version_revision:1}};
 let current=null;
 const browsing=createReportBrowsing({api:route=>new Promise((resolve,reject)=>reads.push({route,resolve,reject})),getState:()=>state,getCurrent:()=>current,
  notice:message=>errors.push(message),openBrief:brief=>{opened.push(brief);return true},page(){},reportStatus:()=>({key:'draft',label:'草稿',cls:''}),reportDescription:b=>b.excerpt||'',reportIconMeta:()=>({cat:'cat-neutral',icon:'layers'}),svgLineIcon:()=>'',runSourceCount:()=>0,openRelease(){},onContext:()=>changes.push(current?.id)});
 return {browsing,$,state,reads,errors,opened,changes,setCurrent:value=>current=value};
}
const brief=(id,run=id)=>({id,run_id:run,hash:id,detail:JSON.stringify({title:id}),created:'2026-09-26',excerpt:'summary',source_count:0,latest_version_id:id});
const context=(id,run=id)=>({run:{id:run,requirements:'{"title":"full requirement"}',source_ids:'["source"]',source_count:1},latest:brief(id,run),original:brief(id,run),assessments:[{id:'a-'+id,version_id:id,data:'{"status":"complete","findings":[{"description":"full evidence"}]}'}]});

test('report pagination discards an older filter response and appends only the current cursor',async()=>{
 const f=fixture();
 const old=f.browsing.fetchReports();f.$('reports-search').value='archived';const fresh=f.browsing.fetchReports();
 f.reads[1].resolve({items:[brief('correct')],next_cursor:'30'});await fresh;
 f.reads[0].resolve({items:[brief('stale')],next_cursor:null});await old;
 assert.match(f.$('reports-list').innerHTML,/correct/);assert.doesNotMatch(f.$('reports-list').innerHTML,/stale/);
 const more=f.browsing.fetchReports(true);assert.match(f.reads[2].route,/cursor=30/);assert.match(f.reads[2].route,/q=archived/);
 f.reads[2].resolve({items:[brief('older')],next_cursor:null});await more;
 assert.match(f.$('reports-list').innerHTML,/correct/);assert.match(f.$('reports-list').innerHTML,/older/);
});

test('full selected context survives summary polls; a late context cannot target another report',async()=>{
 const f=fixture(),a=brief('A');f.setCurrent(a);f.state.briefs=[a];
 const full={...a,markdown:'saved body',context:context('A')};f.browsing.adopt(full);
 const next={briefs:[a],runs:[{id:'A',source_count:1}],assessments:[{id:'a-A',version_id:'A',summary:true,data:'{}'}]};
 f.browsing.mergeContext(next,a);
 assert.match(next.runs[0].source_ids,/source/);assert.match(next.assessments[0].data,/full evidence/);
 const pending={briefs:[{...a,assessment_id:'new-evaluation'}],runs:[{id:'A',source_count:1}],assessments:[{id:'new-evaluation',version_id:'A',summary:true,data:'{}'}]};
 f.browsing.acceptState(pending,a);assert.equal(f.reads.length,1);
 f.setCurrent(brief('B'));f.reads[0].resolve(context('A'));await new Promise(resolve=>setImmediate(resolve));
 assert.deepEqual(f.changes,[],'changing reports makes the old context response inert');
});

test('historical bodies load on demand with a bounded cache and current/pending pins',async()=>{
 const f=fixture();
 for(let i=0;i<7;i++){const request=f.browsing.loadBrief(brief(String(i)));f.reads.at(-1).resolve({...brief(String(i)),markdown:'body'});await request}
 const before=f.reads.length;await f.browsing.loadBrief(brief('6'));assert.equal(f.reads.length,before);
 const evicted=f.browsing.loadBrief(brief('0'));assert.equal(f.reads.length,before+1);f.reads.at(-1).resolve({...brief('0'),markdown:'body'});await evicted;
 const route=f.browsing.stateRoute(brief('A','old-run'),'pending-run');
 assert.match(route,/run_id=old-run/);assert.match(route,/version_id=A/);assert.match(route,/pending_run=pending-run/);
});

test('switching reports during a history fetch never replaces the newer history',async()=>{
 const f=fixture(),a=brief('A'),b=brief('B');f.setCurrent(a);const old=f.browsing.showHistory(a);
 f.setCurrent(b);const fresh=f.browsing.showHistory(b);
 f.reads[1].resolve({items:[b],next_cursor:null});await fresh;
 f.reads[0].resolve({items:[a],next_cursor:'1'});await old;
 assert.match(f.$('history-list').innerHTML,/data-history-version="B"/);assert.doesNotMatch(f.$('history-list').innerHTML,/data-history-version="A"/);
});

test('a background catalog change refreshes the loaded window without dropping older pages',async()=>{
 const f=fixture();const first=f.browsing.fetchReports();f.reads[0].resolve({items:[brief('first')],next_cursor:'1'});await first;
 const second=f.browsing.fetchReports(true);f.reads[1].resolve({items:[brief('older')],next_cursor:'2'});await second;
 f.state.report_catalog.version_revision=2;const refresh=f.browsing.fetchReports();
 f.reads[2].resolve({items:[brief('first')],next_cursor:'1'});await new Promise(resolve=>setImmediate(resolve));
 assert.match(f.reads[3].route,/cursor=1/);assert.match(f.$('reports-list').innerHTML,/older/);
 f.reads[3].resolve({items:[brief('older')],next_cursor:'2'});await refresh;
 assert.match(f.$('reports-list').innerHTML,/older/);
 f.$('reports-search').value='no-match';const filtered=f.browsing.fetchReports();f.reads[4].resolve({items:[],next_cursor:null});await filtered;
 assert.match(f.$('reports-list').innerHTML,/没有符合筛选条件/);assert.doesNotMatch(f.$('reports-list').innerHTML,/还没有报告/);
});
