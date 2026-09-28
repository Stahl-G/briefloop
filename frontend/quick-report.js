// Draft-first is a completion policy, separate from research depth and length.
// Reuse the shared panel/help/buttons; no independent design tokens.
export function createQuickReport({$,api,action,notice,savedVersion,getCurrent,esc,syncSourceHints=()=>{}}){
 let ticket=0,busy=false;
 function sync(){
  const mode=$('completion-mode')?.value,enabled=['draft_first','fast','fast_web'].includes(mode),fast=['fast','fast_web'].includes(mode);
  if($('draft-target-label'))$('draft-target-label').hidden=!enabled;
  if($('draft-target'))$('draft-target').disabled=!enabled;
  for(const node of $('requirements')?.querySelectorAll?.('[name="allow_web"], [name="fact_check"], [name="research_tier"], #auto-revision, #company-mode')||[]){
   if(fast&&!node.hasAttribute('data-fast-disabled')){
    node.setAttribute('data-fast-disabled',String(node.disabled));node.disabled=true;
    if(node.type==='checkbox'){node.setAttribute('data-fast-checked',String(node.checked));node.checked=false}
    else{node.setAttribute('data-fast-value',node.value);node.value=node.id==='company-mode'?'off':'quick'}
   }else if(!fast&&node.hasAttribute('data-fast-disabled')){
    node.disabled=node.getAttribute('data-fast-disabled')==='true';node.removeAttribute('data-fast-disabled');
    if(node.hasAttribute('data-fast-checked')){node.checked=node.getAttribute('data-fast-checked')==='true';node.removeAttribute('data-fast-checked')}
    if(node.hasAttribute('data-fast-value')){node.value=node.getAttribute('data-fast-value');node.removeAttribute('data-fast-value')}
   }
  }
  if(fast){const web=$('requirements')?.querySelector?.('[name="allow_web"]');if(web)web.checked=mode==='fast_web'}
  for(const node of $('requirements')?.querySelectorAll?.('[data-report-options="setup"], #research-budget-controls, .setup-search-launch')||[]){
   const hide=fast&&!(node.matches?.('.setup-search-launch')&&mode==='fast_web');
   if(hide&&!node.hasAttribute('data-fast-hidden')){node.setAttribute('data-fast-hidden',String(node.hidden));node.hidden=true}
   else if(!hide&&node.hasAttribute('data-fast-hidden')){node.hidden=node.getAttribute('data-fast-hidden')==='true';node.removeAttribute('data-fast-hidden')}
  }
  if($('completion-mode-help'))$('completion-mode-help').textContent=mode==='fast_web'
   ?'一轮聚焦检索，最多 3 次搜索、读取 6 篇原文，随后直接出稿。无需先上传材料；可在搜索设置选择渠道。保存后在后台补依据和评价，不自动改写正文；十分钟是目标，非保证或截止。'
   :mode==='fast'
   ?'已有文本材料直接出稿，保存后自动在后台补充依据和评价；可立即编辑、下载。此模式不联网补搜、不维护企业背景、不自动改写正文。材料较多时可能需要更久，不保证固定时限。'
   :'完整流程按所选研究深度检索、写作和检查。先交研究初稿的旧任务仍可手动继续完整检查。';
  if(fast&&$('review-capability-note'))$('review-capability-note').hidden=true;
 }
 function init(){
  $('completion-mode')?.addEventListener('change',()=>{sync();syncSourceHints()});
  $('requirements')?.addEventListener('reset',()=>queueMicrotask(sync));
  sync();
 }
 function read(){
  sync();
  const mode=$('completion-mode')?.value;
  return ['draft_first','fast','fast_web'].includes(mode)
   ?{completion_mode:mode,target_minutes:Number($('draft-target').value),...(['fast','fast_web'].includes(mode)?{research_tier:'quick',allow_web:mode==='fast_web',fact_check:false}:{})}
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
  return {init,read,render,sync,versionLabel};
}
