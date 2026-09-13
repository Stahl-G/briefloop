import path from 'node:path';
import {createJsonLineStream} from '../third_party/open-design/core/json-line-stream.js';

// Protocol: Pi's distributed docs/rpc.md (agent_settled, not agent_end, is terminal).
export function piConnection(bin:string,p:any,launch:any,terminate:any,onEvent:any=()=>{}){
 const args=['--mode','rpc','--no-approve'];
 if(p.metadata)args.push('--no-session');
 if(p.session_id)args.push('--session',p.session_id);
 const child=launch(bin,args,p.cwd),pending=new Map<string,any>();let seq=0;
 const send=(value:any)=>child.stdin.write(JSON.stringify(value)+'\n');
 const rejectPending=(error:any)=>{for(const q of pending.values()){clearTimeout(q.timer);q.reject(error);}pending.clear();};
 const parser=createJsonLineStream((m:any)=>{if(m.type==='response'&&pending.has(m.id)){const q=pending.get(m.id);pending.delete(m.id);clearTimeout(q.timer);m.success?q.resolve(m.data):q.reject(Error(m.error||'Pi rejected '+m.command));}else onEvent(m);});
 child.stdout.setEncoding('utf8');child.stdout.on('data',c=>parser.feed(c));child.stderr.resume();child.stdin.on('error',()=>{});
 child.on('error',rejectPending);child.on('close',()=>{parser.flush();rejectPending(Error('Pi process exited'));});
 return {child,send,close:()=>terminate(child),call:(type:string,data:any={},timeout=20000)=>new Promise<any>((resolve,reject)=>{const id=String(++seq);const timer=setTimeout(()=>{pending.delete(id);reject(Error('Pi '+type+' timed out'));},timeout);pending.set(id,{resolve,reject,timer});send({id,type,...data});})};
}

export async function piModels(bin:string,p:any,launch:any,terminate:any){
 const c=piConnection(bin,{...p,metadata:true},launch,terminate);
 try{const result=await c.call('get_available_models');return {source:'host',models:[{id:'default',label:'宿主默认模型'},...(result.models||[]).map((m:any)=>({id:m.provider+'/'+m.id,label:m.name||m.id}))]};}finally{c.close();}
}

export async function runPi(p:any,state:any,launch:any,terminate:any,emit:any){
 if(p.images?.length)throw Error('Pi image input is not enabled in this adapter');
 let finish:any,fail:any,lastMessage:any=null,textSeen=false,started=false;
 const settled=new Promise<void>((resolve,reject)=>{finish=resolve;fail=reject;});settled.catch(()=>{});
 const c=piConnection(state.bin,p,launch,terminate,(m:any)=>{
  if(!started)return;
  if(m.type==='message_start'&&m.message?.role==='assistant')textSeen=false;
  if(m.type==='message_update'){const e=m.assistantMessageEvent||{};if(e.type==='text_delta'){textSeen=true;emit(p.execution_id,'text',{text:e.delta,delta:true});}else if(e.type==='thinking_delta')emit(p.execution_id,'reasoning',{text:e.delta,delta:true});}
  if(m.type==='message_end'&&m.message?.role==='assistant'){
   lastMessage=m.message;
   if(!textSeen)for(const b of lastMessage.content||[])if(b.type==='text')emit(p.execution_id,'text',{text:b.text,delta:true});
   if(lastMessage.usage)emit(p.execution_id,'usage',{usage:lastMessage.usage});
  }
  if(m.type.startsWith('tool_execution_'))emit(p.execution_id,'tool',{id:m.toolCallId,name:m.toolName,status:m.type==='tool_execution_end'?(m.isError?'failed':'completed'):'running',input:m.args,output:m.result||m.partialResult});
  if(m.type==='extension_ui_request'){
   if(!['select','confirm','input','editor'].includes(m.method))return;
   const options=m.method==='confirm'?[{optionId:'yes',kind:'allow_once',name:'允许本次'},{optionId:'no',kind:'reject_once',name:'拒绝'}]:m.method==='select'?(m.options||[]).map((name:string,i:number)=>({optionId:String(i),name,kind:'choice'})):[];
   if(!options.length){c.send({type:'extension_ui_response',id:m.id,cancelled:true});fail(Error('Pi extension requested unsupported text input'));return;}
   state.questions.set(String(m.id),{options,reply:(r:any)=>{const id=r.outcome?.optionId;const value=options.find((o:any)=>o.optionId===id);c.send({type:'extension_ui_response',id:m.id,...(!value?{cancelled:true}:m.method==='confirm'?{confirmed:id==='yes'}:{value:value.name})});}});
   emit(p.execution_id,'question',{request_id:String(m.id),type:'permission',title:m.title||m.message||'Pi 请求确认',options});
  }
  if(m.type==='agent_settled'){if(state.cancelled)return finish();if(!lastMessage||['error','aborted','toolUse'].includes(lastMessage.stopReason))fail(Error(lastMessage?.errorMessage||'Pi ended without a successful final reply'));else finish();}
 });
 state.child=c.child;state.cancel=()=>{c.send({type:'clear_queue'});c.send({type:'abort'});c.close();finish();};
 c.child.on('error',fail);c.child.on('close',()=>{if(!state.cancelled)fail(Error('Pi exited before the turn settled'));});
 let timer:any;
 try{
  const info=await c.call('get_state');
  if(!info.sessionFile)throw Error('Pi did not provide a persistent session');
  if(p.session_id&&path.resolve(info.sessionFile)!==path.resolve(p.session_id))throw Error('Pi resumed a different session');
  if(p.model&&p.model!=='default'){const split=p.model.indexOf('/');if(split<1)throw Error('Pi model must be provider/model');await c.call('set_model',{provider:p.model.slice(0,split),modelId:p.model.slice(split+1)});}
  emit(p.execution_id,'session',{session_id:info.sessionFile});started=true;
  if(p.timeout_ms)timer=setTimeout(()=>{c.close();fail(Error('Pi turn timed out'));},p.timeout_ms);
  await c.call('prompt',{message:p.prompt});await settled;
 }finally{clearTimeout(timer);c.close();}
}
