import {reasoningModel} from './reasoning-controls.js';

// Form ownership stays here: chat choices are draft-only; defaults are one
// atomic settings patch. In particular, do not invoke backend onchange midway.
export function createRuntimePickerBindings({$ ,api,getState,getChat,getBackend,getChatBackend,assignEffort,setChatPermission,rememberDraft,refreshChat,refreshSettings}){
 const usesVariant=backend=>['opencode','briefloop-native','mimo'].includes(backend);
 function canEdit(target){
  if($(target).disabled)return false;
  if(target!=='chat-model')return true;
  const chat=getChat();return !chat.busy&&!(chat.session?.lifecycle&&chat.session.lifecycle!=='active')&&!(['running','starting'].includes(chat.session?.status)&&$('chat-mode').value==='steer');
 }
 function canSwitchBackend(target){return target!=='chat-model'||![...getChat().events.values()].some(e=>e.kind==='session/internal')}
 function read(target){
  const chat=target==='chat-model',backend=chat?getChatBackend():getBackend();
  return {backend,model:$(target).value,effort:$(chat?(usesVariant(backend)?'chat-variant':'chat-effort'):(usesVariant(backend)?'model-variant':'effort-select')).value,
   context:chat?JSON.stringify([getChat().id,getChat().session?.turn_id]):JSON.stringify([getState().workspace_id,getState().settings.agent_backend,getState().settings.model])};
 }
 async function commit(target,choice,original){
  const current=read(target);
  if(!canEdit(target)||current.context!==original.context||current.backend!==original.backend||current.model!==original.model||current.effort!==original.effort)throw Error('当前选择或任务状态已改变，请关闭后重新选择。');
  const {backend}=choice,effort=choice.effort|| (usesVariant(backend)?'':'none'),model=reasoningModel(backend,choice.model.trim(),effort);
  if(!model||/\s/.test(model))throw Error('请输入完整模型 ID，不含空白字符。');
  if(['opencode','briefloop-native'].includes(backend)&&!model.includes('/'))throw Error('模型 ID 必须是 provider/model 形式');
  const changed=backend!==original.backend;
  if(changed&&!canSwitchBackend(target))throw Error('报告任务沿用冻结运行时；可调整同一运行时的模型与强度。');
  if(target==='chat-model'){
   const chat=getChat();chat.nextBackend=backend;
   if(changed){chat.hostOptions={};setChatPermission('');$('chat-model-provider').value='';$('chat-service-tier').value='';$('chat-mode').value='queue'}
   $('chat-model').value=model;assignEffort('chat-effort',usesVariant(backend)?'none':effort);$('chat-variant').value=usesVariant(backend)?effort:'';
   chat.request=null;rememberDraft();refreshChat();return;
  }
  const state=getState(),patch={agent_backend:backend,model,model_selection_required:false};
  if(changed)Object.assign(patch,{role_models:{},model_provider:null,model_variant:null,service_tier:null});
  if(['opencode','briefloop-native'].includes(backend))patch.model_variant=effort||null;
  else if(backend==='codex')patch.reasoning_effort=effort;
  else Object.assign(patch,{model_provider:null,model_variant:null,runtime_efforts:{...state.settings.runtime_efforts,[backend]:effort==='none'?null:effort||null}});
  const settings=await api('settings',patch);
  // Never replace another workspace's controls after navigation during the save.
  if(getState().workspace_id!==state.workspace_id)return;
  getState().settings=settings;$('agent-backend').value=backend;$('model-select').value=model;
  assignEffort('effort-select',usesVariant(backend)?'none':effort);$('model-variant').value=usesVariant(backend)?effort:'';
  if(changed){$('model-provider').value='';$('service-tier').value=''}
  refreshSettings();
 }
 return {read,commit,canEdit,canSwitchBackend};
}
