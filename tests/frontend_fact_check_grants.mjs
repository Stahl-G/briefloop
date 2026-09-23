import test from 'node:test';
import assert from 'node:assert/strict';
import {api} from '../frontend/api.js';
import {createFactCheckGrants,FACT_CHECK_GRANT_LIMITS} from '../frontend/fact-check-grants.js';
import {createAssessmentPanel} from '../frontend/assessment-panel.js';

function session(){
 const values=new Map();
 return {getItem:key=>values.get(key)||null,setItem:(key,value)=>values.set(key,value),removeItem:key=>values.delete(key)};
}
function panel(grants,notices=[],{request=api,getCurrent=()=>({id:'v1'})}={}){
 let html='',button;
 const box={dataset:{},isConnected:true,querySelectorAll:()=>button?[button]:[],
  get innerHTML(){return html},set innerHTML(value){
   html=value;const match=value.match(/<button data-fact-grant="([^"]+)"([^>]*)>/);
   button=match?{dataset:{factGrant:match[1]},disabled:match[2].includes('disabled')}:null;
  }};
 const view=createAssessmentPanel({api:request,factGrants:grants,action:fn=>fn(),notice:message=>notices.push(message),
  $:()=>box,getCurrent,bindSources(){}});
 return {view,box,click:()=>button.onclick(),button:()=>button};
}

test('the real grant button retains one click through token retry, lost response, reload and a closed stage',async t=>{
 const storage=session(),calls=[],notices=[];
 let stage='budget_exhausted',next=0,outage=false;
 const response=(status,body)=>({status,ok:status===200,json:async()=>body});
 t.mock.method(globalThis,'fetch',async(url,options)=>{
  if(url==='/api/session')return response(200,{token:'renewed'});
  if(url.startsWith('/api/fact-checks?')){
   if(outage)throw new TypeError('network still unavailable');
   return response(200,{version_id:'v1',enabled:true,stage:{status:stage},records:[]});
  }
  assert.equal(url,'/api/fact-check-grant');
  const body=JSON.parse(options.body);calls.push(body);
  if(calls.length===1)return response(403,{error:'token expired'});
  if(calls.length===2){stage='completed';outage=true;throw new TypeError('response lost after commit')}
  return response(200,{request_id:body.request_id,replayed:calls.length===3,status:'active',job_id:'j1'});
 });
 const controller=()=>createFactCheckGrants({api,storage:()=>storage,newId:()=>`click-${++next}`});
 const first=panel(controller(),notices);
 await first.view.renderFactChecks();
 await assert.rejects(first.click(),/response lost/);
 assert.deepEqual(calls[0],calls[1],'403 transport retry preserves the original request body');
 assert.equal(first.button().disabled,false,'a failed status refresh cannot leave the retry button disabled');
 outage=false;await first.view.renderFactChecks();
 assert.match(first.box.innerHTML,/核查阶段已完成/);
 assert.match(first.box.innerHTML,/重试确认上次追加/);
 assert.equal(first.button().disabled,false);
 // Recreate the controller/panel like a tab reload. The stage is closed, but
 // its lost success can still be acknowledged without issuing another grant.
 const reloaded=panel(controller(),notices);
 await reloaded.view.renderFactChecks();
 await reloaded.click();
 assert.deepEqual(calls[2],calls[1]);
 assert.match(notices[0],/没有重复追加/);
 assert.equal(reloaded.button(),null,'a closed stage loses its retry entry only after acknowledgement');
 assert.equal(controller().pending('v1'),null);
 stage='active';await reloaded.view.renderFactChecks();
 await reloaded.click();
 assert.notEqual(calls[3].request_id,calls[2].request_id,'the next explicit click is a new authorization');
 assert.deepEqual(calls[3].limits,FACT_CHECK_GRANT_LIMITS);
});

