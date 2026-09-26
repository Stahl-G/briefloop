import {esc} from './dom.js';

export const REVIEW_MODES={standard:'普通审阅',strict:'严格审阅'};
const SCOPE={standard:'独立会话，只读核对；不承诺完全隔离宿主上下文。',strict:'独立会话，强制只读访问冻结核查包。'};
export const reviewModeLabel=review=>Object.hasOwn(REVIEW_MODES,review.review_mode)?REVIEW_MODES[review.review_mode]:'历史审阅（未记录模式）';

export function reviewModeMetadataHTML(review,backendLabel=id=>id){
 const mode=review.review_mode,known=Object.hasOwn(REVIEW_MODES,mode);
 return `<p class="help" data-testid="review-mode-record">${esc(reviewModeLabel(review))}${known?' · '+esc(SCOPE[mode]):''}${review.review_backend?' · 执行后端：'+esc(backendLabel(review.review_backend)):''}</p>`;
}

// Choices and admission use server-declared capabilities. This module never
// upgrades ordinary review to strict or changes a backend on the user's behalf.
export function createReviewControls({api,action,$,getState,backendValue,friendlyModel,savedVersion,notice,runtimeName=id=>id,find=selector=>document.querySelector(selector)}){
 const modeSelect=()=>find('[data-testid="review-mode"]');
 const modeNote=()=>find('[data-testid="review-mode-note"]');
 const savedMode=()=>getState()?.settings?.review_mode||'standard';
 const runtime=()=>getState()?.settings?.review_runtime||null;
 const choices=()=>getState()?.review_capability?.review_choices||[];
 const supported=(mode,backend)=>{
  const listed=getState()?.review_capability?.[mode==='strict'?'strict_review':'standard_review'];
  return listed?listed.some(item=>item.id===backend):null;
 };
 const reviewerBackend=()=>runtime()?.backend||backendValue();
 const label=backend=>choices().find(item=>item.id===backend)?.label||runtimeName(backend);
 const unsavedMode=()=>!!modeSelect()&&modeSelect().value!==savedMode();
 const effort=current=>current?.backend==='codex'?current.reasoning_effort:current?.model_variant;
 const unsavedRuntime=()=>{
  const current=runtime(),backend=$('review-backend').value;
  return backend!==(current?.backend||'')||!!backend&&($('review-model').value.trim()!==(current?.model||'')||$('review-variant').value.trim()!==(effort(current)||''));
 };
 const unsaved=()=>unsavedMode()||unsavedRuntime();
 let saving=false;

 function updateInputs(){
  const backend=$('review-backend').value,codex=backend==='codex';
  const effortLabel=find('[data-testid="review-effort-label"]');
  if(effortLabel)effortLabel.textContent=codex?'推理强度':'推理档位（Variant）';
  $('review-model').placeholder=codex?'模型 ID，例如 gpt-6-luna':'provider/model，例如 opencode-go/deepseek-v4.1-flash';
  $('review-variant').placeholder='例如 high，留空使用模型默认';
  $('review-model').disabled=$('review-variant').disabled=saving||!backend;
  $('review-backend').disabled=saving;if(modeSelect())modeSelect().disabled=saving;
 }

 function unavailable(mode,backend){
  const alternatives=getState()?.review_capability?.[mode==='strict'?'strict_review':'standard_review']||[];
  return `${label(backend)} 不支持${REVIEW_MODES[mode]||'所选审阅模式'}。`+
   (alternatives.length?`请在“独立审阅执行后端”手动选择 ${alternatives.map(item=>item.label).join('、')}。`:'当前没有可用的审阅后端。')+
   (mode==='strict'?'严格审阅不会自动改为普通审阅。':'');
 }

 function renderSummary(){
  const current=runtime(),mode=savedMode();
  $('review-runtime-summary').textContent=`${REVIEW_MODES[mode]||'未知审阅模式'} · `+(current?`${label(current.backend)} · ${friendlyModel(current.model)}${effort(current)?' / '+effort(current):''}`:'跟随执行后端');
  const help=modeNote();
  if(help)help.textContent=(SCOPE[mode]||'审阅模式未确认。')+' 事实与证据标准相同。'+
   (supported(mode,reviewerBackend())===false?' '+unavailable(mode,reviewerBackend()):'');
  const button=$('review-start');
  if(button){button.textContent=REVIEW_MODES[mode]||'独立审阅';button.disabled=saving||unsaved()||supported(mode,reviewerBackend())===false;button.title=button.disabled?(saving||unsaved()?'审阅设置尚未保存':unavailable(mode,reviewerBackend())):SCOPE[mode]||''}
 }

 function syncFactCheckControl(){
  const form=$('requirements'),box=form?.elements.fact_check;if(!box)return;
  const available=supported(savedMode(),reviewerBackend()),pending=saving||unsaved();
  box.disabled=!form.elements.allow_web.checked||available===false||pending;
  if(!form.elements.allow_web.checked||available===false)box.checked=false;
  const note=$('review-capability-note');
  if(note){
   note.hidden=available!==false&&!pending;
   note.textContent=pending?'审阅设置尚未保存，请先完成设置。':available===false?unavailable(savedMode(),reviewerBackend())+' 当前不能开启事实核查。'+
    (form.elements.writing_mode.value==='internal_report'?'已有稿件仍可编辑和下载；正式交付需要完成独立审阅。':''):'';
  }
  renderSummary();
 }

 function renderBackendOptions(value){
  const select=$('review-backend');if(!select)return;
  const current=runtime(),mode=savedMode(),items=[...choices()];
  if(current&&!items.some(item=>item.id===current.backend))items.push({id:current.backend,label:current.backend});
  const option=(id,text)=>`<option value="${esc(id)}"${supported(mode,id||backendValue())===false?' disabled':''}>${esc(text)}${supported(mode,id||backendValue())===false?'（不支持'+esc(REVIEW_MODES[mode])+'）':''}</option>`;
  select.innerHTML=option('','跟随执行后端')+items.map(item=>option(item.id,item.label+(item.experimental?'（实验）':''))).join('');
  select.value=value;
 }

 function renderReviewRuntime(){
  const current=runtime();renderBackendOptions(current?.backend||'');
  $('review-model').value=current?.model||'';$('review-variant').value=effort(current)||'';
  if(modeSelect())modeSelect().value=savedMode();
  updateInputs();
  syncFactCheckControl();
 }

 async function saveMode(){
  const mode=modeSelect().value,status=$('review-runtime-status');
  if(!Object.hasOwn(REVIEW_MODES,mode))return;
  saving=true;updateInputs();status.textContent='保存中…';syncFactCheckControl();
  try{
   const saved=await api('settings',{review_mode:mode});
   if(saved.review_mode!==mode)throw Error('服务未确认所选模式，尚未应用');
   getState().settings.review_mode=saved.review_mode;
   status.textContent='已保存，之后的新请求使用此模式；已排队任务和历史审阅不变。';
  }catch(error){status.textContent='未保存：'+error.message}
  finally{saving=false;updateInputs();syncFactCheckControl()}
  // Keep a rejected choice visible and disable new review until it is saved;
  // otherwise a click after choosing strict could silently run standard.
  if(!unsavedMode())renderBackendOptions($('review-backend').value);
 }

 async function saveRuntime(){
  const backend=$('review-backend').value,model=$('review-model').value.trim(),status=$('review-runtime-status');
  updateInputs();syncFactCheckControl();
  if(backend&&!model){status.textContent='输入审阅模型'+(backend==='codex'?' ID':'（provider/model）')+'后保存。';return}
  const current=runtime(),preserved={};
  if(current?.backend===backend&&backend==='codex')for(const key of ['model_provider','service_tier'])if(Object.hasOwn(current,key))preserved[key]=current[key];
  const review_runtime=backend?{...preserved,backend,model,[backend==='codex'?'reasoning_effort':'model_variant']:$('review-variant').value.trim()||null}:null;
  saving=true;updateInputs();status.textContent='保存中…';syncFactCheckControl();
  try{const saved=await api('settings',{review_runtime});getState().settings.review_runtime=saved.review_runtime??null;status.textContent='已保存，之后的新请求使用此审阅设置；已排队任务不变。'}
  catch(error){status.textContent='未保存：'+error.message}
  finally{saving=false;updateInputs();syncFactCheckControl()}
 }

 async function chooseBackend(){
  const current=runtime(),same=$('review-backend').value===current?.backend;
  $('review-model').value=same?current.model:'';$('review-variant').value=same?effort(current)||'':'';
  await saveRuntime();
 }

 async function startReview(){
  if(saving||unsaved())throw Error('审阅设置尚未保存，请先完成设置。');
  const mode=savedMode();
  if(supported(mode,reviewerBackend())===false)throw Error(unavailable(mode,reviewerBackend()));
  const version=await savedVersion();
  if(version){await api('review',{version_id:version,review_mode:mode});notice('已提交'+REVIEW_MODES[mode]);}
 }

 function init(){
  modeSelect()?.addEventListener('change',()=>action(saveMode));
  $('review-backend')?.addEventListener('change',()=>action(chooseBackend));
  for(const id of ['review-model','review-variant'])$(id)?.addEventListener('change',()=>action(saveRuntime));
 }
 return {init,syncFactCheckControl,renderReviewRuntime,startReview,backendLabel:label};
}
