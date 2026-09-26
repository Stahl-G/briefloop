import {esc} from './dom.js';

const localFiles=new Set(['export_docx','export_xlsx','release','audit_bundle']);
const parse=value=>{if(value&&typeof value==='object')return value;try{return JSON.parse(value||'{}')}catch{return {}}};
export const isLocalFileJob=job=>localFiles.has(job?.kind);
export function jobExecutionLabel(job,formatModel){
 if(isLocalFileJob(job))return '本地文件处理';
 if(job.kind==='source_extract')return '本地读取材料';
 if(job.kind==='source_refresh')return '来源工具';
 return parse(job.payload).runtime?formatModel(job):'旧任务：沿用当时本机配置';
}
export const jobResumeLabel=job=>isLocalFileJob(job)?'重新制作文件':job.kind==='source_extract'?'重新读取':'沿用原模型恢复';

// File exports have progress events but no model process or model settings.
export function createLocalFileProgress({element,api,action,taskLabel}){
 async function render(job,isCurrent){
  if(!isLocalFileJob(job))return false;
  const events=await api('events?job='+encodeURIComponent(job.id));
  if(!isCurrent())return true;
  const last=[...events].reverse().find(event=>event.kind==='export_progress');
  const progress=parse(last?.data),active=['queued','running'].includes(job.status);
  const stage=job.status==='queued'?'文件制作已排队':job.status==='running'?(progress.message||'正在制作文件'):job.status==='complete'?'文件已生成':job.status==='failed'?'文件制作未完成':'文件制作已停止';
  element.hidden=false;
  element.innerHTML=`<div class="section-title"><div><p class="eyebrow">${esc(taskLabel(job.kind))}</p><h2>${esc(stage)}</h2></div>${active?'<button class="outline" data-file-stop>停止任务</button>':''}</div><p class="help">在本机处理已保存的稿件，无需调用模型。</p>${job.error?`<p class="error">${esc(job.error)}</p>`:''}${!active&&job.status!=='complete'?'<button class="primary" data-file-resume>重新制作文件</button><button class="outline" data-file-dismiss>清除这个任务</button>':''}`;
  element.querySelector('[data-file-stop]')?.addEventListener('click',()=>action(()=>api('stop',{job_id:job.id})));
  element.querySelector('[data-file-resume]')?.addEventListener('click',()=>action(()=>api('resume',{job_id:job.id}),'已重新安排文件制作'));
  element.querySelector('[data-file-dismiss]')?.addEventListener('click',()=>action(()=>api('task-dismiss',{job_id:job.id}),'已清除这个未完成任务'));
  return true;
 }
 return {render};
}
