// Evidence and review dialogs opened from the report toolbar: claim bindings with their
// sources, and review results with the findings still open.
import {$ as lookup,esc} from './dom.js';
import {reviewResultHTML} from './review-results.js';
export function evidenceReviewDialogsUI({api,action,savedVersion,showSource,getEditor,reviewControls,$=lookup}){
 async function showEvidence(blockId=null){
  const version=await savedVersion();if(!version)return;
  const data=await api('evidence?version='+encodeURIComponent(version));
  $('evidence-summary').textContent=data.note;
  const states={unreviewed:'语义待审阅',needs_review:'正文已改，需复核',anchor_missing:'正文锚点已失效',source_changed:'来源已变，需复核',premise_changed:'前提依据已变，需复核',premise_cycle:'前提关联异常'};
  const rows=blockId?data.bindings.filter(b=>b.block_id===blockId):data.bindings;
  $('evidence-list').innerHTML=rows.length?rows.map(b=>`<article class="evidence-card"><span class="tag">${esc(states[b.status]||b.status)}</span><h3>${esc(b.claim.data.statement)}</h3><p>正文：${esc(b.quote)}</p>${b.claim.data.reasoning?`<p>推断依据：${esc(b.claim.data.reasoning)}</p>`:''}${b.evidence.map(e=>`<details open><summary>${esc(e.source_name)} · ${esc(evidenceLocation(e.data.locator))}</summary><p>支持范围：${esc(e.supports_quote)}</p><blockquote>${esc(e.data.excerpt)}</blockquote><p class="help">${esc(e.data.extraction_method)} · ${esc(e.data.location_status)}${e.intact?'':' · 原件或文本已变化'}</p><button type="button" data-evidence-source="${esc(e.source_id)}">查看原始来源</button></details>`).join('')}${premiseCards(b.premises||[])}</article>`).join(''):'<p>当前范围尚未登记证据绑定，不能据此判断已经核验。</p>';
  $('evidence-list').querySelectorAll('[data-evidence-source]').forEach(button=>button.onclick=()=>{ $('evidence-dialog').close();action(async()=>showSource(await api('source?id='+encodeURIComponent(button.dataset.evidenceSource)))) });
  $('evidence-dialog').showModal();
 }
 function evidenceLocation(locator){
  if(locator.kind==='text')return `第 ${locator.start_line}–${locator.end_line} 行`;
  if(locator.kind==='pdf')return `第 ${locator.page} 页`;
  if(locator.kind==='xlsx')return `${locator.sheet} · ${locator.cells}`;
  return locator.region?'图像指定区域':'图像原件';
 }
 function premiseCards(premises){return premises.map(p=>`<details><summary>间接依据／前提：${esc(p.claim?.data.statement||p.claim_id)}${p.status==='unreviewed'?'':' · 依据需复核'}</summary>${(p.evidence||[]).map(e=>`<p>${esc(e.source_name)} · ${esc(evidenceLocation(e.data.locator))}</p><blockquote>${esc(e.data.excerpt)}</blockquote><button type="button" data-evidence-source="${esc(e.source_id)}">查看原始来源</button>`).join('')}${premiseCards(p.premises||[])}</details>`).join('')}
 function init(){
  $('evidence-open').onclick=()=>action(()=>showEvidence());
  $('evidence-close').onclick=()=>$('evidence-dialog').close();
  $('evidence-selection').onclick=()=>action(()=>{
   const selection=getEditor()?.state.selection;let id=selection?.node?.attrs.blockId;
   if(!id&&selection)for(let depth=selection.$from.depth;depth>0;depth--){id=selection.$from.node(depth).attrs.blockId;if(id)break}
   return showEvidence(id||null);
  });
  $('review-start').onclick=()=>action(reviewControls.startReview);
  $('review-close').onclick=()=>$('review-dialog').close();
  $('review-open').onclick=()=>action(async()=>{
   const version=await savedVersion();if(!version)return;const data=await api('review-status?version='+encodeURIComponent(version));
   const states={queued:'等待审阅',running:'审阅中',complete:'已返回审阅结果',incomplete:'审阅未完成',cancelled:'已停止',open:'待处理',addressed_pending_review:'已回应，待复核',resolved:'已复核解决',dismissed_with_evidence:'有依据排除'};
   $('review-list').innerHTML=(data.conflicts||[]).filter(c=>c.status!=='resolved').map(c=>`<article class="evidence-card"><span class="tag error">来源分歧 · ${esc(states[c.status]||c.status)}</span><p>${esc(c.data.description)}</p><p>已提醒不等于已解决，需由独立Reviewer核对双方依据。</p></article>`).join('')+(data.reviews||[]).map(r=>reviewResultHTML(r,states,{backendLabel:reviewControls.backendLabel})).join('')+(data.reviews.length?'':'<p>当前版本尚未审阅，不能视为已通过。</p>')+data.findings.map(f=>`<article class="evidence-card"><span class="tag">${esc(states[f.status]||f.status)} · ${f.data.severity==='major'?'重要问题':'一般问题'}</span><h3>${esc(f.data.description)}</h3><blockquote>${esc(f.data.report_quote)}</blockquote><p>依据：${esc(f.data.evidence)}</p><p>${esc(f.data.suggested_action)}</p><p class="help">目标版本：${esc(f.version_id)}</p>${['open','addressed_pending_review'].includes(f.status)?`<form data-finding-response="${f.id}"><select name="action"><option value="corrected">已修改当前稿</option><option value="removed">已移除相关主张</option><option value="disagree">提出有依据的异议</option></select><textarea name="reason" required placeholder="说明修改位置或异议依据"></textarea><button type="submit">提交处理说明，等待复核</button></form>`:''}</article>`).join('');
   $('review-list').querySelectorAll('[data-finding-response]').forEach(form=>form.onsubmit=e=>{e.preventDefault();action(async()=>{const target=await savedVersion();await api('review-response',{finding_id:form.dataset.findingResponse,version_id:target,action:form.elements.action.value,reason:form.elements.reason.value});$('review-dialog').close()},'处理说明已保存；只有独立复核才能关闭问题')});
   $('review-dialog').showModal();
  });
  $('review-revise').onclick=()=>action(async()=>{const version=await savedVersion();if(version)await api('revise-findings',{version_id:version})},'已安排一次针对性修订及独立复核');
 }
 return {init};
}
