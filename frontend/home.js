// Home page and task banner: the greeting, recent reports and running-task rail,
// and the banner that follows the latest report task.
import {$ as lookup,esc} from './dom.js';
import {dayTime} from './time.js';
import {updatePanel} from './report-panels.js';
import {GENRE_META,ICONS,splitTemplateName} from './templates.js';
const BANNER_RESULT_KINDS=['generate','revise','assess','review'];
const BANNER_RESULT_WINDOW=6*60*60*1000;
function bannerDismissKey(){try{return localStorage.getItem('briefloop-task-banner')||''}catch{return ''}}
function setBannerDismissKey(key){try{localStorage.setItem('briefloop-task-banner',key)}catch{}}
export function svgLineIcon(name,size=18){
 const body=ICONS[name]||ICONS.file||'';
 return `<svg viewBox="0 0 24 24" width="${size}" height="${size}" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${body}</svg>`;
}
export function homeUI({api,action,page,openBrief,openTask,taskFor,taskLabel,statuses,parse,reportStatus,reportDescription,scheduledReports,getState,$=lookup}){
 function bannerBrief(job){const p=parse(job.payload)||{};return (getState().briefs||[]).find(b=>b.id===p.version_id)||(getState().briefs||[]).find(b=>b.run_id===p.run_id)||null}
 function bannerTitle(job){const brief=bannerBrief(job);if(brief){const t=parse(brief.detail).title;if(t)return t}const run=(getState().runs||[]).find(r=>r.id===(parse(job.payload)||{}).run_id);if(run){const req=parse(run.requirements);if(req.title)return req.title}return ''}
 function renderTaskBanner(){
  const box=$('task-banner');if(!box)return;if(!getState()){box.hidden=true;updatePanel(box,'');return}
  const now=Date.now(),dismissed=bannerDismissKey();
  const running=(getState().jobs||[]).filter(j=>taskLabel(j.kind)&&['queued','running'].includes(j.status));
  const finished=(getState().jobs||[]).filter(j=>BANNER_RESULT_KINDS.includes(j.kind)&&['complete','failed','interrupted','cancelled'].includes(j.status)&&now-new Date(j.updated||j.created).getTime()<BANNER_RESULT_WINDOW);
  const candidates=[...running,...finished].filter(j=>j.id+':'+j.status!==dismissed).sort((a,b)=>new Date(b.updated||b.created)-new Date(a.updated||a.created));
  const job=candidates[0];
  if(!job){box.hidden=true;updatePanel(box,'');return}
  const title=bannerTitle(job),label=taskLabel(job.kind);
  box.hidden=false;
  // Unchanged markup keeps its buttons, and keyboard focus, across polls; handlers still rebind.
  if(['queued','running'].includes(job.status)){
   box.className='task-banner running';
   updatePanel(box,`<span class="task-banner-icon" aria-hidden="true"></span><div class="task-banner-main"><strong>正在${esc(label)}${title?'：'+esc(title):''}</strong><small>${esc(statuses[job.status]||job.status)}${job.progress?` · 第 ${job.progress.round}/${job.progress.k} 轮`:''} · 完成后会在这里提示</small></div><button type="button" class="outline" data-banner-open>查看任务</button><button type="button" class="task-banner-close" data-banner-close aria-label="暂时隐藏">✕</button>`);
   box.querySelector('[data-banner-open]').onclick=()=>page('reports');
  }else if(job.status==='complete'){
   box.className='task-banner ok';
   const verb=job.kind==='assess'?'评分已完成':job.kind==='review'?'独立审阅已完成':'新报告已生成';
   updatePanel(box,`<span class="task-banner-icon" aria-hidden="true">✓</span><div class="task-banner-main"><strong>${verb}${title?'：'+esc(title):''}</strong><small>已保存，可直接打开查看</small></div><button type="button" class="primary" data-banner-open>查看报告</button><button type="button" class="task-banner-close" data-banner-close aria-label="关闭">✕</button>`);
   box.querySelector('[data-banner-open]').onclick=()=>{setBannerDismissKey(job.id+':'+job.status);const brief=bannerBrief(job);if(brief&&openBrief(brief,{follow:false}))page('report');else page('reports')};
  }else{
   box.className='task-banner error';
   updatePanel(box,`<span class="task-banner-icon" aria-hidden="true">!</span><div class="task-banner-main"><strong>${esc(label)}未完成${title?'：'+esc(title):''}</strong><small>${esc(job.error||statuses[job.status]||job.status)}</small></div><button type="button" class="outline" data-banner-open>查看详情</button><button type="button" class="outline" data-banner-retry>重试</button><button type="button" class="task-banner-close" data-banner-close aria-label="关闭">✕</button>`);
   box.querySelector('[data-banner-open]').onclick=()=>page('reports');
   box.querySelector('[data-banner-retry]').onclick=()=>action(()=>api('resume',{job_id:job.id}),'已按页面显示的模型重新提交');
  }
  box.querySelector('[data-banner-close]').onclick=()=>{setBannerDismissKey(job.id+':'+job.status);box.hidden=true;updatePanel(box,'')};
 }
 function homeGreeting(){
  const name=getState()?.profile?.name,h=new Date().getHours();
  const part=h<6?'凌晨好':h<12?'早上好':h<14?'中午好':h<18?'下午好':'晚上好';
  return name?`${part}，${name}`:part;
 }
 function homeRecentReports(){
  const seen=new Set(),rows=[];
  for(const b of (getState()?.briefs||[])){if(seen.has(b.run_id))continue;seen.add(b.run_id);rows.push(b);if(rows.length>=3)break}
  return rows;
 }
 function hydrateHomeIcons(){
  document.querySelectorAll('[data-home-icon]').forEach(el=>{
   if(el.dataset.homeIconReady)return;
   el.innerHTML=svgLineIcon(el.dataset.homeIcon,18);
   el.dataset.homeIconReady='1';
  });
 }
 function renderHome(){
  scheduledReports.render();
  if($('home-greeting'))$('home-greeting').textContent=homeGreeting();
  hydrateHomeIcons();
  const rows=homeRecentReports();
  // Reference layout: recent reports stay in the main column; rail is for running jobs only.
  const mainRecent=$('home-block-recent');
  if(mainRecent)mainRecent.hidden=!rows.length;
  const mainBox=$('home-recent-list');
  if(mainBox&&rows.length){
   mainBox.innerHTML=rows.map(homeReportRowHTML).join('');
   mainBox.querySelectorAll('[data-home-report]').forEach(el=>el.onclick=()=>openHomeReport(el.dataset.homeReport));
  }
 }
 function renderHomeTasks(){
  const jobs=(getState().jobs||[]).filter(j=>taskLabel(j.kind)&&['queued','running'].includes(j.status));
  const rail=$('home-rail');
  const showRail=!!rail&&jobs.length>0;
  const chatPage=$('chat');
  if(chatPage)chatPage.classList.toggle('has-home-rail',showRail);
  if(rail)rail.hidden=!showRail;
  const jobBlock=$('home-rail-jobs'),jobList=$('home-rail-job-list');
  if(jobBlock&&jobList){
   jobBlock.hidden=!jobs.length;
   jobList.innerHTML=jobs.map(j=>{
    const label=bannerTitle(j)||taskLabel(j.kind)||j.kind;
    const st=j.status==='queued'?'排队中':'执行中';
    return `<article class="home-rail-job" data-job-id="${esc(j.id)}"><strong>${esc(label)}</strong><small>${esc(st)} · ${esc(dayTime(j.created))}</small><button type="button" class="outline" data-rail-open-job="${esc(j.id)}">打开任务</button></article>`;
   }).join('');
   jobList.querySelectorAll('[data-rail-open-job]').forEach(b=>b.onclick=()=>openTask(taskFor(b.dataset.railOpenJob)));
  }
  // Keep rail recent in sync only when rail is shown with reports too (jobs-only rail may omit recent)
  const recentBlock=$('home-rail-recent'),recentBox=$('home-rail-recent-list');
  if(recentBlock&&recentBox){
   recentBlock.hidden=true;
   recentBox.innerHTML='';
  }
 }
 function reportIconMeta(b){
  const run=(getState().runs||[]).find(r=>r.id===b.run_id),req=run?parse(run.requirements):{};
  const method=req.workflow_snapshot?.id||req.document_workflow?.id||req.workflow_id||b.workflow_id;
  const family={business_report:'商业报告',stock_research:'券商研报',meeting_minutes:'会议纪要',general_report:'通用报告'}[method];
  const template=(getState().templates||[]).find(t=>t.id===(req.template_id||b.template_id));
  return GENRE_META[family||splitTemplateName(template?.name||'').genre]||{icon:'layers',cat:'cat-neutral'};
 }
 function homeReportRowHTML(b){
  const st=reportStatus(b),desc=reportDescription(b);
  const when=dayTime(b.updated||b.created);
  const meta=reportIconMeta(b);
  return `<button type="button" class="home-report" data-home-report="${esc(b.id)}"><span class="home-report-icon ${meta.cat}" aria-hidden="true">${svgLineIcon(meta.icon,16)}</span><span class="home-report-body"><strong>${esc(parse(b.detail).title||'简报')}</strong>${desc?`<small>${esc(desc)}</small>`:''}</span><span class="home-report-meta"><time>${esc(when)}</time><span class="chip ${st.cls}">${esc(st.label)}</span></span></button>`;
 }
 function openHomeReport(id){
  const b=(getState().briefs||[]).find(x=>x.id===id);
  if(b&&openBrief(b,{follow:false}))page('report');
 }
 return {bannerTitle,renderTaskBanner,renderHome,renderHomeTasks,reportIconMeta};
}
