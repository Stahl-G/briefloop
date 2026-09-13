// BriefLoop protocol glue. Upstream Apache helpers: third_party/open-design/NOTICE.md.
import {spawn, execFile} from 'node:child_process';
import {accessSync, constants, readFileSync, existsSync} from 'node:fs';
import {homedir} from 'node:os';
import path from 'node:path';
import {promisify} from 'node:util';
import {createInterface} from 'node:readline';
import {createJsonLineStream} from '../third_party/open-design/core/json-line-stream.js';
import {buildAcpSessionNewParams, buildPromptBlocks} from '../third_party/open-design/acp/session-params.js';
import {normalizeModels, findModelConfigOption, detectAcpModels} from '../third_party/open-design/acp/models.js';
import catalog from './catalog.json';
import {sanitizeCustomModel} from '../third_party/open-design/runtime-models/models.js';
import {loadMmdRouteModels,loadMmdRouteLaunchEnv} from '../third_party/open-design/runtime-models/mmd-routes.js';
import {parseCodexDebugModels} from '../third_party/open-design/runtime-models/codex-models.js';
import {parseOpenCodeModels} from '../third_party/open-design/runtime-models/opencode-models.js';
import fallbackModels from '../third_party/open-design/runtime-models/fallbacks.json';
const rawExec = promisify(execFile);
function exec(bin:string,args:string[],options:any):any {
 if(process.platform!=='win32')return rawExec(bin,args,options);
 if(!env.BRIEFLOOP_PYTHON||!env.BRIEFLOOP_PROCESS_HELPER)throw Error('Windows process owner is unavailable');
 return rawExec(env.BRIEFLOOP_PYTHON,['-X','utf8',env.BRIEFLOOP_PROCESS_HELPER,bin,...args],{...options,windowsHide:true});
}
const acpArgs = {codebuddy:['--acp'],kimi:['acp'],hermes:['acp'],reasonix:['acp'],kilo:['acp'],kiro:['acp'],vibe:[]};
function acpArguments(id:string,bin:string):string[]{
 // The dedicated Hermes entry point starts ACP directly and avoids CLI/plugin startup.
 return id==='hermes'&&/^hermes-acp(?:\.(?:exe|cmd|bat))?$/i.test(path.basename(bin))?[]:[...acpArgs[id]];
}
const active = new Map<string, any>();
const defaults = [{id:'default',label:'宿主默认模型'}];
function claudeConfiguredModel(){
  // Claude Code keeps its default model alias and any alias→model mapping in its own
  // settings; answering a configured user with a bare "default" is not acceptable.
  try{
    const file=JSON.parse(readFileSync(path.join(homedir(),'.claude','settings.json'),'utf8'));
    const alias=typeof file?.model==='string'?file.model.trim():'';
    const configured=file?.env&&typeof file.env==='object'?file.env:{};
    const merged={...configured,...env};
    const concrete=(alias?merged['ANTHROPIC_DEFAULT_'+alias.toUpperCase()+'_MODEL']:'')||merged.ANTHROPIC_MODEL||'';
    return {alias,concrete:typeof concrete==='string'?concrete.trim():''};
  }catch{return {alias:'',concrete:''}}
}
function hostDefaultLabel(id:string){
  if(id!=='claude')return defaults[0].label;
  const {alias,concrete}=claudeConfiguredModel();
  if(concrete&&alias)return `默认：${concrete}（别名 ${alias}）`;
  if(concrete)return `默认：${concrete}`;
  if(alias)return `默认：${alias}`;
  return defaults[0].label;
}
function hostDefaults(id:string){return [{id:'default',label:hostDefaultLabel(id)}]}
const env = {...process.env}; delete env.CLAUDECODE;
if(process.platform==='win32'){env.PATH=process.env.PATH||process.env.Path||'';delete env.Path;}
const dirs = [...(env.PATH||'').split(path.delimiter), path.join(homedir(),'.local/bin'),path.join(homedir(),'.kimi-code/bin'),path.join(homedir(),'.opencode/bin'),path.join(homedir(),'.npm-global/bin'),path.join(homedir(),'.bun/bin'),path.join(homedir(),'.cargo/bin'),path.join(homedir(),'.dsh/bin'),'/opt/homebrew/bin','/usr/local/bin'];
if(process.platform==='win32'&&env.APPDATA)dirs.push(path.join(env.APPDATA,'npm'));
env.PATH=[...new Set(dirs)].join(path.delimiter);
function findBin(def:any, custom?:string) {const extensions=process.platform==='win32'?['.exe','.cmd','.bat','']:[''];for(const f of custom?[path.resolve(custom)]:def.bins.flatMap((b:string)=>dirs.flatMap(d=>extensions.map(e=>path.join(d,b+e))))) {try {accessSync(f,constants.X_OK);return f;} catch {}}return null;}
function defFor(id:string) {const d=catalog.find(d=>d.id===id); if(!d)throw Error('Unknown runtime: '+id);return d;}
function wire(value:any){process.stdout.write(JSON.stringify(value)+'\n');}
function emit(id:string,kind:string,data:any={}){const state=active.get(id);if(state&&((kind==='text'&&data.text?.trim())||kind==='tool'))state.publicActivity=true;wire({method:'event',params:{execution_id:id,kind,...data}});}
function protocol(id:string){return id in acpArgs?'acp':id==='claude'?'claude-stream-json':id==='mimo'?'opencode-json':id==='codex'||id==='opencode'?'native-manager':null;}
function capabilities(id:string){const p=protocol(id);return {chat:!!p,cancel:!!p,resume:p==='acp'?'negotiated':p==='claude-stream-json'||p==='opencode-json',images:p==='acp'?'negotiated':p==='claude-stream-json',questions:p==='acp',steer:false,read_only:false,network_control:false,permission_modes:['runtime-native']};}
function terminate(child:any){if(!child?.pid)return;if(process.platform==='win32'){child.kill();return;}try{process.kill(-child.pid,'SIGTERM');}catch{try{child.kill('SIGTERM');}catch{}}setTimeout(()=>{try{process.kill(-child.pid,'SIGKILL');}catch{}},1200).unref();}
function launch(bin:string,args:string[],cwd:string,childEnv:any=env){
 if(process.platform==='win32'){
  if(!env.BRIEFLOOP_PYTHON||!env.BRIEFLOOP_PROCESS_HELPER)throw Error('Windows process owner is unavailable');
  return spawn(env.BRIEFLOOP_PYTHON,['-X','utf8',env.BRIEFLOOP_PROCESS_HELPER,bin,...args],{cwd,env:childEnv,stdio:['pipe','pipe','pipe'],windowsHide:true});
 }
 return spawn(bin,args,{cwd,env:childEnv,stdio:['pipe','pipe','pipe'],detached:true});
}
function connect(bin:string,args:string[],cwd:string,onUpdate:(v:any)=>void,onRequest:(v:any,reply:(r:any)=>void)=>void){
 const child=launch(bin,args,cwd);let seq=0;const pending=new Map();
 const send=(v:any)=>child.stdin.write(JSON.stringify(v)+'\n');
 const parser=createJsonLineStream((m:any)=>{if(m.method){if(m.id!==undefined)onRequest(m,(result)=>send({jsonrpc:'2.0',id:m.id,result}));else onUpdate(m);}else if(pending.has(m.id)){const p=pending.get(m.id);pending.delete(m.id);clearTimeout(p.timer);m.error?p.reject(Error(m.error.message||'ACP error')):p.resolve(m.result);}});
 child.stdout.setEncoding('utf8');child.stdout.on('data',c=>parser.feed(c));child.stderr.resume();
 const fail=(e:any)=>{for(const p of pending.values()){clearTimeout(p.timer);p.reject(e);}pending.clear();};
 child.on('error',fail);child.stdin.on('error',fail);child.on('close',(code)=>{parser.flush();fail(Error('ACP exited before response: '+code));});
 return {child,notify:(method:string,params:any)=>send({jsonrpc:'2.0',method,params}),call:(method:string,params:any,timeout=20000)=>new Promise<any>((resolve,reject)=>{const id=++seq;const timer=timeout>0?setTimeout(()=>{pending.delete(id);reject(Error(method+' timed out'));},timeout):null;pending.set(id,{resolve,reject,timer});send({jsonrpc:'2.0',id,method,params});})};
}
async function handshake(conn:any,p:any){const init=await conn.call('initialize',{protocolVersion:1,clientCapabilities:{fs:{readTextFile:false,writeTextFile:false},terminal:false},clientInfo:{name:'briefloop',version:'1'}});if(p.session_id&&!init.agentCapabilities?.loadSession)throw Error('Host does not advertise session/load');const session=await conn.call(p.session_id?'session/load':'session/new',{...buildAcpSessionNewParams(p.cwd),...(p.session_id?{sessionId:p.session_id}:{})});return {init,session};}
async function windowsAcpModels(bin:string,args:string[],cwd:string){
 const conn=connect(bin,args,cwd,()=>{},(_m,reply)=>reply({outcome:{outcome:'cancelled'}}));
 try{const {session}=await handshake(conn,{cwd});return normalizeModels(session.models,defaults[0],session.configOptions);}
 finally{terminate(conn.child);}
}
async function discover(p:any){return await Promise.all(catalog.filter(d=>d.id!=='byok-opencode').map(async d=>{const bin=findBin(d,p.paths?.[d.id]);if(!bin)return {...d,path:null,installed:false,status:'not_installed',capabilities:capabilities(d.id)};let version=null,error=null;try{const r=await exec(bin,['--version'],{env,timeout:5000,maxBuffer:16384});version=r.stdout.trim().split('\n')[0].slice(0,160);}catch{error='Version probe failed';}const impl=protocol(d.id);return {...d,path:bin,installed:true,version,status:impl?'detected':'not_integrated',protocol:impl,implemented:!!impl,error,capabilities:capabilities(d.id)};}));}
async function listModels(p:any){const d=defFor(p.runtime_id),bin=findBin(d,p.path);if(!bin)throw Error('Runtime not installed');if(p.runtime_id==='reasonix'){
 const r=await exec(bin,['doctor','--json'],{env,cwd:p.cwd||process.cwd(),timeout:10000,maxBuffer:1024*1024});const d=JSON.parse(r.stdout);
 return {models:[...defaults,...(d.providers||[]).filter(x=>typeof x.name==='string').flatMap(x=>{
  // New Reasonix versions address each configured model as provider/model.
  const models=Array.isArray(x.models)?[...new Set(x.models.filter(m=>typeof m==='string'&&m.trim()))]:[];
  return models.length?models.map(model=>({id:x.name+'/'+model,label:x.name+' · '+model,provider:x.kind||'configured',model_id:model})):[{id:x.name,label:x.name+(x.model?' · '+x.model:''),provider:x.kind||'configured',model_id:x.model}];
 })],source:'native_config',note:'Models declared by the host; account availability is checked by a model call.'};
 }
 const fallback=[...hostDefaults(p.runtime_id),...(fallbackModels[p.runtime_id]||[])];
 if(p.runtime_id==='claude'){const routed=await loadMmdRouteModels(env,fallback);return {models:routed||fallback,source:routed?'local_routes':'builtin_hints',note:'内置选项与已配置路由；可手动输入其他模型 ID。'};}
 try{
  if(p.runtime_id==='codex'){const r=await exec(bin,['debug','models'],{env,timeout:5000,maxBuffer:4*1024*1024});const models=parseCodexDebugModels(r.stdout);return {models:models||fallback,source:models?'host':'builtin_hints'};}
  if(['mimo','opencode'].includes(p.runtime_id)){const r=await exec(bin,['models','--verbose'],{env,timeout:20000,maxBuffer:8*1024*1024});const models=parseOpenCodeModels(r.stdout);return {models:models||fallback,source:models?'host':'builtin_hints'};}
  if(p.runtime_id in acpArgs){const args=acpArguments(p.runtime_id,bin);const models=process.platform==='win32'?await windowsAcpModels(bin,args,p.cwd||process.cwd()):await detectAcpModels({bin,args,cwd:p.cwd||process.cwd(),env,timeoutMs:15000,defaultModelOption:defaults[0],clientName:'briefloop-models'});const live=models.some(m=>m.id!=='default');return {models:live?models:fallback,source:live?'host':'builtin_hints'};}
 }catch{return {models:fallback,source:'builtin_hints',diagnostic:'宿主目录读取失败，已显示内置建议；也可直接输入模型 ID。'};}
 return {models:fallback,source:'builtin_hints'};
}

