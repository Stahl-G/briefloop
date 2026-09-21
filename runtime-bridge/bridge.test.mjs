import test from 'node:test';
import assert from 'node:assert/strict';
import {spawn} from 'node:child_process';
import {mkdtempSync,writeFileSync,readFileSync} from 'node:fs';
import {rm} from 'node:fs/promises';
import os from 'node:os';
import path from 'node:path';
import {createInterface} from 'node:readline';
const fixtureDirectories=new WeakMap();
function bridge(t,extraEnv={}){
 const win=process.platform==='win32';
 if(win)assert.ok(process.env.BRIEFLOOP_PYTHON&&process.env.BRIEFLOOP_PROCESS_HELPER,'Set BRIEFLOOP_PYTHON and BRIEFLOOP_PROCESS_HELPER for Windows tests');
 const p=spawn(win?process.env.BRIEFLOOP_PYTHON:process.execPath,win?['-X','utf8',process.env.BRIEFLOOP_PROCESS_HELPER,process.execPath,'src/briefloop/static/runtime-bridge.mjs']:['src/briefloop/static/runtime-bridge.mjs'],{stdio:['pipe','pipe','inherit'],windowsHide:true,env:{...process.env,...extraEnv}});
 const directories=[];fixtureDirectories.set(t,directories);
 // exitCode is available before close, and separate after hooks do not express
 // this dependency. Gracefully drain the bridge and its owned processes before
 // removing their working directories, including on Windows.
 const closed=new Promise(resolve=>p.once('close',resolve));
 const frames=[],waiters=[];createInterface({input:p.stdout}).on('line',line=>{const f=JSON.parse(line);frames.push(f);for(const w of [...waiters])if(w.predicate(f)){waiters.splice(waiters.indexOf(w),1);clearTimeout(w.timer);w.resolve(f);}});
 t.after(async()=>{
  p.stdin.end();
  const force=setTimeout(()=>p.kill(),5000);
  let deadline;
  try{await Promise.race([closed,new Promise((_,reject)=>{deadline=setTimeout(()=>reject(Error('Bridge did not close before fixture cleanup')),10000)})]);}
  finally{clearTimeout(force);clearTimeout(deadline);}
  for(const directory of directories){
   assert.equal(path.dirname(path.resolve(directory)),path.resolve(os.tmpdir()));
   await rm(directory,{recursive:true,force:true,maxRetries:20,retryDelay:100});
  }
 });
 return {frames,stop:()=>p.stdin.end(),send:(id,method,params)=>p.stdin.write(JSON.stringify({id,method,params})+'\n'),wait:predicate=>{const found=frames.find(predicate);if(found)return Promise.resolve(found);return new Promise((resolve,reject)=>{const w={predicate,resolve,timer:setTimeout(()=>reject(Error('missing frame')),5000)};waiters.push(w);});}};
}
function fixture(t,body,name='cli'){const directories=fixtureDirectories.get(t);assert.ok(directories,'Create the bridge before its fixtures');const d=mkdtempSync(path.join(os.tmpdir(),'bridge-fixture-')),f=path.join(d,name);directories.push(d);writeFileSync(f,'#!/usr/bin/env node\n'+body,{mode:0o755});if(process.platform==='win32'){writeFileSync(path.join(d,'entry.cjs'),body);writeFileSync(f,'exec node "$basedir/entry.cjs" "$@"');writeFileSync(f+'.cmd','@echo off');return {path:f+'.cmd',cwd:d};}return {path:f,cwd:d};}
const rpcFake=`const rl=require('node:readline').createInterface({input:process.stdin});const send=v=>process.stdout.write(JSON.stringify(v)+'\\n');let promptId;rl.on('line',line=>{const m=JSON.parse(line);const result=r=>send({jsonrpc:'2.0',id:m.id,result:r});if(m.method==='initialize')result({agentCapabilities:{loadSession:true,promptCapabilities:{image:true}}});else if(m.method==='session/new'||m.method==='session/load')result({sessionId:'real-session',models:{availableModels:[{modelId:'test/model',name:'Test'}]}});else if(m.method==='session/set_model')result({});else if(m.method==='session/prompt'){promptId=m.id;send({method:'session/update',params:{update:{sessionUpdate:'agent_thought_chunk',content:{type:'text',text:'HIDDEN'}}}});send({id:90,method:'session/request_permission',params:{options:[{optionId:'yes',kind:'allow_once',name:'Allow once'}],toolCall:{title:'Read fixture'}}});}else if(m.id===90){if(m.result.outcome.optionId!=='yes')process.exit(2);send({method:'session/update',params:{update:{sessionUpdate:'agent_message_chunk',content:{type:'text',text:'OK'}}}});send({id:promptId,result:{stopReason:'end_turn'}});}});`;
test('ACP uses host model/session, asks permission and surfaces reasoning as its own kind',async t=>{const b=bridge(t),f=fixture(t,rpcFake);b.send(1,'list_models',{runtime_id:'kimi',...f});const list=await b.wait(x=>x.id===1);assert.ok(list.result.models.some(x=>x.id==='test/model'));b.send(2,'start',{...f,runtime_id:'kimi',execution_id:'a',prompt:'test',allow_web:true,permission:'runtime-native',model:'test/model'});assert.ok((await b.wait(x=>x.id===2)).result);const q=await b.wait(x=>x.params?.kind==='question');b.send(3,'answer',{execution_id:'a',request_id:q.params.request_id,option_id:'yes'});const end=await b.wait(x=>x.params?.kind==='end');assert.equal(end.params.status,'completed');assert.equal(b.frames.find(x=>x.params?.kind==='session').params.session_id,'real-session');assert.ok(b.frames.some(x=>x.params?.text==='OK'));assert.ok(b.frames.some(x=>x.params?.kind==='reasoning'&&x.params.text==='HIDDEN'));assert.ok(!b.frames.some(x=>x.params?.kind==='text'&&String(x.params.text).includes('HIDDEN')));});
test('Restriction refused before launching and active turn can cancel',async t=>{const b=bridge(t),f=fixture(t,rpcFake);b.send(1,'start',{...f,runtime_id:'kimi',execution_id:'deny',prompt:'x',permission:'read-only',allow_web:true});assert.ok((await b.wait(x=>x.id===1)).error);b.send(2,'start',{...f,runtime_id:'kimi',execution_id:'c',prompt:'x',permission:'runtime-native',allow_web:true});await b.wait(x=>x.params?.kind==='question');b.send(3,'cancel',{execution_id:'c'});assert.equal((await b.wait(x=>x.params?.kind==='end')).params.status,'cancelled');});
test('Claude unsuccessful result cannot become complete on zero exit',async t=>{const b=bridge(t),f=fixture(t,`process.stdin.resume();process.stdin.once('data',()=>{console.log(JSON.stringify({type:'result',is_error:true,session_id:'s'}));});`);b.send(1,'start',{...f,runtime_id:'claude',execution_id:'bad',prompt:'x',permission:'runtime-native',allow_web:true});assert.equal((await b.wait(x=>x.params?.kind==='end')).params.status,'failed');});
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
test('Electron Node mode does not escape into host metadata probes or ACP subprocesses',async t=>{
 const b=bridge(t,{ELECTRON_RUN_AS_NODE:'1'});
 const f=fixture(t,`if(process.env.ELECTRON_RUN_AS_NODE!==undefined)process.exit(2);if(process.argv[2]==='doctor'){console.log(JSON.stringify({providers:[{name:'fixture',models:['host-env-clean']}]}));process.exit(0);}`+rpcFake);
 // Wheel smoke has only the built bridge, not source catalog.json. Reasonix's
 // metadata probe exercises the same execFile path using an explicit fixture.
 b.send(1,'list_models',{runtime_id:'reasonix',...f});
 assert.deepEqual((await b.wait(x=>x.id===1)).result.models.map(x=>x.id),['default','fixture/host-env-clean']);
 b.send(2,'list_models',{runtime_id:'codebuddy',...f});
 const result=(await b.wait(x=>x.id===2)).result;
 assert.equal(result.source,'host');
 assert.ok(result.models.some(x=>x.id==='test/model'));
});

