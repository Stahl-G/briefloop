// Explicit writing agreements are local saves. Only existing learning controls
// can request the separately authorized model validation.
export function learningMessage(job){
 if(!job)return '';
 const labels={queued:'改进方法已排队，尚未开始验证。',running:'正在试写比较改进方法，结果会保留在这里。',failed:'方法验证失败，反馈已保留。',interrupted:'方法验证已中断，反馈与进度已保留。',cancelled:'方法验证已停止，反馈已保留。'};
 if(labels[job.status])return labels[job.status];
 if(job.status!=='complete')return '反馈状态待确认，可查看详情。';
 let result;try{result=typeof job.result==='string'?JSON.parse(job.result):job.result}catch{return '整理已结束，采用结果未记录。'}
 const history=result?.history||[];
 if(history.some(x=>x.accepted===true))return '比较完成，已采用改进方法。可查看依据或回退。';
 if(history.some(x=>x.pairs?.length))return '比较完成，未发现足够改善，继续使用原方法。';
 if(result?.comparison_skipped||history.some(x=>x.no_action))return '反馈已整理，本次未采用新的写作方法。';
 return '整理已结束，采用结果未记录。';
}

export function reportFeedbackUI({$,api,esc,getCurrent,savedVersion,openLearning,refresh,notice}){
 let sequence=0,busy=false;
 const result=$('agreement-result'),list=$('writing-agreements'),button=$('comment-submit');
 function status(text,error=false){result.textContent=text;result.hidden=!text;result.classList.toggle('error',error)}
 async function render(){
  const version=getCurrent()?.id,seq=++sequence;
  if(!version){list.hidden=true;$('feedback-next-step').hidden=true;return}
  try{
   const data=await api('writing-agreements?version_id='+encodeURIComponent(version));
   if(seq!==sequence||getCurrent()?.id!==version)return;
   list.hidden=false;
   list.innerHTML=`<summary>下期沿用的约定 · ${data.items.length}</summary><p class="help">更改只影响新任务。</p>`+
    (data.items.length?`<ul class="feedback-items">${data.items.map(x=>`<li><p>${esc(x.text)}</p><span class="help">${x.scope==='workspace'?'本工作区所有新报告':'这份报告及后续期'}</span> <button type="button" class="ghost" data-revoke-agreement="${esc(x.id)}">撤销</button></li>`).join('')}</ul>`:'<p class="help">还没有保存约定。在上方写明要求，点“下期沿用”即可记住。</p>');
   for(const node of list.querySelectorAll('[data-revoke-agreement]'))node.onclick=async()=>{
    if(busy)return;busy=true;node.disabled=true;
    try{await api('writing-agreements/revoke',{id:node.dataset.revokeAgreement});status('已撤销，后续新任务不再沿用。');await render()}
    catch(e){status(e.message,true)}finally{busy=false;node.disabled=false}
   };
   const message=[learningMessage(data.learning),data.pending_feedback?`${data.pending_feedback} 条改稿反馈已保存，尚未开始方法验证。`:''].filter(Boolean).join(' '),next=$('feedback-next-step');next.hidden=!message;
   next.innerHTML=message?`<span>${esc(message)}</span> <button type="button" class="outline" data-feedback-open>查看依据与详情</button>`:'';
   next.querySelector('[data-feedback-open]')?.addEventListener('click',openLearning);
  }catch(e){if(seq===sequence)status('约定暂未读取：'+e.message,true)}
 }
 async function save(){
  if(busy)return;
  const input=$('assistant-input'),text=input.value.trim(),original=getCurrent()?.id,run=getCurrent()?.run_id,scope=$('agreement-scope').value;
  if(!text){status('先在上方写明下期要沿用的要求。',true);input.focus();return}
  busy=true;button.disabled=true;button.textContent='正在记住…';status('');
  try{
   const version=await savedVersion();
   if(getCurrent()?.id!==version||getCurrent()?.run_id!==run)throw Error('报告已切换，请在目标报告中保存约定');
   await api('writing-agreements',{version_id:version,text,scope});
   if(getCurrent()?.id!==version)return;
   if(input.value.trim()===text)input.value='';
   status('已记住，下次生成时沿用。');list.open=true;await render();await refresh();
  }catch(e){if(getCurrent()?.id===original)status(e.message,true);else notice(e.message,true)}
  finally{busy=false;button.disabled=false;button.textContent='下期沿用'}
 }
 button.onclick=save;
 return {render,save};
}
