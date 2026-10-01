// Market direction is report data, never success/error state. Keep this parser
// aligned with market_convention.py; bare financial values remain uncolored.
import {reportLanguage} from './report-language.js';

const NUMBER=String.raw`[+\-−]?\d+(?:,\d{3})*(?:\.\d+)?(?:\s*(?:%|％|个百分点|percentage points?|pp|bps?|基点))?`;
const PREFIX=String.raw`上涨|增长|增幅|上升|提高|增加|下跌|下降|下滑|减少|降低|跌幅|涨跌|变动|变化|同比|环比|\b(?:up|down|rose|rise|grew|growth|increase[ds]?|decrease[ds]?|decline[ds]?|fell|fall|change|delta|yoy|mom|qoq)\b`;
const downPattern=/下跌|下降|下滑|减少|降低|跌幅|\b(?:down|decrease[ds]?|decline[ds]?|fell|fall)\b/i;
const direction=(number,down=false)=>{const value=Number(number.replace('−','-').replaceAll(',','').match(/^[+\-]?\d+(?:\.\d+)?/)[0]);return value===0?'flat':down||value<0?'down':'up'};
export function resolveMarket(requirements={}){return ['cn','intl'].includes(requirements.market_convention)?requirements.market_convention:reportLanguage(requirements.language)==='en'?'intl':'cn'}
export function semanticDeltaSpans(text,{context=''}={}){
 text=String(text);const spans=[];
 for(const match of text.matchAll(new RegExp(`([↑↓↗↘▲▼])\\s*(${NUMBER})`,'gi')))spans.push([match.index,match.index+match[0].length,direction(match[2],'↓↘▼'.includes(match[1]))]);
 for(const match of text.matchAll(new RegExp(`(${PREFIX})\\s*(?:(?:by|of)\\s+)?[:：]?\\s*(${NUMBER})`,'gi'))){
  const end=match.index+match[0].length,start=end-match[2].length;
  if(!spans.some(([a,b])=>start<b&&end>a))spans.push([start,end,direction(match[2],downPattern.test(match[1]))]);
 }
 const numeric=new RegExp(`^\\s*(${NUMBER})\\s*$`,'i').exec(text);
 if(!spans.length&&numeric&&new RegExp(PREFIX,'i').test(context)){const start=text.indexOf(numeric[1]);spans.push([start,start+numeric[1].length,direction(numeric[1],downPattern.test(context))])}
 return spans.sort((a,b)=>a[0]-b[0]);
}

const requirements=run=>{try{return typeof run?.requirements==='string'?JSON.parse(run.requirements):run?.requirements||{}}catch{return {}}};
const choices='<option value="cn">中国大陆 · 红涨绿跌</option><option value="intl">国际 · 绿涨红跌</option>';

export function createMarketConvention({$,api,notice,getState,getCurrent,onApplied=()=>{}}){
 const pending=new Map();
 const flowKey=(workspace,runId)=>`${workspace}\0${runId}`;
 function init(){
  const language=$('report-language');if(!language)return;
  const label=document.createElement('label');label.textContent='涨跌配色';
  const select=document.createElement('select');select.id='setup-market-convention';select.name='market_convention';select.dataset.testid='setup-market-convention';
  select.innerHTML='<option value="">按报告语言选择</option>'+choices;label.append(select);language.closest('label').after(label);
  syncLanguage();
 }
 function syncLanguage(){const select=$('setup-market-convention');if(select?.options?.[0])select.options[0].textContent=reportLanguage($('report-language')?.value)==='en'?'按报告语言 · 绿涨红跌':'按报告语言 · 红涨绿跌'}
 function restore(value){if($('setup-market-convention'))$('setup-market-convention').value=['cn','intl'].includes(value)?value:'';syncLanguage()}
 function prepare(value){if(!value.market_convention)delete value.market_convention}
 function render(){
  const current=getCurrent(),state=getState(),run=state?.runs?.find(item=>item.id===current?.run_id),req=requirements(run),root=$('report');
  if(root)root.dataset.market=resolveMarket(req);
  let card=$('report-market-settings');
  if(!current||!run){card?.remove();onApplied();return}
  if(!card){
   card=document.createElement('div');card.id='report-market-settings';card.className='assistant-card';
   card.innerHTML='<label>本稿涨跌配色<select id="report-market-convention" data-testid="report-market-convention">'+choices+'</select></label><p class="help">按报告保存，Word / Excel 沿用此设置。更改后已有独立审阅可能需要重新核对。已登记的图表图片需重新生成才能换色。</p><p id="report-market-status" class="help" role="status" aria-live="polite"></p>';
   $('assistant-summary')?.after(card);$('report-market-convention').addEventListener('change',save);
  }
  const select=$('report-market-convention'),saving=pending.get(flowKey(state.workspace_id,run.id));select.disabled=!!saving;select.value=saving?saving.value:resolveMarket(req);
  const status=$('report-market-status');if(status)status.textContent=select.disabled?'正在保存…':'';
  onApplied();
 }
 async function save(){
  const state=getState(),current=getCurrent(),select=$('report-market-convention');if(!current||!select)return;
  const flow={workspace:state.workspace_id,runId:current.run_id,value:select.value};
  const key=flowKey(flow.workspace,flow.runId);if(pending.has(key))return;
  pending.set(key,flow);select.disabled=true;$('report-market-status').textContent='正在保存…';
  try{
   const saved=await api('report-settings',{workspace_id:flow.workspace,run_id:flow.runId,market_convention:flow.value});
   if(getState()?.workspace_id!==flow.workspace)return;
   const run=getState().runs?.find(item=>item.id===flow.runId);if(run)run.requirements=JSON.stringify(saved.requirements);
   if(getCurrent()?.run_id===flow.runId){render();notice?.('本稿涨跌配色已保存')}
  }catch(error){if(getState()?.workspace_id===flow.workspace&&getCurrent()?.run_id===flow.runId)notice?.(error.message||'涨跌配色保存失败',true)}
  finally{if(pending.get(key)===flow)pending.delete(key);if(getState()?.workspace_id===flow.workspace&&getCurrent()?.run_id===flow.runId)render()}
 }
 return {init,syncLanguage,restore,prepare,render};
}

