// Render public research decisions, never model thoughts or a verification badge.
export function createResearchGoalProgress({esc}){
 const questions=p=>Array.isArray(p?.questions)?p.questions:[];
 function summary(progress){
  const rows=questions(progress);if(!rows.length)return '';
  const remaining=rows.filter(q=>q.recorded&&q.status!=='answered').length;
  const pending=rows.filter(q=>!q.recorded).length;
  return remaining?`${remaining} 个关键问题待补`:pending?`${pending} 个关键问题待研究`:'关键问题已有依据';
 }
 function html(progress){
  const rows=questions(progress);if(!rows.length)return '';
  return `<section class="research-goals" aria-label="关键问题覆盖"><h4>关键问题</h4><p class="help">以下是研究判断，有依据不代表已通过独立核查。</p><ul>${rows.map(q=>{
   const label=!q.recorded?'待研究':({answered:'有依据',partial:'部分有依据',checked_not_found:'查过未发现',read_failed:'读取失败',not_checked:'未检查'})[q.status]||'尚待回答';
   return `<li><div class="research-goal-heading"><span>${esc(q.question)}</span><span class="chip">${label}</span></div>${q.recorded?`<p>${esc(q.reason)}</p>`:''}${q.recorded&&q.remaining_question?`<p class="help">仍需核对：${esc(q.remaining_question)}</p>`:''}${(q.checked||[]).length?`<p class="help">已核对检索记录：${esc(q.checked.join('；'))}</p>`:''}${(q.self_reported||[]).length?`<p class="help">自述、未计量：${esc(q.self_reported.join('；'))}</p>`:''}${(q.flags||[]).includes('no_readable_source')?'<p class="help">标为有依据但没有可读来源，需核对。</p>':''}${(q.evidence||[]).map(ref=>`<div class="research-goal-evidence"><button type="button" class="ghost" data-progress-source="${esc(ref.source_id)}">${esc(ref.source_name)} ↗</button><span class="help">${esc(ref.locator)}${ref.source_changed?' · 来源已更新，以下保留当时摘录':''}</span><blockquote>${esc(ref.excerpt)}</blockquote></div>`).join('')}</li>`;
  }).join('')}</ul></section>`;
 }
 return {summary,html};
}
