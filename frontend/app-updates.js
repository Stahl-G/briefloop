// App updates: fixed desktop capabilities, with a read-only browser fallback.
import {$} from './dom.js';
export function appUpdatesUI({api,notice}){
 let appUpdateState=null,softwareInfo=null,appUpdatePending=false,appUpdateLastAction='check';
 function renderAppUpdates(value=appUpdateState){
  const box=$('settings-view-updates');if(!box)return;
  const desktop=typeof window.briefloopDesktop?.updateStatus==='function';
  $('app-update-controls').hidden=false;
  $('app-update-version').textContent=softwareInfo?`BriefLoop v${softwareInfo.version}${softwareInfo.build?` · 构建 ${softwareInfo.build}`:''}${desktop?` · 桌面 App v${value?.currentAppVersion||'读取中'}`:''}`:'正在读取实际运行版本…';
  const installations={desktop:'桌面管理的后端 · App 与 CLI 共用',source:'开发源码',pip:'Python 包安装',pipx:'pipx 安装',uv:'uv 工具安装'};
  $('app-update-installation').textContent=softwareInfo?installations[softwareInfo.installation]||'独立安装':'';
  $('app-update-command').hidden=!softwareInfo?.update_command;
  $('app-update-command').textContent=softwareInfo?.update_command||'';
  $('app-update-source').textContent=value?.source==='local-test'?'本地测试更新源 · 仅验证流程，不代表官方发布':desktop?'官方稳定来源：Stahl-G/briefloop · GitHub Releases':'Python 包稳定来源：PyPI · briefloop';
  const reinstall=value?.source==='local-test'&&value?.reinstall===true;
  $('app-update-guidance').textContent=!desktop?(softwareInfo?.guidance||'正在读取安装来源…'):value?.installMode==='zip'?'优先增量下载 ZIP 更新包并校验完整文件。保存并退出后打开 Finder；将 BriefLoop 拖到 Applications 替换，再重新启动。':value?.installMode==='dmg'?`下载后会先保存编辑并处理忙任务，再退出 App、打开 DMG；请在 Finder 中${reinstall?'重新安装当前版本':'手动安装新版本'}。`:'下载后会先保存编辑并处理忙任务，再退出 App 并交给原生安装器更新。';
  const errorOperation=value?.error?.operation||(value?.error?.code==='open_failed'?'install':appUpdateLastAction);
  const errorLabel={check:'更新检查失败（当前安装不受影响）',download:'更新包下载未完成',install:'更新安装未完成'}[errorOperation]||'更新检查失败（当前安装不受影响）';
  const labels={idle:'尚未检查更新',checking:'正在检查更新…',available:'发现可用更新',current:'当前 App 无需更新',downloading:'正在下载更新…',downloaded:'下载完成，等待安装',error:errorLabel};
  const reinstallLabel=`重新安装当前 App v${value?.currentAppVersion||''}`;
  $('app-update-status').textContent=desktop?(reinstall&&value?.state==='available'?reinstallLabel:(labels[value?.state]||'正在读取 App 版本…')+(value?.state==='error'&&errorOperation==='check'?'':reinstall?` · ${reinstallLabel}`:value?.releaseVersion?` · v${value.releaseVersion}`:'')):({idle:'尚未检查更新',checking:'正在检查更新…',available:`发现可用后端版本 v${value?.releaseVersion||''}`,current:'当前后端已是 PyPI 最新稳定版',ahead:`当前后端高于 PyPI 已发布版本 v${value?.releaseVersion||''}`,error:'版本检查未完成'}[value?.state||'idle']||'尚未检查更新');
  $('app-update-download').textContent=reinstall?'下载当前版本安装包':'下载更新';
  const busy=appUpdatePending||['checking','downloading'].includes(value?.state);
  $('app-update-check').disabled=busy;
  $('app-update-download').hidden=!desktop||value?.state!=='available';$('app-update-download').disabled=busy;
  $('app-update-install').hidden=!desktop||value?.state!=='downloaded';$('app-update-install').disabled=busy;
  $('app-update-install').textContent=value?.installMode==='zip'?'保存并打开更新文件夹':value?.installMode==='dmg'?'保存并打开 DMG':'保存并安装更新';
  $('app-update-retry').hidden=value?.state!=='error'||!value?.retryable;$('app-update-retry').disabled=busy;
  $('app-update-error').hidden=!value?.error;$('app-update-error').textContent=value?.error?.message||'';
  const progress=value?.progress;
  $('app-update-progress-box').hidden=!progress;
  $('app-update-progress').value=progress?.percent||0;
  $('app-update-progress-text').textContent=progress?`${Math.round(progress.percent||0)}% · ${(Math.max(0,progress.transferred||0)/1048576).toFixed(1)} / ${(Math.max(0,progress.total||0)/1048576).toFixed(1)} MiB`:'';
  if(progress?.mode==='differential')$('app-update-progress-text').textContent+=` · 增量下载，复用 ${(Math.max(0,progress.reused||0)/1048576).toFixed(1)} MiB`;
  else if(progress?.mode==='full')$('app-update-progress-text').textContent+=' · '+({
   no_baseline:'无有效基准缓存，完整下载并建立缓存',
   little_reuse:'本次可复用内容较少，完整下载',
   range_unavailable:'下载服务不支持分段传输，完整下载',
   range_size:'分段响应不完整，重新完整下载',
   delta_hash:'增量重建校验失败，重新完整下载',
   differential_unavailable:'增量不可用，已回退完整下载',
   blockmap_unavailable:'此版本无可用增量信息，完整下载'
  }[progress.fallback]||'完整下载');
  $('app-update-notes-box').hidden=!value?.notes;$('app-update-notes').textContent=value?.notes||'';
 }
 async function refreshAppUpdates(){
  renderAppUpdates();
  try{softwareInfo=await api('software-version');
  if(typeof window.briefloopDesktop?.updateStatus==='function')appUpdateState=await window.briefloopDesktop.updateStatus();
  renderAppUpdates()}
  catch{notice('无法读取 App 更新状态，请重新打开设置。',true)}
 }
 async function runAppUpdate(command){
  if(appUpdatePending)return;
  const desktop=window.briefloopDesktop;
  if(typeof desktop?.updateStatus!=='function'){
   if(command!=='check')return;
   appUpdatePending=true;appUpdateState={state:'checking'};renderAppUpdates();
   try{appUpdateState=await api('software-update-check',{});softwareInfo=appUpdateState}
   catch(error){appUpdateState={state:'error',retryable:true,error:{message:error.message||'版本检查未完成'}}}
   finally{appUpdatePending=false;renderAppUpdates()}
   return;
  }
  appUpdatePending=true;appUpdateLastAction=command;renderAppUpdates();
  try{
   if(command==='install'){
    const result=await desktop.installUpdate();
    if(result?.cancelled)notice('已保留当前工作区，更新包仍可稍后安装。');
    // Successful installation closes this renderer. A cancelled gate keeps it live.
    if(result?.cancelled)appUpdateState=await desktop.updateStatus();
   }else appUpdateState=await (command==='download'?desktop.downloadUpdate():desktop.checkForUpdates());
  }catch(error){
   notice(error.message||'更新操作未完成，请重试。',true);
   try{appUpdateState=await desktop.updateStatus()}catch{}
  }finally{appUpdatePending=false;renderAppUpdates()}
 }
 function init(){
  if($('settings-view-updates')){
   $('app-update-check').onclick=()=>runAppUpdate('check');
   $('app-update-download').onclick=()=>runAppUpdate('download');
   $('app-update-install').onclick=()=>runAppUpdate('install');
   // A temporary DMG open error can retry the saved asset through the same gate.
   $('app-update-retry').onclick=()=>runAppUpdate(appUpdateState?.error?.code==='open_failed'?'install':(appUpdateState?.error?.operation||appUpdateLastAction)==='install'?'download':appUpdateState?.error?.operation||appUpdateLastAction);
   window.briefloopDesktop?.onUpdateStatus?.(value=>{appUpdateState=value;renderAppUpdates()});
   renderAppUpdates();
  }
 }
 return {init,renderAppUpdates,refreshAppUpdates,runAppUpdate};
}
// End App updates.
