import {citationNumbers} from './citation-sources.js';
// Claim-to-source evidence reuses the shared help/finding/details components.
export function createCitationEvidence({esc}){
 function render(refs,sources=[],markdown=''){
  if(!refs.length)return '尚无引用记录';
  const numbers=citationNumbers(markdown);
  for(const ref of refs)if(!numbers.has(ref.source_id))numbers.set(ref.source_id,numbers.size+1);
  const name=r=>sources.find(s=>s.id===r.source_id)?.name||r.source_title||r.source_id;
  const link=r=>`<button type="button" data-source="${esc(r.source_id)}" aria-label="来源 ${numbers.get(r.source_id)}:${esc(name(r))}"><span class="source-number">${numbers.get(r.source_id)}</span> ${esc(name(r))} · ${esc(r.locator||'')}</button>`;
  const unique=[...new Map(refs.map(r=>[r.source_id,r])).values()];
  const bound=refs.filter(r=>r.report_quote&&r.excerpt);
  return '引用来源 '+unique.map(link).join('')+(bound.length
   ?`<details class="help"><summary>结论与原文依据 · ${bound.length} 条</summary><p>逐字摘录与定位不等于结论得到支持；语义核对结果见评价检查记录。以下对应已保存版本。</p>${bound.map(r=>`<details class="finding"><summary>${esc(r.report_quote.slice(0,100))}</summary><p><strong>正文结论</strong></p><blockquote>${esc(r.report_quote)}</blockquote>${!markdown.includes(r.report_quote)?'<p class="help">对应正文已变化，需重新核对。</p>':''}<p><strong>原文摘录</strong></p><blockquote>${esc(r.excerpt)}</blockquote>${link(r)}${r.source_context?`<p class="help">来源上下文 · ${esc(r.source_title||name(r))} · ${esc(r.context_locator||'')}</p><blockquote>${esc(r.source_context).replaceAll('\n','<br>')}</blockquote>`:''}</details>`).join('')}</details>`:'');
 }
 return {render};
}
