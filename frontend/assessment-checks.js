// Saved model-evaluation observations, distinct from independent review.
export function createAssessmentChecks({esc}){
 const labels={passed:'本项已检查',needs_attention:'仍有问题待处理',not_checked:'尚未复核',failed:'本项未通过',incomplete:'检查未完成',disputed:'原发现有异议','n/a':'不适用'};
 function render(assessment){
  const checks=Array.isArray(assessment.checks)?assessment.checks.filter(c=>c&&typeof c==='object'):[];
  if(!checks.length)return '';
  const pending=checks.filter(c=>!['passed','n/a'].includes(c.status||c.result)).length;
  return `<details class="help assessment-checks" ${pending?'open':''}><summary>评价检查记录${pending?` · ${pending} 项需查看`:''}</summary><p>这是模型评价的检查记录，不等于独立审阅或联网事实核查完成；具体未解决问题见下方发现。</p>${checks.map(c=>`<div class="finding"><strong>${esc(c.name||'未命名检查')}</strong> <span class="tag">${esc(labels[c.status||c.result]||'状态未明确')}</span>${c.prior_description?`<p>上次问题：${esc(c.prior_description)}</p>`:''}${(c.reason||c.detail)?`<p>${esc(c.reason||c.detail)}</p>`:''}${c.consistency_note?`<p>${esc(c.consistency_note)}</p>`:''}${c.report_quote?`<blockquote>${esc(c.report_quote)}</blockquote>`:''}</div>`).join('')}</details>`;
 }
 return {render};
}
