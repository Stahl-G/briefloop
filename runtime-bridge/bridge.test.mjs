import test from 'node:test';
import assert from 'node:assert/strict';
import {spawn} from 'node:child_process';
import {mkdtempSync,writeFileSync,rmSync} from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import {createInterface} from 'node:readline';
function bridge(t){
 const win=process.platform==='win32';
 if(win)assert.ok(process.env.BRIEFLOOP_PYTHON&&process.env.BRIEFLOOP_PROCESS_HELPER,'Set BRIEFLOOP_PYTHON and BRIEFLOOP_PROCESS_HELPER for Windows tests');
 const p=spawn(win?process.env.BRIEFLOOP_PYTHON:process.execPath,win?['-X','utf8',process.env.BRIEFLOOP_PROCESS_HELPER,process.execPath,'src/briefloop/static/runtime-bridge.mjs']:['src/briefloop/static/runtime-bridge.mjs'],{stdio:['pipe','pipe','inherit'],windowsHide:true});
 const frames=[],waiters=[];createInterface({input:p.stdout}).on('line',line=>{const f=JSON.parse(line);frames.push(f);for(const w of [...waiters])if(w.predicate(f)){waiters.splice(waiters.indexOf(w),1);clearTimeout(w.timer);w.resolve(f);}});
 t.after(async()=>{if(p.exitCode!==null)return;const stopped=new Promise(resolve=>p.once('close',resolve));p.kill();await stopped;});
 return {frames,send:(id,method,params)=>p.stdin.write(JSON.stringify({id,method,params})+'\n'),wait:predicate=>{const found=frames.find(predicate);if(found)return Promise.resolve(found);return new Promise((resolve,reject)=>{const w={predicate,resolve,timer:setTimeout(()=>reject(Error('missing frame')),5000)};waiters.push(w);});}};
}
function fixture(t,body,name='cli'){const d=mkdtempSync(path.join(os.tmpdir(),'bridge-fixture-')),f=path.join(d,name);writeFileSync(f,'#!/usr/bin/env node\n'+body,{mode:0o755});t.after(()=>{assert.equal(path.dirname(path.resolve(d)),path.resolve(os.tmpdir()));rmSync(d,{recursive:true,force:true,maxRetries:20,retryDelay:100});});if(process.platform==='win32'){writeFileSync(path.join(d,'entry.cjs'),body);writeFileSync(f,'exec node "$basedir/entry.cjs" "$@"');writeFileSync(f+'.cmd','@echo off');return {path:f+'.cmd',cwd:d};}return {path:f,cwd:d};}
const rpcFake=`const rl=require('node:readline').createInterface({input:process.stdin});const send=v=>process.stdout.write(JSON.stringify(v)+'\\n');let promptId;rl.on('line',line=>{const m=JSON.parse(line);const result=r=>send({jsonrpc:'2.0',id:m.id,result:r});if(m.method==='initialize')result({agentCapabilities:{loadSession:true,promptCapabilities:{image:true}}});else if(m.method==='session/new'||m.method==='session/load')result({sessionId:'real-session',models:{availableModels:[{modelId:'test/model',name:'Test'}]}});else if(m.method==='session/set_model')result({});else if(m.method==='session/prompt'){promptId=m.id;send({method:'session/update',params:{update:{sessionUpdate:'agent_thought_chunk',content:{type:'text',text:'HIDDEN'}}}});send({id:90,method:'session/request_permission',params:{options:[{optionId:'yes',kind:'allow_once',name:'Allow once'}],toolCall:{title:'Read fixture'}}});}else if(m.id===90){if(m.result.outcome.optionId!=='yes')process.exit(2);send({method:'session/update',params:{update:{sessionUpdate:'agent_message_chunk',content:{type:'text',text:'OK'}}}});send({id:promptId,result:{stopReason:'end_turn'}});}});`;
test('ACP uses host model/session, asks permission and surfaces reasoning as its own kind',async t=>{const b=bridge(t),f=fixture(t,rpcFake);b.send(1,'list_models',{runtime_id:'kimi',...f});const list=await b.wait(x=>x.id===1);assert.ok(list.result.models.some(x=>x.id==='test/model'));b.send(2,'start',{...f,runtime_id:'kimi',execution_id:'a',prompt:'test',allow_web:true,permission:'runtime-native',model:'test/model'});assert.ok((await b.wait(x=>x.id===2)).result);const q=await b.wait(x=>x.params?.kind==='question');b.send(3,'answer',{execution_id:'a',request_id:q.params.request_id,option_id:'yes'});const end=await b.wait(x=>x.params?.kind==='end');assert.equal(end.params.status,'completed');assert.equal(b.frames.find(x=>x.params?.kind==='session').params.session_id,'real-session');assert.ok(b.frames.some(x=>x.params?.text==='OK'));assert.ok(b.frames.some(x=>x.params?.kind==='reasoning'&&x.params.text==='HIDDEN'));assert.ok(!b.frames.some(x=>x.params?.kind==='text'&&String(x.params.text).includes('HIDDEN')));});
test('Restriction refused before launching and active turn can cancel',async t=>{const b=bridge(t),f=fixture(t,rpcFake);b.send(1,'start',{...f,runtime_id:'kimi',execution_id:'deny',prompt:'x',permission:'read-only',allow_web:true});assert.ok((await b.wait(x=>x.id===1)).error);b.send(2,'start',{...f,runtime_id:'kimi',execution_id:'c',prompt:'x',permission:'runtime-native',allow_web:true});await b.wait(x=>x.params?.kind==='question');b.send(3,'cancel',{execution_id:'c'});assert.equal((await b.wait(x=>x.params?.kind==='end')).params.status,'cancelled');});
test('Claude unsuccessful result cannot become complete on zero exit',async t=>{const b=bridge(t),f=fixture(t,`process.stdin.resume();process.stdin.on('end',()=>{console.log(JSON.stringify({type:'result',is_error:true,session_id:'s'}));});`);b.send(1,'start',{...f,runtime_id:'claude',execution_id:'bad',prompt:'x',permission:'runtime-native',allow_web:true});assert.equal((await b.wait(x=>x.params?.kind==='end')).params.status,'failed');});
for(const name of ['hermes-acp','hermes'])test(`Hermes ${name} uses its matching ACP entry point for discovery and execution`,async t=>{
 const b=bridge(t),args=name==='hermes-acp'?[]:['acp'];
 const f=fixture(t,`if(JSON.stringify(process.argv.slice(2))!==${JSON.stringify(JSON.stringify(args))})process.exit(2);`+rpcFake,name);
 b.send(1,'list_models',{runtime_id:'hermes',...f});
 assert.ok((await b.wait(x=>x.id===1)).result.models.some(x=>x.id==='test/model'));
 b.send(2,'start',{...f,runtime_id:'hermes',execution_id:name,prompt:'test',allow_web:null,permission:'runtime-native'});
 const q=await b.wait(x=>x.params?.kind==='question');
 b.send(3,'answer',{execution_id:name,request_id:q.params.request_id,option_id:'yes'});
 assert.equal((await b.wait(x=>x.params?.kind==='end')).params.status,'completed');
});
test('Reasonix discovers selectable provider/model IDs and passes the selected ID to ACP',async t=>{
 const b=bridge(t),f=fixture(t,`if(process.argv[2]==='doctor'){console.log(JSON.stringify({providers:[{name:'probe',kind:'openai',model:'flash',models:['flash','pro','flash']},{name:'legacy',model:'old-model'}]}));process.exit(0);}if(JSON.stringify(process.argv.slice(2))!==JSON.stringify(['acp','-model','probe/pro']))process.exit(2);`+rpcFake);
 b.send(1,'list_models',{runtime_id:'reasonix',...f});
 assert.deepEqual((await b.wait(x=>x.id===1)).result.models.map(x=>x.id),['default','probe/flash','probe/pro','legacy']);
 b.send(2,'start',{...f,runtime_id:'reasonix',execution_id:'reasonix',prompt:'test',model:'probe/pro',allow_web:null,permission:'runtime-native'});
 const q=await b.wait(x=>x.params?.kind==='question');
 b.send(3,'answer',{execution_id:'reasonix',request_id:q.params.request_id,option_id:'yes'});
 assert.equal((await b.wait(x=>x.params?.kind==='end')).params.status,'completed');
});
test('ACP resume retains supplied ID and does not replay history as new output',async t=>{
 const b=bridge(t),f=fixture(t,`const rl=require('node:readline').createInterface({input:process.stdin});const send=v=>process.stdout.write(JSON.stringify(v)+'\\n');const text=t=>send({method:'session/update',params:{update:{sessionUpdate:'agent_message_chunk',content:{type:'text',text:t}}}});rl.on('line',line=>{const m=JSON.parse(line);const result=r=>send({id:m.id,result:r});if(m.method==='initialize')result({agentCapabilities:{loadSession:true}});else if(m.method==='session/load'){text('OLD HISTORY');result({});}else if(m.method==='session/prompt'){if(m.params.sessionId!=='saved-native-session')process.exit(2);text('NEW REPLY');result({stopReason:'end_turn'});}});`);
 b.send(1,'start',{...f,runtime_id:'kimi',execution_id:'resume',session_id:'saved-native-session',prompt:'continue',permission:'runtime-native',allow_web:null});
 assert.equal((await b.wait(x=>x.params?.kind==='end')).params.status,'completed');
 assert.equal(b.frames.filter(x=>x.params?.kind==='text').map(x=>x.params.text).join(''),'NEW REPLY');
});
