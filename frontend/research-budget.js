// Research budget: the setup fields with their tier presets, and the run's usage panel and search activity.
import {$ as lookup,esc} from './dom.js';
import {SEARCH_LABELS} from './search-settings.js';
const RESEARCH_BUDGET_PRESETS={weekly:{search_requests:30,candidate_urls:150,source_pages:60},monthly:{search_requests:80,candidate_urls:400,source_pages:150}};
const RESEARCH_TIERS={quick:{search_requests:6,candidate_urls:30,source_pages:12},standard:{search_requests:30,candidate_urls:150,source_pages:60},deep:{search_requests:80,candidate_urls:400,source_pages:150}};
const BUDGET_FIELDS={search_requests:'budget-search-requests',candidate_urls:'budget-candidate-urls',source_pages:'budget-source-pages'};
export function researchBudgetUI({api,parse,getState,getCurrent,readSearchPolicy,selectTierBudget,$=lookup}){
 function readResearchBudget(){return Object.fromEntries(Object.entries(BUDGET_FIELDS).map(([key,id])=>[key,Number($(id).value)]))}
 function reflectBudgetPreset(){const current=readResearchBudget();$('budget-preset').value=Object.keys(RESEARCH_BUDGET_PRESETS).find(name=>Object.keys(BUDGET_FIELDS).every(key=>current[key]===RESEARCH_BUDGET_PRESETS[name][key]))||'custom'}
 function initializeResearchBudget(requirements){const budget=requirements.research_budget||RESEARCH_BUDGET_PRESETS.weekly;for(const [key,id] of Object.entries(BUDGET_FIELDS))$(id).value=budget[key]??RESEARCH_BUDGET_PRESETS.weekly[key];reflectBudgetPreset();renderBudgetProviderScope()}
 function renderBudgetProviderScope(){const p=readSearchPolicy(),native=p.primary_provider==='native'||(p.coverage_mode!=='primary_only'&&p.native_search_enabled);$('budget-provider-scope').textContent='受控 API 渠道共享搜索与候选预算；正文按唯一 URL 计量。'+(native?'宿主原生搜索次数未知，单独显示，不计入受控 API 硬上限。':'')}
 let budgetPolling=false;
 function researchBudgetTarget(){
  const state=getState(),current=getCurrent();
  const job=state?.jobs.find(j=>j.kind==='generate'&&['queued','running'].includes(j.status));const runningId=job?parse(job.payload).run_id:null,runId=runningId||current?.run_id;if(!runId)return null;
  const run=state.runs.find(r=>r.id===runId),title=run?parse(run.requirements).title:'';return {id:runId,label:(runningId?'正在运行的报告':'当前稿件')+(title?' · '+title:'')};
 }
 async function refreshReportBudget(){
  const target=researchBudgetTarget(),panel=$('report-research-budget');if(!target){panel.hidden=true;return}if(budgetPolling)return;budgetPolling=true;
  try{
   const result=await api('research-budget?run='+encodeURIComponent(target.id));if(researchBudgetTarget()?.id!==target.id)return;panel.hidden=false;panel.dataset.runId=target.id;
   if(!result.limits){$('budget-view-title').textContent='研究预算 · 未设置';$('budget-view-body').innerHTML=`<p class="help">${esc(target.label)}创建时未设置研究预算。</p>`;return}
   const scope=result.scope||{},native=!(scope.allowed_providers||[scope.search_provider]).some(p=>['tavily','duckduckgo','bocha'].includes(p)),limits=result.limits,used=result.used||{},remaining=result.remaining||{},format=value=>typeof value==='number'?new Intl.NumberFormat('zh-CN').format(value):'未知';
   $('budget-view-title').textContent='研究预算'+(result.exhausted?' · 已达到上限':'');
   const labels={search_requests:'受控 API 搜索',candidate_urls:'候选 URL',source_pages:'全文获取（URL）'};
   $('budget-view-body').innerHTML=`<p class="budget-run-label">${esc(target.label)}</p><table class="budget-usage-table"><thead><tr><th>项目</th><th>已用</th><th>上限</th><th>剩余</th></tr></thead><tbody>${Object.entries(labels).map(([key,label])=>{const unmetered=native&&key!=='source_pages';return `<tr><th>${label}</th><td>${unmetered?'不可精确计量':format(used[key])}</td><td>${format(limits[key])}</td><td>${unmetered?'—':format(remaining[key])}</td></tr>`}).join('')}</tbody></table><p class="help">${scope.native_search_enabled?'宿主原生搜索次数：未知；表中搜索次数仅包含受控 API。':''}全文获取按本轮不同 URL 计数，不代表已核验或已阅读数量；已有上传材料不扣。</p>${result.exhausted?'<p class="budget-exhausted">已达到预算上限，保留已有结果与缺口，不自动加额。</p>':''}<button type="button" id="search-activity-open">查看本轮搜索渠道与记录</button>`;
   $('search-activity-open').onclick=()=>showSearchActivity(target.id);
  }catch{panel.hidden=false;$('budget-view-title').textContent='研究预算';$('budget-view-body').innerHTML='<p class="help">预算信息暂不可用。</p>'}finally{budgetPolling=false}
 }
 async function showSearchActivity(runId){
  const dialog=$('search-activity-dialog');dialog.showModal();$('search-activity-body').textContent='正在读取…';
  try{const result=await api('search-activity?run='+encodeURIComponent(runId));const p=result.policy,native=p.primary_provider==='native'||(p.coverage_mode!=='primary_only'&&p.native_search_enabled);
  $('search-activity-body').innerHTML=`<p>优先 ${esc(SEARCH_LABELS[p.primary_provider])}${native?' · 宿主原生搜索次数：未知':''}</p>`+result.records.map(r=>`<article class="panel"><strong>${esc(SEARCH_LABELS[r.provider]||r.provider)} · ${esc(r.outcome==='success'?'已返回':'失败')}</strong><p>${esc(r.query||'')}</p><p class="help">${esc(r.reason||r.purpose||'')} · ${esc(r.elapsed_ms??'未知')} ms${r.http_status?' · 状态 '+esc(r.http_status):''}${r.failure_kind?' · '+esc(r.failure_kind):''} · ${r.admitted_urls?.length||0} 个已接纳候选</p></article>`).join('');
  if(!result.records.length)$('search-activity-body').innerHTML+='<p class="help">暂无受控 API 搜索记录，不能据此认定宿主没有搜索。</p>';
  }catch(e){$('search-activity-body').textContent=e.message}
 }
 function init(){
  $('budget-preset').onchange=()=>{const budget=RESEARCH_BUDGET_PRESETS[$('budget-preset').value];if(budget)for(const [key,id] of Object.entries(BUDGET_FIELDS))$(id).value=budget[key]};
  for(const id of Object.values(BUDGET_FIELDS))$(id).oninput=reflectBudgetPreset;
  $('research-tier').onchange=()=>{const budget=RESEARCH_TIERS[$('research-tier').value];if(!budget)return;selectTierBudget(budget);renderBudgetProviderScope()};
  $('search-activity-close').onclick=()=>$('search-activity-dialog').close();
 }
 return {init,readResearchBudget,reflectBudgetPreset,initializeResearchBudget,renderBudgetProviderScope,refreshReportBudget};
}
