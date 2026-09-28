// Draft-first is a completion policy, separate from research depth and length.
// Reuse the shared panel/help/buttons; no independent design tokens.
export function createQuickReport({$,api,action,notice,savedVersion,getCurrent,esc}){
 let ticket=0,busy=false;
 function sync(){
  const enabled=$('completion-mode')?.value==='draft_first';
  if($('draft-target-label'))$('draft-target-label').hidden=!enabled;
  if($('draft-target'))$('draft-target').disabled=!enabled;
 }
 function init(){
  $('completion-mode')?.addEventListener('change',sync);
  $('requirements')?.addEventListener('reset',()=>queueMicrotask(sync));
  sync();
 }
 function read(){
  sync();
  return $('completion-mode')?.value==='draft_first'
   ?{completion_mode:'draft_first',target_minutes:Number($('draft-target').value)}
   :{completion_mode:'standard'};
 }
 async function render(){
  sync();
  const box=$('draft-completion'),current=getCurrent(),request=++ticket;
  if(!box)return;
  if(!current){box.hidden=true;return}
  try{
   const data=await api('completion-status?version='+encodeURIComponent(current.id));
   if(request!==ticket||getCurrent()?.id!==current.id)return;
   box.hidden=data.mode!=='draft_first';if(box.hidden)return;
   const labels={writing:'正在完成初稿，已保存的内容可以下载',deferred:'初稿已保存，完整核验尚未开始',checking:'正在继续检查，工作稿仍可下载',complete:'该保存版本已有检查完成记录',incomplete:'检查未全部完成，已有稿件保留',failed:'检查未完成，已有稿件保留',cancelled:'检查已停止，已有稿件保留',interrupted:'检查已中断，已有稿件保留'};
   const retry=['failed','cancelled','interrupted'].includes(data.state);
   const button=retry?'<button type="button" class="outline" data-continue-checks="resume">恢复检查</button>':data.state==='deferred'?'<button type="button" class="outline" data-continue-checks="start">继续完整检查</button>':'';
   box.innerHTML=`<p><strong>${esc(labels[data.state]||'检查状态待确认')}</strong></p><p class="help">先交初稿只做结构、引用、数字与版式的自动检查，不代表事实已核实。继续检查使用原模型与联网选择，按原设置决定是否进行本轮最多一次自动修订；正式交付仍需独立审阅通过。</p>${data.error?`<p class="help">${esc(data.error)}</p>`:''}${button}`;
   const control=box.querySelector('[data-continue-checks]');
   if(control){control.disabled=busy;control.onclick=()=>action(async()=>{
    if(busy)return;busy=true;control.disabled=true;
    try{
     // Save user edits first. A saved new version starts its own exact check
     // binding; resuming a failed job must not silently follow a changed draft.
     const version=await savedVersion();
     if(retry&&version!==current.id)throw Error('已保存你的修改，请对新版本继续检查，不能恢复旧稿的核验。');
     const job=await api(retry?'resume':'continue-checks',retry?{job_id:data.job_id}:{version_id:version});
     notice(['queued','running'].includes(job.status)?'检查任务已排队，初稿可以继续阅读和下载。':'该版本已有检查记录，请查看检查状态。');
    }finally{busy=false;await render()}
   })}
  }catch(error){
   if(request!==ticket)return;
   box.hidden=false;box.textContent='暂时无法读取初稿检查状态：'+error.message;
  }
 }
 return {init,read,render,sync};
}