// Standalone HTML/PDF has no app stylesheet. Inline only the semantic foreground
// spans; user-authored text colors and all table/background styles stay intact.
export function applyMarketColors(root,req={}){
 const market=resolveMarket(req),colors=market==='intl'?{up:'#1E8E4F',down:'#D9363E',flat:'#5F6675'}:{up:'#D9363E',down:'#1E8E4F',flat:'#5F6675'};
 root.dataset.market=market;
 const doc=root.ownerDocument;
 for(const block of root.querySelectorAll('p,h1,h2,h3,h4,h5,h6')){
  if(block.closest('pre,code'))continue;
  const cell=block.closest('td,th'),row=cell?.parentElement,table=cell?.closest('table'),firstRow=table?.querySelector('tr');let context='';
  const merged=table&&[...table.querySelectorAll('td,th')].some(item=>(item.rowSpan||1)>1||(item.colSpan||1)>1);
  if(cell&&row!==firstRow&&!merged){let column=0;for(const candidate of row.children){if(candidate===cell)break;column+=candidate.colSpan||1}let index=0;for(const header of firstRow?.children||[]){const end=index+(header.colSpan||1);if(column<end){if(header.tagName==='TH')context=header.textContent;break}index=end}}
  const walker=doc.createTreeWalker(block,4),nodes=[];let node,offset=0;
  while((node=walker.nextNode())){nodes.push({node,start:offset});offset+=node.textContent.length}
  const text=nodes.map(item=>item.node.textContent).join(''),spans=semanticDeltaSpans(text,{context});
  if(cell&&spans.length&&new RegExp(`^\\s*(?:[↑↓↗↘▲▼]\\s*)?${NUMBER}\\s*$`,'i').test(text)&&!block.style.textAlign&&!cell.style.textAlign)block.style.textAlign='right';
  for(const {node,start} of nodes){
   const text=node.textContent,end=start+text.length;if(node.parentElement.closest('code,[data-direction]'))continue;
   let colored=false;for(let parent=node.parentElement;parent&&parent!==block.parentElement;parent=parent.parentElement)if(parent.style?.color){colored=true;break}if(colored)continue;
   const hits=spans.filter(([a,b])=>a<end&&b>start).map(([a,b,d])=>[Math.max(a,start)-start,Math.min(b,end)-start,d]);if(!hits.length)continue;
   const cuts=[...new Set([0,text.length,...hits.flatMap(([a,b])=>[a,b])])].sort((a,b)=>a-b),fragment=doc.createDocumentFragment();
   for(let index=0;index<cuts.length-1;index++){const a=cuts[index],b=cuts[index+1],direction=hits.find(([left,right])=>left<=a&&b<=right)?.[2];if(direction){const span=doc.createElement('span');span.dataset.direction=direction;span.style.color=colors[direction];span.textContent=text.slice(a,b);fragment.append(span)}else fragment.append(doc.createTextNode(text.slice(a,b)))}
   node.replaceWith(fragment);
  }
 }
 return root;
}