test('parallel clicks share one flight and recovered operations keep their original limits',async()=>{
 const storage=session();let complete,calls=0;
 const grants=createFactCheckGrants({storage:()=>storage,newId:()=> 'single-click',
  api:async()=>{calls++;return new Promise(resolve=>{complete=resolve})}});
 const first=grants.submit('v1'),second=grants.submit('v1');
 assert.equal(first,second);assert.equal(calls,1);assert.equal(grants.busy('v1'),true);
 complete({request_id:'single-click',replayed:false});await first;
 assert.equal(grants.busy('v1'),false);assert.equal(grants.pending('v1'),null);
 const saved={request_id:'older-click',version_id:'v1',limits:{search_requests:1,candidate_urls:3,source_pages:1}};
 storage.setItem('briefloop-fact-check-grant:v1',JSON.stringify(saved));
 let posted;
 const recovered=createFactCheckGrants({storage:()=>storage,api:async(_path,body)=>{posted=body;return {request_id:body.request_id,replayed:true}}});
 await recovered.submit('v1');assert.deepEqual(posted,saved,'a changed default cannot alter a retry');
 assert.equal(recovered.pending('v2'),null,'another version is a different operation');
 const mismatched=createFactCheckGrants({storage:()=>storage,newId:()=> 'awaiting-confirmation',api:async()=>({request_id:'another-operation'})});
 await assert.rejects(mismatched.submit('v1'),/回执身份不符/);
 assert.equal(mismatched.pending('v1').request_id,'awaiting-confirmation');
});

test('a storage failure cannot send an operation whose identity would be lost on reload',()=>{
 let sent=false;
 const storage=session();storage.setItem=()=>{throw Error('blocked')};
 const grants=createFactCheckGrants({storage:()=>storage,newId:()=> 'cannot-save',api:async()=>{sent=true}});
 assert.throws(()=>grants.submit('v1'),/本次尚未发送/);assert.equal(sent,false);
});


test('a repainted busy button retries the same request even while status GET keeps failing',async()=>{
 const storage=session(),calls=[];let rejectFirst,statusDown=false;
 const request=async(path,body)=>{
  if(path==='fact-check-grant'){
   calls.push(body);
   if(calls.length===1)return new Promise((_,reject)=>{rejectFirst=reject});
   return {request_id:body.request_id,replayed:true};
  }
  if(statusDown)throw Error('status endpoint unavailable');
  return {version_id:'v1',enabled:true,stage:{status:'active'},records:[]};
 };
 const grants=createFactCheckGrants({api:request,storage:()=>storage,newId:()=> 'lost-click'});
 const p=panel(grants,[],{request});
 await p.view.renderFactChecks();const clickedButton=p.button();const flight=p.click();
 await p.view.renderFactChecks();
 assert.notEqual(p.button(),clickedButton,'the poll replaces the original button');
 assert.equal(p.button().disabled,true);
 statusDown=true;rejectFirst(Error('response lost after commit'));
 await assert.rejects(flight,/response lost/);
 assert.equal(grants.busy('v1'),false);
 assert.equal(p.button().disabled,false,'the currently visible button must be usable without a successful GET');
 assert.match(p.box.innerHTML,/重试确认上次追加/);
 await p.click();
 assert.deepEqual(calls[1],calls[0],'the retry sends the retained authorization identity and limits');
 assert.equal(grants.pending('v1'),null);
 assert.equal(p.button().disabled,false);
});

test('a late A receipt does not announce a grant on B or clear B ongoing request',async()=>{
 const storage=session(),calls=[],notices=[],complete=new Map();let current='A',sequence=0;
 const request=async(path,body)=>{
  if(path==='fact-check-grant'){
   calls.push(body);return new Promise(resolve=>complete.set(body.version_id,resolve));
  }
  return {version_id:current,enabled:true,stage:{status:'active'},records:[]};
 };
 const grants=createFactCheckGrants({api:request,storage:()=>storage,newId:()=>`click-${++sequence}`});
 const p=panel(grants,notices,{request,getCurrent:()=>({id:current})});
 await p.view.renderFactChecks();const a=p.click();
 current='B';await p.view.renderFactChecks();const b=p.click();
 complete.get('A')({request_id:calls[0].request_id,replayed:false});await a;
 assert.deepEqual(notices,[],'A success must not appear as a confirmation on the B report');
 assert.equal(grants.pending('A'),null);
 assert.equal(grants.pending('B').request_id,calls[1].request_id);
 assert.equal(p.button().dataset.factGrant,'B');assert.equal(p.button().disabled,true);
 complete.get('B')({request_id:calls[1].request_id,replayed:false});await b;
 assert.equal(notices.length,1);assert.match(notices[0],/已追加核查预算/);
 assert.equal(grants.pending('B'),null);assert.equal(p.button().disabled,false);
 assert.deepEqual(calls.map(call=>call.version_id),['A','B']);
});