test('DeepSeek Harness grouped model choices round-trip and session/resume is negotiated',async t=>{
 const b=bridge(t);
 const fake=rpcFake.replace('loadSession:true','sessionCapabilities:{resume:{}}').replace("m.method==='session/load'","m.method==='session/resume'").replace("models:{availableModels:[{modelId:'test/model',name:'Test'}]}",`configOptions:[{id:'model',category:'model',type:'select',currentValue:JSON.stringify(['deepseek-official','deepseek-flash']),options:[{group:'deepseek-official',options:[{value:JSON.stringify(['deepseek-official','deepseek-flash']),name:'Flash'}]}]}]`).replace("m.method==='session/set_model')result({})","m.method==='session/set_config_option'){if(m.params.value!==JSON.stringify(['deepseek-official','deepseek-flash']))process.exit(3);result({});}").replace("};else if(m.method==='session/prompt')","}else if(m.method==='session/prompt')");
 const f=fixture(t,`if(JSON.stringify(process.argv.slice(2))!==JSON.stringify(['--profile','acp']))process.exit(2);`+fake,'dsh');
 b.send(1,'list_models',{runtime_id:'deepseek-harness',...f});
 assert.ok((await b.wait(x=>x.id===1)).result.models.some(m=>m.id==='deepseek-official/deepseek-flash'));
 b.send(2,'start',{...f,runtime_id:'deepseek-harness',execution_id:'dsh-resume',session_id:'saved-dsh-session',model:'deepseek-official/deepseek-flash',prompt:'continue',permission:'runtime-native',allow_web:null});
 const q=await b.wait(x=>x.params?.kind==='question');
 b.send(3,'answer',{execution_id:'dsh-resume',request_id:q.params.request_id,option_id:'yes'});
 assert.equal((await b.wait(x=>x.params?.kind==='end')).params.status,'completed');
 assert.equal(b.frames.find(x=>x.params?.kind==='session').params.session_id,'saved-dsh-session');
});

