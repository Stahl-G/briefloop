import {createJsonLineStream} from '../third_party/open-design/core/json-line-stream.js';
import {sanitizeCustomModel} from '../third_party/open-design/runtime-models/models.js';

export function sanitizeClaudeModel(value:any):string|null{
 if(typeof value!=='string')return null;
 const trimmed=value.trim();
 if(trimmed.length>200)return null;
 return trimmed.endsWith('[1m]')&&sanitizeCustomModel(trimmed.slice(0,-4))?trimmed:sanitizeCustomModel(trimmed);
}

export function claudePermissionArgs(options:any):string[]{
 const mode=options?.mode??'auto';
 if(mode==='native')return [];
 if(!['auto','manual','acceptEdits','dontAsk','plan'].includes(mode))throw Error('Invalid Claude permission mode');
 return ['--permission-mode',mode];
}

// An initialize control request has no user message and never starts inference.
// Keep its process isolated from prompts, tools, hooks, MCP and session history.
// The full response can contain account details: return only model metadata.
export async function claudeModels(bin:string,p:any,launch:any,terminate:any){
 const child=launch(bin,['-p','--input-format','stream-json','--output-format','stream-json','--verbose',
  '--permission-prompt-tool','stdio','--no-session-persistence','--safe-mode',
  '--strict-mcp-config','--mcp-config','{"mcpServers":{}}','--tools',''],p.cwd);
 try{
  return await new Promise<any>((resolve,reject)=>{
   let settled=false;
   const done=(error:any,value?:any)=>{if(settled)return;settled=true;clearTimeout(timer);error?reject(error):resolve(value)};
   const timer=setTimeout(()=>done(Error('Claude 模型元数据初始化超时')),15000);
   const parser=createJsonLineStream((message:any)=>{
    if(message.type==='control_request'){
     child.stdin.write(JSON.stringify({type:'control_response',response:{subtype:'error',request_id:message.request_id,error:'Metadata probe cannot execute tools or answer dialogs'}})+'\n');return;
    }
    if(message.type!=='control_response'||message.response?.request_id!=='briefloop-models')return;
    const response=message.response;
    if(response.subtype!=='success')return done(Error('Claude 未提供模型初始化元数据'));
    const rows=response.response?.models;
    if(!Array.isArray(rows))return done(Error('Claude 初始化响应缺少模型目录'));
    const seen=new Set(),models=[];
    for(const row of rows){
     if(typeof row?.value!=='string'||!row.value.trim()||seen.has(row.value))continue;
     seen.add(row.value);
     const levels=Array.isArray(row.supportedEffortLevels)?row.supportedEffortLevels.filter((v:any)=>typeof v==='string'&&v.length>0&&v.length<100):undefined;
     models.push({id:row.value,label:typeof row.displayName==='string'?row.displayName:row.value,
      description:typeof row.description==='string'?row.description:'',
      ...(typeof row.resolvedModel==='string'?{resolved_model:row.resolvedModel}:{}),
      ...(typeof row.supportsEffort==='boolean'?{supports_effort:row.supportsEffort}:{}),
      ...(levels?{reasoningOptions:levels.map((id:string)=>({id,label:id})),thinking_levels:levels}:{}),
      provider:'Claude Code'});
    }
    done(null,{models,source:'host',status:models.length?'reachable':'empty',refreshed_at:new Date().toISOString(),inference_tested:false,
     note:'Claude Code 初始化返回的模型与推理档位；别名保留宿主原值，未调用模型。'});
   });
   child.stdout.setEncoding('utf8');child.stdout.on('data',(chunk:any)=>parser.feed(chunk));
   child.stderr.resume();child.stdin.on('error',()=>done(Error('Claude 模型元数据通道已关闭')));
   child.on('error',()=>done(Error('Claude 模型元数据进程无法启动')));
   child.on('close',()=>{parser.flush();done(Error('Claude 未完成模型元数据初始化'))});
   child.stdin.write(JSON.stringify({type:'control_request',request_id:'briefloop-models',request:{subtype:'initialize',hooks:{}}})+'\n');
  });
 }finally{terminate(child)}
}