function validate(p:any){if(p.model&&!sanitizeCustomModel(p.model))throw Error('Invalid model ID');if(!p.execution_id||!p.cwd||typeof p.prompt!=='string')throw Error('execution_id, cwd and prompt required');if(active.has(p.execution_id))throw Error('Execution already active');if((p.permission||'runtime-native')!=='runtime-native')throw Error('This runtime cannot enforce '+p.permission+'; use runtime-native or a restricted native manager');if(p.allow_web===false)throw Error('This runtime cannot enforce network disabled; enable host-native network access or choose a native manager');const d=defFor(p.runtime_id),bin=findBin(d,p.path);if(!bin)throw Error('Runtime not installed');if(!protocol(d.id)||protocol(d.id)==='native-manager')throw Error('Runtime execution belongs to native manager or is not integrated');return bin;}
async function runAcp(p:any,state:any){let sessionId;const args=acpArguments(p.runtime_id,state.bin);if(p.runtime_id==='reasonix'&&p.model&&p.model!=='default')args.push('-model',p.model);const conn=connect(state.bin,args,p.cwd,(m)=>{if(m.method!=='session/update'||!state.promptStarted)return;const u=m.params?.update||{};if(u.sessionUpdate==='agent_message_chunk'&&u.content?.type==='text')emit(p.execution_id,'text',{text:u.content.text,delta:true});else if(u.sessionUpdate==='agent_thought_chunk'&&u.content?.type==='text')emit(p.execution_id,'reasoning',{text:u.content.text,delta:true});else if(['tool_call','tool_call_update'].includes(u.sessionUpdate)&&!['think','thinking','reasoning'].includes(u.kind))emit(p.execution_id,'tool',{id:u.toolCallId,name:u.title||u.kind||'Tool',status:u.status,input:u.rawInput,output:u.rawOutput});else if(u.sessionUpdate==='usage_update')emit(p.execution_id,'usage',{usage:u.usage||u});},(m,reply)=>{if(m.method==='session/request_permission'){const id=String(m.id);state.questions.set(id,{reply,options:m.params?.options||[]});emit(p.execution_id,'question',{request_id:id,type:'permission',title:m.params?.toolCall?.title||'Runtime permission',options:m.params?.options||[]});}else {reply({error:'Client method unsupported'});}});
 state.child=conn.child;state.cancel=()=>{if(sessionId)conn.notify('session/cancel',{sessionId});terminate(conn.child);};
 try{const {init,session}=await handshake(conn,p);sessionId=p.session_id||session.sessionId;if(!sessionId)throw Error('No session ID returned');emit(p.execution_id,'session',{session_id:sessionId,capabilities:init.agentCapabilities||{}});
 if(p.model&&p.model!=='default'&&p.runtime_id!=='reasonix'){const cfg=findModelConfigOption(session.configOptions);await conn.call(cfg?'session/set_config_option':'session/set_model',cfg?{sessionId,configId:cfg.configId,value:p.model}:{sessionId,modelId:p.model});}
 const blocks=buildPromptBlocks(p.prompt,[]);if(p.images?.length&&!init.agentCapabilities?.promptCapabilities?.image)throw Error('Host does not advertise image input');for(const image of p.images||[]){const f=typeof image==='string'?image:image.path;const mime=({'.png':'image/png','.jpg':'image/jpeg','.jpeg':'image/jpeg','.webp':'image/webp'})[path.extname(f).toLowerCase()];if(!mime)throw Error('Unsupported image format');const data=readFileSync(f);if(data.length>20*1024*1024)throw Error('Image exceeds 20 MiB');blocks.push({type:'image',mimeType:mime,data:data.toString('base64')});}
 state.promptStarted=true;const result=await conn.call('session/prompt',{sessionId,prompt:blocks},p.timeout_ms||0);if(result?.usage)emit(p.execution_id,'usage',{usage:result.usage});if(result?.stopReason==='cancelled')state.cancelled=true;
 }finally{terminate(conn.child);}}
