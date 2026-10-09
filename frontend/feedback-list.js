// Saved feedback is a learning input of its own. Show its bounded recent history
// with persisted learning status and workspace-wide counts next to the save box.
export function createFeedbackList({$,esc,getState,openLearning,startLearning}){
 const parse=value=>{try{return typeof value==='string'?JSON.parse(value):value||{}}catch{return {}}};
 const jobStatus={queued:'排队整理',running:'正在整理',complete:'已整理',failed:'整理失败',interrupted:'整理中断',cancelled:'整理已停止'};
 function rows(){
  const state=getState()||{},learnJobs=new Map((state.jobs||[]).filter(job=>job.kind==='learn').map(job=>[job.id,job]));
  return (state.feedback||[]).map(item=>{
   const data=parse(item.data),job=learnJobs.get(item.batch_id);
   const storedStatus='learning_status' in item?item.learning_status:job?.status;
   const status=!item.batch_id?'待整理':jobStatus[storedStatus]||'整理状态未知';
   const revision=item.kind==='revision'||item.kind==='revision_edit';
   const text=data.summary||data.text||data.verified_reason||(revision?'改稿记录':'反馈记录');
   const kind=revision?'改稿':item.kind==='review_correction'?'核查修订':data.learning_intent==='explicit_requirement'?'必须保留的要求':'反馈';
   return {id:item.id,text,kind,status,pending:!item.batch_id,created:item.created};
  });
 }
 function render(){
  const items=rows(),summary=getState()?.feedback_summary;
  const total=summary?.total??items.length,pending=summary?.pending??items.filter(item=>item.pending).length;
  const limited=total>items.length;
  const countText=summary?`共 ${total} 条，其中 ${pending} 条待整理。`:`最近 ${total} 条，其中 ${pending} 条待整理。`;
  const list=$('feedback-list');
  if(list){
   list.innerHTML=`<h2>已保存的反馈</h2><p class="help">${total?countText:'还没有保存的反馈。'}${limited?`仅显示最近 ${items.length} 条。`:''}待整理的反馈在你点“现在整理成经验”或开启自动学习后，由 Wiki 维护者整理；事实纠错只记录，不写进写作技巧。</p>`
    +(items.length?`<ul class="feedback-items">${items.map(item=>`<li><span class="chip">${esc(item.status)}</span> <span class="help">${esc(item.kind)} · ${esc((item.created||'').slice(0,16).replace('T',' '))}</span><p>${esc(item.text)}</p></li>`).join('')}</ul>`:'');
  }
  const hint=$('feedback-saved-hint');
  if(hint){
   hint.hidden=!total;
   hint.innerHTML=total?`${summary?'已保存':'最近已保存'} ${total} 条反馈${pending?`，${pending} 条待整理`:''} · <button type="button" class="outline" data-feedback-open>查看反馈</button>`:'';
   hint.querySelector('[data-feedback-open]')?.addEventListener('click',openLearning);
  }
 }
 return {render,rows};
}
