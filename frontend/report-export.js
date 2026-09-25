// Word/HTML/PDF report export: the Word job download and the standalone
// HTML/PDF export that must survive outside the app shell.
import {$,esc} from './dom.js';
import {Editor} from '@tiptap/core';
import {DOMSerializer} from '@tiptap/pm/model';
import StarterKit from '@tiptap/starter-kit';
import {TableKit} from '@tiptap/extension-table';
import {Markdown} from '@tiptap/markdown';
import {TextStyle,Layout,ReportImage,Citation,editorDocument} from './rich-document.js';
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
export function reportExportUI({api,notice,refresh,savedVersion,toEditor,parse,getState,getCurrent,getEditor}){
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
 async function exportPdf(html,title){
  const desktop=window.briefloopDesktop;
  if(typeof desktop?.exportPdf!=='function'){await printHtml(html);return}
  let result;
  try{result=await desktop.exportPdf({html,title})}
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
    const version=await savedVersion(),brief=getCurrent(),state=getState(),editor=getEditor();
    let doc;
    if(brief.editor_document)doc=parse(brief.editor_document);
    else {const tmp=new Editor({extensions:[StarterKit,TableKit,ReportImage,TextStyle,Layout,Citation,Markdown],content:toEditor(brief.markdown),contentType:'markdown'});try{doc=tmp.getJSON()}finally{tmp.destroy()}}
    const body=document.createElement('article');
    body.append(DOMSerializer.fromSchema(editor.schema).serializeFragment(editor.schema.nodeFromJSON(editorDocument(doc,version)).content));
    const cited=[];
    for(const a of body.querySelectorAll('a[href^="#source-"]')){
     const sid=a.getAttribute('href').slice(8);if(!cited.includes(sid))cited.push(sid);
     const number=cited.indexOf(sid)+1;
     a.setAttribute('href','#reference-'+number);
     // A numeric citation from an older draft still shows the workspace source
     // number; renumber it with the reference list. Named link text is kept.
     const label=a.textContent.trim(),numeric=/^([[(（【]?)\s*(\d+)\s*([\])）】]?)$/.exec(label);
     if(numeric)a.textContent=numeric[1]+number+numeric[3];
    }
    if(cited.length){const heading=document.createElement('h2');heading.textContent='来源';body.append(heading);const list=document.createElement('ol');
     for(const [i,sid] of cited.entries()){const source=state.sources.find(s=>s.id===sid);const row=document.createElement('li');row.id='reference-'+(i+1);const name=source?.name||'未关联来源';
      if(source?.url&&/^https?:\/\//i.test(source.url)){const link=document.createElement('a');link.href=source.url;link.textContent=name;row.append(link)}else row.textContent=name;
      list.append(row);
     }body.append(list);
    }
    // A saved HTML file opens outside the app: keep only web, mail and in-page links.
    for(const a of body.querySelectorAll('a[href]'))if(!/^(https?:|mailto:|#)/i.test(a.getAttribute('href')))a.removeAttribute('href');
    for(const img of body.querySelectorAll('img')){
     const url=new URL(img.getAttribute('src'),location.href);
     if(url.protocol==='data:')continue;
     if(url.origin!==location.origin)throw Error('图片尚未保存到工作区，无法生成独立文件');
     const res=await fetch(url);if(!res.ok)throw Error('图片读取失败');
     const blob=await res.blob();img.src=await new Promise((resolve,reject)=>{const r=new FileReader();r.onload=()=>resolve(r.result);r.onerror=reject;r.readAsDataURL(blob)});
    }
    const title=parse(brief.detail).title||'报告';
    const html='<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width"><title>'+esc(title)+'</title><style>body{font-family:-apple-system,BlinkMacSystemFont,"Segoe UI","PingFang SC",sans-serif;color:#1E2320;line-height:1.7;margin:40px auto;padding:0 24px;max-width:900px}table{border-collapse:collapse;width:100%}td,th{border:1px solid #DEDFD8;padding:6px 8px;vertical-align:top}td p,th p{margin:0}img{max-width:100%;height:auto}figure{margin:16px 0}figure p{margin:4px 0}a{color:#006838}h1,h2,h3{break-after:avoid}tr,img{break-inside:avoid}@page{size:A4;margin:20mm}@page briefloop-report{size:A4;margin:20mm}@media print{body{page:briefloop-report;margin:0;padding:0;max-width:none;font-size:11pt;line-height:1.55}p{margin:0 0 8pt}h1{font-size:20pt;margin:0 0 14pt}h2{font-size:14pt;margin:14pt 0 7pt}h3{font-size:12pt;margin:12pt 0 6pt}td,th{padding:4pt 6pt}td p,th p{margin:0}table{margin:8pt 0 12pt}thead{display:table-header-group}p{orphans:3;widows:3}figure{margin:10pt 0}img{max-height:220mm;object-fit:contain}}</style><body>'+body.innerHTML+'</body></html>';
    if(kind==='pdf')await exportPdf(html,title);
    else{const url=URL.createObjectURL(new Blob([html],{type:'text/html;charset=utf-8'}));const a=document.createElement('a');a.href=url;a.download=exportFileName(title)+'.html';document.body.append(a);a.click();a.remove();notice('HTML 已生成，正在下载');setTimeout(()=>URL.revokeObjectURL(url),60000)}
   }catch(e){notice('导出未完成：'+e.message,true)}
  });
 }
 return {init,downloadWord,exportPdf};
}
