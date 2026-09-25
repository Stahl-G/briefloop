// Formal delivery is an explicit action over a saved version; draft export stays available.
import {$,esc} from './dom.js';
import {moment} from './time.js';
export const changeTypeLabel={initial:'首次交付',correction:'更正',update:'后续信息更新'};
export const displayDate=value=>value?moment(value):'时间未记录';
export function deliveryUI({api,notice,action,page,openBrief,savedVersion,statuses,getState,getCurrent,isDirty,isSaving,office}){
 let releaseView={version:null,data:null,loading:false},auditTarget=null;
 const releaseStatus={pending:'等待制作',released:'正式件已保存',failed:'制作未完成',cancelled:'已停止'};
 function releaseEligibilityHTML(eligibility){
  if(!eligibility)return '<p>交付条件暂不可用，尚未判定通过。</p>';
  const blockers=eligibility.blockers||[],notices=eligibility.notices||[];
  return `<h3>${eligibility.eligible?'当前版本满足正式交付条件':'当前版本还有需处理事项'}</h3>${blockers.length?`<ul class="release-blockers">${blockers.map(item=>`<li>${esc(item.message||item)}</li>`).join('')}</ul>`:''}${notices.length?`<details open><summary>保留的提示 · ${notices.length} 项</summary><ul>${notices.map(item=>`<li>${esc(item.message||item)}</li>`).join('')}</ul></details>`:''}${eligibility.eligible?'':'<p class="help">工作稿仍可编辑和下载。请在“审阅与需处理”中处理问题后复核。</p>'}`;
 }
 function releaseCardHTML(release){
  const state=getState(),current=getCurrent();
  const job=state.jobs.find(j=>j.id===release.job_id),status=release.status==='released'?releaseStatus.released:statuses[job?.status]||releaseStatus[release.status]||release.status;
  const change=changeTypeLabel[release.change_type]||(release.previous_id?'关联旧正式件':'首次交付');
  return `<article class="release-card"><div class="section-title"><strong>${esc(change)} · ${esc(displayDate(release.created))}</strong><span class="tag">${esc(status)}</span></div><p class="help">${release.version_id===current?.id?'当前正在查看的稿件版本':'历史稿件版本'}${release.previous_id?' · 关联旧正式件 '+esc(release.previous_id.slice(-8)):''}</p>${release.change_reason?`<p>${esc(release.change_reason)}</p>`:''}${job?.error?`<p class="error">${esc(job.error)}</p>`:''}${release.status==='released'?`<div class="release-actions"><a href="/api/release-file?id=${encodeURIComponent(release.id)}" download>下载正式 Word</a><button type="button" data-audit-release="${esc(release.id)}">导出审计包…</button></div>`:'<p class="help">任务进度见报告下方的文件制作记录。</p>'}</article>`;
 }
 async function refreshReleaseState(){
  if(releaseView.loading||!$('release-dialog').open)return;
  const version=getCurrent()?.id;if(!version)return;
  releaseView.loading=true;
  try{
   const data=await api('release-state?version='+encodeURIComponent(version));
   if(!$('release-dialog').open||getCurrent()?.id!==version)return;
   releaseView.version=version;releaseView.data=data;
   $('release-eligibility').innerHTML=(isDirty()?'<p class="help">有未保存修改，下列条件针对上次保存的版本。</p>':'')+releaseEligibilityHTML(data.eligibility);
   $('release-submit').disabled=isDirty()||isSaving()||!data.eligibility?.eligible||releaseView.submitting;
   const released=(data.releases||[]).filter(r=>r.status==='released');
   const previous=$('release-previous'),choices=released.map(r=>r.id).join(',');
   if(previous.dataset.choices!==choices){const chosen=previous.value;previous.innerHTML='<option value="">首次交付</option>'+released.map(r=>`<option value="${esc(r.id)}">${esc(displayDate(r.created))} · ${esc(changeTypeLabel[r.change_type]||'正式件')} · ${esc(r.id.slice(-8))}</option>`).join('');previous.dataset.choices=choices;if(released.some(r=>r.id===chosen))previous.value=chosen;updateReleaseChangeFields()}
   const html=(data.releases||[]).map(releaseCardHTML).join('')||'<p class="help">此报告尚无正式交付记录。</p>';
   if($('release-list').innerHTML!==html){$('release-list').innerHTML=html;$('release-list').querySelectorAll('[data-audit-release]').forEach(button=>button.onclick=()=>openAuditBundle(button.dataset.auditRelease))}
  }finally{releaseView.loading=false}
 }
 function updateReleaseChangeFields(){const linked=!!$('release-previous').value;$('release-change-fields').hidden=!linked;$('release-change-reason').required=linked}
 async function openReleaseDialog(brief=null){
  if(brief&&!openBrief(brief,{follow:false}))return;
  // A list choice is accepted before its body arrives. Never save/check the old editor.
  const request=openBrief.request;
  if(request){const opened=await request.promise;if(!opened||openBrief.request||getCurrent()?.id!==request.id)return}
  if(brief&&getCurrent()?.id!==brief.id)return;
  const runId=getCurrent()?.run_id,version=await savedVersion();
  if(openBrief.request||getCurrent()?.id!==version||getCurrent()?.run_id!==runId)return;
  page('report');$('release-eligibility').textContent='正在核对当前版本的交付条件…';$('release-submit').disabled=true;$('release-dialog').showModal();await refreshReleaseState();
 }
 async function submitFormalRelease(){
  const previous=$('release-previous').value,changeType=$('release-change-type').value,reason=$('release-change-reason').value.trim();
  if(previous&&!reason)throw Error('请说明本次更正或更新的依据和影响');
  const version=await savedVersion();
  const payload={version_id:version};if(previous)Object.assign(payload,{previous_id:previous,change_type:changeType,change_reason:reason});
  const result=await api('release',payload);
  return result;
 }
 function openAuditBundle(releaseId){
  const release=releaseView.data?.releases.find(r=>r.id===releaseId);if(!release||release.status!=='released'){notice('请先等待正式件制作完成',true);return}
  auditTarget=release;
  // Every open resets the optional render choice so no stale checkbox leaks in.
  const officeRender=$('audit-office-render');if(officeRender)officeRender.checked=false;
  const officeRow=$('audit-office-row');if(officeRow)officeRow.hidden=!office?.officeEnabled();
  $('audit-target').textContent='正式件：'+displayDate(release.created)+' · '+(changeTypeLabel[release.change_type]||'首次交付')+' · '+release.id.slice(-8);
  $('audit-source-list').innerHTML=(release.sources||release.data?.snapshot?.sources||[]).map(source=>`<label class="audit-source-row"><span>${esc(source.name||source.id)}</span><select data-audit-source="${esc(source.id)}" aria-label="${esc(source.name||source.id)}的打包范围"><option value="metadata">仅定位，不含原件和摘录</option><option value="excerpt">定位与证据摘录</option><option value="original">原件及证据摘录</option></select></label>`).join('')||'<p class="help">此正式件没有登记来源文件。</p>';
  $('audit-submit').disabled=false;$('audit-dialog').showModal();
 }
 async function submitAuditBundle(){
  if(!auditTarget)throw Error('请先选择一个已保存的正式件');
  const releaseId=auditTarget.id,permissions=Object.fromEntries([...$('audit-source-list').querySelectorAll('[data-audit-source]')].map(select=>[select.dataset.auditSource,select.value]));
  return api('audit-bundle',{release_id:releaseId,source_permissions:permissions,include_office_render:$('audit-office-render')?.checked===true});
 }
 function init(){
  $('release-previous').onchange=updateReleaseChangeFields;
  $('release-open').onclick=()=>action(()=>openReleaseDialog());
  $('release-close').onclick=()=>$('release-dialog').close();
  $('release-form').onsubmit=event=>{event.preventDefault();if(releaseView.submitting)return;releaseView.submitting=true;$('release-submit').disabled=true;action(async()=>{try{await submitFormalRelease();$('release-dialog').close();notice('正式 Word 已排队，使用本次提交时固定的版本')}finally{releaseView.submitting=false;await refreshReleaseState()}})};
  $('audit-close').onclick=()=>$('audit-dialog').close();
  $('audit-all-original').onclick=()=>{$('audit-source-list').querySelectorAll('[data-audit-source]').forEach(select=>select.value='original')};
  $('audit-all-metadata').onclick=()=>{$('audit-source-list').querySelectorAll('[data-audit-source]').forEach(select=>select.value='metadata')};
  $('audit-form').onsubmit=event=>{event.preventDefault();if($('audit-submit').disabled)return;$('audit-submit').disabled=true;action(async()=>{try{await submitAuditBundle();$('audit-dialog').close();$('release-dialog').close();notice('审计包已排队，完成后可在文件制作记录下载')}finally{$('audit-submit').disabled=false}})};
 }
 return {init,refreshReleaseState,openReleaseDialog,openAuditBundle,submitFormalRelease,submitAuditBundle};
}
