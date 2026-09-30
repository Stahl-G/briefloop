// Edit triage records preliminary positive flags, without a report-wide absence
// assessment or calibrated acceptance. Legacy automatic labels are not proof.
import {esc} from './dom.js';

export function verificationBadge(value){
 if(!value)return '';
 const untracked=value.status==='untracked'&&!value.registration_conflict;
 const status=untracked?'untracked':'pending';
 const label=untracked?'无可追踪改动':'待验收';
 const parts=untracked?['没有可追踪的改动']:['初步观察，未验收',`完整核验 ${value.observed||0} 份 · 未知 ${value.unknown??value.reports??0} 份`];
 if(value.flagged_reports)parts.push(`明确标出复发 ${value.repeats||0} 次（${value.flagged_reports} 份报告）`);
 if(value.excluded)parts.push(`范围不一致，排除 ${value.excluded} 份`);
 if(value.registration_conflict)parts.push('同一技能对应不同读者或批次，绑定待澄清');
 if(!untracked)parts.push(value.note||'尚未完成完整复发核验、双模型 family 校准、人工分歧处理与留出验收');
 if(value.legacy_status_corrected||['adopted','not_improving'].includes(value.status))parts.push(value.legacy_note||'旧自动状态不证明验收，原记录与技能保留');
 return `<span class="skill-verification skill-verification-${esc(status)}"><span class="tag">${esc(label)}</span><small>${esc(parts.join(' · '))}</small></span>`;
}
