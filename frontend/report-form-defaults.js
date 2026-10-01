// Form defaults follow the backend's normalized period, while actual input and
// preset choices stay explicit even when their numbers equal a built-in default.
import {LENGTH_PRESETS,DEEP_LENGTH,INDUSTRY_LENGTH,reportLanguage} from './report-language.js';

const MONTHLY_LENGTH={zh:[9000,10000],en:[5800,6500]};
const BUDGETS={weekly:{search_requests:30,candidate_urls:150,source_pages:60},monthly:{search_requests:80,candidate_urls:400,source_pages:150}};
const LENGTH_FIELDS={target_words:'target-words',max_words:'max-words'};
const BUDGET_FIELDS={search_requests:'budget-search-requests',candidate_urls:'budget-candidate-urls',source_pages:'budget-source-pages'};
const PERIOD_FIELDS=['period','period_start','period_end','report_timezone'];

export function createReportFormDefaults({$,onChange=()=>{}}){
 let explicit=new Set(),window=null,windowKey=null,tierBudget=null;
 const form=()=>$('requirements');
 const periodValues=()=>Object.fromEntries(PERIOD_FIELDS.map(key=>[key,form()?.elements?.[key]?.value||'']));
 const periodKey=values=>JSON.stringify(PERIOD_FIELDS.map(key=>values[key]||''));
 function monthly(){
  const title=form()?.elements?.title?.value||'';
  if(/月报|月度|monthly/i.test(title))return true;
  // Never reuse a previous response while a changed period is being resolved.
  // Count local calendar dates, matching covers_month across DST transitions.
  const calendarDay=value=>Date.parse(String(value||'').slice(0,10));
  return windowKey===periodKey(periodValues())&&calendarDay(window?.end_exclusive)-calendarDay(window?.start)>=25*86400000;
 }
 function sync(){
  const language=reportLanguage($('report-language')?.value)||'zh',isMonthly=monthly();
  let [target,maximum]=$('research-tier')?.value==='deep'?DEEP_LENGTH[language]
   :$('report-profile')?.value==='industry_periodic'?(isMonthly?MONTHLY_LENGTH:INDUSTRY_LENGTH)[language]
   :LENGTH_PRESETS[language][$('length-preset')?.value]||LENGTH_PRESETS[language].balanced;
  if(explicit.has('max_words'))target=Math.min(target,Number($('max-words').value));
  if(!explicit.has('target_words'))$('target-words').value=String(target);
  if(!explicit.has('max_words'))$('max-words').value=String(Math.max(maximum,Number($('target-words').value)));
  if(!explicit.has('research_budget'))for(const [key,id] of Object.entries(BUDGET_FIELDS))$(id).value=String((tierBudget||BUDGETS[isMonthly?'monthly':'weekly'])[key]);
  onChange();
 }
 function restore(requirements={}){
  explicit=new Set([...Object.keys(LENGTH_FIELDS),'research_budget'].filter(key=>requirements[key]!=null));
  window=null;windowKey=null;tierBudget=null;
 }
 function setWindow(value,requirements=periodValues()){
  window=value;windowKey=periodKey(requirements);sync();
 }
 function budgetSelected(){explicit.add('research_budget')}
 function selectTierBudget(budget){
  if(!explicit.has('research_budget'))tierBudget={...budget};
  sync();
 }
 function prepare(requirements){
  // If Generate beats an in-flight preview, admission still computes the same
  // defaults from the actual period. Only user-provided numbers are overrides.
  for(const key of [...Object.keys(LENGTH_FIELDS),'research_budget'])if(!explicit.has(key)&&!(key==='research_budget'&&tierBudget))delete requirements[key];
  return requirements;
 }
 function init(){
  for(const [key,id] of Object.entries(LENGTH_FIELDS))for(const event of ['input','change'])$(id)?.addEventListener(event,()=>explicit.add(key));
  $('length-preset')?.addEventListener('change',()=>{explicit.add('target_words');explicit.add('max_words')});
  $('length-mode')?.addEventListener('change',()=>{if($('length-mode').value==='strict')explicit.add('max_words')});
  $('budget-preset')?.addEventListener('change',budgetSelected);
  for(const id of Object.values(BUDGET_FIELDS))for(const event of ['input','change'])$(id)?.addEventListener(event,budgetSelected);
  for(const key of ['title',...PERIOD_FIELDS])for(const event of ['input','change'])form()?.elements?.[key]?.addEventListener(event,sync);
  form()?.addEventListener('reset',()=>queueMicrotask(()=>{restore();sync()}));
 }
 return {init,restore,sync,setWindow,selectTierBudget,prepare};
}
