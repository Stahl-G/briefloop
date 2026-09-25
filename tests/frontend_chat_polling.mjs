import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';
import {section as sectionOf} from './source_section.mjs';

const source=fs.readFileSync(new URL('../frontend/app.js',import.meta.url),'utf8').replace(/\r\n/g,'\n');
const section=(start,end)=>sectionOf(source,start,end,'frontend/app.js');
const flush=async()=>{for(let i=0;i<30;i++)await Promise.resolve()};
const snapshot=(id,status,seq,text=status)=>({session:{id,status},messages:[{text}],requests:status==='running'?[{id:'pending-request'}]:[],token_usage:{total:seq*10},events:[{seq,kind:status}]});

function fixture(){
 const nodes=new Map(),lists=[],reads=[],calls=[],renders=[],errors=[],restores=[];let active=0,peak=0;
 const $=id=>{if(!nodes.has(id))nodes.set(id,{value:'',hidden:false,textContent:''});return nodes.get(id)};
 const chat={id:'A',session:{id:'A',status:'running'},view:'active',sessions:[],messages:[],requests:[],events:new Map(),after:0,tokenUsage:null,polling:false,busy:false,uploading:0,drafts:new Map()};
 const context=vm.createContext({$,chat,renderMessages:{},rememberDraft(){},restoreDraft:()=>restores.push(chat.id),page(){},updateComposer(){},
  localStorage:{setItem(){},removeItem(){}},sessionMissing:error=>error.message==='会话不存在',chatError:message=>errors.push(message),
  renderChat:()=>renders.push({id:chat.id,status:chat.session?.status,text:chat.messages[0]?.text,cursor:chat.after,usage:chat.tokenUsage?.total}),
  api:(route,payload)=>{
   calls.push({route,payload});if(route==='harness/cancel')return Promise.resolve({});
   active++;peak=Math.max(peak,active);
   return new Promise((resolve,reject)=>{
    const request={route,resolve:value=>{active--;resolve(value)},reject:error=>{active--;reject(error)}};
    (route.startsWith('harness/sessions?')?lists:reads).push(request);
   });
  },
 });
 vm.runInContext([
  section('async function selectChat(id){','async function openChatHome('),
  section('async function pollChat(force=false){','async function sendChat(event){'),
  source.split('\n').find(row=>row.startsWith("$('chat-stop').onclick=")),
 ].join('\n'),context);
 const complete=(index,value)=>{lists[index].resolve({sessions:[value.session]});reads[index].resolve(value)};
 return {context,$,chat,lists,reads,calls,renders,errors,restores,complete,get peak(){return peak},get active(){return active}};
}

test('forced refreshes coalesce and discard an older partial snapshot before applying the complete result',async()=>{
 const f=fixture(),background=f.context.pollChat();await flush();
 const first=f.context.pollChat(true),second=f.context.pollChat(true);await flush();
 assert.equal(f.reads.length,1,'forced callers must wait for the in-flight read');
 f.complete(0,snapshot('A','running',1,'Old partial'));await background;await flush();
 assert.deepEqual(f.renders,[]);assert.equal(f.chat.after,0);assert.equal(f.chat.tokenUsage,null);
 assert.equal(f.reads.length,2);assert.match(f.reads[1].route,/after=0/);
 f.complete(1,snapshot('A','complete',2,'New complete'));await Promise.all([first,second]);
 assert.equal(f.reads.length,2);assert.equal(f.peak,2,'only one list/body request pair may run at once');
 assert.deepEqual(f.renders,[{id:'A',status:'complete',text:'New complete',cursor:2,usage:20}]);
 assert.deepEqual(f.chat.requests,[]);assert.deepEqual([...f.chat.events.keys()],[2]);assert.equal(f.chat.polling,false);
});

test('an early failure waits for its sibling read before releasing the queue, and the next force can recover',async()=>{
 const f=fixture(),first=f.context.pollChat(true);await flush();
 const failed=assert.rejects(first,/list unavailable/);
 f.lists[0].reject(Error('list unavailable'));await flush();
 assert.equal(f.chat.polling,true);assert.equal(f.active,1);
 f.reads[0].resolve(snapshot('A','running',1));await failed;
 assert.equal(f.chat.polling,false);
 const retry=f.context.pollChat(true);await flush();f.complete(1,snapshot('A','complete',2));await retry;
 assert.equal(f.chat.session.status,'complete');assert.equal(f.peak,2);
});

test('rapid session changes ignore an old missing-session response and refresh only the final selection',async()=>{
 const f=fixture(),old=f.context.pollChat();await flush();
 const b=f.context.selectChat('B'),c=f.context.selectChat('C');await flush();
 f.lists[0].resolve({sessions:[]});f.reads[0].reject(Error('会话不存在'));await old;await flush();
 assert.equal(f.chat.id,'C');assert.equal(f.reads.length,2);assert.match(f.reads[1].route,/id=C&after=0/);
 f.complete(1,snapshot('C','complete',7,'Report C'));await Promise.all([b,c]);
 assert.equal(f.chat.id,'C');assert.equal(f.chat.session.status,'complete');assert.equal(f.chat.after,7);
 assert.deepEqual([...f.chat.events.keys()],[7]);assert.deepEqual(f.errors,[undefined,undefined]);
 assert.deepEqual(f.restores,['B','C','C'],'the obsolete selection must not restore the current composer again');
 assert.equal(f.peak,2);
});

test('cancelling while a poll is in flight waits for a fresh cancelled snapshot without reviving running state',async()=>{
 const f=fixture(),old=f.context.pollChat();await flush();
 const stopped=f.$('chat-stop').onclick();await flush();
 assert.equal(f.chat.busy,true);assert.equal(f.calls.filter(call=>call.route==='harness/cancel').length,1);
 f.complete(0,snapshot('A','running',1));await old;await flush();assert.deepEqual(f.renders,[]);
 f.complete(1,snapshot('A','cancelled',2));await stopped;
 assert.equal(f.chat.busy,false);assert.equal(f.chat.session.status,'cancelled');assert.equal(f.chat.tokenUsage.total,20);assert.equal(f.chat.after,2);
 assert.deepEqual(f.renders.map(row=>row.status),['cancelled']);assert.equal(f.peak,2);
});
