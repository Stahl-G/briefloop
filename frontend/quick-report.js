// New report requests let the backend choose their execution path.
export function prepareNewReportRequirements(requirements){
 const prepared={...requirements};
 for(const key of ['completion_mode','research_strategy'])delete prepared[key];
 if(['fast','fast_web','draft_first'].includes(requirements.completion_mode))delete prepared.target_minutes;
 return prepared;
}

// Saved draft-first and fast reports retain their original check continuation.
export function createQuickReport({$,api,action,notice,savedVersion,getCurrent,esc}){
 let ticket=0,busy=false;
 async function render(){
  const box=$('draft-completion'),current=getCurrent(),request=++ticket;
  if(!box)return;
  if(!current){box.hidden=true;return}
  try{
   const data=await api('completion-status?version='+encodeURIComponent(current.id));
   if(request!==ticket||getCurrent()?.id!==current.id)return;
   box.hidden=!['draft_first','fast','fast_web'].includes(data.mode);if(box.hidden)return;
   const labels={writing:'正在完成初稿，已保存的内容可以下载',deferred:'初稿已保存，完整核验尚未开始',checking:'正在继续检查，工作稿仍可下载',complete:'该保存版本已有检查完成记录',incomplete:'检查未全部完成，已有稿件保留',failed:'检查未完成，已有稿件保留',cancelled:'检查已停止，已有稿件保留',interrupted:'检查已中断，已有稿件保留'};
   const retry=['failed','cancelled','interrupted'].includes(data.state);
   const button=retry?'<button type="button" class="outline" data-continue-checks="resume">恢复检查</button>':data.state==='deferred'?'<button type="button" class="outline" data-continue-checks="start">继续完整检查</button>':'';
   const help=['fast','fast_web'].includes(data.mode)?'快速初稿不代表事实已核实。后台补充依据和评价，不自动改写正文；你的编辑优先保留。检查仅适用于对应保存版本，正式交付仍需独立审阅。':'先交初稿只做结构、引用、数字与版式的自动检查，不代表事实已核实。继续检查使用原模型与联网选择，按原设置决定是否进行本轮最多一次自动修订；正式交付仍需独立审阅通过。';
   const label=['fast','fast_web'].includes(data.mode)&&data.state==='checking'?'初稿已保存，后台正在补充依据和评价':labels[data.state];
   box.innerHTML=`<p><strong>${esc(label||'检查状态待确认')}</strong></p><p class="help">${esc(help)}</p>${data.checked_version&&data.checked_version!==current.id?'<p class="help">当前检查对应先前保存的版本，你的新修改尚未检查。</p>':''}${data.error?`<p class="help">${esc(data.error)}</p>`:''}${button}`;
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
  const versionLabel=brief=>brief?.author==='agent'&&brief?.id?.endsWith('_evidence')?'依据补全 · 正文未改':'修订稿';
  return {render,versionLabel};
}