test('Antigravity stream uses explicit model/resume and does not duplicate final text or cumulative usage',async t=>{
 const b=bridge(t),f=fixture(t,`const a=process.argv.slice(2);if(a[0]==='models'){console.log('fixture-model\tFixture');process.exit(0);}if(a[a.indexOf('--print-timeout')+1]!=='87600h'||!a.includes('--disable-slash-commands')||a[a.indexOf('--conversation')+1]!=='saved-agy'||a[a.indexOf('--model')+1]!=='fixture-model')process.exit(3);let s='';process.stdin.on('data',c=>s+=c);process.stdin.on('end',()=>{const m=JSON.parse(s);if(m.event!=='user'||m.message.content!=='hello')process.exit(4);const send=x=>console.log(JSON.stringify(x));send({event:'init',conversation_id:'saved-agy'});send({event:'step_update',step_update:{step_type:'agent_response',text_delta:'OK',state:'DONE',usage:{input_tokens:12}}});send({event:'result',result:{status:'SUCCESS',response:'OK',conversation_id:'saved-agy',usage:{input_tokens:999}}});});`);
 b.send(1,'list_models',{runtime_id:'antigravity',...f});assert.ok((await b.wait(x=>x.id===1)).result.models.some(m=>m.id==='fixture-model'));
 b.send(2,'start',{...f,runtime_id:'antigravity',execution_id:'agy',session_id:'saved-agy',model:'fixture-model',prompt:'hello',permission:'runtime-native',allow_web:null});
 assert.equal((await b.wait(x=>x.params?.kind==='end')).params.status,'completed');assert.equal(b.frames.filter(x=>x.params?.kind==='text').map(x=>x.params.text).join(''),'OK');assert.equal(b.frames.filter(x=>x.params?.kind==='usage').length,1);
});
test('Antigravity zero exit with error result fails, and cancellation stops an active host',async t=>{
 const b=bridge(t),f=fixture(t,`process.stdin.resume();process.stdin.on('end',()=>{console.log(JSON.stringify({event:'result',result:{status:'ERROR',conversation_id:'bad'}}));});`);
 b.send(1,'start',{...f,runtime_id:'antigravity',execution_id:'agy-bad',prompt:'x',permission:'runtime-native',allow_web:null});assert.equal((await b.wait(x=>x.params?.execution_id==='agy-bad'&&x.params.kind==='end')).params.status,'failed');
 const slow=fixture(t,`console.log(JSON.stringify({event:'init',conversation_id:'slow'}));process.stdin.resume();setInterval(()=>{},1000);`);
 b.send(2,'start',{...slow,runtime_id:'antigravity',execution_id:'agy-stop',prompt:'x',permission:'runtime-native',allow_web:null});await b.wait(x=>x.params?.session_id==='slow');b.send(3,'cancel',{execution_id:'agy-stop'});assert.equal((await b.wait(x=>x.params?.execution_id==='agy-stop'&&x.params.kind==='end')).params.status,'cancelled');
});

