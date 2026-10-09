// Everyday report creation starts with a conversation. This only prepares a
// draft: it never sends a message, spends a model budget or changes permissions.
export function reportStartUI({$,openChatHome,rememberDraft,updateComposer,page,notice,isBusy=()=>false}){
 async function start(){
  if(isBusy()){notice('请等待当前发送或上传完成');return}
  await openChatHome();
  const input=$('chat-input');
  // Navigation may restore an unsent draft. Do not overwrite it with a shortcut.
  if(!input.value.trim())input.value='我想做一份报告，';
  rememberDraft();updateComposer();input.focus();
  input.setSelectionRange?.(input.value.length,input.value.length);
 }
 function bind(){
  for(const id of ['new-report','home-start-report']){
   const button=$(id);if(button)button.onclick=()=>start().catch(error=>notice(error.message,true));
  }
  const previous=$('home-import-previous');
  if(previous)previous.onclick=()=>{if(isBusy()){notice('请等待当前发送或上传完成');return}$('previous-report-file')?.click()};
  const manual=$('home-report-form');if(manual)manual.onclick=()=>page('setup');
 }
 return {start,bind};
}
