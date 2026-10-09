// Sources page: the source library with search and filters, and the source detail drawer.
import {$ as lookup,esc} from './dom.js';
import {day,dateTime} from './time.js';
import {uploadSource,getUploadLimits} from './api.js';
import {preflightSources,sourceStatusLabel} from './uploads.js';
import {copyText} from './clipboard.js';
import {createSourceLibrarySearch} from './source-library-search.js';
import {createSourceLibraryControls} from './source-library-controls.js';
import {SEARCH_LABELS} from './search-settings.js';
const parse=s=>JSON.parse(s||'{}');
export function sourceState(s){return s.status!=='ready'?s.status:s.needs_visual?'visual':'ready'}
export function sourceStatusChip(st){return `<span class="chip ${st==='ready'?'success':st==='visual'?'warn':'danger'}">${st==='ready'?'可用':st==='visual'?'需视觉读取':esc(sourceStatusLabel({status:st}))}</span>`}
export function sourceIsWeb(s){return !!(s&&s.url)}
export function sourceHost(s){try{return new URL(s.url||s.name).hostname.replace(/^www\./,'')}catch{return ''}}
export function sourceTitle(s){const n=(s&&s.name)||'';let label;if(n&&!/^https?:\/\//i.test(n))label=n;else{const u=(s&&s.url)||n;try{const p=new URL(u);let seg=decodeURIComponent((p.pathname.split('/').filter(Boolean).pop()||''));seg=seg.replace(/\.[a-z0-9]{1,5}$/i,'').replace(/[-_]+/g,' ').trim();label=seg||p.hostname.replace(/^www\./,'')}catch{label=n||(s&&s.id)||'来源'}}return label.length>160?label.slice(0,159)+'…':label}
export function prettifyUrl(url){if(!url)return '';let out=url;try{const p=new URL(url);out=p.hostname.replace(/^www\./,'')+decodeURI(p.pathname)+(p.search||'')}catch{}return out.length>110?out.slice(0,107)+'…':out}
export function runSourceIds(run){if(!run)return[];if(Array.isArray(run.all_source_ids))return run.all_source_ids;try{const ids=parse(run.source_ids);return Array.isArray(ids)?ids:[]}catch{return[]}}

// Library order is a view preference, not the report's citation/source order.
export function sortLibrarySources(sources,order='newest'){
 const timestamp=s=>{const value=Date.parse(s.created);return Number.isFinite(value)?value:null};
 return sources.map((source,index)=>({source,index,time:timestamp(source)})).sort((a,b)=>{
  if(a.time===null||b.time===null)return a.time===b.time?b.index-a.index:a.time===null?1:-1;
  return (order==='oldest'?a.time-b.time:b.time-a.time)||(order==='oldest'?a.index-b.index:b.index-a.index);
 }).map(item=>item.source);
}
export function sourcesPageUI({api,action,notice,page,openBrief,markTab,reportBrowsing,getState,showSourceMedia,drawerSourceMediaView,applySourceLinks,sourceProvenanceRows,$=lookup}){
 function sourceUsage(){const map=new Map();for(const run of(getState().runs||[])){const ids=runSourceIds(run);const brief=(getState().briefs||[]).find(b=>b.run_id===run.id);const title=brief?(parse(brief.detail).title||'简报'):'报告';for(const id of ids){if(!map.has(id))map.set(id,[]);map.get(id).push({run_id:run.id,title})}}return map}
 function usageHTML(id,usage){const count=getState().source_report_counts?.[id];return `<div data-source-report-usage="${esc(id)}"><p class="help">${count?`${count} 份报告，正在读取关联报告…`:'正在核对关联报告…'}</p></div>`}
 function wireUsage(pane){if(!pane)return;pane.querySelectorAll('[data-open-report]').forEach(b=>b.onclick=()=>{const brief=(getState().briefs||[]).find(x=>x.run_id===b.dataset.openReport);if(brief&&openBrief(brief,{follow:false})){closeSourceDrawer();page('report')}})}
 const controls=createSourceLibraryControls({$,api,action,notice,getState,render:()=>{renderSourcesPage.sig=null;renderSourcesPage()}});
 const sourceLibrarySearch=createSourceLibrarySearch({api,onChange:()=>{renderSourcesPage.sig=null;renderSourcesPage()}});
 const sourceSearchReasons={failed:'获取失败',visual_partial:'仅检索提取文字，图片未检索',empty_text:'没有可读正文',too_large:'正文超过2 MiB，未检索',changed:'文件已被改动，未检索',unreadable:'正文无法读取',metadata_unreadable:'读取状态无法确认，未检索'};
 function renderSourcesPage(){
  const box=$('sources-page-list');if(!box||!getState())return;
  controls.sync();
  const all=getState().sources||[],usage=sourceUsage();
  const q=($('sources-search')?.value||'').trim();
  const type=$('sources-type-filter')?.value||'';
  const channel=$('sources-channel-filter')?.value||'';
  const status=renderSourcesPage.status||'';
  const order=$('sources-sort')?.value||'newest';
  sourceLibrarySearch.update({query:q,type,channel,status,scope:controls.scope(),runId:controls.runId,order,workspace:getState().workspace_id,
   sources:all.map(s=>[s.id,s.hash,s.status,s.needs_visual,s.name,s.url,s.discovery_providers,s.archived_at])});
  const search=sourceLibrarySearch.state,matches=new Map(search.items.map(item=>[item.source_id,item]));
  const failed=all.filter(s=>controls.includes(s)&&sourceState(s)==='failed');
  const rows=sortLibrarySources(all.filter(s=>{
   if(!controls.includes(s))return false;
   if(type&&(type==='web'?!sourceIsWeb(s):sourceIsWeb(s)))return false;
   if(channel&&(channel==='unrecorded'?(s.discovery_providers||[]).length:!(s.discovery_providers||[]).includes(channel)))return false;
   if(status&&sourceState(s)!==status)return false;
   if(q&&!matches.has(s.id))return false;
   return true;
  }),order);
  const visible=controls.windowRows(rows,JSON.stringify([q,type,status,channel,order,controls.scope(),controls.runId,getState().workspace_id]));
  if($('sources-page-count'))$('sources-page-count').textContent=q?
   `已检查 ${search.scanned}/${search.scanned||search.exhausted?search.total:'…'} 个候选，找到 ${search.items.length} 个来源`+(search.unsearchedCount?` · ${search.unsearchedCount} 份正文未完整检索`:'')+(search.exhausted&&!search.unsearchedCount?' · 搜索完成':''):
   `已显示 ${visible.length} / ${rows.length} 个来源`+(failed.length?` · ${failed.length} 个获取失败`:'');
  const retry=$('sources-retry-all');if(retry){retry.hidden=!failed.length;retry.disabled=!failed.length}
  const sig=JSON.stringify([q,type,status,channel,order,controls.scope(),controls.runId,controls.pageLimit,controls.selectionKey,search,rows.map(s=>{const u=usage.get(s.id)||[];return [s.id,s.status,s.needs_visual,s.name,s.url,s.created,s.archived_at,getState().source_report_counts?.[s.id]??u.length,s.discovery_providers]})]);if(renderSourcesPage.sig===sig)return;renderSourcesPage.sig=sig;
  box.innerHTML=visible.length?visible.map(s=>{const host=sourceHost(s),web=sourceIsWeb(s),u=usage.get(s.id)||[],st=sourceState(s);const when=day(s.created);return `<div class="sources-row" data-src-row="${esc(s.id)}"><div class="src-name"><div class="source-library-name"><label class="source-library-selection">${controls.selectBox(s)}</label><span class="src-title">${esc(sourceTitle(s))}</span></div><small>${esc(host||'本地文件')} · ${esc(when)} 入库${s.archived_at?' · 已归档':''}${(s.discovery_providers||[]).length?' · 发现：'+esc(s.discovery_providers.map(p=>SEARCH_LABELS[p]||p).join('、')):''}</small></div><div class="src-type">${web?'网站':'文件'}</div><div class="src-status">${sourceStatusChip(st)}</div><div class="src-usage">${(getState().source_report_counts?.[s.id]??u.length)?esc((getState().source_report_counts?.[s.id]??u.length)+' 份报告'):'—'}</div><div class="src-actions"><div class="menu-wrap"><button type="button" class="ghost" data-src-menu aria-haspopup="menu" aria-expanded="false" aria-label="更多">⋯</button><div class="popover" role="menu" hidden><button type="button" role="menuitem" data-source-archive="${esc(s.id)}" data-archived="${!s.archived_at}">${s.archived_at?'恢复到日常':'归档来源'}</button>${['failed','cancelled','interrupted','visual'].includes(st)?'<button type="button" role="menuitem" data-sources-retry="'+esc(s.id)+'">重新读取</button>':''}${web?'<button type="button" role="menuitem" data-sources-copy="'+esc(s.url||'')+'">复制链接</button><a role="menuitem" href="'+esc(s.url)+'" target="_blank" rel="noreferrer">打开原文</a>':''}</div></div></div></div>`}).join(''):'<p class="help">没有匹配的来源。</p>';
  if(controls.loadingRun)box.innerHTML='<p class="help">正在读取这份报告的来源…</p>';
  if(q){
   if(!rows.length)box.innerHTML=`<p class="help">${search.loading?'正在检索本地材料…':search.error?'搜索未完成。':search.exhausted?(search.unsearchedCount?'已检索正文未找到匹配，仍有正文未能读取。':'没有匹配的来源。'):'本页未命中，仍有材料未检索。'}</p>`;
   box.querySelectorAll('[data-src-row]').forEach(row=>{const match=matches.get(row.dataset.srcRow),name=row.querySelector('.src-name');for(const hit of match?.hits||[]){const context=document.createElement('p');context.className='source-search-context';context.textContent=`第 ${hit.start_line} 行 · ${hit.context}`;name.append(context)}});
   const footer=document.createElement('div');footer.className='source-search-footer';
   if(search.unsearchedCount){const details=document.createElement('details'),summary=document.createElement('summary');summary.textContent=`${search.unsearchedCount} 份正文未完整检索`;details.append(summary);for(const item of search.unsearched){const line=document.createElement('p');line.textContent=`${item.name}：${sourceSearchReasons[item.reason]||'未检索'}`;details.append(line)}footer.append(details)}
   if(search.error){const error=document.createElement('p');error.textContent=search.error;footer.append(error);const restart=document.createElement('button');restart.type='button';restart.className='outline';restart.textContent='重新搜索';restart.onclick=()=>{sourceLibrarySearch.update({});renderSourcesPage.sig=null;renderSourcesPage()};footer.append(restart)}
   else if(search.next||search.loading){const more=document.createElement('button');more.type='button';more.className='outline';more.textContent=search.loading?'正在检索…':'继续搜索';more.disabled=search.loading;more.onclick=()=>sourceLibrarySearch.more();footer.append(more)}
   box.append(footer);
  }
  box.querySelectorAll('[data-src-row]').forEach(row=>row.onclick=e=>{if(e.target.closest('.menu-wrap,.source-library-selection'))return;openSourceDrawer(row.dataset.srcRow,usage,matches.get(row.dataset.srcRow)).catch(err=>notice(err.message,true))});
  controls.wire(box,visible,rows);
  box.querySelectorAll('[data-sources-retry]').forEach(b=>b.onclick=e=>{e.stopPropagation();action(async()=>{const src=await api('retry-source',{source_id:b.dataset.sourcesRetry});notice(sourceStatusLabel(src),['failed','cancelled','interrupted'].includes(src.status))})});
  box.querySelectorAll('[data-sources-copy]').forEach(b=>b.onclick=async e=>{e.stopPropagation();try{await copyText(b.dataset.sourcesCopy);notice('链接已复制')}catch{notice('复制失败',true)}});
  box.querySelectorAll('.menu-wrap').forEach(wrap=>{const t=wrap.querySelector('[data-src-menu]'),pop=wrap.querySelector('.popover');if(!t||!pop)return;t.onclick=e=>{e.stopPropagation();const open=pop.hidden;document.querySelectorAll('.popover').forEach(x=>x.hidden=true);pop.hidden=!open;t.setAttribute('aria-expanded',String(open))}});
 }
 function renderSourceOverview(s,result,usage){
  const pane=document.querySelector('[data-source-pane="overview"]');if(!pane)return;
  const st=sourceState(s),host=sourceHost(s);
  const date=dateTime(s.created);
  const statusText=st==='ready'?'正文已成功解析':st==='visual'?'正文提取不完整，需要视觉读取':sourceStatusLabel(s);
  const prov=result?sourceProvenanceRows(result):[];
  let html=`<section class="source-section"><h3>来源</h3><p class="source-value">${esc(sourceTitle(s))}</p><p class="help">${esc(host||'本地文件')} · ${esc(date)}</p></section>`
   +`<section class="source-section"><h3>读取</h3><p class="source-value">${sourceStatusChip(st)} ${statusText}</p>${s.error?`<p class="help">${esc(s.error)}</p>`:''}</section>`;
  if(prov.length)html+=`<section class="source-section"><h3>抓取信息</h3><dl class="source-provenance">${prov.map(([k,v])=>`<div><dt>${esc(k)}</dt><dd>${esc(v)}</dd></div>`).join('')}</dl></section>`;
  html+=`<section class="source-section"><h3>原始名称</h3><p class="source-value mono">${esc(s.name||'—')}</p>${s.url?`<h3>原始链接</h3><a class="source-link" href="${esc(s.url)}" target="_blank" rel="noreferrer">${esc(prettifyUrl(s.url))} ↗</a>`:''}</section>`
   +`<section class="source-section"><h3>使用于</h3>${usageHTML(s.id,usage)}</section>`;
  pane.innerHTML=html;
  wireUsage(pane);
 }
 function renderSourceText(result){
  const body=$('source-drawer-body');if(!body)return;
  const source=result.source||{};
  body.textContent=source.error||result.text||'没有可读正文。';
  showSourceMedia(result,drawerSourceMediaView());
 }
 function setSourceDrawerTab(name){
  const drawer=$('source-drawer');if(!drawer)return;
  if(!['overview','text','usage'].includes(name))name='overview';
  drawer.querySelectorAll('[data-source-pane]').forEach(p=>p.hidden=p.dataset.sourcePane!==name);
  drawer.querySelectorAll('[data-source-tab]').forEach(b=>markTab(b,b.dataset.sourceTab===name));
 }
 async function openSourceDrawer(id,usage,match){
  const s=(getState().sources||[]).find(x=>x.id===id);if(!s)return;
  const drawer=$('source-drawer'),backdrop=$('source-drawer-backdrop');if(!drawer)return;
  const workspace=getState().workspace_id;
  drawer.dataset.sourceId=id;
  const request=String((Number(drawer.dataset.request)||0)+1);drawer.dataset.request=request;
  const host=sourceHost(s);
  const brand=$('source-drawer-brand');if(brand)brand.textContent=host||'本地文件';
  const title=$('source-drawer-title');if(title)title.textContent=sourceTitle(s);
  const domain=$('source-drawer-domain');if(domain)domain.textContent=s.url?prettifyUrl(s.url):'';
  const chips=$('source-drawer-chips');if(chips)chips.innerHTML=`<span class="chip">${sourceIsWeb(s)?'网站':'文件'}</span>${sourceStatusChip(sourceState(s))}`;
  const original=$('source-drawer-original');if(original){original.hidden=true;original.removeAttribute('href')}
  const link=$('source-drawer-source');if(link){link.hidden=true;link.removeAttribute('href')}
  const retry=$('source-drawer-retry');if(retry)retry.hidden=!['failed','cancelled','interrupted','visual'].includes(sourceState(s));if(retry)retry.onclick=()=>action(async()=>{const r=await api('retry-source',{source_id:s.id});notice(sourceStatusLabel(r),['failed','cancelled','interrupted'].includes(r.status))});
  renderSourceOverview(s,null,usage);
  const up=document.querySelector('[data-source-pane="usage"]');if(up){up.innerHTML=usageHTML(s.id,usage);wireUsage(up)}
  const body=$('source-drawer-body');if(body)body.textContent='正在读取…';showSourceMedia({source:s,attachment:null},drawerSourceMediaView());
  drawer.hidden=false;if(backdrop)backdrop.hidden=false;
  setSourceDrawerTab('overview');
  let result=null;
  try{result=await api('source?id='+encodeURIComponent(id))}catch(e){if(drawer.dataset.request===request&&getState().workspace_id===workspace&&body)body.textContent='读取失败：'+e.message}
  if(!result||drawer.dataset.request!==request||getState().workspace_id!==workspace)return;
  applySourceLinks(result,{link:$('source-drawer-source'),linkLabel:'打开原文 ↗',original:$('source-drawer-original')});
  renderSourceOverview(s,result,usage);
  reportBrowsing.sourceUsage(id,()=>drawer.querySelectorAll('[data-source-report-usage]'),()=>drawer.dataset.request===request&&getState().workspace_id===workspace);
  renderSourceText(result);
  if(match?.hits?.length){
   setSourceDrawerTab('text');
   if(result.source.hash!==match.source_hash){body.textContent='来源已变化，请重新搜索后定位。';return}
   const hit=match.hits[0],lines=result.text.split(/\r\n|[\n\r\v\f\x1c-\x1e\x85\u2028\u2029]/),line=lines[hit.start_line-1];
   if(line===undefined){body.textContent='来源行号已变化，请重新搜索后定位。';return}
   const before=document.createTextNode(lines.slice(0,hit.start_line-1).join('\n')+(hit.start_line>1?'\n':''));
   const target=document.createElement('mark');target.className='source-search-hit';target.textContent=line;target.title=`第 ${hit.start_line} 行`;
   const after=document.createTextNode((hit.start_line<lines.length?'\n':'')+lines.slice(hit.start_line).join('\n'));
   body.replaceChildren(before,target,after);target.scrollIntoView({block:'center'});
  }
 }
 function closeSourceDrawer(){const d=$('source-drawer'),b=$('source-drawer-backdrop');if(d){d.hidden=true;d.dataset.request=String((Number(d.dataset.request)||0)+1);showSourceMedia({attachment:null},drawerSourceMediaView())}if(b)b.hidden=true}
 function init(){
  controls.init();
  if($('sources-sort'))$('sources-sort').onchange=()=>{renderSourcesPage.sig=null;renderSourcesPage()};
  $('sources-channel-filter').onchange=()=>{renderSourcesPage.sig=null;renderSourcesPage()};
  if($('sources-upload'))$('sources-upload').onchange=e=>action(async()=>{preflightSources(e.target.files,getUploadLimits());for(const f of e.target.files){await uploadSource(f)}e.target.value=''},'来源已保存');
  if($('sources-add-url'))$('sources-add-url').onclick=()=>action(async()=>{const s=await api('source-url',{url:$('sources-url').value});$('sources-url').value='';const row=$('sources-add-url-row');if(row)row.hidden=true;notice(s.status==='ready'?'网页已读取':'来源已保存，但读取失败：'+s.error,s.status!=='ready')});
  if($('sources-search'))$('sources-search').oninput=()=>{renderSourcesPage.sig='';renderSourcesPage()};
  if($('sources-type-filter'))$('sources-type-filter').onchange=()=>{renderSourcesPage.sig='';renderSourcesPage()};
  document.querySelectorAll('[data-sources-status]').forEach(b=>b.onclick=()=>{renderSourcesPage.status=b.dataset.sourcesStatus;document.querySelectorAll('[data-sources-status]').forEach(x=>markTab(x,x===b));renderSourcesPage.sig='';renderSourcesPage()});
  if($('sources-retry-all'))$('sources-retry-all').onclick=()=>action(async()=>{const list=(getState().sources||[]).filter(s=>controls.includes(s)&&['failed','cancelled','interrupted'].includes(s.status));if(!list.length)return;for(const s of list){try{await api('retry-source',{source_id:s.id})}catch(e){}}notice(`已重试 ${list.length} 个失败来源`)});
  if($('sources-add-file'))$('sources-add-file').onclick=()=>$('sources-upload').click();
  if($('sources-add-url-open'))$('sources-add-url-open').onclick=()=>{const row=$('sources-add-url-row');if(row){row.hidden=false;const u=$('sources-url');if(u)u.focus()}};
  if($('sources-add-url-cancel'))$('sources-add-url-cancel').onclick=()=>{const row=$('sources-add-url-row');if(row)row.hidden=true};
  if($('source-drawer-close'))$('source-drawer-close').onclick=()=>closeSourceDrawer();
  if($('source-drawer-backdrop'))$('source-drawer-backdrop').onclick=()=>closeSourceDrawer();
  document.querySelectorAll('[data-source-tab]').forEach(b=>b.onclick=()=>setSourceDrawerTab(b.dataset.sourceTab));
 }
 return {init,activate:controls.activate,sourceUsage,renderSourcesPage,setSourceDrawerTab,openSourceDrawer,closeSourceDrawer};
}
