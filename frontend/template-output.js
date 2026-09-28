import {$,esc} from './dom.js';
import {dateTimeSeconds} from './time.js';

const running=job=>['queued','running'].includes(job?.status);
const object=value=>{try{return typeof value==='string'?JSON.parse(value):value||{}}catch{return {}}};
const title=brief=>object(brief?.detail).title||brief?.title||'未命名报告';

// Each operation keeps its chosen workspace, template and saved version. Closing
// the dialog hides it; an accepted export keeps running and can be reopened.
export function createTemplateOutput({api,notice,refresh,getState,openBrief,uploadPayload,getUploadLimits,getCurrent=()=>null,savedVersion}){
 let dialog=null,active=null,epoch=0;
 const flows=new Map();
 const current=flow=>flow.epoch===epoch&&getState()?.workspace_id===flow.workspace;
 const visible=flow=>active===flow&&dialog?.open&&current(flow);
 function ensureDialog(){
  if(dialog)return;
  dialog=document.createElement('dialog');dialog.id='template-output-dialog';dialog.className='template-output-dialog';
  dialog.setAttribute('aria-labelledby','template-output-title');dialog.dataset.testid='template-output-dialog';
  dialog.addEventListener('cancel',event=>{event.preventDefault();close()});
  document.body.append(dialog);
 }
 function close(){dialog?.close()}
 function reset(){
  ++epoch;for(const flow of flows.values())clearTimeout(flow.timer);
  flows.clear();active=null;close();
 }
 function stopStale(flow){clearTimeout(flow.timer);flow.busy=false;return false}
 function isCurrent(flow){return current(flow)||stopStale(flow)}
 function selected(flow){return flow.items.find(item=>item.id===flow.selectedId)}
 function render(flow){
  if(!visible(flow))return;
  const complete=flow.job?.status==='complete',hasJob=Boolean(flow.job),disabled=flow.busy||complete;
  const status=flow.error||flow.message||'',focused=document.activeElement,focusId=dialog.contains?.(focused)?focused?.id:null;
  dialog.innerHTML=`<div class="section-title"><h2 id="template-output-title">${flow.mode==='upload'?'上传原稿套用':'从已有报告套用'}</h2><button type="button" id="template-output-close" data-testid="template-output-close" class="ghost" aria-label="关闭套用面板">关闭</button></div>`
   +`<p class="template-output-choice">版式：<strong>${esc(flow.template.name.replace('·',' · '))}</strong><span class="help">输出 Word（.docx）</span></p>`
   +(flow.mode==='upload'?`<p class="help">支持 DOCX、Markdown、TXT 的正文、标题、列表和表格；不联网、不改写。复杂 Word 内容暂不支持，原文件会保留。</p><label class="template-output-field" ${flow.file?'hidden':''}>选择要排版的原稿<input type="file" id="template-output-file" data-testid="template-output-file" accept=".docx,.md,.markdown,.txt" ${disabled||hasJob?'disabled':''}></label>${flow.file?`<div class="template-output-file-choice"><p class="help">已选原稿：${esc(flow.file.name)}</p>${disabled||hasJob?'':'<button type="button" id="template-output-change-file" data-testid="template-output-change-file" class="outline">更换文件</button>'}</div>`:''}`
   :`<p class="help">选择当前工作区的报告，以选中的已保存版本生成 Word。若该报告正在编辑，会先保存当前修改。</p><form id="template-output-search-form" class="template-output-search"><label for="template-output-search">查找报告</label><input type="search" id="template-output-search" data-testid="template-output-search" placeholder="搜索标题或正文" maxlength="200" value="${esc(flow.query)}" ${disabled||hasJob?'disabled':''}><button type="submit" class="outline" ${disabled||hasJob?'disabled':''}>查找</button></form><label class="template-output-field">选择报告的已保存版本<select id="template-output-report" data-testid="template-output-report" ${disabled||hasJob||flow.loading?'disabled':''}><option value="">${flow.loading?'正在读取报告…':'请选择一份报告'}</option>${flow.items.map(item=>`<option value="${esc(item.id)}" ${flow.selectedId===item.id?'selected':''}>${esc(title(item))} · ${esc(dateTimeSeconds(item.created)||'保存时间未知')}</option>`).join('')}</select></label>${!flow.loading&&!flow.items.length?`<p class="help">${flow.query?'没有找到符合搜索条件的报告。':'当前工作区还没有已保存报告。'}</p>`:''}${flow.listError?`<p class="template-output-error" role="alert">${esc(flow.listError)}</p><button type="button" id="template-output-reload" class="outline">重新读取报告</button>`:''}${flow.cursor?`<button type="button" id="template-output-more" data-testid="template-output-more" class="ghost" ${disabled||hasJob||flow.loading?'disabled':''}>${flow.loading?'正在读取…':'加载更多报告'}</button>`:''}`)
   +`<p id="template-output-status" data-testid="template-output-status" class="template-output-status${flow.error?' template-output-error':''}" role="status" aria-live="polite">${esc(status)}</p>`
   +(flow.notes.length?`<ul class="template-output-notes">${flow.notes.map(note=>`<li>${esc(typeof note==='string'?note:note?.message||note?.description||String(note))}</li>`).join('')}</ul>`:'')
   +`<div class="template-output-actions">${complete?`<a class="primary template-output-download" id="template-output-download" data-testid="template-output-download" href="/api/export-file?${new URLSearchParams({job:flow.job.id,workspace_id:flow.workspace})}" download>下载 Word</a><button type="button" id="template-output-open" data-testid="template-output-open" class="outline">${flow.mode==='upload'?'打开转换稿':'打开所选报告'}</button><button type="button" id="template-output-again" class="ghost">${flow.mode==='upload'?'套用另一份原稿':'套用另一份报告'}</button>`
    :flow.pollError?'<button type="button" id="template-output-poll" data-testid="template-output-poll" class="primary">重新检查进度</button>'
    :`<button type="button" id="template-output-submit" data-testid="template-output-submit" class="primary" ${flow.busy||flow.loading||!(flow.mode==='upload'?flow.file:flow.selectedId)?'disabled':''}>${flow.busy?'正在制作…':hasJob?'重试生成 Word':'套用并生成 Word'}</button>`}</div>`
   +(flow.busy?'<p class="help">关闭面板不会停止已提交的制作任务；再次点击同一模板的套用入口可查看进度。</p>':'');
  $('template-output-close').onclick=close;
  if($('template-output-file'))$('template-output-file').onchange=event=>{
   flow.file=event.target.files?.[0]||null;flow.version=null;flow.error='';flow.message='';
   if(flow.file&&!/\.(docx|md|markdown|txt)$/i.test(flow.file.name)){flow.file=null;flow.error='请选择 DOCX、Markdown 或 TXT 原稿。'}render(flow);
  };
  if($('template-output-change-file'))$('template-output-change-file').onclick=()=>$('template-output-file').click();
  if($('template-output-search-form'))$('template-output-search-form').onsubmit=event=>{event.preventDefault();flow.query=$('template-output-search').value.trim();flow.selectedId='';flow.version=null;fetchReports(flow)};
  if($('template-output-report'))$('template-output-report').onchange=event=>{flow.selectedId=event.target.value;flow.version=null;flow.error='';render(flow)};
  if($('template-output-more'))$('template-output-more').onclick=()=>fetchReports(flow,true);
  if($('template-output-reload'))$('template-output-reload').onclick=()=>fetchReports(flow);
  if($('template-output-submit'))$('template-output-submit').onclick=()=>submit(flow);
  if($('template-output-poll'))$('template-output-poll').onclick=()=>{if(flow.busy)return;flow.busy=true;flow.pollError=false;flow.error='';render(flow);poll(flow)};
  if($('template-output-download'))$('template-output-download').onclick=event=>{if(!current(flow)){event.preventDefault();notice('工作区已切换，请回到原工作区下载。',true)}};
  if($('template-output-open'))$('template-output-open').onclick=async()=>{
   if(!isCurrent(flow))return;
   const button=$('template-output-open');button.disabled=true;
   try{if(await openBrief(flow.version))close()}
   catch(error){if(visible(flow))notice(error.message,true)}
   finally{if(visible(flow))button.disabled=false}
  };
  if($('template-output-again'))$('template-output-again').onclick=()=>{
   flow.job=null;flow.version=null;flow.file=null;flow.selectedId='';flow.error='';flow.message='';flow.notes=[];render(flow);if(flow.mode==='report')fetchReports(flow);
  };
  if(focusId)(focusId==='template-output-file'&&flow.file?$('template-output-change-file'):$(focusId))?.focus?.();
 }
 async function fetchReports(flow,append=false){
  if(!isCurrent(flow)||flow.busy||flow.job)return;
  const ticket=++flow.listTicket,query=flow.query;
  flow.loading=true;flow.listError='';render(flow);
  const params=new URLSearchParams({q:query,workspace_id:flow.workspace});if(append&&flow.cursor)params.set('cursor',flow.cursor);
  try{
   const result=await api('reports?'+params);
   if(!isCurrent(flow)||ticket!==flow.listTicket)return;
   const items=Array.isArray(result.items)?result.items:[];
   flow.items=append?[...flow.items,...items.filter(item=>!flow.items.some(old=>old.id===item.id))]:items;
   flow.cursor=result.next_cursor||'';
   if(!flow.items.some(item=>item.id===flow.selectedId))flow.selectedId='';
  }catch(error){if(current(flow)&&ticket===flow.listTicket)flow.listError='报告读取失败：'+error.message}
  finally{if(current(flow)&&ticket===flow.listTicket){flow.loading=false;render(flow)}}
 }
 function acceptJob(flow,job){
  if(!job?.id)throw Error('未收到文件制作任务，请检查报告列表后再试。');
  flow.job=job;
  if(job.status==='complete'){
   flow.busy=false;flow.pollError=false;flow.error='';flow.message=flow.mode==='upload'?'Word 已生成，转换稿已保存。':'Word 已生成。';render(flow);
   Promise.resolve().then(()=>current(flow)&&refresh?.()).catch(()=>{if(current(flow)){flow.message+=' 报告列表暂未刷新，可稍后重试。';render(flow)}});
   return;
  }
  if(!running(job)){
   flow.busy=false;flow.pollError=false;flow.error=job.error||({cancelled:'Word 制作已取消，可重试。',interrupted:'Word 制作已中断，可重试。',failed:'Word 制作失败，可重试。'}[job.status]||'Word 尚未制作完成，请重试。');render(flow);return;
  }
  const message=job.status==='queued'?'已提交，等待制作 Word…':'正在制作 Word…';
  if(flow.message!==message){flow.message=message;render(flow)}
  flow.timer=setTimeout(()=>poll(flow),1000);
 }
 async function poll(flow){
  clearTimeout(flow.timer);
  if(!isCurrent(flow)||!flow.job)return;
  try{const job=await api('export-status?'+new URLSearchParams({job:flow.job.id,workspace_id:flow.workspace}));if(isCurrent(flow))acceptJob(flow,job)}
  catch(error){if(current(flow)){flow.busy=false;flow.pollError=true;flow.error='暂时无法读取制作进度：'+error.message+'。任务可能仍在运行，请重新检查进度。';render(flow)}}
 }
 async function submit(flow){
  if(!isCurrent(flow)||flow.busy||flow.pollError||flow.job?.status==='complete')return;
  const template=(getState().templates||[]).find(t=>t.id===flow.template.id);
  if(template?.status!=='ready'){flow.error='所选模板尚不可用，请返回模板页重新选择。';render(flow);return}
  const report=selected(flow);
  if(flow.mode==='upload'&&!flow.file||flow.mode==='report'&&!report)return;
  flow.busy=true;flow.error='';flow.message='正在准备原稿…';render(flow);
  try{
   if(flow.mode==='upload'&&!flow.version){
    const payload=await uploadPayload(flow.file,getUploadLimits(),{template_id:flow.template.id,workspace_id:flow.workspace});
    if(!isCurrent(flow))return;
    const result=await api('template-convert',payload);
    if(!isCurrent(flow))return;
    if(!result.version?.id)throw Error('未收到已保存的转换稿，请检查报告列表后再试。');
    flow.version=result.version;flow.notes=Array.isArray(result.notes)?result.notes:[];acceptJob(flow,result.job);
   }else{
    let version=flow.version||report;
    if(!flow.version&&getCurrent()?.run_id===report.run_id&&savedVersion){
     const id=await savedVersion();if(!isCurrent(flow))return;
     if(getCurrent()?.run_id!==report.run_id)throw Error('正在编辑的报告已切换，请重新选择报告。');
     version={...getCurrent(),id};
    }
    if(!isCurrent(flow))return;
    flow.version=version;
    const job=await api('export',{version_id:version.id,template_id:flow.template.id,workspace_id:flow.workspace});
    if(isCurrent(flow))acceptJob(flow,job);
   }
  }catch(error){if(current(flow)){flow.busy=false;flow.error=error.message;render(flow)}}
 }
 function open(templateId,mode='upload'){
  const state=getState(),template=state?.templates?.find(t=>t.id===templateId);
  if(!state?.workspace_id||template?.status!=='ready'){notice('所选模板尚不可用，请重新选择。',true);return false}
  mode=mode==='report'?'report':'upload';ensureDialog();
  const key=JSON.stringify([state.workspace_id,templateId,mode]);
  let flow=flows.get(key);
  if(!flow){flow={epoch,workspace:state.workspace_id,template,mode,file:null,items:[],query:'',cursor:'',selectedId:'',listTicket:0,loading:false,listError:'',busy:false,pollError:false,job:null,version:null,notes:[],message:'',error:''};flows.set(key,flow)}
  active=flow;if(!dialog.open)dialog.showModal();render(flow);
  ($('template-output-download')||$('template-output-change-file')||$(mode==='upload'?'template-output-file':'template-output-search'))?.focus?.();
  if(mode==='report'&&!flow.job&&!flow.loading)fetchReports(flow);
  return true;
 }
 return {open,close,reset};
}