test('Antigravity soft-denied tool without reply fails even when host says SUCCESS',async t=>{
 const b=bridge(t),f=fixture(t,`process.stdin.resume();process.stdin.on('end',()=>{console.log(JSON.stringify({event:'step_update',step_update:{step_type:'tool',step_index:5,state:'DONE',tool_info:{name:'view_file',error:{type:'permission_denied',message:'user denied read_file'}}}}));console.log(JSON.stringify({event:'result',result:{status:'SUCCESS',conversation_id:'denied',response:''}}));});`);
 b.send(1,'start',{...f,runtime_id:'antigravity',execution_id:'denied',prompt:'read',permission:'runtime-native',allow_web:null});assert.equal((await b.wait(x=>x.params?.kind==='end')).params.status,'failed');assert.ok(b.frames.some(x=>x.params?.kind==='tool'&&x.params.output==='user denied read_file'));
});

test('Pi RPC selects exact provider model, resumes its session and waits for agent_settled',async t=>{
 const b=bridge(t),f=fixture(t,`let effort='low';const rl=require('node:readline').createInterface({input:process.stdin});const send=x=>console.log(JSON.stringify(x));rl.on('line',l=>{const m=JSON.parse(l),ok=data=>send({type:'response',id:m.id,command:m.type,success:true,data});if(m.type==='get_available_models')ok({models:[{provider:'test',id:'flash',name:'Flash'}]});else if(m.type==='get_state')ok({sessionFile:'/tmp/pi-test.jsonl',thinkingLevel:effort});else if(m.type==='set_thinking_level'){if(m.level!=='high')process.exit(6);effort=m.level;ok({});}else if(m.type==='set_model'){if(m.provider!=='test'||m.modelId!=='flash')process.exit(3);ok({});}else if(m.type==='prompt'){if(!process.argv.includes('--no-tools')||!process.argv.includes('--no-extensions'))process.exit(5);ok({});send({type:'turn_start'});setTimeout(()=>{send({type:'message_start',message:{role:'assistant'}});send({type:'message_update',assistantMessageEvent:{type:'text_delta',delta:'Pi OK'}});send({type:'message_end',message:{role:'assistant',content:[{type:'text',text:'Pi OK'}],stopReason:'stop'}});send({type:'agent_end',messages:[]});setTimeout(()=>send({type:'agent_settled'}),50);},40);}});`);
 b.send(1,'list_models',{...f,runtime_id:'pi'});assert.ok((await b.wait(x=>x.id===1)).result.models.some(m=>m.id==='test/flash'));
 b.send(2,'start',{...f,runtime_id:'pi',execution_id:'pi',thinking:'high',host_options:{mode:'none'},session_id:'/tmp/pi-test.jsonl',model:'test/flash',prompt:'hello',permission:'runtime-native',allow_web:null});assert.equal((await b.wait(x=>x.params?.kind==='end')).params.status,'completed');assert.equal(b.frames.filter(x=>x.params?.kind==='text').map(x=>x.params.text).join(''),'Pi OK');
 assert.ok(b.frames.some(x=>x.params?.kind==='performance'&&x.params.phase==='configuration'&&x.params.thinking==='high'));
 const timing=b.frames.find(x=>x.params?.kind==='performance'&&x.params.phase==='model_message').params;
 assert.ok(timing.turn_to_first_delta_ms>=20);assert.ok(timing.turn_to_message_end_ms>=timing.duration_ms);
});

