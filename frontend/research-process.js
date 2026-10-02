import {esc} from './dom.js';

const activeStates=new Set(['queued','running']);
const warnings=new Set(['warn','warning','gap','paused','skipped','unknown','pending_init']);
const errors=new Set(['error','failed','errored','conflict','contradiction']);
const completed=new Set(['done','complete','completed','closed','recorded']);
const labels={done:'已完成',active:'进行中',pending:'待执行',warn:'有缺口',error:'有冲突或错误'};
export function elapsedText(start,end=Date.now()){
 const seconds=Math.max(0,Math.floor((Number(end)-Date.parse(start))/1000));
 if(!Number.isFinite(seconds))return '时间待确认';
 return seconds<60?`${seconds} 秒`:`${Math.floor(seconds/60)} 分 ${seconds%60} 秒`;
}
function stepState(value,active){
 if(errors.has(value))return 'error';
 if(warnings.has(value))return 'warn';
 if(completed.has(value))return 'done';
 return ['running','active'].includes(value)?(active?'active':'pending'):'pending';
}
function stepTime(step,now){
 if(step.not_started)return '尚未开始';
 const duration=step.duration_ms??(step.duration_seconds==null?null:Number(step.duration_seconds)*1000);
 if(duration!==null&&Number.isFinite(Number(duration))&&Number(duration)>=0)return elapsedText('1970-01-01T00:00:00Z',Number(duration));
 if(step.started&&(step.ended||step.status==='active'))return elapsedText(step.started,step.ended?Date.parse(step.ended):now);
 return '用时未记录';
}
// This is an allowlist projection of public action records. Never render runtime
// message / thinking / reasoning fields or raw worker activity/output here.
export function processSteps(job,p={}){
 const active=activeStates.has(job.status),steps=[];
 for(const item of p.timeline||[])if(item?.label)steps.push({...item,label:String(item.label),detail:item.detail||'',status:stepState(item.status,active)});
 if(!steps.length)for(const item of p.stages||[])if(item?.label)steps.push({...item,label:String(item.label),detail:'',status:stepState(item.status,active)});
 for(const item of p.agents||[])if(item?.task||item?.role)steps.push({label:item.task||item.role,detail:'',status:stepState(item.status,active),started:item.started,ended:item.ended,duration_ms:item.duration_ms});
 for(const [kind,items] of [['warn',p.gaps||[]],['error',p.conflicts||[]]])for(const item of items){const text=typeof item==='string'?item:item?.text||item?.description||item?.question;if(text)steps.push({label:kind==='warn'?'研究缺口':'来源冲突',detail:String(text),status:kind});}
 return steps;
}
export function processDisclosureKey(job,p={}){
 const attention=processSteps(job,p).filter(step=>['warn','error'].includes(step.status)).map(step=>[step.label,step.detail]);
 return JSON.stringify([job.id,activeStates.has(job.status)?'active':job.status,attention]);
}
export function researchProcessHTML(job,p={}, {expanded,now=Date.now()}={}){
 const active=activeStates.has(job.status),steps=processSteps(job,p),attention=steps.some(step=>['warn','error'].includes(step.status));
 const title=active?(p.needs_attention?'等待你的确认':p.stage||'等待开始'):job.status==='complete'?'研究完成':{failed:'研究未完成',interrupted:'研究已中断',cancelled:'研究已停止'}[job.status]||'研究过程';
 const timing=job.status==='queued'?'尚未执行':p.started?`用时 ${elapsedText(p.started,active?now:Date.parse(p.ended||job.updated))}`:'执行用时未记录';
 const opened=typeof expanded==='boolean'?expanded:attention;
 const visibleSteps=attention?[...steps].sort((a,b)=>Number(['warn','error'].includes(b.status))-Number(['warn','error'].includes(a.status))):steps;
 return `<details class="research-process task-progress-detail" data-testid="research-process" data-process-state="${esc(processDisclosureKey(job,p))}" data-task-detail="${esc(job.id)}" ${opened?'open':''}><summary><span class="ai-mark" aria-hidden="true"></span><span class="research-process-title">${esc(title)} · ${steps.length} 步 · ${esc(timing)}</span>${attention?'<span class="research-process-attention">需要关注</span>':''}</summary><ol class="research-process-steps" aria-label="可核对的执行动作">${visibleSteps.map(step=>`<li class="process-step is-${step.status}" ${['warn','error'].includes(step.status)?'data-process-attention="true"':''}><span class="process-step-dot" aria-hidden="true"></span><span class="process-step-description"><span class="sr-only">${labels[step.status]}：</span>${esc(step.label)}${step.detail?`<span class="process-step-detail">${esc(step.detail)}</span>`:''}</span><span class="process-step-time">${esc(stepTime(step,now))}</span></li>`).join('')||'<li class="process-step-empty">尚无可核对的执行记录</li>'}</ol></details>`;
}