async function runStream(p:any,state:any){const claude=p.runtime_id==='claude';let args=claude?['-p','--input-format','stream-json','--output-format','stream-json','--verbose']:['run','--format','json'];if(p.model&&p.model!=='default')args.push('--model',p.model);if(p.session_id)args.push(claude?'--resume':'--session',p.session_id);if(claude&&p.web_tools===true)args.push('--allowedTools','WebSearch','WebFetch');if(!claude&&p.images?.length)throw Error('MiMo direct image transport not verified');const route=claude?await loadMmdRouteLaunchEnv(env,p.model):null;const child=launch(state.bin,args,p.cwd,route?{...env,...route}:env);state.child=child;state.cancel=()=>terminate(child);let resultSeen=false,lastSession=null;
 await new Promise<void>((resolve,reject)=>{const timer=p.timeout_ms?setTimeout(()=>{terminate(child);reject(Error('Runtime turn timed out'));},p.timeout_ms):null;const parser=createJsonLineStream((m:any)=>{const sid=m.session_id||m.sessionID;if(sid&&sid!==lastSession){lastSession=sid;emit(p.execution_id,'session',{session_id:sid});}if(claude){if(m.type==='assistant'){for(const b of m.message?.content||[]){if(b.type==='text')emit(p.execution_id,'text',{text:b.text,delta:true});if(b.type==='thinking'&&b.thinking)emit(p.execution_id,'reasoning',{text:b.thinking,delta:true});if(b.type==='tool_use'&&!/^(think|thinking|reasoning)$/i.test(b.name))emit(p.execution_id,'tool',{id:b.id,name:b.name,status:'running',input:b.input});}}if(m.type==='user')for(const b of m.message?.content||[])if(b.type==='tool_result')emit(p.execution_id,'tool',{id:b.tool_use_id,status:b.is_error?'failed':'completed',output:b.content});if(m.type==='result'){resultSeen=true;if(m.usage)emit(p.execution_id,'usage',{usage:m.usage});if(m.is_error){reject(Error('Host reported unsuccessful result'));terminate(child);}}}else{const part=m.part||{};if(m.type==='text')emit(p.execution_id,'text',{text:part.text||m.text||'',delta:true});if(m.type==='reasoning'||part.type==='reasoning')emit(p.execution_id,'reasoning',{text:part.text||m.text||'',delta:true});if(m.type==='tool_use'&&!/^(think|thinking|reasoning)$/i.test(part.tool))emit(p.execution_id,'tool',{id:part.callID,name:part.tool,status:part.state?.status,input:part.state?.input,output:part.state?.output});if(m.type==='step_finish'){resultSeen=true;emit(p.execution_id,'usage',{usage:part.tokens||{},cost:part.cost});}if(m.type==='error'){reject(Error([m.error?.name||'Host error',m.error?.data?.statusCode?'HTTP '+m.error.data.statusCode:''].filter(Boolean).join(' · ')));terminate(child);}}});child.stdout.setEncoding('utf8');child.stdout.on('data',c=>parser.feed(c));child.stderr.resume();child.on('error',e=>{clearTimeout(timer);reject(e);});child.stdin.on('error',()=>{});child.on('close',code=>{clearTimeout(timer);parser.flush();if(state.cancelled)resolve();else if(code===0&&resultSeen)resolve();else reject(Error('Runtime exited without successful result (code '+code+')'));});
 if(claude){const content:any[]=[{type:'text',text:p.prompt}];for(const img of p.images||[]){const f=typeof img==='string'?img:img.path;const mime=({'.png':'image/png','.jpg':'image/jpeg','.jpeg':'image/jpeg','.webp':'image/webp'})[path.extname(f).toLowerCase()];if(!mime){terminate(child);reject(Error('Unsupported image'));return;}const data=readFileSync(f);if(data.length>20*1024*1024){terminate(child);reject(Error('Image exceeds 20 MiB'));return;}content.push({type:'image',source:{type:'base64',media_type:mime,data:data.toString('base64')}});}child.stdin.end(JSON.stringify({type:'user',message:{role:'user',content}})+'\n');}else child.stdin.end(p.prompt);});}