test('Claude forwards native allow and deny over open stdio',async t=>{
 const b=bridge(t),f=fixture(t,`const rl=require('node:readline').createInterface({input:process.stdin});let count=0;const send=x=>console.log(JSON.stringify(x));rl.on('line',line=>{const m=JSON.parse(line);if(m.type==='user')send({type:'control_request',request_id:'first',request:{subtype:'can_use_tool',tool_name:'Read',input:{file_path:'/fixture'}}});else if(m.type==='control_response'){const r=m.response;if(r.request_id==='first'){if(r.response.behavior!=='allow'||r.response.updatedInput.file_path!=='/fixture')process.exit(3);send({type:'control_request',request_id:'second',request:{subtype:'can_use_tool',tool_name:'Write',input:{file_path:'/fixture'}}});}else{if(r.response.behavior!=='deny')process.exit(4);send({type:'assistant',message:{content:[{type:'text',text:'Permission answered'}]}});send({type:'result',is_error:false});}}});`);
 b.send(1,'start',{...f,runtime_id:'claude',execution_id:'claude-permission',prompt:'x',permission:'runtime-native',allow_web:null,host_options:{mode:'manual'}});
 await b.wait(x=>x.params?.request_id==='first');b.send(2,'answer',{execution_id:'claude-permission',request_id:'first',option_id:'allow'});
 await b.wait(x=>x.params?.request_id==='second');b.send(3,'answer',{execution_id:'claude-permission',request_id:'second',option_id:'deny'});
 assert.equal((await b.wait(x=>x.params?.kind==='end')).params.status,'completed');
});
test('ACP accepts only an advertised permission mode before prompting',async t=>{
 const b=bridge(t),f=fixture(t,rpcFake.replace("models:{availableModels:","modes:{availableModes:[{id:'plan',name:'Plan'}]},models:{availableModels:").replace("m.method==='session/set_model'","m.method==='session/set_mode'||m.method==='session/set_model'"));
 b.send(1,'permission_options',{...f,runtime_id:'kimi'});assert.deepEqual((await b.wait(x=>x.id===1)).result.modes,[{id:'plan',name:'Plan'}]);
 b.send(2,'start',{...f,runtime_id:'kimi',execution_id:'forged-mode',prompt:'x',permission:'runtime-native',host_options:{mode:'unknown'}});
 assert.equal((await b.wait(x=>x.params?.execution_id==='forged-mode'&&x.params.kind==='end')).params.status,'failed');
 b.send(3,'start',{...f,runtime_id:'kimi',execution_id:'plan-mode',prompt:'x',permission:'runtime-native',host_options:{mode:'plan'}});
 const q=await b.wait(x=>x.params?.execution_id==='plan-mode'&&x.params.kind==='question');b.send(4,'answer',{execution_id:'plan-mode',request_id:q.params.request_id,option_id:'yes'});
 assert.equal((await b.wait(x=>x.params?.execution_id==='plan-mode'&&x.params.kind==='end')).params.status,'completed');
});

