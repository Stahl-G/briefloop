// Excel export: the backend renders the report's tables into a real .xlsx
// (openpyxl base file; OfficeCLI only adds an optional enhancement pass), so
// this entry point works the same whether or not the tool is installed.
import {$} from './dom.js';

export function excelExportUI({api,notice,refresh,savedVersion,parse,getState}){
 let xlsxDownloading=false;
 async function downloadXlsx(){
  if(xlsxDownloading)return;xlsxDownloading=true;
  const button=$('download-xlsx');button.disabled=true;button.textContent='正在制作…';
  try{
   const version=await savedVersion(),workspace=getState().workspace_id;
   const layout=$('export-xlsx-layout')?.value||'sheets';
   let job=await api('export-xlsx',{version_id:version,layout});
   while(['queued','running'].includes(job.status)){
    button.textContent=job.status==='queued'?'等待制作…':'正在制作…';
    await new Promise(resolve=>setTimeout(resolve,1000));
    if(getState().workspace_id!==workspace)throw Error('工作区已切换，请在原工作区下载');
    job=await api('export-status?job='+encodeURIComponent(job.id));
   }
   if(job.status!=='complete')throw Error(job.error||'Excel 制作未完成，请重试');
   const link=document.createElement('a');link.href='/api/export-file?job='+encodeURIComponent(job.id)+'&workspace_id='+encodeURIComponent(workspace);link.download='';link.click();notice('Excel 已生成，正在下载');await refresh();
  }catch(e){notice('Excel 下载未完成：'+e.message,true)}finally{xlsxDownloading=false;button.disabled=false;button.textContent='生成 Excel'}
 }
 function init(){
  const button=$('download-xlsx');if(!button)return;
  button.onclick=downloadXlsx;
 }
 return {init,downloadXlsx};
}
