import {$,esc} from './dom.js';
import {moment} from './time.js';
// Unread state lives in the workspace, not the browser's transient UI state.
export function activityCenter({api,getState,page,openBrief,showSettings,settingsView}){
 let signature='',checking=false,checkedWorkspace=null;
 const categoryNames={reports:'报告',templates:'模板',learning:'Wiki',updates:'版本'};
 const nav={reports:'[data-page="reports"]',templates:'[data-page="templates"]',learning:'[data-page="learning"]',updates:'#settings-open'};
 const data=()=>getState()?.notifications||{items:[],counts:{},unread:0,through:0};
 async function read(category,seq){
  const snapshot=data();
  const next=await api('notifications/read',{through:snapshot.through,...(category?{category}:{}),...(seq?{seq}:{})});
  getState().notifications=next;render();
 }
 async function open(item){
  const state=getState();
  if(item.category==='reports'){
   const target=item.target||{};
   const brief=state.briefs.find(b=>b.id===target.version_id)||state.briefs.find(b=>target.run_id&&b.run_id===target.run_id);
   if(brief){if(!openBrief(brief,{follow:false}))return;page('report')}else page('reports');
  }else if(item.category==='updates'){showSettings();settingsView('updates')}
  else page(item.category);
  if(item.category!=='updates'&&$(item.category==='reports'?'reports':item.category)?.hidden&&$('report')?.hidden)return;
  $('notifications-dialog').close();await read(null,item.seq);
 }
 async function checkVersion(){
  const state=getState();if(!state?.workspace_id||checking||checkedWorkspace===state.workspace_id)return;
  checking=true;checkedWorkspace=state.workspace_id;
  try{
   const desktop=window.briefloopDesktop;
   if(typeof desktop?.updateStatus==='function'){
    let value=await desktop.updateStatus();
    if(['idle','current'].includes(value.state))value=await desktop.checkForUpdates();
    await desktopVersion(value);
   }else await api('software-update-check',{});
  }catch{/* Explicit checks show their errors in Settings; no startup popup. */}
  finally{checking=false}
 }
 async function desktopVersion(value){
  if(value?.source==='local-test'||value?.state!=='available'||!value.releaseVersion)return;
  const next=await api('notifications/version',{current:value.currentAppVersion,latest:value.releaseVersion});
  getState().notifications=next;render();
 }
 function render(){
  const value=data();
  for(const [category,selector] of Object.entries(nav)){
   const button=document.querySelector(selector);if(!button)continue;
   let dot=button.querySelector('.notification-dot');
   if(!dot){dot=document.createElement('span');dot.className='notification-dot';dot.setAttribute('role','img');button.append(dot)}
   const count=value.counts[category]||0;dot.hidden=!count;dot.setAttribute('aria-label',`${count} 条未读${categoryNames[category]}动态`);
  }
  $('notifications-open').textContent=value.unread?`动态 · ${value.unread} 条未读`:'动态';
  $('notifications-open').classList.toggle('has-unread',value.unread>0);
  $('notifications-read-all').disabled=!value.unread;
  const next=JSON.stringify(value.items);if(signature!==next){
   signature=next;
   $('notifications-list').innerHTML=value.items.length?value.items.map(item=>`<article class="notification-item ${item.read_at?'':'unread'} ${item.severity==='error'?'activity-error':''}"><div><strong>${esc(item.title)}</strong><small>${esc(categoryNames[item.category]||'动态')} · ${esc(moment(item.created))}${item.read_at?'':' · 未读'}</small><p>${esc(item.body)}</p></div><button type="button" class="outline" data-activity-open="${item.seq}">查看</button></article>`).join(''):'<p class="help">还没有新动态。报告任务、模板、Wiki 和版本更新会显示在这里。</p>';
   $('notifications-list').querySelectorAll('[data-activity-open]').forEach(button=>button.onclick=()=>open(value.items.find(i=>i.seq===Number(button.dataset.activityOpen))).catch(error=>{$('notifications-error').textContent=error.message}));
  }
  checkVersion();
 }
 $('notifications-open').onclick=()=>{$('notifications-error').textContent='';render();$('notifications-dialog').showModal()};
 $('notifications-close').onclick=()=>$('notifications-dialog').close();
 $('notifications-read-all').onclick=()=>read().catch(error=>{$('notifications-error').textContent=error.message});
 window.briefloopDesktop?.onUpdateStatus?.(value=>desktopVersion(value).catch(()=>{}));
 return {render,readCategory:category=>{if(data().counts[category])read(category).catch(()=>{})}};
}
