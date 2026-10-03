// Word/HTML/PDF report export: the Word job download and the standalone
// HTML/PDF export that must survive outside the app shell.
import {$} from './dom.js';
export function exportFileName(title){return String(title??'').replace(/[\x00-\x1f<>:"/\\|?*]/g,'_').replace(/^[. ]+|[. ]+$/g,'').slice(0,120)||'报告'}
// Print from a sandboxed frame instead of a new window: the desktop shell
// denies window.open, and a frame needs no pop-up permission in browsers.
// Its load event already waits for the inline images; img.decode() would not
// settle here because browsers pause rendering in a hidden frame.
export function printHtml(html){
 printHtml.frame?.remove();
 const frame=document.createElement('iframe');printHtml.frame=frame;
 frame.setAttribute('sandbox','allow-same-origin allow-modals');frame.setAttribute('aria-hidden','true');frame.tabIndex=-1;
 frame.style.cssText='position:fixed;right:0;bottom:0;width:0;height:0;border:0;visibility:hidden';
 return new Promise((resolve,reject)=>{
  frame.onload=()=>{
   const view=frame.contentWindow;if(view?.location.href!=='about:srcdoc')return;
   try{
    view.addEventListener('afterprint',()=>{if(printHtml.frame===frame){frame.remove();printHtml.frame=null}},{once:true});
    view.focus();view.print();resolve();
   }catch(e){frame.remove();reject(e)}
  };
  frame.srcdoc=html;document.body.append(frame);
 });
}
export function reportExportUI({api,notice,refresh,savedVersion,parse,getState,getCurrent}){
 let wordDownloading=false;
 async function downloadWord(){
  if(wordDownloading)return;wordDownloading=true;const button=$('download-word');button.disabled=true;button.textContent='正在制作…';
  try{
   const version=await savedVersion(),workspace=getState().workspace_id;
   const override=$('export-template')?.value;
   let job=await api('export',{version_id:version,...(override?{template_id:override}:{})});
   while(['queued','running'].includes(job.status)){
    button.textContent=job.status==='queued'?'等待制作…':'正在制作…';
    await new Promise(resolve=>setTimeout(resolve,1000));
    if(getState().workspace_id!==workspace)throw Error('工作区已切换，请在原工作区下载');
    job=await api('export-status?job='+encodeURIComponent(job.id));
   }
   if(job.status!=='complete')throw Error(job.error||'Word 制作未完成，请重试');
   const link=document.createElement('a');link.href='/api/export-file?job='+encodeURIComponent(job.id)+'&workspace_id='+encodeURIComponent(workspace);link.download='';link.click();notice('Word 已生成，正在下载');await refresh();
  }catch(e){notice('Word 下载未完成：'+e.message,true)}finally{wordDownloading=false;button.disabled=false;button.textContent='下载 Word'}
 }
 async function exportPdf(html,title,context){
  const desktop=window.briefloopDesktop;
  if(typeof desktop?.exportPdf!=='function'){await printHtml(html);if(context?.label)notice('浏览器打印会保留可见 AI 标识；完整 PDF 元数据标识请使用桌面版导出');return}
  let result;
  try{result=await desktop.exportPdf({html,title,...(context?{version_id:context.version,workspace_id:context.workspace,market_convention:context.market}:{})})}
  catch(e){throw Error(String(e.message||e).replace(/^Error invoking remote method '[^']+': (Error: )?/,''))}
  if(result?.status==='saved')notice('PDF 已保存：'+result.name);
 }
 function init(){
  $('download-word').onclick=downloadWord;
  if(typeof window.briefloopDesktop?.exportPdf==='function'&&$('download-pdf'))$('download-pdf').textContent='导出 PDF';
  document.addEventListener('click',async event=>{
   const id=event.target.closest('button')?.id;if(!['download-html','download-pdf'].includes(id))return;
   const kind=id==='download-html'?'html':'pdf';
   try{
    // savedVersion() settles pending edits and returns the open draft's id, whose
    // full body is `current`; the report list does not need to carry bodies.
    const version=await savedVersion(),brief=getCurrent(),state=getState();
    const exportInfo=await api('export-label?version='+encodeURIComponent(version)+'&workspace_id='+encodeURIComponent(state.workspace_id));
    if(getState().workspace_id!==state.workspace_id)throw Error('工作区已切换，请在原工作区导出');
    // The server renders the same document model and citation order as Word;
    // the label call above still supplies PDF metadata context.
    const response=await fetch('/api/export-html?version='+encodeURIComponent(version)+'&workspace_id='+encodeURIComponent(state.workspace_id));
    let html;
    if(response.ok)html=await response.text();
    else{let message='导出失败';try{message=(await response.json()).error||message}catch(_){}throw Error(message)}
    const title=parse(brief.detail).title||'报告';
    if(kind==='pdf')await exportPdf(html,title,{version,workspace:state.workspace_id,label:exportInfo.label,market:exportInfo.market_convention});
    else{const url=URL.createObjectURL(new Blob([html],{type:'text/html;charset=utf-8'}));const a=document.createElement('a');a.href=url;a.download=exportFileName(title)+'.html';document.body.append(a);a.click();a.remove();notice('HTML 已生成，正在下载');setTimeout(()=>URL.revokeObjectURL(url),60000)}
   }catch(e){notice('导出未完成：'+e.message,true)}
  });
 }
 return {init,downloadWord,exportPdf};
}
