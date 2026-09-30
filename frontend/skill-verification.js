// Skill verification (#858): whether an adopted skill actually reduced the
// edits it was learned from, measured on later reports.
import {esc} from './dom.js';

export function verificationBadge(value){
 if(!value)return '';
 const rate=value.rate==null?'':`复发率 ${Math.round(value.rate*100)}%`;
 const detail={
  pending:`未核验：已观察 ${value.observed}/${value.min_observed} 份后续报告${rate?' · '+rate:''}`,
  adopted:`${rate} · ${value.observed} 份报告`,
  not_improving:`${rate} · ${value.observed} 份报告，没有减少同类改动，可回退`,
  untracked:'只来自评论，没有可追踪的改动',
 }[value.status]||'';
 return `<span class="skill-verification skill-verification-${esc(value.status)}"><span class="tag">${esc(value.label)}</span><small>${esc(detail)}</small></span>`;
}
