// Saved feedback is a learning input of its own: list every item with where it is
// in the learning flow, on the learning page and as a count next to the save box.
export function createFeedbackList({$,esc,getState,openLearning}){
 const parse=value=>{try{return typeof value==='string'?JSON.parse(value):value||{}}catch{return {}}};
 const jobStatus={queued:'排队整理',running:'正在整理',complete:'已整理',failed:'整理失败',interrupted:'整理中断',cancelled:'整理已停止'};
 function rows(){
  const state=getState()||{},learnJobs=(state.jobs||[]).filter(job=>job.kind==='learn');
  return (state.feedback||[]).map(item=>{
   const data=parse(item.data),job=item.batch_id?learnJobs.find(j=>(parse(j.payload).feedback_ids||[]).includes(item.id)):null;
   const status=!item.batch_id?'待整理':job?jobStatus[job.status]||job.status:'已整理';
   const text=item.kind==='revision_edit'?(data.summary||data.text||'改稿记录'):(data.text||'');
   const kind=item.kind==='revision_edit'?'改稿':data.learning_intent==='explicit_requirement'?'必须保留的要求':'反馈';
   return {id:item.id,text,kind,status,pending:!item.batch_id,created:item.created};
  });
 }
 function render(){
  const items=rows(),pending=items.filter(item=>item.pending).length;
  const list=$('feedback-list');
  if(list){
   list.innerHTML=`<h2>已保存的反馈</h2><p class="help">${items.length?`共 ${items.length} 条，其中 ${pending} 条待整理。`:'还没有保存的反馈。'}待整理的反馈在你点“现在整理成经验”或开启自动学习后，由 Wiki 维护者整理；事实纠错只记录，不写进写作技巧。</p>`
    +(items.length?`<ul class="feedback-items">${items.map(item=>`<li><span class="chip">${esc(item.status)}</span> <span class="help">${esc(item.kind)} · ${esc((item.created||'').slice(0,16).replace('T',' '))}</span><p>${esc(item.text)}</p></li>`).join('')}</ul>`:'');
  }
  const hint=$('feedback-saved-hint');
  if(hint){
   hint.hidden=!items.length;
   hint.innerHTML=items.length?`已保存 ${items.length} 条反馈${pending?`，${pending} 条待整理`:''} · <button type="button" class="outline" data-feedback-open>查看全部</button>`:'';
   hint.querySelector('[data-feedback-open]')?.addEventListener('click',openLearning);
  }
 }
 return {render,rows};
}
