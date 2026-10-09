import {esc} from './dom.js';

export function libraryScope(source,scope='active'){
 return scope==='all'||Boolean(source.archived_at)===(scope==='archived');
}
export function createSourceLibraryControls({$,api,action,notice,getState,render}){
 let selected=new Set(),pageKey='',limit=50,workspace,runId='',runSources=null,loadingRun=false;
 let reports=[],cursor=null,loaded=false,loading=false,ticket=0,reportTicket=0,optionsKey='',sourceRevision='';
 function sync(){
  const next=getState()?.workspace_id;if(next===workspace){reportOptions();if(runId&&!loadingRun&&sourceRevision!==revision())loadRun(runId);return}
  workspace=next;optionsKey='';sourceRevision='';ticket++;reportTicket++;selected.clear();runId='';runSources=null;loadingRun=false;
  reports=[];cursor=null;loaded=false;loading=false;pageKey='';limit=50;
  if($('sources-report-filter'))$('sources-report-filter').innerHTML='<option value="">所有报告</option>';
 }
 const scope=()=>$('sources-scope')?.value||'active';
 function reportOptions(){
  const select=$('sources-report-filter');if(!select)return;
  const pending=(getState()?.runs||[]).filter(r=>!reports.some(b=>b.run_id===r.id)).map(r=>({run_id:r.id,detail:r.requirements}));
  const choices=[...pending,...reports],key=JSON.stringify([choices.map(r=>[r.run_id,r.detail]),cursor]);if(key===optionsKey)return;optionsKey=key;
  select.innerHTML='<option value="">所有报告</option>'+choices.map(r=>`<option value="${esc(r.run_id)}">${esc(JSON.parse(r.detail||'{}').title||'未命名报告')}</option>`).join('')+(cursor?'<option value="__more">加载更多报告…</option>':'');select.value=runId;
 }
 async function loadReports(){
  sync();if(loading||loaded&&!cursor)return;
  loading=true;const request=++reportTicket,ws=workspace;
  try{const data=await api('reports?limit=50'+(cursor?'&cursor='+encodeURIComponent(cursor):''));
   if(request!==reportTicket||ws!==getState()?.workspace_id)return;
   const merged=new Map([...reports,...data.items].map(r=>[r.run_id,r]));reports=[...merged.values()];cursor=data.next_cursor;loaded=true;reportOptions();
  }catch(error){if(request===reportTicket)notice(error.message,true)}finally{if(request===reportTicket)loading=false}
 }
 const revision=()=>JSON.stringify([getState()?.runs?.find(r=>r.id===runId)?.source_count,getState()?.sources?.length]);
 async function loadRun(value){
  sourceRevision=revision();loadingRun=true;const request=++ticket;
  try{const data=await api('source-library-report?run_id='+encodeURIComponent(value));
   if(request!==ticket||workspace!==getState()?.workspace_id)return;
   runSources=new Set(data.source_ids);loadingRun=false;render();
  }catch(error){if(request===ticket){loadingRun=false;runSources=new Set();notice(error.message,true);render()}}
 }
 async function chooseReport(){
  const value=$('sources-report-filter').value;
  if(value==='__more'){$('sources-report-filter').value=runId;await loadReports();return}
  ticket++;runId=value;runSources=null;loadingRun=!!value;selected.clear();
  if(value){sourceRevision=revision();render();await loadRun(value)}else{loadingRun=false;render()}
 }
 function windowRows(rows,key){
  if(key!==pageKey){pageKey=key;limit=50;selected.clear()}
  const present=new Set(rows.map(s=>s.id));for(const id of selected)if(!present.has(id))selected.delete(id);
  return rows.slice(0,limit);
 }
 function selectBox(source){return `<input type="checkbox" data-source-select="${esc(source.id)}" aria-label="选择来源 ${esc(source.name)}" ${selected.has(source.id)?'checked':''}>`}
 async function change(ids,archived){
  if(!ids.length)return;
  await action(async()=>{await api('source-archive',{source_ids:ids,archived});selected.clear();notice(`${ids.length} 个来源已${archived?'归档，报告引用仍保留':'恢复到日常列表'}`)});
 }
 function buttons(rows){
  const bar=$('sources-batch');if(!bar)return;
  const choices=rows.filter(s=>selected.has(s.id));
  bar.innerHTML=`<span>已选 ${choices.length} 项</span><button type="button" data-select-page>选择已显示</button><button type="button" data-clear-selection ${!choices.length?'disabled':''}>清空选择</button><button type="button" data-archive-selection ${!choices.some(s=>!s.archived_at)?'disabled':''}>归档所选</button><button type="button" data-restore-selection ${!choices.some(s=>s.archived_at)?'disabled':''}>恢复所选</button>`;
  bar.querySelector('[data-select-page]').onclick=()=>{rows.slice(0,Math.min(limit,200)).forEach(s=>selected.add(s.id));if(limit>200)notice('每次最多选择 200 个来源');render()};
  bar.querySelector('[data-clear-selection]').onclick=()=>{selected.clear();render()};
  bar.querySelector('[data-archive-selection]').onclick=()=>change(choices.filter(s=>!s.archived_at).map(s=>s.id),true);
  bar.querySelector('[data-restore-selection]').onclick=()=>change(choices.filter(s=>s.archived_at).map(s=>s.id),false);
 }
 function wire(box,visible,rows){
  box.querySelectorAll('[data-source-select]').forEach(input=>input.onchange=()=>{if(input.checked&&selected.size>=200){input.checked=false;notice('每次最多选择 200 个来源');return}input.checked?selected.add(input.dataset.sourceSelect):selected.delete(input.dataset.sourceSelect);buttons(rows)});
  box.querySelectorAll('[data-source-archive]').forEach(button=>button.onclick=()=>change([button.dataset.sourceArchive],button.dataset.archived==='true'));
  buttons(rows);
  const more=$('sources-load-more');if(more){more.hidden=visible.length>=rows.length;more.onclick=()=>{limit+=50;render()}}
 }
 function init(){
  if($('sources-scope'))$('sources-scope').onchange=()=>{selected.clear();render()};
  if($('sources-report-filter')){$('sources-report-filter').onchange=chooseReport;$('sources-report-filter').onfocus=()=>{if(!loaded)loadReports()}}
 }
 return {sync,init,activate:()=>{if(!loaded)loadReports()},scope,windowRows,selectBox,wire,
  get runId(){return runId},get loadingRun(){return loadingRun},
  includes:source=>libraryScope(source,scope())&&(!runId||runSources?.has(source.id)),
  get selectionKey(){return [...selected].join(',')},get pageLimit(){return limit}};
}