test('MiMo retains JSON execution and applies only an advertised native agent mode',async t=>{
 const b=bridge(t),f=fixture(t,`if(process.argv[2]!=='acp'){if(!process.argv.includes('--agent')||!process.argv.includes('plan'))process.exit(3);process.stdin.resume();process.stdin.on('end',()=>{console.log(JSON.stringify({type:'text',part:{text:'MiMo mode OK'}}));console.log(JSON.stringify({type:'step_finish',part:{tokens:{input:1}}}));});}else{`+rpcFake.replace("models:{availableModels:","modes:{availableModes:[{id:'plan',name:'Plan'}]},models:{availableModels:")+`}`);
 b.send(1,'start',{...f,runtime_id:'mimo',execution_id:'mimo-mode',prompt:'hello',permission:'runtime-native',host_options:{mode:'plan'}});
 assert.equal((await b.wait(x=>x.params?.kind==='end')).params.status,'completed');
 assert.ok(b.frames.some(x=>x.params?.text==='MiMo mode OK'));
});


test('stdin EOF reaps an in-flight metadata probe, not only active turns',async t=>{
 const b=bridge(t);
 const f=fixture(t,`require('node:fs').writeFileSync(require('node:path').join(__dirname,'probe.pid'),String(process.pid));setInterval(()=>{},1000);`);
 b.send(1,'list_models',{...f,runtime_id:'reasonix'});
 let pid;
 for(let i=0;i<100&&!pid;i++){
  try{pid=Number(readFileSync(path.join(f.cwd,'probe.pid'),'utf8'));}catch{}
  if(!pid)await new Promise(r=>setTimeout(r,20));
 }
 assert.ok(pid,'metadata probe really started');
 b.stop();
 const alive=()=>{try{process.kill(pid,0);return true;}catch{return false;}};
 for(let i=0;i<150&&alive();i++)await new Promise(r=>setTimeout(r,20));
 assert.equal(alive(),false,'owned metadata process must exit after bridge EOF');
});

test('Antigravity partial timeout warning cannot count as successful completion',async t=>{
 const b=bridge(t),f=fixture(t,`process.stdin.resume();process.stdin.on('end',()=>{console.error('warning: print timeout reached; returning partial output');console.log(JSON.stringify({event:'result',result:{status:'SUCCESS',conversation_id:'partial',response:'Still working'}}));});`);
 b.send(1,'start',{...f,runtime_id:'antigravity',execution_id:'partial',prompt:'x',permission:'runtime-native',allow_web:null});
 assert.equal((await b.wait(x=>x.params?.kind==='end')).params.status,'failed');
});

