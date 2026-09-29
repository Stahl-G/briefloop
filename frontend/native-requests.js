import {esc} from './dom.js';

export function requestKind(request){
 const data=request.data||{};
 if(data.kind==='question'||data.kind==='permission')return data.kind;
 return data.native_options||/Approval|permission/i.test(request.method||'')?'permission':'question';
}

export function questionAnswers(questions,values){
 const answers=Object.create(null);
 for(const [index,question] of questions.entries()){
  const id=question.id||String(index),value=values[id]||{},allowed=new Set((question.options||[]).map(option=>option.label));
  if(question.inputType==='editor'){answers[id]={answers:[String(value.custom??question.prefill??'')]};continue}
  const selected=[...new Set((value.selected||[]).filter(label=>allowed.has(label)))];
  const custom=question.allowCustom!==false?String(value.custom||'').trim():'';
  const chosen=question.multiSelect?[...selected,...(custom?[custom]:[])]:custom?[custom]:selected.slice(0,1);
  if(!chosen.length)throw Error('请回答“'+(question.header||question.question||'问题 '+(index+1))+'”后提交。');
  answers[id]={answers:[...new Set(chosen)]};
 }
 if(!questions.length)throw Error('这条提问没有可回答的问题，请等待刷新或停止当前任务。');
 return answers;
}

function permissionOptions(request){
 const data=request.data||{};
 if(Array.isArray(data.native_options))return data.native_options.filter(option=>option.optionId).map(option=>({id:option.optionId,label:option.name||option.optionId,kind:option.kind||''}));
 return (data.questions?.[0]?.options||[]).map(option=>({id:option.value||option.label,label:option.label,kind:''}));
}
export function permissionAnswers(request,optionId){
 if(!permissionOptions(request).some(option=>option.id===optionId))throw Error('这项授权已不在当前请求中，请刷新后重试。');
 return {[request.data?.questions?.[0]?.id||'permission']:{answers:[optionId]}};
}
function permissionContext(data){
 const input=data.input&&typeof data.input==='object'?data.input:{};
 const rows=[['工具',data.tool||data.tool_name],['命令',input.command],['目录',input.cwd||input.directory],['文件',input.file_path||input.path],['网址',input.url],['宿主说明',data.questions?.[0]?.question]];
 return `<p class="native-permission-summary">${esc(input.description||data.tool||data.tool_name||'宿主请求确认一项操作，请展开详情查看。')}</p><details class="native-permission-details"><summary>操作详情</summary><dl class="native-permission-context">`+rows.filter(([,value])=>typeof value==='string'&&value.trim()).map(([label,value])=>`<dt>${esc(label)}</dt><dd>${esc(value)}</dd>`).join('')+'</dl></details>';
}
export function requestMarkup(request){
 const data=request.data||{},permission=requestKind(request)==='permission';
 if(permission){
  const options=permissionOptions(request);
  return `<section class="native-request native-permission" data-native-request="${esc(request.id)}"><div class="native-request-heading"><span aria-hidden="true">◇</span><strong>确认执行操作</strong></div>${permissionContext(data)}<p class="native-request-note">此处授权执行操作；不会改变你的回答或自动放宽其他权限。</p><div class="native-permission-actions">${options.map(option=>`<button type="button" class="outline" data-permission-option="${esc(option.id)}">${esc(option.label)}</button>`).join('')}</div><p class="native-request-error" role="alert">${options.length?'':'宿主未提供可用的授权选项，请刷新或停止当前任务。'}</p><p class="native-request-status" role="status"></p></section>`;
 }
 const questions=data.questions||[];
 return `<form class="native-request native-question" data-native-request="${esc(request.id)}"><div class="native-request-heading"><span aria-hidden="true">?</span><strong>补充这次任务的要求</strong></div>${questions.map((question,index)=>{
  const options=question.options||[],custom=question.inputType==='editor'||question.allowCustom!==false||!options.length;
  return `<fieldset data-question-index="${index}"><legend>${esc(question.question||question.header||'请补充')}${question.multiSelect?'<small>可多选</small>':''}</legend><div class="native-question-options">${options.map((option,choice)=>`<label class="native-question-option"><input type="${question.multiSelect?'checkbox':'radio'}" name="answer-${index}" value="${choice}"><span><b>${esc(option.label)}</b>${option.description?`<small>${esc(option.description)}</small>`:''}</span></label>`).join('')}</div>${custom?`<label class="native-question-custom"><span>${options.length?'其他回答':'你的回答'}</span><textarea name="custom-${index}" rows="${question.inputType==='editor'?5:2}" placeholder="${question.multiSelect?'可以补充其他内容':'输入你的回答，可换行'}">${question.inputType==='editor'?esc(question.prefill||''):''}</textarea></label>`:''}</fieldset>`;
 }).join('')}<div class="native-request-footer"><span class="native-request-note">一次提交全部回答，不授予执行权限。</span><button class="primary" type="submit" ${questions.length?'':'disabled'}>提交回答</button></div><p class="native-request-error" role="alert"></p><p class="native-request-status" role="status"></p></form>`;
}

