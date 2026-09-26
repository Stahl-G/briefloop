import {$,esc} from './dom.js';
import {dayTime,dateTimeSeconds} from './time.js';

// The poll owns summaries; only the selected report owns a full context.
export function createReportBrowsing({api,getState,getCurrent,openBrief,page,notice,reportStatus,reportDescription,reportIconMeta,svgLineIcon,runSourceCount,openRelease,onUsageOpen=()=>{},onContext=()=>{}}){
 let pageData={items:[],next_cursor:null},listKey='',filterKey='',listTicket=0,loading=false,listError='',searchTimer;
 let selectedContext=null,contextTicket=0,contextRequest='',historyTicket=0,historyData=null;
 const bodies=new Map();
 const parse=value=>JSON.parse(value||'{}');
 function briefSummary(full){const {id,run_id,parent_id,author,hash,created,position,latest_version_id}=full;return {id,run_id,parent_id,author,hash,created,position,latest_version_id,detail:JSON.stringify({title:parse(full.detail).title})}}
 const filters=()=>({q:($('reports-search')?.value||'').trim(),status:$('reports-filter-status')?.value||'',days:$('reports-filter-time')?.value||'',sources:$('reports-filter-source')?.value||''});
 function stateRoute(current,pending){const q=new URLSearchParams();if(current){q.set('run_id',current.run_id);q.set('version_id',current.id)}if(pending)q.set('pending_run',pending);return 'state'+(q.size?'?'+q:'')}
 function contextKey(snapshot,current){const b=snapshot.briefs?.find(b=>b.id===current?.id),r=snapshot.runs?.find(r=>r.id===current?.run_id);return JSON.stringify([current?.id,b?.assessment_id||null,r?.source_count,snapshot.briefs?.find(b=>b.run_id===current?.run_id)?.id])}
 function mergeContext(snapshot,current){
  if(!selectedContext||selectedContext.version!==current?.id)return;
  const {data}=selectedContext;
  snapshot.runs=[data.run,...snapshot.runs.filter(r=>r.id!==data.run.id)];
  // Keep one full evaluation while its matching summary is current.
  const summary=snapshot.assessments.find(a=>a.version_id===current.id),full=data.assessments[0];
  if(full&&(!summary||summary.id===full.id))snapshot.assessments=[full,...snapshot.assessments.filter(a=>a.version_id!==current.id)];
  for(const b of [data.latest,data.original,selectedContext.brief])if(b&&!snapshot.briefs.some(item=>item.id===b.id))snapshot.briefs.push(b);
  snapshot.briefs.sort((a,b)=>(b.position||0)-(a.position||0));
 }
 function acceptState(next,current){
  if(current?.context&&selectedContext?.version!==current.id){const data=current.context;selectedContext={version:current.id,key:JSON.stringify([current.id,data.assessments[0]?.id||null,data.run.source_count,data.latest.id]),data,brief:briefSummary(current)}}
  const key=contextKey(next,current);mergeContext(next,current);
  if(current&&selectedContext?.key!==key&&contextRequest!==key){
   const ticket=++contextTicket;contextRequest=key;
   api('report-context?version_id='+encodeURIComponent(current.id)).then(data=>{
    if(ticket!==contextTicket||getCurrent()?.id!==current.id)return;
    selectedContext={version:current.id,key,data,brief:briefSummary(current)};mergeContext(getState(),current);onContext();
   }).catch(error=>{if(ticket===contextTicket)notice(error.message,true)}).finally(()=>{if(ticket===contextTicket)contextRequest=''});
  }
  return next;
 }
 function adopt(full){
  if(full.context){++contextTicket;contextRequest='';selectedContext={version:full.id,key:JSON.stringify([full.id,full.context.assessments[0]?.id||null,full.context.run.source_count,full.context.latest.id]),data:full.context,brief:briefSummary(full)};mergeContext(getState(),full)}
  remember(full);
 }
 function remember(full){bodies.delete(full.id);bodies.set(full.id,full);while(bodies.size>6)bodies.delete(bodies.keys().next().value)}
 async function loadBrief(b){
  if(!b||'markdown' in b)return b;
  const cached=bodies.get(b.id);if(cached&&(!b.hash||cached.hash===b.hash))return cached;
  const full=await api('brief?id='+encodeURIComponent(b.id));remember(full);return full;
 }
 async function fetchReports(append=false){
  const currentFilters=JSON.stringify(filters()),keepCount=!append&&filterKey===currentFilters?pageData.items.length:0;filterKey=currentFilters;
  const ticket=++listTicket,key=JSON.stringify([filters(),getState()?.report_catalog]);listKey=key;loading=true;listError='';renderReports.sig='';renderReports();
  const q=new URLSearchParams(filters());if(append&&pageData.next_cursor)q.set('cursor',pageData.next_cursor);
  try{let result=await api('reports?'+q);if(ticket!==listTicket)return;
   while(!append&&result.next_cursor&&result.items.length<keepCount){q.set('cursor',result.next_cursor);const more=await api('reports?'+q);if(ticket!==listTicket)return;result={...more,items:[...result.items,...more.items.filter(b=>!result.items.some(old=>old.id===b.id))]}}
   pageData={...result,items:append?[...pageData.items,...result.items.filter(b=>!pageData.items.some(old=>old.id===b.id))]:result.items}}
  catch(error){if(ticket!==listTicket)return;listError=error.message;notice(error.message,true);listKey=''}
  finally{if(ticket===listTicket){loading=false;renderReports.sig='';renderReports()}}
 }
 function render(){
  if(!getState())return;
  const key=JSON.stringify([filters(),getState().report_catalog]);
  if(!$('reports')?.hidden&&key!==listKey){fetchReports();return}renderReports();
 }
 function reset(){clearTimeout(searchTimer);++listTicket;loading=false;listKey='';pageData={items:[],next_cursor:null};renderReports.sig='';render()}
 function init(){
  if($('reports-search'))$('reports-search').oninput=()=>{++listTicket;loading=false;clearTimeout(searchTimer);searchTimer=setTimeout(reset,250)};
  for(const id of ['reports-filter-status','reports-filter-time','reports-filter-source'])if($(id))$(id).onchange=reset;
  document.querySelectorAll('[data-reports-view]').forEach(button=>button.onclick=()=>{renderReports.view=button.dataset.reportsView;document.querySelectorAll('[data-reports-view]').forEach(x=>x.classList.toggle('active',x===button));renderReports.sig='';renderReports()});
 }
 async function historyPage(runId,cursor='',before=''){return api('report-history?'+new URLSearchParams({run_id:runId,cursor,before}))}
 async function showHistory(current){
  const ticket=++historyTicket;historyData=null;$('history-list').textContent='正在读取历史版本…';$('history-dialog').showModal();
  async function read(append=false){
   const result=await historyPage(current.run_id,append?historyData?.next_cursor||'':'');
   if(ticket!==historyTicket||getCurrent()?.run_id!==current.run_id||!$('history-dialog').open)return;
   historyData={...result,items:append?[...historyData.items,...result.items]:result.items};
   $('history-list').innerHTML=historyData.items.map(b=>`<button class="history-row" data-history-version="${esc(b.id)}"><strong>${b.id===b.latest_version_id?'当前稿件':b.author==='agent'?'生成稿件':'历史快照'}</strong><span>${dateTimeSeconds(b.created)}</span></button>`).join('')+(result.next_cursor?'<button type="button" data-testid="history-load-more">加载更多历史版本</button>':'');
   $('history-list').querySelectorAll('[data-history-version]').forEach(button=>button.onclick=()=>{if(openBrief(historyData.items.find(b=>b.id===button.dataset.historyVersion))){$('history-dialog').close()}});
   const more=$('history-list').querySelector('[data-testid="history-load-more"]');if(more)more.onclick=()=>{more.disabled=true;read(true).catch(error=>{more.disabled=false;notice(error.message,true)})};
  }
  try{await read()}catch(error){if(ticket===historyTicket){$('history-list').textContent='历史版本读取失败';notice(error.message,true)}}
 }
 async function sourceUsage(sourceId,containers,isCurrent){
  let items=[],cursor='';
  async function read(){
   const result=await api('reports?'+new URLSearchParams({source_id:sourceId,cursor}));if(!isCurrent())return;
   items.push(...result.items);cursor=result.next_cursor;
   for(const pane of containers()){
    pane.innerHTML=items.length?`<ul class="source-usage">${items.map(b=>`<li><button type="button" data-usage-version="${esc(b.id)}">${esc(parse(b.detail).title||'简报')}<span aria-hidden="true">→</span></button></li>`).join('')}</ul>`:'<p class="help">还没有报告使用这个来源。</p>';
    pane.querySelectorAll('[data-usage-version]').forEach(button=>button.onclick=()=>{const brief=items.find(b=>b.id===button.dataset.usageVersion);if(openBrief(brief,{follow:false})){onUsageOpen();page('report')}});
    if(cursor){const more=document.createElement('button');more.type='button';more.dataset.testid='source-reports-more';more.textContent='加载更多关联报告';more.onclick=()=>{more.disabled=true;read().catch(error=>{more.disabled=false;notice(error.message,true)})};pane.append(more)}
   }
  }
  try{await read()}catch(error){if(isCurrent())for(const pane of containers()){pane.textContent='关联报告读取失败：'+error.message;const retry=document.createElement('button');retry.type='button';retry.textContent='重试';retry.onclick=()=>sourceUsage(sourceId,containers,isCurrent);pane.append(retry)}}
 }
 async function comparisonVersions(current,cursor=''){
  const result=await historyPage(current.run_id,cursor,current.id);
  const original=selectedContext?.version===current.id?selectedContext.data.original:null;
  return {...result,items:original&&original.position<current.position&&!result.items.some(b=>b.id===original.id)?[...result.items,original]:result.items};
 }
function renderReports(){
 const state=getState(),box=$('reports-list');if(!box||!state)return;
 const all=pageData.items,rows=all,q=filters().q,fStatus=filters().status,fTime=filters().days,fSource=filters().sources;
 const view=renderReports.view||'list';
 box.className='report-list'+(view==='grid'?' grid':'');
 const makingFirst=!state.briefs.length&&state.jobs.some(j=>j.kind==='generate'&&['queued','running'].includes(j.status));
 const sig=JSON.stringify([view,q,fStatus,fTime,fSource,makingFirst,all.length,loading,listError,pageData.next_cursor,rows.map(b=>{const st=reportStatus(b);return [b.id,b.status,b.updated,st.label,(b.source_count??runSourceCount(b.run_id))]})]);if(renderReports.sig===sig)return;renderReports.sig=sig;
 const end=$('reports-end');if(end){end.hidden=false;end.replaceChildren();const more=document.createElement('button');more.type='button';more.dataset.testid='reports-load-more';more.disabled=loading;more.textContent=loading?'正在读取…':listError?'重试读取报告':pageData.next_cursor?'加载更多报告':rows.length?'已显示全部报告':'刷新报告';more.onclick=()=>fetchReports(!!pageData.next_cursor).catch(()=>{});if(loading||listError||pageData.next_cursor)end.append(more);else end.textContent=rows.length?'已显示全部报告':'';}
 box.innerHTML=rows.length?rows.map(b=>{const st=reportStatus(b),sources=(b.source_count??runSourceCount(b.run_id)),desc=reportDescription(b);const when=dayTime(b.updated||b.created),meta=reportIconMeta(b);return `<article class="report-card"><span class="report-card-icon ${meta.cat}" aria-hidden="true">${svgLineIcon(meta.icon,24)}</span><div class="report-card-body"><h2 class="report-card-title">${esc(parse(b.detail).title||'简报')}</h2>${desc?`<p class="report-card-desc">${esc(desc)}</p>`:''}<div class="report-card-meta"><span>${sources} 个来源</span><span>${esc(when)} 最后编辑</span></div></div><div class="report-card-side"><span class="chip ${st.cls}">${esc(st.label)}</span><button type="button" class="primary" data-report-open="${esc(b.id)}">${st.key==='draft'?'继续编辑':'打开'}</button><div class="menu-wrap report-card-menu"><button type="button" class="ghost" data-report-menu aria-haspopup="menu" aria-expanded="false" aria-label="更多操作">⋯</button><div class="popover" role="menu" hidden><button type="button" role="menuitem" data-report-open="${esc(b.id)}">打开</button><a role="menuitem" href="/api/download?version=${encodeURIComponent(b.id)}">下载 Markdown</a><button type="button" role="menuitem" data-report-release="${esc(b.id)}">正式交付与审计包</button></div></div></div></article>`}).join(''):(makingFirst?'<div class="empty-inline"><strong>首份报告正在制作</strong><p class="help">初稿保存后会显示在这里。</p></div>':(all.length||q||fStatus||fTime||fSource)?'<div class="empty-inline"><p class="help">没有符合筛选条件的报告，请调整搜索或筛选。</p></div>':'<div class="empty-inline"><p class="help">还没有报告。使用左侧「新建报告」开始制作。</p></div>');
 box.querySelectorAll('[data-report-open]').forEach(el=>el.onclick=()=>{const b=rows.find(x=>x.id===el.dataset.reportOpen);if(b&&openBrief(b,{follow:false}))page('report')});
 box.querySelectorAll('[data-page="setup"]').forEach(el=>el.onclick=()=>page('setup'));
 box.querySelectorAll('.report-card-menu').forEach(wrap=>{const toggle=wrap.querySelector('[data-report-menu]'),pop=wrap.querySelector('.popover');if(!toggle||!pop)return;toggle.onclick=e=>{e.stopPropagation();const open=pop.hidden;document.querySelectorAll('.popover').forEach(p=>p.hidden=true);document.querySelectorAll('[aria-haspopup="menu"]').forEach(b=>b.setAttribute('aria-expanded','false'));pop.hidden=!open;toggle.setAttribute('aria-expanded',String(open))}});
 box.querySelectorAll('[data-report-release]').forEach(el=>el.onclick=()=>{const b=rows.find(x=>x.id===el.dataset.reportRelease);if(b)return openRelease(b)});
}
 return {stateRoute,acceptState,adopt,loadBrief,render,init,showHistory,comparisonVersions,historyPage,fetchReports,mergeContext,sourceUsage};
}