test('Codex catalog keeps new host models and skips both hidden spellings',async t=>{
 const b=bridge(t),f=fixture(t,`console.log(JSON.stringify({models:[
  {slug:'new-host-model',display_name:'New host model',visibility:'list'},
  {slug:'internal-reserve',visibility:'hide'},
  {slug:'internal-review',visibility:'hidden'},
  {slug:'legacy-visible'}]}));`);
 b.send(1,'list_models',{runtime_id:'codex',...f});
 const {result}=await b.wait(x=>x.id===1);
 assert.equal(result.source,'host');
 assert.deepEqual(result.models.map(x=>x.id),['default','new-host-model','legacy-visible']);
});
const zcodeFake=(tail)=>`const args=process.argv.slice(2);const flag=n=>{const i=args.indexOf(n);return i<0?null:args[i+1]};
if(flag('--output-format')!=='stream-json')process.exit(2);
const sid='sess_fixture';const ev=(type,payload)=>process.stdout.write(JSON.stringify({type,payload,sessionId:sid,seq:1})+'\\n');
${tail}`;
test('ZCode always sends build by default and yolo only after an explicit selection',async t=>{
 for(const options of [{},{mode:'native'},{mode:'yolo'}]){
  const expected=options.mode==='yolo'?'yolo':'build';
  const b=bridge(t),f=fixture(t,zcodeFake(`
   if(flag('--mode')!==${JSON.stringify(expected)})process.exit(2);
   ev('turn.completed',{response:'OK',resultType:'success'});`));
  b.send(1,'start',{...f,runtime_id:'zcode',execution_id:'mode',prompt:'x',host_options:options,permission:'runtime-native',allow_web:null});
  assert.equal((await b.wait(x=>x.params?.kind==='end')).params.status,'completed');
 }
});
test('ZCode maps its session stream and keeps reasoning out of the answer',async t=>{
 const b=bridge(t),f=fixture(t,zcodeFake(`
 if(flag('--prompt')!=='continue'||flag('--mode')!=='build'||flag('--resume')!=='sess_saved')process.exit(2);
 ev('model.streaming',{kind:'reasoning_delta',delta:'HIDDEN'});
 ev('model.streaming',{kind:'tool_call',toolCallId:'call_1',toolName:'Read',input:{file_path:'probe.txt'}});
 ev('tool.updated',{kind:'result',toolCallId:'call_1',result:{success:true,content:'probe'}});
 ev('model.streaming',{kind:'text_delta',delta:'OK'});
 ev('turn.completed',{response:'OK',resultType:'success',usage:{inputTokens:10,outputTokens:2}});
 ev('result',{});`));
 b.send(1,'start',{...f,runtime_id:'zcode',execution_id:'z',prompt:'continue',session_id:'sess_saved',host_options:{mode:'build'},permission:'runtime-native',allow_web:null});
 assert.equal((await b.wait(x=>x.params?.kind==='end')).params.status,'completed');
 assert.equal(b.frames.find(x=>x.params?.kind==='session').params.session_id,'sess_fixture');
 assert.ok(b.frames.some(x=>x.params?.kind==='reasoning'&&x.params.text==='HIDDEN'));
 assert.ok(!b.frames.some(x=>x.params?.kind==='text'&&String(x.params.text).includes('HIDDEN')));
 assert.ok(b.frames.some(x=>x.params?.kind==='text'&&x.params.text==='OK'));
 const tool=b.frames.filter(x=>x.params?.kind==='tool').at(-1).params;
 assert.equal(tool.name,'Read');assert.equal(tool.status,'completed');assert.equal(tool.output,'probe');
 assert.equal(b.frames.find(x=>x.params?.kind==='usage').params.usage.outputTokens,2);
});
test('ZCode plan mode that only files a plan is not a completed answer',async t=>{
 const b=bridge(t),f=fixture(t,zcodeFake(`
 if(flag('--mode')!=='plan')process.exit(2);
 ev('model.streaming',{kind:'tool_call',toolCallId:'call_1',toolName:'ExitPlanMode',input:{plan:'x'}});
 ev('tool.updated',{kind:'error',toolCallId:'call_1',error:{message:'headless cannot approve'}});
 ev('turn.completed',{response:'',resultType:'success',usage:{}});`));
 b.send(1,'start',{...f,runtime_id:'zcode',execution_id:'plan',prompt:'x',host_options:{mode:'plan'},permission:'runtime-native',allow_web:null});
 const end=await b.wait(x=>x.params?.kind==='end');
 assert.equal(end.params.status,'failed');
 assert.match(end.params.error,/没有给出回答/);
 assert.equal(b.frames.filter(x=>x.params?.kind==='tool').at(-1).params.status,'failed');
});
test('ZCode refuses a model choice it cannot apply and reports the configured one',async t=>{
 const b=bridge(t),f=fixture(t,zcodeFake(`process.exit(2);`));
 b.send(1,'list_models',{runtime_id:'zcode',...f});
 const list=await b.wait(x=>x.id===1);
 assert.equal(list.result.source,'host_default_only');
 assert.deepEqual(list.result.models.map(x=>x.id),['default']);
 b.send(2,'start',{...f,runtime_id:'zcode',execution_id:'model',prompt:'x',model:'glm-5.3',permission:'runtime-native',allow_web:null});
 const end=await b.wait(x=>x.params?.kind==='end');
 assert.equal(end.params.status,'failed');
 assert.match(end.params.error,/不接受模型参数/);
});
