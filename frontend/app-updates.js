// App updates: fixed desktop capabilities, with a read-only browser fallback.
import {$} from './dom.js';
export function appUpdatesUI({api,notice}){
 let appUpdateState=null,softwareInfo=null,appUpdatePending=false,appUpdateLastAction='check';
 const releaseNotes=new Map();
 function showNotes(id,title,value){
  $(`${id}-title`).textContent=title;
  $(id).textContent=value?.state==='loaded'?value.notes:value?.state==='empty'?'该版本未提供发布说明。':value?.message||'正在读取发布说明…';
 }
 function renderNotes(value){
  const current=softwareInfo?.version;
  const installed=value?.currentAppVersion||current;
  if(current)showNotes('app-update-current-notes',`${current===installed?'当前版本':'当前后端'} v${current} 的改动`,releaseNotes.get(current)||softwareInfo.release_notes);
  const latest=value?.releaseVersion;
  // A failed check may retain the old download target; do not call it latest.
  const known=latest&&value?.state!=='checking'&&!(value?.state==='error'&&(value.error?.operation||appUpdateLastAction)==='check');
  $('app-update-latest-version').textContent=known?`最近检查的发布版本 v${latest}`:value?.state==='checking'?'正在检查可用版本…':value?.state==='error'?'可用版本检查未完成':'尚未检查可用版本';
  $('app-update-notes-box').hidden=!known||latest===current;
  if(known&&latest!==current)showNotes('app-update-notes',`${['available','downloading','downloaded'].includes(value.state)?'可用更新':'发布版本'} v${latest} 的改动`,value.source==='local-test'?{state:value.notes?'loaded':'empty',notes:value.notes}:releaseNotes.get(latest));
 }
 async function loadNotes(version,force=false){
  if(!version||releaseNotes.get(version)?.state==='loading'||(!force&&releaseNotes.has(version)))return;
  releaseNotes.set(version,{state:'loading'});renderNotes(appUpdateState);
  try{
   const result=await api('software-release-notes',{version});
   releaseNotes.set(version,result?.version===version?result:{state:'error',message:'发布说明版本不匹配，请重新检查。'});
  }catch{releaseNotes.set(version,{state:'error',message:'无法获取该版本的发布说明，请检查网络后重试。'})}
  renderNotes(appUpdateState);
 }
 function refreshNotes(force=false){
  const current=softwareInfo?.version;
  if(current&&!softwareInfo.release_notes)void loadNotes(current,force);
  const target=appUpdateState?.releaseVersion;
  if(target&&target!==current&&appUpdateState.source!=='local-test')void loadNotes(target,force);
 }
 function renderAppUpdates(value=appUpdateState){
  const box=$('settings-view-updates');if(!box)return;
  const desktop=typeof window.briefloopDesktop?.updateStatus==='function';
  $('app-update-controls').hidden=false;
  $('app-update-version').textContent=desktop?`当前 App v${value?.currentAppVersion||'读取中'}`:softwareInfo?`当前版本 v${softwareInfo.version}`:'正在读取实际运行版本…';
  const installations={desktop:'桌面管理的后端 · App 与 CLI 共用',source:'开发源码',pip:'Python 包安装',pipx:'pipx 安装',uv:'uv 工具安装'};
  $('app-update-installation').textContent=softwareInfo?`${installations[softwareInfo.installation]||'独立安装'} · 后端 v${softwareInfo.version}${softwareInfo.build?` · 构建 ${softwareInfo.build}`:''}`:'';
  $('app-update-command').hidden=!softwareInfo?.update_command;
  $('app-update-command').textContent=softwareInfo?.update_command||'';
  $('app-update-source').textContent=value?.source==='local-test'?'本地测试更新源 · 仅验证流程，不代表官方发布':desktop?'官方稳定来源：Stahl-G/briefloop · GitHub Releases':'版本检查：PyPI · 发布说明：GitHub Releases';
  $('app-update-test-source').hidden=value?.source!=='local-test';
  const reinstall=value?.source==='local-test'&&value?.reinstall===true;
  $('app-update-guidance').textContent=!desktop?(softwareInfo?.guidance||'正在读取安装来源…'):value?.installMode==='zip'?'安装前会保存编辑并处理忙任务。退出后，在 Finder 中将新版 App 拖入“安装位置”并替换，再打开 BriefLoop；工作区保留。':value?.installMode==='dmg'?`安装前会保存编辑并处理忙任务，再退出并打开 DMG；请在 Finder 中${reinstall?'重新安装当前版本':'手动安装新版本'}。`:'安装前会保存编辑并处理忙任务，再退出并交给安装器更新；工作区保留。';
  $('app-update-guidance').hidden=desktop&&!['available','downloading','downloaded','error'].includes(value?.state);
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
  $('app-update-install').textContent=value?.installMode==='zip'?'保存并退出，打开安装文件夹':value?.installMode==='dmg'?'保存并打开 DMG':'保存并安装更新';
  $('app-update-retry').hidden=value?.state!=='error'||!value?.retryable;$('app-update-retry').disabled=busy;
  $('app-update-error').hidden=!value?.error;$('app-update-error').textContent=value?.error?.message||'';
  const progress=value?.progress;
  $('app-update-progress-box').hidden=!progress;
  $('app-update-progress').value=progress?.percent||0;
  $('app-update-progress-text').textContent=progress?`${Math.round(progress.percent||0)}% · ${(Math.max(0,progress.transferred||0)/1048576).toFixed(1)} / ${(Math.max(0,progress.total||0)/1048576).toFixed(1)} MiB`:'';
  $('app-update-progress-detail').textContent='';
  if(progress?.mode==='differential')$('app-update-progress-detail').textContent=`增量下载，复用 ${(Math.max(0,progress.reused||0)/1048576).toFixed(1)} MiB`;
  else if(progress?.mode==='full')$('app-update-progress-detail').textContent=({
   no_baseline:'无有效基准缓存，完整下载并建立缓存',
   little_reuse:'本次可复用内容较少，完整下载',
   range_unavailable:'下载服务不支持分段传输，完整下载',
   range_size:'分段响应不完整，重新完整下载',
   delta_hash:'增量重建校验失败，重新完整下载',
   differential_unavailable:'增量不可用，已回退完整下载',
   blockmap_unavailable:'此版本无可用增量信息，完整下载'
  }[progress.fallback]||'完整下载');
  renderNotes(value);
 }
 async function refreshAppUpdates(){
  renderAppUpdates();
  try{softwareInfo=await api('software-version');
  if(typeof window.briefloopDesktop?.updateStatus==='function')appUpdateState=await window.briefloopDesktop.updateStatus();
  renderAppUpdates();refreshNotes()}
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
   finally{appUpdatePending=false;renderAppUpdates();refreshNotes(command==='check')}
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
  }finally{appUpdatePending=false;renderAppUpdates();refreshNotes(command==='check')}
 }
 function init(){
  if($('settings-view-updates')){
   $('app-update-check').onclick=()=>runAppUpdate('check');
   $('app-update-download').onclick=()=>runAppUpdate('download');
   $('app-update-install').onclick=()=>runAppUpdate('install');
   // A temporary DMG open error can retry the saved asset through the same gate.
   $('app-update-retry').onclick=()=>runAppUpdate(appUpdateState?.error?.code==='open_failed'?'install':(appUpdateState?.error?.operation||appUpdateLastAction)==='install'?'download':appUpdateState?.error?.operation||appUpdateLastAction);
   window.briefloopDesktop?.onUpdateStatus?.(value=>{appUpdateState=value;renderAppUpdates();refreshNotes()});
   renderAppUpdates();
  }
 }
 return {init,renderAppUpdates,refreshAppUpdates,runAppUpdate};
}
// End App updates.