// Both the conversation and the permissions panel share one submission state.
// Accepted answers stay disabled even if the following snapshot refresh fails.
export function createNativeRequests({api,getSessionId,refresh}){
 const states=new Map(),containers=new Map();
 const key=(sessionId,requestId)=>JSON.stringify([sessionId,requestId]);
 function sync(){
  for(const [container,record] of containers){
   for(const node of container.querySelectorAll('[data-native-request]')){
    const state=states.get(key(record.sessionId,node.dataset.nativeRequest));if(!state)continue;
    node.querySelectorAll('button,input,textarea').forEach(control=>{control.disabled=state.busy||state.accepted});
    const error=node.querySelector('.native-request-error');if(error)error.textContent=state.error||'';
    const status=node.querySelector('.native-request-status');if(status)status.textContent=state.busy?'正在提交…':state.accepted?'已提交，等待任务继续。':'';
   }
  }
 }
 async function submit(request,answers,sessionId=getSessionId()){
  const identity=key(sessionId,request.id),old=states.get(identity);
  if(old?.busy||old?.accepted)return false;
  const state={busy:true,accepted:false,error:''};states.set(identity,state);sync();
  try{
   await api('harness/answer',{session_id:sessionId,request_id:request.id,answers});
   state.accepted=true;state.busy=false;sync();
   try{await refresh()}catch(error){state.error='回答已提交，刷新任务状态失败：'+error.message}
   return true;
  }catch(error){state.error=error.message||'提交失败，请重试。';state.busy=false;sync();try{await refresh()}catch{}return false}
  finally{state.busy=false;sync()}
 }
 function render(container,requests,{permissionsOnly=false,empty=''}={}){
  if(!container)return;
  const sessionId=getSessionId(),pending=requests.filter(request=>request.status==='pending'&&(!permissionsOnly||requestKind(request)==='permission'));
  const signature=JSON.stringify([sessionId,pending,empty]);
  const previous=containers.get(container);
  if(previous?.signature===signature){sync();return}
  const drafts=new Map();
  if(previous?.sessionId===sessionId)for(const node of container.querySelectorAll('[data-native-request]')){
   const id=node.dataset.nativeRequest,request=pending.find(item=>item.id===id);
   if(!request||previous.requests?.get(id)!==JSON.stringify(request))continue;
   drafts.set(id,[...node.querySelectorAll('input,textarea')].map(input=>({name:input.name,type:input.type,value:input.value,checked:input.checked})));
  }
  containers.set(container,{signature,sessionId,requests:new Map(pending.map(request=>[request.id,JSON.stringify(request)]))});container.innerHTML=pending.map(requestMarkup).join('')||empty;
  for(const request of pending){
   const node=[...container.querySelectorAll('[data-native-request]')].find(element=>element.dataset.nativeRequest===request.id);if(!node)continue;
   for(const [index,question] of (request.data?.questions||[]).entries())if(question.inputType==='editor'){
    const editor=node.querySelector(`textarea[name="custom-${index}"]`);if(editor)editor.value=String(question.prefill??'');
   }
   for(const input of node.querySelectorAll('input,textarea')){
    const draft=drafts.get(request.id)?.find(saved=>saved.name===input.name&&(input.type==='checkbox'||input.type==='radio'?saved.value===input.value:true));
    if(draft){if(input.type==='checkbox'||input.type==='radio')input.checked=draft.checked;else input.value=draft.value}
   }
   if(requestKind(request)==='permission')node.querySelectorAll('[data-permission-option]').forEach(button=>{button.onclick=()=>submit(request,permissionAnswers(request,button.dataset.permissionOption),sessionId)});
   else{
    for(const [index,question] of (request.data?.questions||[]).entries()){
     if(question.multiSelect)continue;
     const custom=node.querySelector(`textarea[name="custom-${index}"]`);
     node.querySelectorAll(`input[name="answer-${index}"]`).forEach(input=>{input.onchange=()=>{if(custom)custom.value=''}});
     if(custom)custom.oninput=()=>node.querySelectorAll(`input[name="answer-${index}"]:checked`).forEach(input=>{input.checked=false});
    }
    node.onsubmit=event=>{
    event.preventDefault();const values=Object.create(null);
    for(const [index,question] of (request.data?.questions||[]).entries()){
     const selected=[...node.querySelectorAll(`input[name="answer-${index}"]:checked`)].map(input=>question.options?.[Number(input.value)]?.label).filter(Boolean);
     values[question.id||String(index)]={selected,custom:node.querySelector(`textarea[name="custom-${index}"]`)?.value||''};
    }
    try{return submit(request,questionAnswers(request.data?.questions||[],values),sessionId)}catch(error){node.querySelector('.native-request-error').textContent=error.message}
    };
   }
  }
  sync();
 }
 return {render,submit};
}
