import {preflightSources,sendSourceFile,sourceStatusLabel} from './uploads.js';

const terminal=new Set(['ready','failed','cancelled','interrupted']);
const pause=ms=>new Promise(resolve=>setTimeout(resolve,ms));

// Dependency-injected transport and view; source selection remains with the caller.
export function createSourceUploads({api,getToken,getUploadLimits,progressHost=()=>null,onAccepted=()=>{},sendFile=sendSourceFile,delay=pause}) {
 function card(file,cancel){
  const host=progressHost();if(!host)return {update(){},finish(){}};
  const box=document.createElement('section');box.className='panel source-upload-progress';box.dataset.testid='source-upload-progress';
  const title=document.createElement('strong');title.textContent=file.name;
  const status=document.createElement('p');status.className='help';status.setAttribute('role','status');status.setAttribute('aria-live','polite');
  const progress=document.createElement('progress');progress.setAttribute('aria-label',file.name+' 上传或读取进度');
  const button=document.createElement('button');button.type='button';button.textContent='取消上传';button.onclick=cancel;
  box.append(title,status,progress,button);host.append(box);
  return {
   update(value){
    if(value.phase==='uploading'){
     progress.max=value.bytes_total||1;progress.value=value.bytes_sent||0;
     status.textContent=`正在上传 ${((value.bytes_sent||0)/1048576).toFixed(1)} / ${((value.bytes_total||0)/1048576).toFixed(1)} MiB`;
    }else{
     button.textContent='取消读取';status.textContent=value.message||'原件已保存，等待读取';
     if(Number.isInteger(value.pages_total)&&value.pages_total>0){progress.max=value.pages_total;progress.value=value.pages_completed||0;status.textContent+=` · ${value.pages_completed||0} / ${value.pages_total} 页`}
     else progress.removeAttribute('value');
    }
   },
   finish(source,error){
    progress.hidden=true;const label=source?.status==='ready'&&source.needs_visual&&source.media_type==='application/pdf'?'需要视觉读取（未执行 OCR）':source?sourceStatusLabel(source):'';status.textContent=error||[label,source?.error].filter(Boolean).join('：');
    button.disabled=false;button.textContent='收起';button.onclick=()=>box.remove();
   }
  };
 }
 async function waitForSource(source,onProgress,signal){
  let jobId=source.extraction_job_id,stopSent=false;
  while(!terminal.has(source.status)){
   if(signal?.aborted&&jobId&&!stopSent){await api('stop',{job_id:jobId});stopSent=true}
   const state=await api('source-status?id='+encodeURIComponent(source.id));
   source=state.source;jobId=state.job?.id||jobId;
   onProgress(state.progress||{phase:source.status,message:sourceStatusLabel(source)});
   if(!terminal.has(source.status))await delay(500);
  }
  return source;
 }
 async function uploadSource(file,{onProgress=()=>{},signal}={}){
  preflightSources([file],getUploadLimits());
  const controller=new AbortController(),cancel=()=>controller.abort();signal?.addEventListener('abort',cancel,{once:true});
  if(signal?.aborted)cancel();
  const view=card(file,cancel),progress=data=>{view.update(data);onProgress(data)};
  let source;
  try{
   progress({phase:'uploading',bytes_sent:0,bytes_total:file.size});
   let response=await sendFile(file,{token:getToken(),onProgress:progress,signal:controller.signal});
   if(response.status===403){await api('session');response=await sendFile(file,{token:getToken(),onProgress:progress,signal:controller.signal})}
   if(response.status<200||response.status>=300)throw Error(response.body.error||'上传失败');
   source=response.body;onAccepted(source);progress({phase:'queued',message:'原件已保存，等待读取'});
   source=await waitForSource(source,progress,controller.signal);
   view.finish(source);return source;
  }catch(error){
   if(source)error.message='原件已保存，可在数据源查看或继续读取。'+error.message;
   view.finish(source,error.message);throw error
  }
  finally{signal?.removeEventListener('abort',cancel)}
 }
 return {uploadSource,waitForSource};
}
