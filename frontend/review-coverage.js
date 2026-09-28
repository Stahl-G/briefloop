import {esc} from './dom.js';

const labels={supported_for_scope:'证据支持（限所查范围）',contradicted:'证据矛盾',insufficient_evidence:'证据不足',unknown:'无法判断'};

export function reviewCoverageHTML(review){
 const result=review.result||{},checks=Array.isArray(result.claim_checks)?result.claim_checks:[];
 if(!checks.length)return '<p class="help">尚无逐条结论核查记录；未列出问题不表示全文已核实。</p>';
 const items=new Map((review.claim_items||[]).map(item=>[item.claim_id,item.statement]));
 const counts=new Map();for(const check of checks)counts.set(check.status,(counts.get(check.status)||0)+1);
 const summary=[...counts].map(([key,count])=>`${labels[key]||'状态未知'} ${count}`).join(' · ');
 return `<details class="review-checks" open data-testid="review-claim-coverage"><summary>结论核查范围 · ${checks.length} 条</summary><p class="help">${esc(summary)}。仅统计本次返回的结论，不是全稿正确率；未查事项另列。</p><ul>${checks.map((check,index)=>`<li><strong>${esc(labels[check.status]||'状态未知')}</strong><blockquote>${esc(items.get(check.claim_id)||`第 ${index+1} 条结论（原文索引不可用）`)}</blockquote><p>${esc(check.reason||'未记录判断依据')}</p></li>`).join('')}</ul></details>`;
}