async function execute(p:any,state:any){try{if(state.cancelled)return;if(p.runtime_id in acpArgs)await runAcp(p,state);else await runStream(p,state);if(!state.cancelled&&!state.publicActivity)throw Error('Host ended without visible output or tool activity; verify host configuration');emit(p.execution_id,'end',{status:state.cancelled?'cancelled':'completed'});}catch(e){if(!state.cancelled)emit(p.execution_id,'error',{message:String(e.message||e)});emit(p.execution_id,'end',{status:state.cancelled?'cancelled':'failed',error:state.cancelled?undefined:String(e.message||e)});}finally{state.questions.clear();active.delete(p.execution_id);}}
async function handle(method:string,p:any){if(method==='discover')return discover(p);if(method==='list_models')return listModels(p);if(method==='start'){const bin=validate(p);const state={bin,cancelled:false,questions:new Map()};active.set(p.execution_id,state);setImmediate(()=>execute(p,state));return {execution_id:p.execution_id};}if(method==='cancel'){const s=active.get(p.execution_id);if(!s)return {cancelled:false};s.cancelled=true;s.cancel?.();return {cancelled:true};}if(method==='answer'){const s=active.get(p.execution_id),q=s?.questions.get(String(p.request_id));if(!q)throw Error('Request no longer pending');if(p.option_id&&!q.options.some(o=>o.optionId===p.option_id))throw Error('Unknown permission option');q.reply({outcome:p.option_id?{outcome:'selected',optionId:p.option_id}:{outcome:'cancelled'}});s.questions.delete(String(p.request_id));return {accepted:true};}throw Error('Unknown bridge method');}
const input=createInterface({input:process.stdin});input.on('line',async line=>{let m;try{m=JSON.parse(line);wire({id:m.id,result:await handle(m.method,m.params||{})});}catch(e){wire({id:m?.id??null,error:{message:String(e.message||e)}});}});function shutdown(){for(const s of active.values()){s.cancelled=true;s.cancel?.();}setTimeout(()=>process.exit(0),1500).unref();}input.on('close',shutdown);process.on('SIGINT',shutdown);process.on('SIGTERM',shutdown);
