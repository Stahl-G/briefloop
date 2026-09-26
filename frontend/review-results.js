import {esc} from './dom.js';
import {reviewModeMetadataHTML} from './review-controls.js';

export function reviewResultHTML(review,states,{backendLabel}={}){
 const result=review.result||{},items=review.requirement_items||[],clauses=review.clause_items||[];
 const labels={covered:'已完成',manual:'用户安排人工填写',partial:'部分完成',missing:'未完成',unverified:'未核验',not_applicable:'不适用'};
 const kinds={reader_content:'读者内容',research_method:'研究方法',writing_preference:'写作偏好',manual_assignment:'人工安排',objective:'目标',question:'必答问题',writing:'写作偏好',manual:'人工填写'};
 const clauseChecks=result.clause_checks||[],requirementChecks=result.requirement_checks||[];
 const clauseHTML=clauseChecks.map(check=>{
  const item=clauses.find(item=>item.clause_id===check.clause_id);
  return `<li><strong>${esc(labels[check.status]||check.status)}</strong> · ${item?`<span class="tag">${esc(kinds[item.kind]||item.kind)}</span><p>${esc(item.source_quote)}</p>${item.instruction&&item.instruction!==item.source_quote?`<p>执行要求：${esc(item.instruction)}</p>`:''}`:`未知条款 ID：${esc(check.clause_id)}`}<p>理由：${esc(check.reason)}</p>${check.basis?.length?`<p>依据：</p><ul>${check.basis.map(text=>`<li>${esc(text)}</li>`).join('')}</ul>`:''}</li>`;
 }).join('');
 const requirementHTML=requirementChecks.map(check=>{
  const item=items.find(item=>item.requirement_id===check.requirement_id);
  return `<li><strong>${esc(labels[check.status]||check.status)}</strong> · ${item?`${item.kind?`<span class="tag">${esc(kinds[item.kind]||item.kind)}</span> `:''}${esc(item.text)}`:`未知要求 ID：${esc(check.requirement_id)}`}<p>理由：${esc(check.reason)}</p></li>`;
 }).join('');
 const unchecked=[...(result.unchecked_items||[]),...(result.unchecked||[]).map(description=>({description,importance:'unknown'}))];
 return `<article class="review-version"><strong>${esc(states[review.status]||review.status)}</strong>${reviewModeMetadataHTML(review,backendLabel)}<p>${esc(result.summary||'当前没有完整审阅结果')}</p><p class="help">${result.coverage_scan_complete?'已检查正文是否遗漏重要主张绑定':'重要主张覆盖尚未完成检查'}；审阅完成不代表全部要求已满足，正式交付另按当前版本的条件判断。</p>${review.requirement_index_error?`<p class="help">${esc(review.requirement_index_error)}</p>`:''}${unchecked.length?`<details class="review-checks" open><summary>尚未核验 · ${unchecked.length} 项</summary><ul>${unchecked.map(item=>`<li><span class="tag">${item.importance==='core'?'核心事项':item.importance==='supporting'?'非核心事项':'重要性未确定'}</span> ${esc(item.description)}</li>`).join('')}</ul></details>`:''}${clauseChecks.length?`<details class="review-checks" open><summary>条款核查结果 · ${clauseChecks.length} 项</summary><ul>${clauseHTML}</ul></details>`:''}${requirementChecks.length?`<details class="review-checks" open><summary>原始要求核查结果 · ${requirementChecks.length} 项</summary><ul>${requirementHTML}</ul></details>`:''}${!clauseChecks.length&&!requirementChecks.length?'<p class="help">尚无逐项要求核查结果。</p>':''}</article>`;
}
