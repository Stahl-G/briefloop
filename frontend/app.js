import {reasoningControls,settingsEffort,reasoningModel} from './reasoning-controls.js';
import {$,esc} from './dom.js';
import {clock,day,dayTime,dateTime,dateTimeSeconds,moment} from './time.js';
import {api,uploadSource,setToken,getUploadLimits} from './api.js';
import {createSourceLibrarySearch} from './source-library-search.js';
const reasoning=reasoningControls({api});
import {createAssessmentPanel} from './assessment-panel.js';
import {renderVersionDiff} from './version-diff.js';
import {DOMSerializer} from '@tiptap/pm/model';
import {beginPanel,updatePanel} from './report-panels.js';
import {taskProgressCard} from './task-progress.js';
import {copyText} from './clipboard.js';
import {scheduleUI} from './schedules.js';
import {adaptivePoll} from './polling.js';
import {preflightSources,uploadPayload} from './uploads.js';
import {runtimeCard,runtimeModelSummary} from './runtime-cards.js';
import {createOfficeTools} from './office-tools.js';
import {welcomeAgents,welcomeAgentCard,welcomeReady} from './welcome.js';
import {activityCenter} from './notifications.js';
var activity=null;
import {reviewPending,withoutSupersededRetries,factCheckHTML} from './review-status.js';
import {Editor,Extension} from '@tiptap/core';
import {Plugin,PluginKey} from '@tiptap/pm/state';
import {Decoration,DecorationSet} from '@tiptap/pm/view';
import StarterKit from '@tiptap/starter-kit';
import {TableKit} from '@tiptap/extension-table';
import Image from '@tiptap/extension-image';
import {Markdown} from '@tiptap/markdown';
import {connectorSettings} from './connectors.js';
import {mcpSelection} from './mcp-selection.js';
import {appUpdatesUI} from './app-updates.js';
import {templatesUI,GENRE_META,ICONS,splitTemplateName} from './templates.js';
import {deliveryUI,changeTypeLabel,displayDate} from './delivery.js';
import {reportExportUI} from './report-export.js';
import {TextStyle,Layout,ReportImage,Citation,ReportTrailingParagraph,editorDocument,savedDocument,readerHighlights} from './rich-document.js';
// Reader-appropriateness marks are editor decorations: they never enter the saved
// document, Word export or Markdown. Hover shows the violation and its requirement.
let highlightQuotes=[],highlightFindings=new Map(),highlightKinds=new Map();
const mustFixKey=new PluginKey('mustFixHighlight');
function buildHighlightDecorations(doc){
 const decos=[];
 for(const item of highlightQuotes){
  const quote=item.quote;if(!quote)continue;
  doc.descendants((node,pos)=>{if(!node.isText)return;const text=node.text||'';let from=0,idx;while((idx=text.indexOf(quote,from))>=0){decos.push(Decoration.inline(pos+idx,pos+idx+quote.length,{class:item.kind==='must'?'must-fix':'suggestion-fix','data-finding':String(item.findingIndex),'aria-label':item.title||''}));from=idx+quote.length}})
 }
 return DecorationSet.create(doc,decos);
}
const MustFixHighlight=Extension.create({name:'mustFixHighlight',addProseMirrorPlugins(){return [new Plugin({key:mustFixKey,state:{init:(_,editorState)=>buildHighlightDecorations(editorState.doc),apply:(tr,old)=>tr.docChanged||tr.getMeta(mustFixKey)?buildHighlightDecorations(tr.doc):old},props:{decorations:editorState=>mustFixKey.getState(editorState)}})]}})
function applyHighlights(){if(editor&&editor.view)editor.view.dispatch(editor.state.tr.setMeta(mustFixKey,true).setMeta('addToHistory',false))}
const dimensionLabels={evidence:'证据与准确性',coverage:'覆盖与取舍',analysis:'分析有效性',expression:'表达与可用性'};
let highlightTip=null;
function highlightTipElement(){if(!highlightTip){highlightTip=document.createElement('div');highlightTip.className='highlight-tip';highlightTip.setAttribute('role','tooltip');document.body.append(highlightTip)}return highlightTip}
function findingTipHTML(index){const f=highlightFindings.get(String(index));if(!f)return '';const kind=highlightKinds.get(String(index))||'must';const label=(kind==='must'?'必须修正':'建议')+' · '+(dimensionLabels[f.dimension]||f.dimension||'问题');return `<div class="highlight-tip-head ${kind}">${esc(label)}</div>${f.report_quote?`<blockquote>${esc(f.report_quote)}</blockquote>`:''}${f.requirement?`<p><strong>对应要求</strong>${esc(f.requirement)}</p>`:''}${f.description?`<p><strong>问题</strong>${esc(f.description)}</p>`:''}${f.suggestion?`<p><strong>建议</strong>${esc(f.suggestion)}</p>`:''}<p class="highlight-tip-hint">点击此句定位右侧条目</p>`}
function placeHighlightTip(event){const tip=highlightTip;if(!tip||tip.hidden)return;const pad=12;let x=event.clientX+14,y=event.clientY+16;const rect=tip.getBoundingClientRect();if(x+rect.width>window.innerWidth-pad)x=Math.max(pad,event.clientX-rect.width-14);if(y+rect.height>window.innerHeight-pad)y=Math.max(pad,event.clientY-rect.height-16);tip.style.left=x+'px';tip.style.top=y+'px'}
function showHighlightTip(node,event){const tip=highlightTipElement();tip.innerHTML=findingTipHTML(node.dataset.finding);if(!tip.innerHTML)return;tip.hidden=false;placeHighlightTip(event)}
function hideHighlightTip(){if(highlightTip)highlightTip.hidden=true}
function jumpToFinding(index){const node=$('assessment').querySelector('[data-finding="'+index+'"]');if(!node)return;node.open=true;node.scrollIntoView({block:'center',behavior:'smooth'});node.classList.add('flash');setTimeout(()=>node.classList.remove('flash'),1400)}
document.addEventListener('mouseover',e=>{const node=e.target.closest?.('.must-fix,.suggestion-fix');if(node)showHighlightTip(node,e);else if(!e.target.closest?.('.highlight-tip'))hideHighlightTip()});
document.addEventListener('mousemove',e=>{if(e.target.closest?.('.must-fix,.suggestion-fix'))placeHighlightTip(e)});
document.addEventListener('mouseout',e=>{if(e.target.closest?.('.must-fix,.suggestion-fix')&&!e.relatedTarget?.closest?.('.highlight-tip'))hideHighlightTip()});
document.addEventListener('click',e=>{const node=e.target.closest?.('.must-fix,.suggestion-fix');if(node)jumpToFinding(node.dataset.finding)});
const parse=s=>JSON.parse(s||'{}');
let followUpdates=true;
let state,current,pendingRun=null,editor,dirty=false,saving=false,saveTimer,learnTimer,markdownMode=false,selected=new Set(),referenceSelected=new Set();
function notice(s,error=false){$('notice').textContent=s;$('notice').classList.toggle('error',error);$('notice').hidden=false;clearTimeout(notice.timer);notice.timer=setTimeout(()=>$('notice').hidden=true,error?12000:4500)}
function page(name){if(document.body.classList.contains('report-chat-open'))setReportChatOpen(false);if(((name==='chat'&&!chat.id)||name==='setup')&&(state?.settings?.model_selection_required||!state?.settings?.model)){notice('请先选择 Agent 和模型');name='welcome'}if(name!=='settings-dialog'&&$('custom-api-key'))$('custom-api-key').value='';for(const id of ['chat','report','reports','sources','templates','setup','learning','settings-dialog','welcome'])$(id).hidden=id!==name;document.querySelectorAll('nav [data-page]').forEach(b=>b.classList.toggle('active',b.dataset.page===name));if(name==='learning')refreshCandidates();if(name==='setup'){if(typeof reportMcpSelection!=='undefined')reportMcpSelection.refresh();moveSearchSettings('setup');applyPendingSetupFields()}else if($('tavily-key')){$('tavily-key').value='';$('bocha-key').value='';$('zhipu-key').value=''}if(name==='reports'){renderTasks();renderTaskGraph()}if(['reports','templates','learning'].includes(name))activity?.readCategory(name)}
document.querySelectorAll('[data-page]').forEach(b=>b.onclick=()=>page(b.dataset.page));
async function action(fn,message){try{await fn();if(message)notice(message);await refresh()}catch(e){notice(e.message,true)}}
// Optional OfficeCLI enhancement; every surface it adds hides itself while the
// switch is off, so behaviour matches a machine without the binary.
const office=createOfficeTools({api,action,$,esc,parse,getState:()=>state,notice});
let tooltipTarget=null;
function showTip(el){const tip=$('tooltip');if(!tip)return;const text=el.getAttribute('data-tip');if(!text)return;tooltipTarget=el;tip.textContent=text;tip.hidden=false;const r=el.getBoundingClientRect(),t=tip.getBoundingClientRect();let left=r.left+r.width/2-t.width/2;left=Math.max(8,Math.min(left,window.innerWidth-t.width-8));let top=r.bottom+8;if(top+t.height>window.innerHeight-8)top=r.top-t.height-8;tip.style.left=left+'px';tip.style.top=top+'px'}
function hideTip(){const tip=$('tooltip');if(tip)tip.hidden=true;tooltipTarget=null}
document.addEventListener('mouseover',e=>{const el=e.target.closest('[data-tip]');if(el)showTip(el)});
document.addEventListener('mouseout',e=>{const el=e.target.closest('[data-tip]');if(el&&!el.contains(e.relatedTarget))hideTip()});
document.addEventListener('focusin',e=>{const el=e.target.closest('[data-tip]');if(el)showTip(el)});
document.addEventListener('focusout',e=>{if(e.target.closest('[data-tip]'))hideTip()});
document.addEventListener('scroll',()=>{if(tooltipTarget)hideTip()},true);
function refresh(first=false){if(refresh.pending)return refresh.pending;refresh.pending=refreshState(first).finally(()=>{refresh.pending=null});return refresh.pending}
function backgroundActive(){return !!state?.jobs?.some(j=>['queued','running'].includes(j.status))||chat.busy||chat.sessions.some(sessionBusy)}
async function refreshState(first=false,signal){try{const next=await api('state');if(signal?.aborted)return false;$('connection').textContent='本地已连接';const signature=JSON.stringify(next);state=next;scheduledReports.render();activity?.render();renderWordExports();if($('release-dialog')?.open)delivery.refreshReleaseState().catch(e=>notice(e.message,true));const initialize=first&&!refresh.initialized;if(initialize||signature!==refresh.signature){render(initialize);refresh.signature=signature;if(initialize)refresh.initialized=true}await refreshProgress();if(first||!$('learning').hidden)await refreshCandidates();if(first||!$('report').hidden)await refreshReportBudget();return !signal?.aborted}catch(e){if(signal?.aborted)return false;$('connection').textContent='连接中断';if(first)throw e}}
// BEGIN_FIGURE_EDITOR_MAPPING: also exercised against the real MarkdownManager.
const figureImagePattern=/(!\[(?:\\.|[^\]\\])*\]\()\s*(<?[^)\s]+>?)(\s+(?:"(?:\\.|[^"])*"|'(?:\\.|[^'])*'))?\s*(\))/g;
function figureIdFromUrl(value){
 const src=value.replace(/^<|>$/g,'');
 const internal=/^briefloop-figure:([A-Za-z0-9_-]+)$/.exec(src);if(internal)return internal[1];
 try{const url=new URL(src,window.location.origin);if(url.origin===window.location.origin&&url.pathname==='/api/figure'){const id=url.searchParams.get('id');return /^[A-Za-z0-9_-]+$/.test(id||'')?id:null}}catch{}
 return null;
}
function mapFigureImages(markdown,toApi,version){
 return markdown.replace(figureImagePattern,(whole,prefix,src,title='',suffix)=>{const id=figureIdFromUrl(src);if(!id)return whole;const target=toApi?'/api/figure?id='+encodeURIComponent(id)+'&version='+encodeURIComponent(version||''):'briefloop-figure:'+id;return prefix+target+title+suffix});
}
const toEditor=(md,version=current?.id)=>mapFigureImages(md.replace(/\\?\[@(src\\?_[a-zA-Z0-9]+)\\?\]/g,(_,raw)=>{const id=raw.replaceAll('\\','');return `[${(state.sources.findIndex(s=>s.id===id)+1)||'?'}](#source-${id})`}),true,version);
const fromEditor=md=>mapFigureImages(md.replace(/\[([^\]]+)\]\(#source-(src_[a-zA-Z0-9]+)\)/g,(_,label,id)=>`${/^(?:\d+|\?)$/.test(label)?'':label}[@${id}]`),false);
// END_FIGURE_EDITOR_MAPPING
function updateDownloads(brief){
 const query='version='+encodeURIComponent(brief.id);$('download').href='/api/download?'+query;$('download-docx').href='/api/download?format=docx&'+query;
 const picker=$('export-template');
 if(picker){
  const chosen=picker.value;
  const ready=(state.templates||[]).filter(t=>t.status==='ready');
  picker.innerHTML='<option value="">跟随报告设置</option>'+ready.map(t=>`<option value="${esc(t.id)}">${esc(t.name)}（换版式）</option>`).join('');
  picker.value=[...picker.options].some(o=>o.value===chosen)?chosen:'';
 }
 const bundle=$('download-bundle');if(bundle){bundle.href='/api/download?format=bundle&'+query;bundle.hidden=!/briefloop-figure:[A-Za-z0-9_-]+/.test(brief.markdown)}
}

const statuses={queued:'等待运行',running:'正在运行',complete:'已完成',failed:'未完成',interrupted:'已中断',cancelled:'已停止'};
const DISCUSS_INSTRUCTION='（讨论模式：现在不要生成报告。）请根据我已经提供的信息整理写作简报：给谁看、解决什么问题、必答内容、期间、篇幅和写作偏好。只问会改变结果的缺口，不重复确认已知信息。按需求准备约定在最后提供 briefloop-requirements JSON，供我应用到材料与需求。';
const CHAT_COMMANDS=[
 {name:'discuss',desc:'讨论需求：先确认目的、读者、范围，不直接生成'},
 {name:'new',desc:'开始一个新对话'},
 {name:'help',desc:'查看可用命令'},
];
const COMMAND_HELP='可用命令：'+CHAT_COMMANDS.map(c=>'/'+c.name+' — '+c.desc).join('；');
let commandIndex=0;
function commandPanel(){return $('chat-commands')}
function renderCommands(){
 const panel=commandPanel();if(!panel)return;
 const input=$('chat-input'),match=/^\/(\w*)$/.exec(input.value);
 if(!match){panel.hidden=true;panel.innerHTML='';panel._items=[];commandIndex=0;return}
 const query=match[1].toLowerCase(),items=CHAT_COMMANDS.filter(c=>c.name.startsWith(query));
 if(!items.length){panel.hidden=true;panel.innerHTML='';panel._items=[];commandIndex=0;return}
 if(commandIndex>=items.length)commandIndex=0;
 panel._items=items;
 panel.innerHTML=items.map((c,i)=>`<button type="button" class="chat-command${i===commandIndex?' active':''}" data-command-index="${i}"><span class="chat-command-name">/${esc(c.name)}</span><span class="chat-command-desc">${esc(c.desc)}</span></button>`).join('');
 panel.hidden=false;
 panel.querySelectorAll('[data-command-index]').forEach(button=>button.onmousedown=event=>{event.preventDefault();commandIndex=Number(button.dataset.commandIndex);acceptCommand()});
}
function acceptCommand(){
 const panel=commandPanel();if(!panel)return;
 const item=(panel._items||[])[commandIndex];if(!item)return;
 const input=$('chat-input');input.value='/'+item.name+' ';
 panel.hidden=true;panel.innerHTML='';panel._items=[];commandIndex=0;
 rememberDraft();updateComposer();input.focus();input.setSelectionRange(input.value.length,input.value.length);
}
// Only editorial preferences lack a form control. Runtime/template snapshots are
// deliberately rebuilt by the server and must never be copied from a prior run.
let writingPreferencesOverride;
function preserveWritingPreferences(req,previous,override){
 if(!Object.prototype.hasOwnProperty.call(req,'writing_preferences'))req.writing_preferences=[...(Array.isArray(override)?override:Array.isArray(previous?.writing_preferences)?previous.writing_preferences:[])];
 return req;
}
function applyRequirements(text){
 let data;try{data=JSON.parse(text)}catch(e){notice('要求清单无法解析：'+e.message,true);return}
 // Accept the observed title-shaped manual section response, never stringify objects.
 const list=(key,manual=false)=>{
  if(data[key]===undefined)return;
  if(!Array.isArray(data[key]))throw Error(key+' 应为文字清单');
  data[key]=data[key].map(item=>{
   const text=typeof item==='string'?item:manual&&item&&typeof item.title==='string'?item.title:null;
   if(text===null)throw Error(key+' 中存在无法识别的项目');
   return text;
  });
 };
 try{list('key_questions');list('manual_sections',true);list('writing_preferences')}catch(e){notice('要求清单无法应用：'+e.message,true);return}
 const form=$('requirements');if(!form)return;
 const set=(name,value)=>{const el=form.elements[name];if(el&&value!=null){el.value=value;el.dispatchEvent(new Event('change',{bubbles:true}))}};
 set('title',data.title);set('objective',data.objective);set('audience',data.audience);set('period',data.period);
 for(const key of ['period_start','period_end','report_timezone'])set(key,data[key]);
 if(Array.isArray(data.key_questions))set('key_questions_text',data.key_questions.join('\n'));
 if(Array.isArray(data.manual_sections))set('manual_sections_text',data.manual_sections.join('\n'));
 if(data.report_profile)set('report_profile',data.report_profile);
 if(data.research_tier)set('research_tier',data.research_tier);
 initializeWorkflowChoice(data,true);syncWorkflowProfile(false);
 if(data.writing_mode)set('writing_mode',data.writing_mode);
 if(data.target_words)set('target_words',data.target_words);
 if(data.max_words)set('max_words',data.max_words);
 if(Array.isArray(data.writing_preferences))writingPreferencesOverride=[...data.writing_preferences];
 page('setup');notice('已填入材料与需求，请检查后生成');
}
// The backend names every task kind (task_labels.py) and says which ones a
// user sees as their own; the page renders that map and keeps no copy.
const taskLabel=kind=>state?.task_labels?.[kind];
// Generic message actions: every entry renders as a small icon button under the message.
const MESSAGE_ACTIONS=[
 {id:'copy',label:'复制回复',run:copyMessage,icon:'<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"><rect x="9" y="9" width="11" height="11" rx="2"/><path d="M5 15V6a2 2 0 0 1 2-2h9"/></svg>'},
];
async function copyMessage(message){try{await copyText(message.text||'')}catch(e){notice('复制失败：'+e.message,true);return}notice('已复制消息文本')}
function messageActionsHTML(){return '<div class="message-actions-row">'+MESSAGE_ACTIONS.map(a=>`<button type="button" class="message-action" data-action="${a.id}" data-tip="${esc(a.label)}" aria-label="${esc(a.label)}">${a.icon}</button>`).join('')+'</div>'}
function bindMessageActions(node,message){node.querySelectorAll('.message-action').forEach(b=>{const action=MESSAGE_ACTIONS.find(a=>a.id===b.dataset.action);if(action)b.onclick=()=>Promise.resolve(action.run(message)).catch(e=>notice(e.message,true))})}
function taskFor(id){return state.jobs.find(j=>j.id===id)}
function openTask(job){
 if(!job)return;
 const payload=parse(job.payload);
 const brief=state.briefs.find(b=>b.id===payload.version_id)||state.briefs.find(b=>b.run_id===payload.run_id);
 if(brief){if(!openBrief(brief,{follow:true}))return;pendingRun=null}
 else if(payload.run_id){if(!showPendingReport(payload.run_id))return;}
 page('report');refreshProgress();
}
const taskSnapshots=new Map(),taskExpanded=new Set(),taskMaterials=new Set(),taskActions=new Map();
let taskGraphRequest=null;
function visibleReportTasks(){
 const all=(state.jobs||[]).filter(j=>taskLabel(j.kind)).sort((a,b)=>new Date(b.created)-new Date(a.created));
 const activeParents=new Set(all.filter(j=>['queued','running'].includes(j.status)).map(j=>j.id));
 return (renderTasks.showAll?all:all.filter(j=>['queued','running','failed','interrupted','cancelled'].includes(j.status)&&!activeParents.has(parse(j.payload).parent_job_id))).slice(0,15);
}
function taskCardHTML(j){const cached=taskSnapshots.get(j.id);return taskProgressCard(j,cached?.data,{label:bannerTitle(j)||taskLabel(j.kind),kindLabel:taskLabel(j.kind),expanded:taskExpanded.has(j.id),materials:taskMaterials.has(j.id),error:cached?.error,busy:taskActions.get(j.id)})}
function bindTaskCards(){
 const box=$('report-task-cards');
 const mutate=async(id,route,label)=>{if(taskActions.has(id))return;taskActions.set(id,label);renderTasks();try{await action(()=>api(route,{job_id:id}))}finally{taskActions.delete(id);renderTasks()}};
 box.querySelectorAll('[data-task-detail]').forEach(d=>d.ontoggle=()=>{if(d.open)taskExpanded.add(d.dataset.taskDetail);else taskExpanded.delete(d.dataset.taskDetail)});
 box.querySelectorAll('[data-task-materials]').forEach(d=>d.ontoggle=()=>{if(d.open)taskMaterials.add(d.dataset.taskMaterials);else taskMaterials.delete(d.dataset.taskMaterials)});
 box.querySelectorAll('[data-task-open]').forEach(b=>b.onclick=()=>openTask(taskFor(b.dataset.taskOpen)));
 box.querySelectorAll('[data-task-stop]').forEach(b=>b.onclick=()=>mutate(b.dataset.taskStop,'stop','正在停止任务…'));
 box.querySelectorAll('[data-task-resume]').forEach(b=>b.onclick=()=>mutate(b.dataset.taskResume,'resume','正在恢复任务…'));
 box.querySelectorAll('[data-task-dismiss]').forEach(b=>b.onclick=()=>mutate(b.dataset.taskDismiss,'task-dismiss','正在清除任务…'));
 box.querySelectorAll('[data-progress-version]').forEach(b=>b.onclick=()=>{const brief=state.briefs.find(x=>x.id===b.dataset.progressVersion);if(brief&&openBrief(brief,{follow:false})){pendingRun=null;page('report')}});
 box.querySelectorAll('[data-progress-source]').forEach(b=>b.onclick=()=>action(async()=>showSource(await api('source?id='+encodeURIComponent(b.dataset.progressSource)))));
}
async function renderTaskGraph(){
 if($('reports').hidden)return;
 const jobs=visibleReportTasks();
 const key=jobs.map(j=>j.id+':'+j.status).join('|');
 if(taskGraphRequest?.key===key)return;
 const request={key};taskGraphRequest=request;
 const isCurrent=()=>taskGraphRequest===request&&visibleReportTasks().map(j=>j.id+':'+j.status).join('|')===key;
 try{
  const replies=await Promise.all(jobs.map(async j=>{try{const data=await api('task-progress?job='+encodeURIComponent(j.id));if(data.session_id&&['queued','running'].includes(j.status)){try{const session=await api('harness/session?id='+encodeURIComponent(data.session_id)+'&requests_only=1');data.needs_attention=(session.requests||[]).some(r=>r.status==='pending')}catch{data.attention_unknown=true}}return {id:j.id,data}}catch{return {id:j.id,error:true}}}));
  if(!isCurrent())return;
  for(const r of replies)taskSnapshots.set(r.id,r.error?{...taskSnapshots.get(r.id),error:true}:r);
  renderTasks();
 }finally{if(taskGraphRequest===request)taskGraphRequest=null}
}
function renderTasks(){
 const box=$('report-task-cards');if(!box)return;
 const tasks=visibleReportTasks(),panel=$('report-tasks');if(panel)panel.hidden=!(state.jobs||[]).some(j=>taskLabel(j.kind));
 $('report-tasks-count').textContent=tasks.length?`${tasks.length} 个任务`:'暂无待完成任务';
 const allBtn=$('report-tasks-all');allBtn.hidden=false;allBtn.textContent=renderTasks.showAll?'只看待完成':'查看最近任务';
 // Preserve disclosure choices across polling.
 const html=tasks.map(taskCardHTML).join('');
 if(box._rendered!==html){
  // Polling must not collapse nested disclosures or steal keyboard focus.
  const nodeKey=node=>{
   const owner=node.closest('[data-progress-card]')?.dataset.progressCard;
   const target=node.tagName==='SUMMARY'?node.parentElement:node;
   return owner+':'+JSON.stringify(target.dataset);
  };
  const focused=box.contains(document.activeElement)?nodeKey(document.activeElement):null;
  const disclosures=new Map([...box.querySelectorAll('details')].map(d=>[nodeKey(d),d.open]));
  box.innerHTML=html;box._rendered=html;
  box.querySelectorAll('details').forEach(d=>{if(disclosures.has(nodeKey(d)))d.open=disclosures.get(nodeKey(d))});
  if(focused)[...box.querySelectorAll('button,summary')].find(n=>nodeKey(n)===focused)?.focus({preventScroll:true});
  bindTaskCards();
 }
 const oldGraph=$('report-task-graph');if(oldGraph){oldGraph.hidden=true;oldGraph.innerHTML=''}
}
const BANNER_RESULT_KINDS=['generate','revise','assess','review'];
const BANNER_RESULT_WINDOW=6*60*60*1000;
function bannerDismissKey(){try{return localStorage.getItem('briefloop-task-banner')||''}catch{return ''}}
function setBannerDismissKey(key){try{localStorage.setItem('briefloop-task-banner',key)}catch{}}
function bannerBrief(job){const p=parse(job.payload)||{};return (state.briefs||[]).find(b=>b.id===p.version_id)||(state.briefs||[]).find(b=>b.run_id===p.run_id)||null}
function bannerTitle(job){const brief=bannerBrief(job);if(brief){const t=parse(brief.detail).title;if(t)return t}const run=(state.runs||[]).find(r=>r.id===(parse(job.payload)||{}).run_id);if(run){const req=parse(run.requirements);if(req.title)return req.title}return ''}
function renderTaskBanner(){
 const box=$('task-banner');if(!box)return;if(!state){box.hidden=true;box.innerHTML='';return}
 const now=Date.now(),dismissed=bannerDismissKey();
 const running=(state.jobs||[]).filter(j=>taskLabel(j.kind)&&['queued','running'].includes(j.status));
 const finished=(state.jobs||[]).filter(j=>BANNER_RESULT_KINDS.includes(j.kind)&&['complete','failed','interrupted','cancelled'].includes(j.status)&&now-new Date(j.updated||j.created).getTime()<BANNER_RESULT_WINDOW);
 const candidates=[...running,...finished].filter(j=>j.id+':'+j.status!==dismissed).sort((a,b)=>new Date(b.updated||b.created)-new Date(a.updated||a.created));
 const job=candidates[0];
 if(!job){box.hidden=true;box.innerHTML='';return}
 const title=bannerTitle(job),label=taskLabel(job.kind);
 box.hidden=false;
 if(['queued','running'].includes(job.status)){
  box.className='task-banner running';
  box.innerHTML=`<span class="task-banner-icon" aria-hidden="true"></span><div class="task-banner-main"><strong>正在${esc(label)}${title?'：'+esc(title):''}</strong><small>${esc(statuses[job.status]||job.status)}${job.progress?` · 第 ${job.progress.round}/${job.progress.k} 轮`:''} · 完成后会在这里提示</small></div><button type="button" class="outline" data-banner-open>查看任务</button><button type="button" class="task-banner-close" data-banner-close aria-label="暂时隐藏">✕</button>`;
  box.querySelector('[data-banner-open]').onclick=()=>page('reports');
 }else if(job.status==='complete'){
  box.className='task-banner ok';
  const verb=job.kind==='assess'?'评分已完成':job.kind==='review'?'独立审阅已完成':'新报告已生成';
  box.innerHTML=`<span class="task-banner-icon" aria-hidden="true">✓</span><div class="task-banner-main"><strong>${verb}${title?'：'+esc(title):''}</strong><small>已保存，可直接打开查看</small></div><button type="button" class="primary" data-banner-open>查看报告</button><button type="button" class="task-banner-close" data-banner-close aria-label="关闭">✕</button>`;
  box.querySelector('[data-banner-open]').onclick=()=>{setBannerDismissKey(job.id+':'+job.status);const brief=bannerBrief(job);if(brief&&openBrief(brief,{follow:false}))page('report');else page('reports')};
 }else{
  box.className='task-banner error';
  box.innerHTML=`<span class="task-banner-icon" aria-hidden="true">!</span><div class="task-banner-main"><strong>${esc(label)}未完成${title?'：'+esc(title):''}</strong><small>${esc(job.error||statuses[job.status]||job.status)}</small></div><button type="button" class="outline" data-banner-open>查看详情</button><button type="button" class="outline" data-banner-retry>重试</button><button type="button" class="task-banner-close" data-banner-close aria-label="关闭">✕</button>`;
  box.querySelector('[data-banner-open]').onclick=()=>page('reports');
  box.querySelector('[data-banner-retry]').onclick=()=>action(()=>api('resume',{job_id:job.id}),'已按页面显示的模型重新提交');
 }
 box.querySelector('[data-banner-close]').onclick=()=>{setBannerDismissKey(job.id+':'+job.status);box.hidden=true;box.innerHTML=''};
}
let welcomeMode=null,welcomeBackend,welcomeExpanded=false,welcomeBusy=false,welcomeScanning=false,welcomeFromProvider=false;
async function chooseWelcomeAgent(id){
 if(welcomeBusy||!runtimeCatalog.some(r=>r.id===id&&r.available))return;
 if(id===state.settings.agent_backend){renderWelcome();return}
 welcomeBusy=true;renderWelcome();
 try{$('agent-backend').value=id;await $('agent-backend').onchange()}
 finally{$('agent-backend').value=state.settings.agent_backend;welcomeBusy=false;renderWelcome()}
}
function setWelcomeMode(mode){
 if(welcomeBusy)return;
 welcomeMode=mode;
 if(mode==='native')chooseWelcomeAgent('briefloop-native');
 renderWelcome();
}
function applyPendingSetupFields(){let fields=null;try{fields=JSON.parse(sessionStorage.getItem('briefloop-welcome-fields')||'null')}catch{}if(!fields)return;sessionStorage.removeItem('briefloop-welcome-fields');const form=$('requirements');if(!form)return;for(const [name,value] of Object.entries(fields)){const el=form.elements[name];if(!el)continue;if(el.type==='checkbox')el.checked=!!value;else el.value=value;el.dispatchEvent(new Event('change',{bubbles:true}))}initializeWorkflowChoice(fields,true);syncWorkflowProfile(false)}
function renderWelcome(){
 const box=$('welcome-runtimes');if(!box)return;
 const settings=state?.settings||{},chosen=settings.agent_backend;
 if(welcomeMode===null||welcomeBackend!==chosen){welcomeMode=chosen==='briefloop-native'?'native':'cli';welcomeBackend=chosen}
 for(const mode of ['native','cli']){
  const tab=$('welcome-'+mode+'-tab');tab.setAttribute('aria-selected',String(welcomeMode===mode));tab.tabIndex=welcomeMode===mode?0:-1;tab.disabled=welcomeBusy;
  $('welcome-'+mode).hidden=welcomeMode!==mode;
 }
 const {visible,total}=welcomeAgents(runtimeCatalog,chosen,welcomeExpanded);
 const html=visible.length?visible.map(r=>welcomeAgentCard(r,chosen,welcomeBusy)).join(''):`<p class="help">${runtimeScanned?'未检测到可用的 Agent CLI。安装并登录后重新扫描，或选择 BriefLoop Agent。':'正在检测本机 Agent…'}</p>`;
 if(box.dataset.cards!==html){box.dataset.cards=html;box.innerHTML=html;box.querySelectorAll('[data-welcome-agent]').forEach(b=>b.onclick=()=>chooseWelcomeAgent(b.dataset.welcomeAgent))}
 $('welcome-more').hidden=total<=6;$('welcome-more').textContent=welcomeExpanded?'收起更多 Agent⌃':'显示更多 Agent⌄';$('welcome-more').setAttribute('aria-expanded',String(welcomeExpanded));
 $('welcome-refresh').disabled=welcomeScanning||welcomeBusy;$('welcome-refresh').textContent=welcomeScanning?'正在扫描…':'↻ 重新扫描';
 const selected=(welcomeMode==='native')===(chosen==='briefloop-native')&&runtimeCatalog.some(r=>r.id===chosen&&r.available);
 const model=selected&&!settings.model_selection_required?settings.model:'';
 $('welcome-model-label').textContent='模型'+(selected?' · '+runtimeName(chosen):'');
 $('welcome-model-name').textContent=model||'选择或搜索模型';$('welcome-model').disabled=!selected||welcomeBusy;
 $('welcome-start').disabled=!welcomeReady(settings,runtimeCatalog,welcomeMode,welcomeBusy);
 $('welcome-choice').textContent=welcomeBusy?'正在保存选择…':!selected?'请先选择一个 Agent。':!model?'请选择模型；连接状态可在设置中测试。':'已保存选择；连接状态可在设置中测试。';
 if(!model){
  const effort=$('welcome-effort');effort.hidden=false;effort.disabled=true;effort.replaceChildren(new Option('模型默认','none'));
  $('welcome-variant').hidden=true;const choices=$('welcome-variant-choices');if(choices)choices.hidden=true;
  effort.parentElement.querySelectorAll('[data-reasoning-note]').forEach(note=>note.hidden=true);
  delete effort.dataset.reasoningSignature;if(choices)delete choices.dataset.reasoningSignature;
  return;
 }
 const variant=['opencode','briefloop-native','mimo'].includes(chosen),control=$(variant?'welcome-variant':'welcome-effort');
 $('welcome-effort').hidden=variant;if(!variant)$('welcome-variant').hidden=true;
 const variantSelect=$('welcome-variant-choices');if(variantSelect)variantSelect.hidden=!variant;
 control.disabled=!model||welcomeBusy;
 if(variant)control.value=chosen==='mimo'?(settings.runtime_efforts?.mimo||''):(settings.model_variant||'');
 else assignEffort('welcome-effort',settingsEffort(settings,chosen));
 reasoning.configure(control,chosen,model||'',{variant});
}
$('welcome-demo').onclick=()=>action(async()=>{const result=await api('demo',{});await refresh();$('welcome').hidden=true;openBrief(state.briefs.find(b=>b.id===result.version_id));page('report');notice(result.notice)});
$('welcome-model').onclick=()=>openModelPicker('model-select');
$('welcome-native-tab').onclick=()=>setWelcomeMode('native');
$('welcome-cli-tab').onclick=()=>setWelcomeMode('cli');
for(const mode of ['native','cli'])$('welcome-'+mode+'-tab').onkeydown=event=>{
 if(!['ArrowLeft','ArrowRight','Home','End'].includes(event.key)||welcomeBusy)return;
 event.preventDefault();const next=event.key==='Home'?'native':event.key==='End'?'cli':mode==='native'?'cli':'native';
 setWelcomeMode(next);$('welcome-'+next+'-tab').focus();
};
$('welcome-more').onclick=()=>{welcomeExpanded=!welcomeExpanded;renderWelcome()};
$('welcome-refresh').onclick=async()=>{welcomeScanning=true;renderWelcome();try{await refreshRuntimeDiscovery(true)}finally{welcomeScanning=false;renderWelcome()}};
$('welcome-configure').onclick=()=>{welcomeFromProvider=true;showSettings();settingsView('models');$('settings-tab-api').click();$('provider-close').textContent='返回选择';$('provider-close').setAttribute('aria-label','返回选择')};
for(const id of ['welcome-effort','welcome-variant'])$(id).onchange=async()=>{
 if(welcomeBusy)return;
 const target=id==='welcome-variant'?'model-variant':'effort-select';
 if(target==='effort-select')assignEffort(target,$(id).value);else $(target).value=$(id).value;
 welcomeBusy=true;renderWelcome();
 try{await action(saveModel,'思考强度已保存')}finally{welcomeBusy=false;renderWelcome()}
};
$('welcome-start').onclick=()=>{
 const settings=state?.settings||{};
 if(!welcomeReady(settings,runtimeCatalog,welcomeMode,welcomeBusy)){notice('请先选择 Agent 和模型',true);return}
 const backend=settings.agent_backend||'codex';
 if(chat.nextBackend!==backend)chat.hostOptions={};
 chat.nextBackend=backend;
 $('chat-model').value=settings.model;$('chat-model-provider').value=settings.model_provider||'';
 assignEffort('chat-effort',settingsEffort(settings,backend));
 $('chat-variant').value=backend==='mimo'?(settings.runtime_efforts?.mimo||''):(settings.model_variant||'');$('chat-service-tier').value=settings.service_tier||'';
 $('welcome').hidden=true;page('chat');updateComposer();refreshInlineModelPickers();$('chat-input').focus();rememberDraft();
};
function render(first){
 renderTemplates(first);
 renderWorkflowChoices(first);
 if($('review-open'))$('review-open').textContent='审阅与需处理'+(state.conflicts?.length?' · '+state.conflicts.length+' 项来源分歧':'');
 if(first&&$('report-system-clock')&&state.system_clock)$('report-system-clock').textContent=`本机日期：${state.system_clock.today} · ${state.system_clock.timezone}；提交时再次由后台核对。`;
 if(first){$('settings-chat-web-default').checked=state.settings.chat_allow_web!==false;$('chat-allow-web').checked=state.settings.chat_allow_web!==false;$('timeout-minutes').value=state.settings.timeout_minutes;$('hard-timeout-minutes').value=state.settings.hard_timeout_minutes||0;$('company-mode').value=state.settings.company_context_enabled==null?'ask':state.settings.company_context_enabled?'on':'off';$('auto-revision').checked=state.settings.auto_revision!==false;state.sources.forEach(s=>selected.add(s.id));$('rounds').value=state.settings.k;$('auto-learn').checked=state.learning_authorization?.state==='authorized';$('model-select').value=state.settings.model_selection_required?'':state.settings.model||'gpt-5.6-luna';assignEffort('effort-select',settingsEffort(state.settings,state.settings.agent_backend||'codex'));$('model-provider').value=state.settings.model_provider||'';$('service-tier').value=state.settings.service_tier||'';$('agent-backend').value=state.settings.agent_backend||'codex';$('model-variant').value=state.settings.agent_backend==='mimo'?(state.settings.runtime_efforts?.mimo||''):(state.settings.model_variant||'');updateModelLabel();renderRoleModels();renderReviewRuntime();renderBackend();loadSearchPolicy();renderSearchProvider();refreshRuntimeDiscovery();office.syncSettingsToggle();if(state.requirements)for(const [k,v] of Object.entries(state.requirements)){const e=$('requirements').elements[k];if(e)e.type==='checkbox'?e.checked=v:e.value=v}$('requirements').elements.key_questions_text.value=(state.requirements?.key_questions||[]).join('\n');$('requirements').elements.manual_sections_text.value=(state.requirements?.manual_sections||[]).join('\n');initializeLengthInputs(state.requirements||{});initializeResearchBudget(state.requirements||{});initializeReportProfile(state.requirements||{});if(!state.requirements||state.requirements.fact_check==null)$('requirements').elements.fact_check.checked=!!state.settings.fact_checker;syncFactCheckControl();syncWorkflowProfile(false);previewReportTime()}
 $('source-count').textContent=state.sources.length+' 份';$('source-list').innerHTML=state.sources.map(s=>`<div class="source-item"><input type="checkbox" data-check="${s.id}" ${selected.has(s.id)?'checked':''} aria-label="选择 ${esc(s.name)}"><button data-source="${s.id}">${esc(s.name)}</button><span class="tag ${s.status==='failed'?'error':''}">${s.status==='failed'?'读取失败':s.needs_visual?'需视觉读取':'可读取'}</span>${s.status==='failed'?`<button data-retry-source="${s.id}">重试</button>`:''}</div>`).join('');
 document.querySelectorAll('[data-retry-source]').forEach(b=>b.onclick=()=>action(async()=>{const s=await api('retry-source',{source_id:b.dataset.retrySource});selected.delete(b.dataset.retrySource);selected.add(s.id);notice(s.status==='ready'?'来源已重新读取':s.error,s.status!=='ready')}));
 document.querySelectorAll('[data-check]').forEach(b=>b.onchange=()=>{if(b.checked){selected.add(b.dataset.check);referenceSelected.delete(b.dataset.check);renderReferenceSources()}else selected.delete(b.dataset.check)});renderReferenceSources();
 const visible=[];
 for(const runId of [...new Set(state.briefs.map(b=>b.run_id))]){
  const versions=state.briefs.filter(b=>b.run_id===runId),latest=versions[0],original=[...versions].reverse().find(b=>['agent','example'].includes(b.author));
  if(latest)visible.push({brief:latest,label:latest.author==='example'?'合成示例':latest.author==='user'?'当前编辑稿':latest.parent_id?'修订稿':'生成原稿'});
  if(original&&original.id!==latest?.id)visible.push({brief:original,label:original.author==='example'?'合成示例':'生成原稿'});
 }
 if(current&&!visible.some(v=>v.brief.id===current.id))visible.push({brief:current,label:'正在查看历史快照'});
 const pendingOptions=state.jobs.filter(j=>j.kind==='generate'&&['queued','running'].includes(j.status)&&!state.briefs.some(b=>b.run_id===parse(j.payload).run_id)).map(j=>{const rid=parse(j.payload).run_id,r=state.runs.find(r=>r.id===rid);return `<option value="run:${esc(rid)}">${esc(parse(r?.requirements).title||'新报告')} · ${j.status==='queued'?'排队中':'正在生成'}</option>`}).join('');
 $('version-select').innerHTML=pendingOptions+visible.map(({brief:b,label})=>`<option value="${b.id}">${esc(parse(b.detail).title||'简报')} · ${label}</option>`).join('');
 if($('version-history'))$('version-history').textContent='编辑历史'+(current?'（'+state.briefs.filter(b=>b.run_id===current.run_id&&b.author==='user').length+'）':'');

 tryOpenPending();if(!current&&!pendingRun&&!openBrief.request&&state.briefs.length)openBrief(state.briefs[0],{follow:true});if(current&&followUpdates&&!dirty&&!saving){const latest=state.briefs.find(b=>b.run_id===current.run_id);if(latest?.parent_id===current.id&&latest.author==='agent')openBrief(latest,{follow:true})}if(current){$('version-select').value=current.id;assessment();citations();renderBriefLength()}
 syncPendingReport();
 $('jobs').innerHTML=state.jobs.filter(j=>j.status!=='dismissed').map(j=>`<div class="job"><span class="tag ${j.status==='failed'?'error':''}">${statuses[j.status]}</span><div class="job-main">${esc(taskLabel(j.kind)||j.kind)}<small>${['export_docx','release','audit_bundle'].includes(j.kind)?'本地脚本':j.kind==='source_refresh'?'来源工具':parse(j.payload).runtime?esc(jobModelLabel(j)):'旧任务：沿用当时本机配置'} · ${j.progress?`第 ${j.progress.round}/${j.progress.k} 轮 · ${{maintainer:'整理经验',proposer:'提出候选',validation:'验证候选'}[j.progress.phase]||j.progress.phase} · `:''}${esc(j.error||(j.kind==='source_refresh'?sourceRefreshOutcome(parse(j.result).outcome):'')||moment(j.created))}</small></div>${j.kind==='learn'?`<button data-details="${j.id}">查看比较</button>`:''}${['queued','running'].includes(j.status)?`<button data-stop="${j.id}">停止</button>`:''}${['failed','interrupted','cancelled'].includes(j.status)?`<button data-resume="${j.id}">沿用原模型恢复</button>${['review','learn'].includes(j.kind)?`<button data-retry-current="${j.id}">按当前模型重试</button>`:''}`:''}</div>`).join('');
 document.querySelectorAll('[data-stop]').forEach(b=>b.onclick=()=>action(()=>api('stop',{job_id:b.dataset.stop})));document.querySelectorAll('[data-resume]').forEach(b=>b.onclick=()=>action(()=>api('resume',{job_id:b.dataset.resume})));document.querySelectorAll('[data-retry-current]').forEach(b=>b.onclick=()=>action(()=>api('resume',{job_id:b.dataset.retryCurrent,use_current_model:true})));renderTasks();renderTaskGraph();renderTaskBanner();renderAssistantSummary();renderReportStatus();renderReports();renderSourcesPage();templatesPage.render();if($('welcome')&&!$('welcome').hidden)renderWelcome();
 document.querySelectorAll('[data-details]').forEach(b=>b.onclick=()=>action(async()=>{const d=await api('learning-details?job='+b.dataset.details);$('source-title').textContent='技能比较与依据';$('source-original').hidden=true;$('source-provenance').hidden=true;$('source-link').textContent='';$('source-body').textContent=d.rounds.length?d.rounds.map((r,i)=>`第 ${i+1} 轮\n${r.result?.reason||'比较尚未完成'}\n${(r.result?.pairs||[]).map(p=>({better:'候选更好',tie:'差不多，保留原技能',worse:'原稿更好'}[p.verdict])+': '+p.reason).join('\n')}\n\n`+r.cases.map(c=>`任务：${c.requirements.title}\n\n旧版\n${gradeSummary(c.baseline.assessment)}\n${c.baseline.reader_markdown||c.baseline.markdown}\n\n候选\n${gradeSummary(c.candidate.assessment)}\n${c.candidate.reader_markdown||c.candidate.markdown}`).join('\n\n')).join('\n\n'):d.job.error||'比较尚未开始；先整理 Wiki 和提出候选。';$('source-dialog').showModal()}));
 $('skills').innerHTML=`<div class="skill">${state.active_skill?'当前启用 '+esc(state.active_skill):'当前使用基础任务提示词'}${state.active_skill?'<button data-rollback="">回到基础版本</button>':''}</div>`+state.skills.map(s=>`<div class="skill"><strong>${esc(s.id)}</strong><p>${esc(s.reason)}</p>${s.id===state.active_skill?'<span class="tag">正在使用</span>':`<button data-rollback="${s.id}" class="outline">使用这个版本</button>`}</div>`).join('');document.querySelectorAll('[data-rollback]').forEach(b=>b.onclick=()=>action(()=>api('rollback',{skill_id:b.dataset.rollback||null}),'下一轮将使用所选技能'));
 if(state.wiki!==render.wiki){render.wiki=state.wiki;if(state.wiki)api('render',{markdown:state.wiki}).then(r=>$('wiki').innerHTML=r.html);else $('wiki').innerHTML='<h2>还没有学习经验</h2><p class="muted">生成简报后直接改稿，或留下评论。Maintainer 会在这里整理观察、方法与适用条件。</p>'}bindSources();
}
function showPendingReport(runId){
 if(dirty||saving){notice('请先保存当前修改，再切换报告',true);return false}
 openBrief.request=null;pendingRun=runId;current=null;followUpdates=true;
 if(editor){editor.destroy();editor=null}
 syncPendingReport();return true;
}
function syncPendingReport(){
 const waiting=!!pendingRun&&!current;
 let box=$('pending-report');if(!box){box=document.createElement('div');box.id='pending-report';box.className='empty';$('document-area').before(box)}
 box.hidden=!waiting;$('document-area').hidden=!current;$('empty').hidden=!!current||waiting||state.jobs.length>0;
 for(const id of ['download-word','export-menu-toggle','more-menu-toggle','version-history','version-diff'])if($(id))$(id).hidden=waiting;
 if(!waiting)return;
 const run=state.runs.find(r=>r.id===pendingRun),job=state.jobs.find(j=>j.kind==='generate'&&parse(j.payload).run_id===pendingRun);
 $('report-title').textContent=parse(run?.requirements).title||'新报告';$('save-state').textContent='';$('version-select').value='run:'+pendingRun;
 for(const id of ['brief-length','report-status'])if($(id))$(id).textContent='';
 box.textContent=job?.status==='queued'?'报告已排队，等待生成':job&&['failed','cancelled','interrupted'].includes(job.status)?'报告生成已停止，尚未保存正文':'报告正在生成中';
}
function tryOpenPending(){
 if(!pendingRun||dirty||saving)return false;
 const incoming=state?.briefs.find(b=>b.run_id===pendingRun);
 // A body still loading keeps the waiting page; openBrief clears pendingRun once it opens.
 if(incoming&&openBrief(incoming,{follow:true})&&current?.id===incoming.id){pendingRun=null;return true}
 return false;
}
async function loadBrief(b){if(!b||'markdown' in b)return b;const bodies=openBrief.bodies||(openBrief.bodies=new Map()),cached=bodies.get(b.id);if(cached&&cached.hash===b.hash)return cached;const full=await api('brief?id='+encodeURIComponent(b.id));bodies.set(full.id,full);return full}
function openBrief(b,{follow=false}={}){if(!b)return false;if(dirty||saving){notice('请先保存当前修改，再切换版本',true);return false}if(!('markdown' in b)){const cached=openBrief.bodies?.get(b.id);if(cached?.hash!==b.hash){/* Polled state lists versions only. Choosing one records the request; it opens (and clears pending reports) only when its body arrives and is still the latest choice. */if(openBrief.request?.id===b.id&&openBrief.request.hash===b.hash)return true;const request=openBrief.request={id:b.id,hash:b.hash};request.promise=loadBrief(b).then(full=>{if(openBrief.request!==request)return false;openBrief.request=null;return openBrief(full,{follow})}).catch(e=>{if(openBrief.request===request){openBrief.request=null;notice(e.message,true)}return false});return true}b=cached}openBrief.request=null;(openBrief.bodies||(openBrief.bodies=new Map())).set(b.id,b);pendingRun=null;followUpdates=follow;current=b;syncPendingReport();renderWordExports();$('report-title').textContent=parse(b.detail).title||'简报';updateDownloads(b);if(editor)editor.destroy();highlightQuotes=[];editor=new Editor({element:$('editor'),editable:state.briefs.find(x=>x.run_id===b.run_id)?.id===b.id,extensions:[StarterKit.configure({link:{openOnClick:false},trailingNode:false}),ReportTrailingParagraph,TableKit,ReportImage.configure({HTMLAttributes:{class:'briefloop-figure'},allowBase64:false}),TextStyle,Layout,Citation,Markdown,MustFixHighlight],content:b.editor_document?editorDocument(parse(b.editor_document),b.id):toEditor(b.markdown),...(b.editor_document?{}:{contentType:'markdown'}),onUpdate:changed,onSelectionUpdate:updateFormattingTools});$('markdown-source').value=b.markdown;const historical=state.briefs.find(x=>x.run_id===b.run_id)?.id!==b.id;$('markdown-source').readOnly=historical;$('toolbar').querySelectorAll('button,input,select').forEach(x=>x.disabled=historical);$('save-state').textContent=historical?'历史记录（只读）':b.author==='example'?'合成示例已保存':b.author==='user'?'当前编辑稿已自动保存':'原稿已保存';$('version-select').value=b.id;assessment();citations();renderBriefLength();setReportView('edit');renderReportStatus();renderAssistantSummary();return true}
function changed(){followUpdates=false;dirty=true;assessmentPanel.bumpDeliveryTicket();const check=$('assessment').querySelector('.delivery-checks');if(check)updatePanel(check,'有未保存修改；保存后重新检查。');renderBriefLength();$('save-state').textContent='有未保存修改';clearTimeout(saveTimer);saveTimer=setTimeout(save,1400)}
$('version-select').onchange=e=>{const value=e.target.value;if(value.startsWith('run:'))showPendingReport(value.slice(4));else openBrief(state.briefs.find(b=>b.id===value));refreshProgress()};
let savePromise=null,lastSaveError=null;
function save(){
 if(savePromise)return savePromise;
 if(!dirty||!current)return Promise.resolve();
 lastSaveError=null;
 let text;
 try{text=markdownMode?$('markdown-source').value:JSON.stringify(savedDocument(editor.getJSON()))}
 catch(e){lastSaveError=e;$('save-state').textContent='未保存，请保留编辑';notice(e.message,true);return Promise.resolve()}
 saving=true;$('save-state').textContent='保存中…';
 const base=current.id;
 savePromise=(async()=>{try{
  current=await api('save',{base_version:base,markdown:markdownMode?text:'',editor_document:markdownMode?null:JSON.parse(text)});
  dirty=(markdownMode?$('markdown-source').value:JSON.stringify(savedDocument(editor.getJSON())))!==text;
  $('save-state').textContent=dirty?'有新的修改':'已保存';updateDownloads(current);await refresh();const outline=$('report-outline');if(outline&&!outline.hidden)setReportView('outline');
  if(dirty)saveTimer=setTimeout(save,1400);else scheduleLearning();
 }catch(e){lastSaveError=e;$('save-state').textContent='未保存，请保留编辑';notice(e.message,true)}
 finally{saving=false;savePromise=null;
  // Let awaiting comment/download actions capture the just-saved version
  // before switching the visible report in the next browser task.
  setTimeout(tryOpenPending,0);
 }})();
 return savePromise;
}
async function savedVersion(){
 clearTimeout(saveTimer);
 while(savePromise||dirty){
  await (savePromise||save());
  if(lastSaveError)throw lastSaveError;
  clearTimeout(saveTimer);
 }
 if(!current)throw Error('尚无稿件');
 return current.id;
}
const reportExport=reportExportUI({api,notice,refresh,savedVersion,toEditor,parse,getState:()=>state,getCurrent:()=>current,getEditor:()=>editor});
reportExport.init();
for(const id of ['download','download-docx','download-bundle']){
 const link=$(id);if(!link)continue;
 link.onclick=async e=>{e.preventDefault();try{const version=await savedVersion();if(id==='download-docx'){
   await reportExport.downloadWord();return}const format=id==='download-docx'?'docx':id==='download-bundle'?'bundle':null;window.location.assign('/api/download?version='+encodeURIComponent(version)+(format?'&format='+format:''))}catch(e){notice('下载未开始：'+e.message,true)}};
}


function scheduleLearning(){ /* Worker consumes the durable feedback after inactivity. */ }
document.addEventListener('click',event=>{if(event.target.closest('button')?.id!=='delete-report')return;action(async()=>{
 const version=await savedVersion();
 if(!confirm('删除这份报告及全部稿件版本？报告将从列表移除，来源文件和已下载文件保留；内部核查与学习引用记录保留。'))return;
 await api('reports/delete',{version_id:version});current=null;dirty=false;await refresh();page('reports');notice('报告已删除');
})});

function bindSources(){document.querySelectorAll('[data-source]').forEach(b=>b.onclick=()=>action(async()=>{const r=await api('source?id='+b.dataset.source);showSource(r)}))}
const assessmentPanel=createAssessmentPanel({
 api,action,notice,$,esc,parse,
 getState:()=>state,
 getCurrent:()=>current,
 getEditor:()=>editor,
 isDirty:()=>dirty,
 bindSources,
 applyHighlightState({quotes,findings,kinds}){
  highlightQuotes=quotes;
  highlightFindings=findings;
  highlightKinds=kinds;
  applyHighlights();
 },
 readerHighlights,
 toEditor:(md)=>toEditor(md),
});
const {assessment,citations,renderDeliveryChecks,renderReportIssues,renderFactChecks}=assessmentPanel;
$('close-source').onclick=()=>$('source-dialog').close();
$('requirements').addEventListener('reset',()=>{writingPreferencesOverride=[];syncFactCheckControl()});
// The server lists backends with a verified restricted Reviewer; the page keeps no list (#726).
function reviewBackends(){return state?.review_capability?.restricted_review}
function reviewRuntime(){return state?.settings?.review_runtime||null}
function syncFactCheckControl(){
 const form=$('requirements'),box=form.elements.fact_check,listed=reviewBackends(),backend=backendValue();
 // A Reviewer chosen apart from the main chain is a route to independent review too.
 const reviewer=!!reviewRuntime()||!listed||listed.some(b=>b.id===backend);
 box.disabled=!form.elements.allow_web.checked||!reviewer;if(box.disabled)box.checked=false;
 const note=$('review-capability-note');if(!note)return;
 note.hidden=reviewer;if(reviewer){note.textContent='';return}
 const label=$('agent-backend')?.selectedOptions?.[0]?.textContent||backend,alternatives=listed.map(b=>b.label).join('、')||'暂无已验证的执行后端';
 note.textContent=`${label} 尚未验证受限独立审阅，不能开启事实核查。`+(form.elements.writing_mode.value==='internal_report'?'企业内部报告仍可生成，评分为普通评分（不是独立审阅），正式交付需要先完成独立审阅。':'')+`需要时可在“独立审阅执行后端”单独选择审阅后端，或在“执行后端”改用 ${alternatives}。`;
}
function renderReviewRuntime(){
 const select=$('review-backend');if(!select)return;
 const choices=state?.review_capability?.review_choices||[],current=reviewRuntime();
 select.innerHTML='<option value="">跟随执行后端</option>'+choices.map(c=>`<option value="${esc(c.id)}">${esc(c.label)}${c.experimental?'（实验）':''}</option>`).join('');
 select.value=current?.backend||'';$('review-model').value=current?.model||'';$('review-variant').value=current?.model_variant||'';
 $('review-model').disabled=$('review-variant').disabled=!select.value;
 renderReviewRuntimeSummary();
}
function renderReviewRuntimeSummary(){
 const current=reviewRuntime(),choice=(state?.review_capability?.review_choices||[]).find(c=>c.id===current?.backend);
 $('review-runtime-summary').textContent=current?`${choice?.label||current.backend} · ${friendlyModel(current.model)}${current.model_variant?' / '+current.model_variant:''}`:'跟随执行后端';
}
async function saveReviewRuntime(){
 const backend=$('review-backend').value,model=$('review-model').value.trim(),status=$('review-runtime-status');
 $('review-model').disabled=$('review-variant').disabled=!backend;
 if(backend&&!model){status.textContent='输入审阅模型（provider/model）后保存。';return}
 const review_runtime=backend?{backend,model,model_variant:$('review-variant').value.trim()||null}:null;
 status.textContent='保存中…';
 try{const saved=await api('settings',{review_runtime});state.settings.review_runtime=saved.review_runtime??null;renderReviewRuntimeSummary();syncFactCheckControl();status.textContent='已保存，之后新建的任务使用这个审阅设置；已排队的任务不变。'}
 catch(e){status.textContent='未保存：'+e.message}
}
$('review-backend').addEventListener('change',()=>action(saveReviewRuntime));
$('review-model').addEventListener('change',()=>action(saveReviewRuntime));
$('review-variant').addEventListener('change',()=>action(saveReviewRuntime));
$('requirements').elements.allow_web.addEventListener('change',syncFactCheckControl);
$('requirements').elements.writing_mode.addEventListener('change',syncFactCheckControl);
$('agent-backend').addEventListener('change',syncFactCheckControl);
let reportTimePreviewTicket=0;
async function previewReportTime(){
 const ticket=++reportTimePreviewTicket;
 const form=$('requirements'),box=$('report-system-clock');
 try{const requirements={title:form.elements.title.value||'预览',objective:form.elements.objective.value||'预览'};for(const key of ['period','period_start','period_end','report_timezone'])requirements[key]=form.elements[key].value;const t=await api('report-time-preview',{requirements});if(ticket!==reportTimePreviewTicket)return;box.textContent=`系统日期：${t.today} · ${t.timezone}；报告范围：${t.start} 至 ${t.end_exclusive}（不含结束时刻）。提交时冻结。`;}catch(e){if(ticket===reportTimePreviewTicket)box.textContent=e.message}
}
for(const key of ['period','period_start','period_end','report_timezone'])$('requirements').elements[key].addEventListener('change',previewReportTime);
$('requirements').onsubmit=e=>{e.preventDefault();action(async()=>{const f=new FormData(e.target),req=Object.fromEntries(f.entries());if(req.writing_mode==='internal_report'&&state.settings.company_context_enabled==null){$('company-choice-dialog').showModal();return}req.allow_web=f.has('allow_web');req.fact_check=f.has('fact_check');req.target_words=Number(req.target_words);req.max_words=Number(req.max_words);req.research_budget=readResearchBudget();req.search_policy=readSearchPolicy();Object.assign(req,readWorkflowChoice());req.reference_source_ids=[...referenceSelected];req.template_id=req.template_id||null;req.sections=readTemplateSections();req.key_questions=(req.key_questions_text||'').split('\n').map(x=>x.trim()).filter(Boolean);delete req.key_questions_text;req.manual_sections=(req.manual_sections_text||'').split('\n').map(x=>x.trim()).filter(Boolean);for(const title of req.manual_sections){const found=req.sections.find(s=>s.title===title);if(found){found.mode='manual';found.placeholder='待填充'}}delete req.manual_sections_text;preserveWritingPreferences(req,state.requirements,writingPreferencesOverride);req.raw_input=req.objective;delete req.runtime_model;delete req.runtime_effort;await saveModel();if(current)await savedVersion();const connector_selection=reportMcpSelection.selection();const job=await api('generate',{...(connector_selection?{connector_selection}:{}),requirements:req,session_id:chat.id||undefined,source_ids:[...selected].filter(id=>!req.reference_source_ids.includes(id))});showPendingReport(parse(job.payload).run_id);page('report');notice('任务已排队，后台会生成简报')})};
$('upload').onchange=e=>action(async()=>{preflightSources(e.target.files,getUploadLimits());for(const f of e.target.files){const s=await uploadSource(f);selected.add(s.id)}e.target.value=''},'来源已保存');
$('add-url').onclick=()=>action(async()=>{const s=await api('source-url',{url:$('source-url').value});selected.add(s.id);$('source-url').value='';notice(s.status==='ready'?'网页已读取':'来源已保存，但读取失败：'+s.error,s.status!=='ready')});
$('rescore').onclick=()=>action(async()=>{await savedVersion();await api('assess',{version_id:current.id,session_id:chat.id||undefined})},'已提交评分');
$('comment-submit').onclick=()=>action(async()=>{const text=$('comment').value,required=$('comment-required').checked;const version=await savedVersion();await api('comment',{version_id:version,text,learning_intent:required?'explicit_requirement':'feedback'});if($('comment').value===text&&$('comment-required').checked===required){$('comment').value='';$('comment-required').checked=true;}scheduleLearning()},'反馈已保存');
// Saving feedback is free; starting a learning validation calls models (#727).
function learningPlanText(plan){
 return `每轮最多用 ${plan.cases} 份历史报告，每份基线和候选各试写一次（最多 ${plan.trial_generations_per_round} 次，可复用的基线不重写），另有整理经验、提出候选和一次成对比较；`+
  `最多 ${plan.rounds} 轮，含用户明确需求时最多 ${plan.rounds_with_explicit_requirement} 轮，试写合计不超过 ${plan.max_trial_generations} 次。试写使用固定来源，不联网检索。`+
  `执行后端：${plan.backend_label} · ${plan.model||'尚未选择模型'}${roleModelText(plan)}。`+
  `上限计的是试写次数，不含宿主内部子 agent 的回合或 token 数；费用以宿主或 API 账户实际计费为准，BriefLoop 无法估算金额。`;
}
function roleModelText(plan){
 const roles=Object.entries(plan.role_models||{}).filter(([,value])=>value&&value.model);
 return roles.length?('；角色模型 '+roles.map(([role,value])=>`${role}=${value.model}`).join('、')):'';
}
function confirmLearning(message){const plan=state?.learning_authorization?.plan;return !!plan&&confirm(message+'\n\n'+learningPlanText(plan))}
async function setAutoLearn(enabled){
 if(!enabled){await api('settings',{auto_learn:false});return false}
 if(!confirmLearning('开启后，改稿或评论会在后台自动启动学习验证并调用模型。'))return false;
 const plan=state.learning_authorization.plan;
 await api('settings',{auto_learn:true,confirm_learning_rounds:plan.rounds,confirm_plan:plan.fingerprint});return true;
}
$('learn-now').onclick=()=>action(async()=>{await savedVersion();clearTimeout(learnTimer);if(!confirmLearning('现在用已保存的反馈启动一次学习验证。'))return;const result=await api('learn',{confirm_plan:state.learning_authorization.plan.fingerprint});notice(result.message||'已提交反馈学习')});
async function setK(v){const k=Math.max(1,Math.min(20,Number(v)||1));$('rounds').value=k;await action(async()=>{await api('settings',{k});await refresh();if(state.learning_authorization?.state==='rounds_exceed')notice(learningAuthorizationNote());else notice('轮数已保存，下一批生效')})}
$('rounds').onchange=e=>setK(e.target.value);$('k-minus').onclick=()=>setK(Number($('rounds').value)-1);$('k-plus').onclick=()=>setK(Number($('rounds').value)+1);
$('toolbar').querySelectorAll('button').forEach(b=>b.onclick=()=>{if(!editor)return;const c=editor.chain().focus();({bold:()=>c.toggleBold().run(),italic:()=>c.toggleItalic().run(),heading:()=>c.toggleHeading({level:2}).run(),bullet:()=>c.toggleBulletList().run(),table:()=>c.insertTable({rows:3,cols:3,withHeaderRow:true}).run(),undo:()=>c.undo().run(),redo:()=>c.redo().run(),addRow:()=>c.addRowAfter().run(),deleteRow:()=>c.deleteRow().run(),addColumn:()=>c.addColumnAfter().run(),deleteColumn:()=>c.deleteColumn().run(),mergeCells:()=>c.mergeCells().run(),splitCell:()=>c.splitCell().run(),imageCaption:()=>{const a=editor.getAttributes('image');if(!a.src)return;const caption=window.prompt('图注',a.caption||'');if(caption!==null)c.updateAttributes('image',{caption}).run()},imageWidth:()=>{const a=editor.getAttributes('image');if(!a.src)return;const raw=window.prompt('图像宽度（像素）',String(a.width||480));if(raw===null)return;const width=Number(raw);if(Number.isInteger(width)&&width>0&&width<=10000)c.updateAttributes('image',{width,height:null}).run();else notice('请输入有效宽度',true)}})[b.dataset.command]()});
$('markdown-toggle').onclick=()=>$('markdown-import').click();
let pendingMarkdownImport=null;
function cancelMarkdownImport(){pendingMarkdownImport=null;$('markdown-import').value='';$('markdown-import-dialog').close()}
$('markdown-import').onchange=e=>action(async()=>{const file=e.target.files[0];if(!file)return;const base=await savedVersion();pendingMarkdownImport={file,base};$('markdown-import-dialog').showModal()});
$('markdown-import-cancel').onclick=cancelMarkdownImport;
$('markdown-import-dialog').addEventListener('cancel',()=>{pendingMarkdownImport=null;$('markdown-import').value=''});
$('markdown-import-confirm').onclick=()=>action(async()=>{const pending=pendingMarkdownImport;if(!pending)return;const text=await pending.file.text();const base=await savedVersion();if(base!==pending.base)throw Error('报告已有修改，请取消后重新选择 Markdown 文件');editor.commands.setContent(toEditor(text),{contentType:'markdown',emitUpdate:true});await savedVersion();cancelMarkdownImport();notice('已导入为新的富文档版本')});
$('markdown-source').oninput=changed;window.addEventListener('beforeunload',e=>{if(dirty){e.preventDefault();e.returnValue=''}});
// BEGIN_WORKSPACE_STARTUP
const startup={sessionReady:false,stateReady:false,chatReady:false,ready:false,paused:false,suspended:false,running:null,controller:null,pollStops:[]};
const startupDelays=[1000,2000,5000,10000,30000];
function startupStatus(message,{retry=false,pausable=true}={}){
 if(!startup.panel){
  const box=document.createElement('section');box.className='panel';box.dataset.testid='startup-status';box.setAttribute('role','status');
  const text=document.createElement('p'),again=document.createElement('button'),pause=document.createElement('button');
  again.type=pause.type='button';again.className='outline';pause.className='ghost';again.textContent='重试连接';pause.textContent='暂停重试';
  again.onclick=()=>{cancelStartup(false);void startWorkspace()};pause.onclick=()=>cancelStartup();
  box.append(text,again,pause);document.querySelector('main').prepend(box);startup.panel={box,text,again,pause};
 }
 const {box,text,again,pause}=startup.panel;box.hidden=false;text.textContent=message;again.disabled=!retry;pause.hidden=!pausable;
 $('connection').textContent='工作区尚未连接';
}
function cancelStartup(paused=true){
 startup.paused=paused;startup.controller?.abort();
 if(paused&&!startup.ready)startupStatus('连接重试已暂停，输入内容保留。',{retry:true,pausable:false});
}
function startupWait(ms,signal){
 return new Promise(resolve=>{
  const done=()=>{clearTimeout(timer);signal.removeEventListener('abort',done);resolve()};
  const timer=setTimeout(done,ms);signal.addEventListener('abort',done,{once:true});
  if(signal.aborted)done();
 });
}
function startWorkspacePolls(){
 if(startup.pollStops.length)return;
 startup.pollStops=[adaptivePoll(()=>refresh(),{active:backgroundActive,fast:3000}),adaptivePoll(()=>pollChat(),{active:backgroundActive,fast:1300})];
}
function startWorkspace(){
 if(startup.paused||startup.suspended)return Promise.resolve(false);
 if(startup.running)return startup.controller?.signal.aborted?startup.running.then(()=>startWorkspace()):startup.running;
 if(startup.ready){startWorkspacePolls();return Promise.resolve(true)}
 const controller=startup.controller=new AbortController(),signal=controller.signal;
 startup.running=(async()=>{
  for(let attempt=0;!signal.aborted;attempt++){
   startupStatus('正在连接本地工作区…');
   try{
    if(!startup.sessionReady){const session=await api('session');if(signal.aborted)return false;setToken(session.token);startup.sessionReady=true}
    if(!startup.stateReady){if(!await refreshState(true,signal))return false;startup.stateReady=true}
    if(!startup.chatReady){if(!await initChat(signal))return false;startup.chatReady=true}
    if(signal.aborted)return false;
    startup.ready=true;startup.panel.box.hidden=true;$('connection').textContent='本地已连接';startWorkspacePolls();refreshWorkspaces().catch(()=>{});return true;
   }catch(error){
    if(signal.aborted)return false;
    const delay=startupDelays[attempt];
    if(delay===undefined){startupStatus('连接仍未恢复，自动重试已暂停。网络恢复后可重试连接。原因：'+error.message,{retry:true,pausable:false});return false}
    startupStatus(`连接未完成，${delay/1000} 秒后重试（${attempt+1}/${startupDelays.length}）。输入内容保留。原因：${error.message}`,{retry:true});
    await startupWait(delay,signal);
   }
  }
  return false;
 })().finally(()=>{startup.running=null});
 return startup.running;
}
window.addEventListener('online',()=>{if(!startup.paused)void startWorkspace()});
window.addEventListener('pagehide',()=>{startup.suspended=true;startup.controller?.abort();for(const stop of startup.pollStops)stop();startup.pollStops=[]});
window.addEventListener('pageshow',()=>{startup.suspended=false;if(!startup.paused)void startWorkspace()});
void startWorkspace();
// END_WORKSPACE_STARTUP

$('editor').addEventListener('click',e=>{const a=e.target.closest('a[href^="#source-"]');if(a){e.preventDefault();const id=a.getAttribute('href').slice(8);action(async()=>{const r=await api('source?id='+id);showSource(r)})}});

function gradeSummary(a){return a?`评分：证据 ${a.evidence??'—'}/5 · 覆盖 ${a.coverage??'—'}/5 · 分析 ${a.analysis??'—'}/5 · 表达 ${a.expression??'—'}/5\n${a.summary}\n`:'尚未评分\n'}

function effectiveReportJobs(){
 const runId=pendingRun||current?.run_id;
 const version=current?.run_id===runId?current:state.briefs.find(b=>b.run_id===runId);
 const superseded=new Set(state.jobs.map(j=>parse(j.payload).previous_job_id).filter(Boolean));
 const latestChecks=new Set();
 return state.jobs.filter(j=>{
  if(['export_docx','release','audit_bundle'].includes(j.kind)||superseded.has(j.id))return false;
  if(j.kind==='learn'||!runId)return true;
  const payload=parse(j.payload),result=parse(j.result)||{};
  if(payload.run_id!==runId&&!state.briefs.some(b=>b.run_id===runId&&(b.id===payload.version_id||b.id===result.version_id)))return false;
  // Keep ongoing work visible; finished attempts describe the selected document.
  if(['running','queued'].includes(j.status)||!version)return true;
  if(['review','assess'].includes(j.kind)){
   if(payload.version_id!==version.id||latestChecks.has(j.kind))return false;
   latestChecks.add(j.kind);return true;
  }
  if(['generate','revise'].includes(j.kind)){
   // A failed producer may have admitted its document before recording a result.
   const produced='brief_'+j.id.slice(4);
   return result.version_id===version.id||version.id===produced||version.id===produced+'_r1'||(j.kind==='revise'&&payload.version_id===version.id);
  }
  return true;
 });
}
function stageRailHTML(stages){
 if(!Array.isArray(stages)||!stages.length)return '';
 const lanes=stages.filter(s=>s.agents&&s.agents.length);
 return `<div class="stage-rail">${stages.map(s=>`<div class="stage ${esc(s.status||'pending')}"><span class="stage-dot"></span><span class="stage-name">${esc(s.label)}</span></div>`).join('')}</div>`+
  (lanes.length?`<div class="stage-lanes">${lanes.map(s=>`<div class="stage-lane"><span class="stage-lane-name">${esc(s.label)}</span><div class="stage-agents">${s.agents.map(a=>{const status=a.status||'running';const cls=['completed','done','closed'].includes(status)?'done':['failed','errored'].includes(status)?'failed':'running';return `<span class="agent-chip ${cls}" title="${esc(a.task||'')}"><i></i>${esc(a.role||'子任务')}</span>`}).join('')}</div></div>`).join('')}</div>`:'');
}
let progressRequest=null;
function jobModelLabel(job,started={}){const payload=parse(job.payload),runtime=payload.runtime||started.runtime;const backend=runtime?.agent_backend||runtime?.backend||payload.agent_backend||payload.backend||started.agent_backend||started.backend;return modelLabel({...runtime,agent_backend:backend})}
function progressSelection(){
 const relevant=withoutSupersededRetries(effectiveReportJobs());
 const job=relevant.find(j=>j.status==='running')||relevant.find(j=>j.status==='queued');
 const paused=job?null:relevant.find(j=>['cancelled','interrupted','failed'].includes(j.status));
 return {job,paused,key:JSON.stringify([current?.id,pendingRun,(job||paused)?.id,(job||paused)?.status])};
}
async function refreshProgress(){
 if(!state)return;
 const {job,paused,key}=progressSelection();
 if(progressRequest?.key===key)return;
 const request={key};progressRequest=request;
 // A stop, retry, completion or report switch supersedes old network replies.
 // Let the new selection refresh immediately, even if the old fetch is pending.
 const isCurrent=()=>progressRequest===request&&progressSelection().key===key;
 try{
 if(!job){
 $('run-progress').hidden=!paused;
 if(paused){const service=await api('runtime');if(!isCurrent())return;$('run-progress').innerHTML=`<div class="section-title"><h2>${paused.status==='failed'?'任务未完成':'任务已暂停'}</h2><button id="paused-resume" class="primary">恢复任务（沿用原模型）</button></div><p>当前没有继续执行这个任务。已有来源和产物保留。</p><p class="help">本地服务 PID ${service.server_pid||'—'}（页面与任务管理） · ${service.pid?'模型进程 PID '+service.pid:'本工作区没有模型进程'}</p><p class="help">${esc(paused.error||'')}</p><p class="help">恢复会沿用原来的模型与后端。审阅和学习也可按当前模型重试，旧任务记录会保留。</p>${['review','learn'].includes(paused.kind)?'<button id="paused-retry-current" class="outline">按当前模型重试</button>':''}<button id="paused-settings" class="outline">修改模型与要求</button><button id="paused-dismiss" class="outline">清除这个任务</button>`;if($('paused-retry-current'))$('paused-retry-current').onclick=()=>action(()=>api('resume',{job_id:paused.id,use_current_model:true}),'已按当前设置创建重试，旧任务保留');$('paused-settings').onclick=()=>page('setup');$('paused-resume').onclick=()=>action(()=>api('resume',{job_id:paused.id}),'已按页面显示的模型提交');$('paused-dismiss').onclick=()=>action(()=>api('task-dismiss',{job_id:paused.id}),'已清除这个未完成任务')}
 return
}
  if(job.kind==='source_refresh'){
   const payload=parse(job.payload),source=state.sources.find(s=>s.id===payload.source_id);
   $('run-progress').hidden=false;$('run-progress').innerHTML=`<div class="section-title"><h2>${job.status==='queued'?'来源复查已排队':'正在复查来源'}</h2><button class="outline" id="progress-stop">停止任务</button></div><p>${esc(source?.name||'当前来源')}</p><p class="help">按本轮联网范围和预算获取新快照；已有来源与报告保留。复查完成后，来源变化仍需判断和复核。</p>`;
   $('progress-stop').onclick=()=>action(()=>api('stop',{job_id:job.id}));return;
  }
  const [events,live]=await Promise.all([api('events?job='+job.id),api('runtime?job_id='+encodeURIComponent(job.id))]);
  if(!isCurrent())return;
  const last=[...events].reverse().find(e=>e.kind==='runtime_progress');
  const p=last?parse(last.data):{};p.started=[...events].reverse().find(e=>e.kind==='job_started')?.created;const started=[...events].reverse().find(e=>e.kind==='runtime_started');const start=started?parse(started.data):{};
  const taskSession=start.session_id;
  let pendingRequests=[];
  if(taskSession){
   const snapshot=await api('harness/session?id='+encodeURIComponent(taskSession)+'&requests_only=1');
   if(!isCurrent())return;
   pendingRequests=(snapshot.requests||[]).filter(r=>r.status==='pending');
  }
  const run=state.runs.find(r=>r.id===parse(job.payload).run_id);
  const req=run?parse(run.requirements):{};
  const agents=p.agents||[];
  const stageLabel=typeof p.stage==='string'?p.stage:(p.stages||[]).find(item=>item.status==='active')?.label;
  const elapsed=Math.max(0,Math.floor((Date.now()-new Date(p.started||job.created))/1000));
  const mins=Math.floor(elapsed/60),secs=elapsed%60;
  // Independent review/check jobs need not own the main runtime's PID.
  const running=job.status==='running';
  const labels={running:'进行中',pending_init:'启动中',completed:'已完成',done:'已完成',closed:'已结束',failed:'失败',errored:'失败'};
  $('run-progress').hidden=false;
  $('run-progress').innerHTML=`<div class="section-title"><div><p class="eyebrow">${taskLabel(job.kind)||'简报生成'} · ${running?'后台正在运行':'等待后台执行'}</p><h2>${esc(pendingRequests.length?'等待你的确认':stageLabel||(job.status==='queued'?'任务已排队':'正在启动 BriefLoop'))}</h2></div><button class="outline" id="progress-stop">停止任务</button></div><p><strong>${esc(jobModelLabel(job,start))}</strong> · 模型进程 PID ${live.pid||'—'}${live.server_pid?' · 本地服务 PID '+live.server_pid:''}</p><p class="help">${job.status==='queued'?'排队等待':p.started?'已执行':'起始时间待确认'} ${job.status==='queued'||p.started?`${mins} 分 ${secs} 秒`:''} · ${req.target_minutes?'目标约 '+req.target_minutes+' 分钟'+(running&&p.started&&elapsed>req.target_minutes*60?' · 已超过目标，继续执行':''):'未设目标用时'}${req.hard_timeout_minutes?' · 单轮最长运行 '+req.hard_timeout_minutes+' 分钟':''} <button id="progress-timeout" class="subtle-button">后续任务设置</button>${run?` · ${JSON.parse(run.source_ids).length} 份初始来源 · ${req.allow_web?'允许联网':'仅本地来源'}`:''}</p>${p.message?`<p class="progress-message">${esc(p.message)}</p>`:''}${stageRailHTML(p.stages)||`<div class="agent-progress">${agents.map(a=>`<div><strong>${esc(a.role)}</strong><span>${labels[a.status]||esc(a.status)}</span>${a.task?`<p>${esc(a.task)}</p>`:''}</div>`).join('')}</div>`}<p class="help">${p.draft_ready?'正文已可查看，评分独立完成。':'正文保存后会自动显示；等待子 agent 时可能暂时没有新消息。'}${p.last_activity?' 最近活动：'+clock(p.last_activity):''}</p>`;
  if(pendingRequests.length){
   $('run-progress').insertAdjacentHTML('beforeend',`<div class="agent-question" role="status"><strong>有 ${pendingRequests.length} 项操作等待确认</strong><p>打开任务对话，查看具体操作并选择允许、拒绝或补充信息。回答后任务会继续。</p><button id="progress-requests" class="primary">查看并处理</button></div>`);
   $('progress-requests').onclick=()=>selectChat(taskSession);
  }
  $('progress-timeout').onclick=showSettings;
  $('progress-stop').onclick=()=>action(()=>api('stop',{job_id:job.id}));
 }catch(e){if(isCurrent()){$('run-progress').hidden=false;$('run-progress').textContent='进度连接暂时中断，任务没有重新提交。'}}
 finally{if(progressRequest===request)progressRequest=null}
}

function friendlyModel(model){return ({'default':'宿主默认模型','gpt-5.6-luna':'Luna','gpt-5.6-terra':'Terra','gpt-5.6-sol':'Sol','gpt-6-astra':'Astra'}[model]||model)}
function effortValue(runtime,key){return Object.prototype.hasOwnProperty.call(runtime,key)?(runtime[key]||'none'):'high'}
function assignEffort(id,value){const input=$(id);if(![...input.options].some(o=>o.value===value))input.add(new Option(value,value));input.value=value}
function activeChatRuntime(){return chat.messages.find(m=>m.role==='user'&&m.turn_id===chat.session?.turn_id&&m.runtime)?.runtime||chat.session?.runtime||{}}
const fastCapabilities=new Map();
function fastChoiceKey(cfg){return JSON.stringify([cfg.backend||cfg.agent_backend||'codex',cfg.model||'',cfg.model_provider||''])}
function fastAvailable(cfg,capability){return (cfg.backend||cfg.agent_backend)==='codex'&&capability?.official_connection===true}
function selectedServiceTier(id,cfg,requireResolved=false){const tier=['default','fast'].includes($(id).value)?$(id).value:null,capability=fastCapabilities.get(fastChoiceKey(cfg));if(requireResolved&&tier&&(cfg.backend||cfg.agent_backend)==='codex'&&typeof capability?.official_connection!=='boolean')throw Error('正在确认 Codex Provider，请稍后重试；速度选择已保留。');return fastAvailable(cfg,capability)?tier:null}
function fastControlConfig(target){const isChat=target==='chat',role=target.startsWith('role-')?target.slice(5):null;return {backend:isChat?(chatBackendChoice()):backendValue(),model:$(role?`role-${role}-model`:isChat?'chat-model':'model-select').value.trim(),model_provider:$(role?`role-${role}-provider`:isChat?'chat-model-provider':'model-provider').value.trim()||null}}
const fastCapabilityRequests=new Map();
async function loadFastCapability(cfg){const key=fastChoiceKey(cfg);if(fastCapabilityRequests.has(key))return fastCapabilityRequests.get(key);const request=(async()=>{try{const query=new URLSearchParams({backend:cfg.backend,model:cfg.model,model_provider:cfg.model_provider||''});const result=await api('runtime/fast-capability?'+query);if(fastChoiceKey(result)===key)fastCapabilities.set(key,result);else fastCapabilities.set(key,{enabled:false});}catch{fastCapabilities.set(key,{enabled:false})}finally{fastCapabilityRequests.delete(key);renderFastControls(false);updateModelLabel()}})();fastCapabilityRequests.set(key,request);return request}
function renderFastControls(fetchMissing=true){for(const target of ['main','chat','role-evaluator','role-maintainer','role-proposer']){const id=target==='main'?'service-tier':target==='chat'?'chat-service-tier':target+'-service-tier',control=$(id);if(!control)continue;const cfg=fastControlConfig(target),key=fastChoiceKey(cfg),capability=fastCapabilities.get(key),supported=fastAvailable(cfg,capability);control.hidden=!supported;const wrapper=control.closest('[data-fast-control]');if(wrapper)wrapper.hidden=!supported;if(fetchMissing&&cfg.backend==='codex'&&cfg.model&&!capability)loadFastCapability(cfg);}}
function modelLabel(cfg){if(!cfg?.model)return '未指定模型';const backend=cfg.agent_backend||cfg.backend;const prefix=backend?runtimeName(backend)+' · ':'';if(cfg.model_variant!=null||['opencode','briefloop-native'].includes(backend)){const variant=cfg.model_variant||'模型默认';return prefix+friendlyModel(cfg.model)+' / '+variant}if(backend&&!['codex','opencode'].includes(backend)&&!Object.hasOwn(cfg,'reasoning_effort'))return prefix+friendlyModel(cfg.model);const effort=cfg.reasoning_effort,effortLabel=Object.prototype.hasOwnProperty.call(cfg,'reasoning_effort')?(!effort||effort==='none'?'模型默认':effort):'未记录';return prefix+friendlyModel(cfg.model)+' / '+effortLabel+(cfg.model_provider?' · '+cfg.model_provider:'')+(cfg.service_tier==='fast'?' · Fast（请求）':cfg.service_tier==='default'?' · 标准':'')}
function backendValue(){return ($('agent-backend')&&$('agent-backend').value)||state.settings.agent_backend||'codex'}
function renderBackend(){
 const backend=backendValue(),variant=['opencode','briefloop-native','mimo'].includes(backend),codex=backend==='codex';
 $('variant-field').hidden=!variant;document.querySelector('.main-provider-field').style.display=codex?'':'none';
 $('effort-select').hidden=variant;$('effort-select').closest('label').hidden=variant;
 reasoning.configure($(variant?'model-variant':'effort-select'),backend,$('model-select').value.trim(),{variant});
 $('model-select').placeholder=['opencode','briefloop-native'].includes(backend)?'如 opencode-go/gpt-5.6-luna':'输入任意模型 ID';
 document.querySelectorAll('.role-variant-field').forEach(e=>e.hidden=!variant);
 document.querySelectorAll('.role-provider-field').forEach(e=>e.style.display=codex?'':'none');
 document.querySelectorAll('.role-effort-select').forEach(e=>{e.style.display=variant?'none':'';reasoning.configure(variant?$(`role-${e.dataset.roleEffort}-variant`):e,backend,$(`role-${e.dataset.roleEffort}-model`).value.trim()||$('model-select').value.trim(),{variant})});
 renderSearchProvider();updateModelLabel();renderFastControls();
}
function updateModelLabel(){refreshRuntimeModelSummaries();const op=['opencode','briefloop-native'].includes(backendValue());const cfg=op?{model:$('model-select').value.trim(),model_variant:$('model-variant').value.trim()||null,agent_backend:backendValue()}:{model:$('model-select').value.trim(),reasoning_effort:backendValue()==='mimo'?($('model-variant').value.trim()||null):$('effort-select').value,model_provider:$('model-provider').value.trim(),service_tier:selectedServiceTier('service-tier',fastControlConfig('main')),agent_backend:backendValue()};$('execution-choice').textContent='即将使用：'+modelLabel(cfg);if($('setup-model-summary'))$('setup-model-summary').textContent=modelLabel(cfg);$('generate-button').textContent='使用 '+modelLabel(cfg)+' 生成简报 →';$('model-select').title=cfg.model?friendlyModel(cfg.model)+' · '+cfg.model:'输入模型 ID'}
async function saveModel(){if(backendValue()==='codex')await loadFastCapability(fastControlConfig('main'));const model=reasoningModel(backendValue(),$('model-select').value.trim(),$('effort-select').value);$('model-select').value=model;if(!model)throw Error('请输入模型 ID');const op=['opencode','briefloop-native'].includes(backendValue());if(op){if(!model.includes('/'))throw Error('模型 ID 必须是 provider/model 形式');await api('settings',{agent_backend:backendValue(),model_selection_required:false,model,model_variant:$('model-variant').value.trim()||null})}else if(backendValue()!=='codex')await api('settings',{agent_backend:backendValue(),model_selection_required:false,model,model_provider:null,model_variant:null,runtime_efforts:{...state.settings.runtime_efforts,[backendValue()]:backendValue()==='mimo'?($('model-variant').value.trim()||null):($('effort-select').value==='none'?null:$('effort-select').value)}});else await api('settings',{agent_backend:backendValue(),model_selection_required:false,model,reasoning_effort:$('effort-select').value,model_provider:$('model-provider').value.trim()||null,service_tier:selectedServiceTier('service-tier',fastControlConfig('main'),true)});updateModelLabel();if($('welcome')&&!$('welcome').hidden)renderWelcome()}
let runtimeCatalog=[],runtimeScanned=false;
function runtimeName(id){return id==='briefloop-native'?'BriefLoop Agent':runtimeCatalog.find(r=>r.id===id)?.name||id}
function renderSettingsSessionNote(){
 const box=$('settings-session-note');if(!box)return;
 const session=chat.session,backend=session?.runtime?.backend,chosen=backendValue();
 if(!session){box.hidden=false;box.innerHTML='当前没有打开的会话。本页的模型与执行引擎设置用于新会话。';return}
 const model=session.runtime?.model;
 const head=`当前会话：<strong>${esc(runtimeName(backend))} · ${esc(model?friendlyModel(model):'宿主默认')}</strong>`;
 box.hidden=false;
 if(chosen&&chosen!==backend){
  box.innerHTML=head+`。本页把执行引擎设为 <strong>${esc(runtimeName(chosen))}</strong>，与当前会话不同；可应用到下一回合，或新建会话。 <button type="button" class="outline" id="settings-new-session">用以上设置开新会话</button>`;
  const button=$('settings-new-session');if(button)button.onclick=()=>newChat();
 }else{
  box.innerHTML=head+'。本页改动用于新会话；当前会话的模型可在对话里的模型选择器调整。';
 }
}
function refreshRuntimeModelSummaries(){
 if(!state)return;
 document.querySelectorAll('[data-runtime-model]').forEach(el=>{el.innerHTML=runtimeModelSummary(el.dataset.runtimeModel,backendValue(),$('model-select').value.trim(),modelCatalogs.get(el.dataset.runtimeModel))});
}
function renderRuntimeDiscovery(){
 const select=$('agent-backend'),chosen=select.value||state.settings.agent_backend||'codex';
 select.replaceChildren();
 for(const runtime of runtimeCatalog){const option=new Option(runtime.name+(runtime.available?'':(runtime.installed?' · 尚未支持':' · 未安装')),runtime.id);option.disabled=!runtime.available;select.add(option)}
 if(![...select.options].some(o=>o.value===chosen))select.add(new Option(chosen+' · 未检测到',chosen));select.value=chosen;
 const modelBlock=$('settings-model-block');
 const cli=runtimeCatalog.filter(r=>r.id!=='briefloop-native');
 const installed=cli.filter(r=>r.installed).sort((a,b)=>Number(b.id===chosen)-Number(a.id===chosen)),missing=cli.filter(r=>!r.installed);
 const model=$('model-select').value.trim();
 const row=r=>runtimeCard(r,{chosen,model,catalog:modelCatalogs.get(r.id)});
 const roots=[$('runtime-discovery-details'),$('settings-native-runtime')];
 roots[0].innerHTML=installed.map(row).join('')+`<details class="runtime-uninstalled"><summary>未安装的 CLI · ${missing.length}</summary><div class="runtime-missing-grid">${missing.map(r=>runtimeCard(r,{chosen,model:'',compact:true})).join('')}</div></details>`;
 roots[1].innerHTML=runtimeCatalog.filter(r=>r.id==='briefloop-native').map(row).join('');
 const selectedCard=roots.flatMap(root=>[...root.querySelectorAll('[data-runtime-card]')]).find(c=>c.dataset.runtimeCard===chosen);
 if(selectedCard)selectedCard.append(modelBlock);else $(chosen==='briefloop-native'?'settings-api':'settings-cli').append(modelBlock);
 for(const root of roots){
  root.querySelectorAll('[data-runtime-test]').forEach(button=>button.onclick=()=>action(async()=>{
   const model=$('model-select').value.trim();if(!model)throw Error('请先选择或输入模型 ID');
   const r=await api('runtime-test',{backend:button.dataset.runtimeTest,model});chat.view='tests';await selectChat(r.session_id);
  },'已提交模型测试；进展见对话，可随时停止'));
  root.querySelectorAll('[data-runtime-select]').forEach(button=>button.onclick=()=>{if(button.dataset.runtimeSelect===backendValue())return;select.value=button.dataset.runtimeSelect;select.dispatchEvent(new Event('change'))});
 }
 renderSettingsSessionNote();
}
async function refreshRuntimeDiscovery(force=false){
 const select=$('agent-backend');if(!select.value){const backend=state.settings.agent_backend||'codex';if(!Array.from(select.options).some(o=>o.value===backend))select.add(new Option(backend,backend));select.value=backend;}
 if(!$('runtime-discovery-status')){const box=document.createElement('section');box.className='runtime-discovery';box.innerHTML='<div class="section-title"><strong>本机 Agent CLI</strong><button type="button" id="runtime-discovery-refresh" class="outline">重新检测</button></div><p id="runtime-discovery-status" class="help" role="status"></p><div id="runtime-discovery-details" class="help"></div><p id="runtime-model-status" class="help" role="status"></p>';$('settings-runtime-list').append(box);$('runtime-discovery-refresh').onclick=()=>refreshRuntimeDiscovery(true)}
 $('runtime-discovery-status').textContent='正在检测执行引擎…';$('runtime-discovery-refresh').disabled=true;
 try{const data=await api('runtimes'+(force?'?refresh=1':''));runtimeCatalog=data.runtimes||[];renderRuntimeDiscovery();if(typeof office!=='undefined'&&data.capabilities?.officecli)office.renderSettingsCapability(data.capabilities.officecli);$('runtime-discovery-status').textContent=`检测到 ${runtimeCatalog.filter(r=>r.installed).length} 个本机 CLI，其中 ${runtimeCatalog.filter(r=>r.available).length} 个可选择；检测未验证账号与模型调用，需另行短测试。`+(data.diagnostic?` ${data.diagnostic}`:'');await refreshModelSuggestions(force)}catch(e){$('runtime-discovery-status').textContent='检测失败：'+e.message}finally{$('runtime-discovery-refresh').disabled=false;runtimeScanned=true;if($('welcome')&&!$('welcome').hidden)renderWelcome()}
}
$('agent-backend').onchange=()=>action(async()=>{const backend=backendValue(),dropped=Object.keys(state.settings.role_models||{}).length;await api('settings',{agent_backend:backend,model_selection_required:true,role_models:{}});state.settings.agent_backend=backend;state.settings.model_selection_required=true;state.settings.role_models={};assignEffort('effort-select',settingsEffort(state.settings,backend));$('model-variant').value=backend==='mimo'?(state.settings.runtime_efforts?.mimo||''):(state.settings.model_variant||'');reasoning.refresh();$('model-select').value='';$('chat-model').value='';modelCatalog={backend:null,at:0,models:[]};renderRuntimeDiscovery();renderRoleModels();renderBackend();updateComposer();await refreshModelSuggestions();notice(dropped?'宿主已切换；原宿主的角色模型已清空，留空即继承主链模型':'Runtime 已保存；请选择或输入模型')});$('model-variant').onchange=()=>action(saveModel,'Variant 已保存；下一次启动生效');
let modelCatalog={backend:null,at:0,models:[]};
const modelCatalogs=new Map();
function modelTargetBackend(target){return target==='chat-model'?chatBackendChoice():backendValue()}
async function fetchModelCatalog(force=false,backend=backendValue()){
 const now=Date.now(),cached=modelCatalogs.get(backend);
 if(!force&&cached&&now-cached.at<3600000){if(backend===backendValue())modelCatalog=cached;return cached.models;}
 const data=await api('models?backend='+encodeURIComponent(backend)+(force?'&refresh=1':''));
 const catalog={backend,at:now,models:(data.models||[]).map(m=>typeof m==='string'?{id:m,name:friendlyModel(m)}:{...m,name:m.name||m.label||friendlyModel(m.id),provider:m.provider||runtimeName(backend)}),diagnostic:data.diagnostic||data.error||'',source:data.source||''};modelCatalogs.set(backend,catalog);if(backend===backendValue())modelCatalog=catalog;refreshRuntimeModelSummaries();return catalog.models;
}
async function refreshModelSuggestions(force=false){
 if(force)fastCapabilities.clear();renderFastControls();
 const backend=backendValue();$('model-suggestions').innerHTML='';refreshInlineModelPickers();
 try{const models=await fetchModelCatalog(force);if(backend!==backendValue())return;
 $('model-suggestions').innerHTML=models.map(m=>`<option value="${esc(m.id)}" label="${esc(m.name||m.id)}"></option>`).join('');refreshInlineModelPickers();
 if($('runtime-model-status'))$('runtime-model-status').textContent=modelCatalog.diagnostic||`${runtimeName(backend)}：${models.length} 个模型${modelCatalog.source?' · '+({native_config:'本机配置',host:'宿主目录',host_default_only:'宿主未提供目录',builtin_hints:'内置建议',local_routes:'本机路由'}[modelCatalog.source]||modelCatalog.source):''}。可直接输入其他模型 ID。`;
 }catch(e){if($('runtime-model-status'))$('runtime-model-status').textContent='模型目录读取失败：'+e.message+'；可手动输入模型 ID。'}
}
let modelPickerTarget=null;
async function openModelPicker(targetId){
  modelPickerTarget=targetId;
  $('model-picker-search').value='';
  $('model-picker').showModal();
  await renderModelPicker();
}
async function renderModelPicker(){
  const backend=modelTargetBackend(modelPickerTarget);let models,status,error='';
  try{
    models=await fetchModelCatalog(false,backend);status=`${runtimeName(backend)} · ${models.length} 个模型；可手填模型 ID`;
  }catch(e){models=[];error=e.message||'模型目录读取失败'}
  const q=$('model-picker-search').value.trim().toLowerCase();
  const shown=models.filter(m=>!q||m.id.toLowerCase().includes(q)||(m.name||'').toLowerCase().includes(q)||(m.provider||'').toLowerCase().includes(q));
  $('model-picker-status').textContent=(error?('读取失败：'+error+'；可直接手填模型 ID'):(models.length?status:emptyCatalogLabel(backend,modelCatalogs.get(backend))))+(q?` · 筛出 ${shown.length} 个`:'');
  let lastProvider=null,html='';
  for(const m of shown){
    if(m.provider!==lastProvider){lastProvider=m.provider;html+=`<h3 class="workspace-list-heading">${esc(m.provider)}</h3>`}
    html+=`<button type="button" class="workspace-choice" data-model-pick="${esc(m.id)}"><span><strong>${esc(m.id)}</strong><small>${esc(m.name||'')}</small></span><em>选用</em></button>`;
  }
  const custom=$('model-picker-search').value.trim();
  const customChoice=custom&&!/\s/.test(custom)&&!shown.some(m=>m.id===custom)?`<button type="button" class="workspace-choice" data-model-pick="${esc(custom)}"><span><strong>${esc(custom)}</strong><small>使用此模型 ID</small></span><em>选用</em></button>`:'';
  $('model-picker-list').innerHTML=(html||(error?('<p class="help">读取失败：'+esc(error)+'</p>'):(models.length?'<p class="help">没有匹配的模型。可直接输入完整模型 ID 后按回车选用。</p>':'<p class="help">'+esc(emptyCatalogLabel(backend,modelCatalogs.get(backend)))+'</p>')))+customChoice;
  $('model-picker-list').querySelectorAll('[data-model-pick]').forEach(b=>b.onclick=()=>pickModel(b.dataset.modelPick));
}
function pickModel(id){
  const el=modelPickerTarget&&$(modelPickerTarget);
  $('model-picker').close();
  if(!el)return;
  el.value=id;
  el.dispatchEvent(new Event('change',{bubbles:true}));
}
$('model-picker-close').onclick=()=>$('model-picker').close();
$('model-picker-search').oninput=()=>renderModelPicker();
$('model-picker-search').onkeydown=event=>{if(event.key==='Enter'&&!event.isComposing){event.preventDefault();const id=event.target.value.trim();if(id&&!/\s/.test(id))pickModel(id)}};
$('model-picker-refresh').onclick=()=>action(async()=>{await refreshModelSuggestions(true);await renderModelPicker()},'模型目录已刷新');
$('model-select').onchange=()=>{reasoning.refresh();renderBackend();return action(saveModel,'模型已保存；下一次启动生效')};$('service-tier').onchange=()=>action(saveModel,'速度已保存；下一次启动生效');$('effort-select').onchange=()=>action(saveModel,'推理档位已保存；下一次启动生效');$('model-provider').onchange=()=>action(saveModel,'Provider 已保存；下一次启动生效');$('model-browse').onclick=()=>openModelPicker('model-select');
$('model-apply-session').onclick=()=>{
 const session=chat.session;
 if(!session){notice('当前没有打开的会话，请先新建或选择对话',true);return}
 if(chatActive()){notice('会话正在运行，请等待或停止后再应用',true);return}
 if([...chat.events.values()].some(e=>e.kind==='session/internal')&&session.runtime?.backend!==backendValue()){notice('报告任务沿用冻结宿主',true);return}
 chat.nextBackend=backendValue();
 const model=$('model-select').value.trim();if(!model){notice('请先选择或输入模型 ID',true);return}
 $('chat-model').value=model;$('chat-model-provider').value=$('model-provider').value;assignEffort('chat-effort',$('effort-select').value);$('chat-variant').value=$('model-variant').value.trim();$('chat-service-tier').value=$('service-tier').value;rememberDraft();updateComposer();renderSettingsSessionNote();notice('已应用到下一回合；发送后按所选宿主执行');
};

$('version-history').onclick=()=>action(async()=>{
 await savedVersion();if(!current)return;
 const versions=state.briefs.filter(b=>b.run_id===current.run_id);
 $('history-list').innerHTML=versions.map((b,i)=>`<button class="history-row" data-history-version="${b.id}"><strong>${b.author==='agent'?'生成原稿':i===0?'当前编辑稿':'自动保存快照'}</strong><span>${dateTimeSeconds(b.created)}</span></button>`).join('');
 $('history-list').querySelectorAll('[data-history-version]').forEach(button=>button.onclick=()=>{const b=state.briefs.find(x=>x.id===button.dataset.historyVersion);openBrief(b);render(false);$('history-dialog').close()});
 $('history-dialog').showModal();
});
$('close-history').onclick=()=>$('history-dialog').close();
$('version-diff').onclick=()=>action(async()=>{
 await savedVersion();if(!current)return;
 const versions=state.briefs.filter(b=>b.run_id===current.run_id);
 const index=versions.findIndex(b=>b.id===current.id),older=versions.slice(index+1);
 if(!older.length){notice('这是第一稿，还没有可比较的上一版本');return}
 const target=current;
 const label=b=>moment(b.created)+' · '+(b.author==='agent'?'AI 稿件':b.author==='user'?'用户修改':'原稿');
 $('diff-base').innerHTML=older.map((b,i)=>`<option value="${esc(b.id)}">${i===0?'上一稿 · ':i===older.length-1?'第一稿 · ':''}${esc(label(b))}</option>`).join('');
 $('diff-target').textContent='当前稿 · '+label(target);
 function documentFor(b){
  if(b.editor_document)return editor.schema.nodeFromJSON(parse(b.editor_document)).toJSON();
  const temporary=new Editor({extensions:[StarterKit,TableKit,ReportImage,TextStyle,Layout,Citation,Markdown],content:toEditor(b.markdown),contentType:'markdown'});
  try{return temporary.getJSON()}finally{temporary.destroy()}
 }
 const after=documentFor(target);
 // Polled state lists earlier versions without bodies: load the chosen base on
 // demand, and let only the latest choice render.
 let comparison=0;
 async function compare(){
  const base=older.find(b=>b.id===$('diff-base').value);if(!base)return;
  const ticket=++comparison;$('diff-summary').textContent='正在读取比较版本…';
  const full=await loadBrief(base);if(ticket!==comparison)return;
  const before=documentFor(full),serializer=DOMSerializer.fromSchema(editor.schema);
  const count=renderVersionDiff($('diff-body'),before.content,after.content,(node,side)=>{
   const doc=editorDocument({type:'doc',content:[node]},side==='before'?base.id:target.id);
   return serializer.serializeNode(editor.schema.nodeFromJSON(doc.content[0]));
  });
  $('diff-next').disabled=!count;let changeIndex=0;$('diff-next').onclick=()=>{const rows=$('diff-body').querySelectorAll('[data-change]');if(rows.length)rows[changeIndex++%rows.length].scrollIntoView({block:'center',behavior:'smooth'})};
  $('diff-summary').textContent=count?`${count} 处内容或格式变化 · 绿色为新增，红色删除线为删去；边框标出图表或格式变化`:'两稿内容和格式相同';
 }
 $('diff-base').onchange=()=>{const ticket=comparison+1;compare().catch(e=>{if(ticket===comparison){$('diff-body').replaceChildren();$('diff-next').disabled=true;$('diff-summary').textContent='比较版本读取失败：'+e.message}})};
 await compare();$('diff-dialog').showModal();
});
$('close-diff').onclick=()=>$('diff-dialog').close();


// Interactive agent conversations. Artifact editors keep their existing state.
const chat = {view:'active',home:true,sessions:[],id:null,session:null,messages:[],requests:[],events:new Map(),after:0,busy:false,uploading:0,polling:false,drafts:new Map(),attachments:new Set(),request:null};
const chatStates={idle:'准备就绪',starting:'正在启动',running:'正在处理',complete:'已完成',completed:'已完成',failed:'运行失败',interrupted:'已中断',cancelled:'已停止',queued:'已排队',sending:'发送中',delivered:'已发送',streaming:'正在回复'};
const chatActive=()=>['running','starting'].includes(chat.session?.status);
function chatBackendChoice(){return chat.nextBackend||chat.session?.runtime?.backend||state?.settings?.agent_backend||'codex'}
function renderChatBackendChoice(){
 const select=$('chat-backend');if(!select)return;
 const chosen=chatBackendChoice(),signature=JSON.stringify([chosen,runtimeCatalog.map(r=>[r.id,r.available])]);
 if(select.dataset.signature!==signature){select.dataset.signature=signature;select.replaceChildren();for(const r of runtimeCatalog){const option=new Option(r.name+(r.available?'':' · 不可用'),r.id);option.disabled=!r.available;select.add(option)}if(![...select.options].some(o=>o.value===chosen))select.add(new Option(runtimeName(chosen),chosen));select.value=chosen}
 select.disabled=chat.busy||[...chat.events.values()].some(e=>e.kind==='session/internal')||!!(chat.session?.lifecycle&&chat.session.lifecycle!=='active')||(chatActive()&&$('chat-mode').value==='steer');
}
$('chat-backend').onchange=()=>{
 chat.nextBackend=$('chat-backend').value;chat.hostOptions={};reasoning.refresh();$('chat-mode').value='queue';
 const previous=[...chat.messages].reverse().find(m=>m.role==='user'&&m.runtime?.backend===chat.nextBackend)?.runtime;
 $('chat-model').value=previous?.model||'';$('chat-model-provider').value=previous?.model_provider||'';$('chat-service-tier').value=previous?.service_tier||'';assignEffort('chat-effort',previous?effortValue(previous,'effort'):settingsEffort(state.settings,chat.nextBackend));if($('chat-variant'))$('chat-variant').value=previous?.variant||(chat.nextBackend==='mimo'?previous?.effort:'')||'';
 chat.hostOptions=previous?.host_options||{};renderChatRuntimePermissions();if(previous?.permission)$('chat-permission').value=previous.permission;
 chat.request=null;rememberDraft();updateComposer();refreshInlineModelPickers();
};
function rememberDraft(){chat.drafts.set(chat.id||'new',{text:$('chat-input').value,sources:[...chat.attachments],backend:chatBackendChoice(),model:$('chat-model').value,model_provider:$('chat-model-provider').value.trim()||null,effort:$('chat-effort').value,variant:($('chat-variant')?.value||'').trim(),service_tier:(chatBackendChoice())==='codex'?($('chat-service-tier').value||null):null,allow_web:$('chat-allow-web').checked,permission:$('chat-permission').value,host_options:chat.hostOptions||{}});try{sessionStorage.setItem('briefloop-chat-drafts',JSON.stringify([...chat.drafts].slice(-30)))}catch{}}
function restoreDraft({preserveNewDraft=false}={}){
 const d=chat.drafts.get(chat.id||'new'),sessionRuntime=chat.session?.runtime;
 const backend=((chat.id||preserveNewDraft)&&d?.backend)||sessionRuntime?.backend||state.settings.agent_backend||'codex';
 chat.nextBackend=backend;
 const saved=d&&(chat.id||d.backend===backend||(!d.backend&&sessionRuntime))?d:null;
 const fallback={model:state.settings.model_selection_required?'':state.settings.model,backend,effort:settingsEffort(state.settings,backend),variant:backend==='mimo'?state.settings.runtime_efforts?.mimo:state.settings.model_variant,model_provider:state.settings.model_provider,service_tier:state.settings.service_tier};
 const runtime=saved||sessionRuntime||fallback;
 // Searching is the expected default for a fresh chat; a saved draft keeps the user's own choice.
 $('chat-input').value=d?.text||'';chat.attachments=new Set(d?.sources||[]);$('chat-allow-web').checked=d&&('allow_web' in d)?!!d.allow_web:(chat.id?[...(chat.messages||[])].reverse().find(m=>m.role==='user')?.allow_web??(state.settings.chat_allow_web!==false):state.settings.chat_allow_web!==false);chat.hostOptions=runtime.host_options||{};
 $('chat-model').value=runtime.model||'';assignEffort('chat-effort',Object.hasOwn(runtime,'effort')?effortValue(runtime,'effort'):(backend==='codex'?'high':'none'));if($('chat-variant'))$('chat-variant').value=runtime.variant||(runtime.backend==='mimo'?runtime.effort:'')||'';
 $('chat-model-provider').value=runtime.model_provider||'';$('chat-service-tier').value=runtime.service_tier||'';$('chat-permission').value=runtime.permission||'workspace-write';
 renderAttachments();updateComposer();autoSizeChatInput();refreshInlineModelPickers();
}
const PERMISSION_MODES={
 'workspace-write':{label:'读写工作区',note:'本轮可以读取并修改工作区文件。'},
 'read-only':{label:'只读',note:'本轮只读取和解释资料，不修改文件，也不启动生成、评分或学习任务。'},
 'runtime-native':{label:'由 CLI 自己控制',note:'该 CLI 按自己的权限设置运行，BriefLoop 不限制它的文件与联网范围。'}
};
function permissionModes(runtime){
 const caps=((runtimeCatalog||[]).find(r=>r.id===runtime)||{}).capabilities;
 // Before discovery finishes, fall back to what each host family supports.
 const modes=(caps?.permission_modes||(['codex','opencode','briefloop-native'].includes(runtime)?['workspace-write','read-only']:['runtime-native'])).filter(mode=>PERMISSION_MODES[mode]);
 return modes.length?modes:['workspace-write'];
}
function renderChatRuntimePermissions(){
 const backend=chatBackendChoice(),select=$('chat-permission'),modes=permissionModes(backend);
 const signature=JSON.stringify([backend,modes]);
 if(renderChatRuntimePermissions.signature!==signature){
  renderChatRuntimePermissions.signature=signature;
  select.replaceChildren(...modes.map(mode=>new Option(PERMISSION_MODES[mode].label,mode)));
  select.title=modes.map(mode=>PERMISSION_MODES[mode].note).join(' ');
  // One option is not a choice; hide the control instead of showing an abstract label.
  select.hidden=modes.length<2;
 }
 if(!modes.includes(select.value))select.value=modes[0];
 // Only offer mid-run interjection where the runtime can actually take it.
 const declaredCaps=((runtimeCatalog||[]).find(r=>r.id===backend)||{}).capabilities;
 const canSteer=backend===(chat.session?.runtime?.backend||backend)&&(declaredCaps?declaredCaps.steer!==false:backend==='codex');
 for(const option of $('chat-mode').options){if(option.value==='steer'){option.hidden=!canSteer;option.disabled=!canSteer}}
 if(!canSteer&&$('chat-mode').value==='steer')$('chat-mode').value='queue';
 $('chat-effort').hidden=['opencode','briefloop-native','mimo'].includes(backend);reasoning.configure($(['opencode','briefloop-native','mimo'].includes(backend)?'chat-variant':'chat-effort'),backend,$('chat-model').value.trim(),{variant:['opencode','briefloop-native','mimo'].includes(backend)});document.querySelector('.chat-provider-row').hidden=backend!=='codex';
 const variantField=document.querySelector('label[for="chat-variant"]');
 if(variantField)variantField.hidden=!['opencode','briefloop-native','mimo'].includes(backend);
 const effortField=document.querySelector('label[for="chat-effort"]');
 if(effortField)effortField.hidden=['opencode','briefloop-native','mimo'].includes(backend);
}
function runtimeChoice(){const model=$('chat-model').value.trim();if(!model)throw Error('请输入模型 ID');const backend=chatBackendChoice();if(!['codex','opencode','briefloop-native'].includes(backend)){return {model:reasoningModel(backend,model,$('chat-effort').value),backend,effort:backend==='mimo'?($('chat-variant').value.trim()||null):($('chat-effort').value==='none'?null:$('chat-effort').value),permission:'runtime-native',host_options:chat.hostOptions||{}}}if(['opencode','briefloop-native'].includes(backend)){if(!model.includes('/'))throw Error('Opencode 模型必须是 provider/model 形式，例如 opencode-go/gpt-5.6-luna');return {model,backend,variant:($('chat-variant')?.value||'').trim()||null,permission:$('chat-permission').value}}return {model,backend,model_provider:$('chat-model-provider').value.trim()||null,effort:$('chat-effort').value,service_tier:selectedServiceTier('chat-service-tier',fastControlConfig('chat'),true),permission:$('chat-permission').value}}
function messageTime(value){return clock(value)}
function chatError(text=''){$('chat-error').textContent=text;$('chat-error').hidden=!text}
function sessionMissing(error){return /会话或消息不存在|会话不存在/.test(String(error&&error.message||error||''))}
function updateComposer(){syncCompactReportControls();renderChatBackendChoice();renderChatRuntimePermissions();const readonly=chat.session&&chat.session.lifecycle&&chat.session.lifecycle!=='active';const active=chatActive(),steering=active&&$('chat-mode').value==='steer';if(steering){const runtime=activeChatRuntime();$('chat-permission').value=runtime.permission||'workspace-write';$('chat-model').value=runtime.model||'';assignEffort('chat-effort',effortValue(runtime,'effort'));if($('chat-variant'))$('chat-variant').value=runtime.variant||(runtime.backend==='mimo'?runtime.effort:'')||'';$('chat-model-provider').value=runtime.model_provider||'';$('chat-service-tier').value=runtime.service_tier||'';const activeMessage=chat.messages.find(m=>m.role==='user'&&m.turn_id===chat.session?.turn_id);$('chat-allow-web').checked=!!activeMessage?.allow_web} renderFastControls();$('chat-allow-web').disabled=readonly||steering||chat.busy;for(const id of ['chat-model','chat-effort','chat-variant','chat-model-provider','chat-service-tier'])if($(id)){$(id).disabled=readonly||steering||chat.busy;if($(id+'-choices'))$(id+'-choices').disabled=$(id).disabled;}$('chat-permission').disabled=readonly||steering||chat.busy;$('new-session').disabled=chat.busy||chat.uploading>0;$('chat-input').readOnly=chat.busy||readonly;document.querySelectorAll('[data-chat-session]').forEach(b=>b.disabled=chat.busy||chat.uploading>0);$('chat-send').disabled=readonly||chat.busy||chat.uploading>0||(!$('chat-input').value.trim()&&!chat.attachments.size)||!$('chat-model').value.trim();const sendLabel=chat.busy?'发送中…':active?($('chat-mode').value==='steer'?'立即补充':'排队发送'):'发送消息';$('chat-send').textContent=chat.busy?'…':'发送';$('chat-send').setAttribute('aria-label',sendLabel);$('chat-send').title=sendLabel;$('chat-stop').hidden=!sessionBusy(chat.session);$('chat-stop').disabled=chat.busy;$('chat-mode').disabled=!active||chat.busy;$('chat-attach').disabled=readonly||chat.busy||chat.uploading>0;$('attach-existing').disabled=readonly||chat.busy;$('chat-attach').querySelector('span').textContent=chat.uploading?'上传中…':'附件';const model=$('chat-model').value.trim(),label=friendlyModel(model)||'输入模型 ID';$('chat-model').title=model?friendlyModel(model)+' · '+model:'输入模型 ID';if(!model)$('chat-send').title='请先选择模型';const chatBackend=chatBackendChoice();const speed=$('chat-service-tier').hidden?'':($('chat-service-tier').value==='fast'?' · Fast（请求）':$('chat-service-tier').value==='default'?' · 标准':'');const effort=['opencode','briefloop-native','mimo'].includes(chatBackend)?(($('chat-variant')?.value||'').trim()||'模型默认'):($('chat-effort').value==='none'?'模型默认':$('chat-effort').value);$('composer-help').textContent=`Enter 发送 · Shift + Enter 换行 · ${label}${' / '+effort}${speed}${active?' · 立即补充沿用当前联网与模型设置；更改设置请排队到下一回合':''}${model?'':' · 请先从模型列表选择或输入模型 ID'}`}
function sessionBusy(session){if(!session)return false;if(typeof session.busy==='boolean')return session.busy;return ['starting','running','stopping'].includes(session.status)||!!session.turn_id||(session.id===chat.id&&(chat.messages.some(m=>['queued','sending','delivered','streaming'].includes(m.status))||chat.requests.some(r=>r.status==='pending')))}
function renderSessions(){
 const signature=JSON.stringify([chat.view,chat.id,chat.sessions]);if(renderSessions.signature===signature)return;renderSessions.signature=signature;$('session-view').value=chat.view;
 $('archive-completed').hidden=chat.view!=='active';$('archive-completed').disabled=chat.busy||!chat.sessions.some(s=>!sessionBusy(s));
 const empty={active:'对话会保存在这里。随时回来继续。',archived:'暂无归档对话。',deleted:'回收站为空。',tests:'暂无连接测试；可在设置中测试已配置的模型。'}[chat.view];
 $('session-list').innerHTML=chat.sessions.length?chat.sessions.map(s=>`<div class="session-row"><button class="session-item ${s.id===chat.id?'active':''}" data-chat-session="${esc(s.id)}" ${s.id===chat.id?'aria-current="page"':''}><span class="session-name">${esc(s.title||'新对话')}</span><span class="session-meta"><i class="session-dot ${sessionBusy(s)?'running':''}"></i>${esc(chatStates[s.status]||s.status)}<time>${messageTime(s.updated)}</time></span></button><button type="button" class="session-more" data-manage-session="${esc(s.id)}" aria-label="更多操作：${esc(s.title||'新对话')}" aria-haspopup="menu">⋯</button></div>`).join(''):`<p class="sidebar-empty">${empty}</p>`;
 $('session-list').querySelectorAll('[data-chat-session]').forEach(b=>b.onclick=()=>selectChat(b.dataset.chatSession).catch(e=>chatError(e.message)));
 $('session-list').querySelectorAll('[data-manage-session]').forEach(b=>b.onclick=()=>openSessionMenu(b.dataset.manageSession,b));
 if(!$('session-actions-menu').hidden)renderSessionMenu();
}
function renderAttachments(){
 const find=id=>state?.sources.find(s=>s.id===id);
 $('chat-attachments').innerHTML=[...chat.attachments].map(id=>`<span class="attachment-chip"><button type="button" data-preview-attachment="${esc(id)}" aria-label="查看附件 ${esc(find(id)?.name||id)}">▤ ${esc(find(id)?.name||id)}</button><button type="button" data-remove-attachment="${esc(id)}" aria-label="移除附件 ${esc(find(id)?.name||id)}">×</button></span>`).join('');
 $('chat-attachments').querySelectorAll('[data-preview-attachment]').forEach(button=>button.onclick=()=>action(async()=>showSource(await api('source?id='+encodeURIComponent(button.dataset.previewAttachment)))));
 $('chat-attachments').querySelectorAll('[data-remove-attachment]').forEach(b=>b.onclick=()=>{if(chat.busy)return;chat.attachments.delete(b.dataset.removeAttachment);renderAttachments();rememberDraft();updateComposer()});
 const sources=state?.sources||[];
 $('existing-sources').innerHTML=sources.length?sources.map(s=>`<label><input type="checkbox" data-chat-source="${esc(s.id)}" ${chat.attachments.has(s.id)?'checked':''} ${s.status==='failed'?'disabled':''}><span>${esc(s.name)}</span><small>${s.status==='failed'?'读取失败':s.needs_visual?'需视觉读取':''}</small></label>`).join(''):'<p class="help">还没有已保存来源。可以上传文件，也可以开启联网检索。</p>';
 $('existing-sources').querySelectorAll('[data-chat-source]').forEach(b=>b.onchange=()=>{b.checked?chat.attachments.add(b.dataset.chatSource):chat.attachments.delete(b.dataset.chatSource);renderAttachments();rememberDraft();updateComposer()});
}
function publicActivity(event){
 const data=event.data||{},item=data.item;
 if(item){
  const types={runtime_tool:'调用工具',opencode_tool:'调用工具',commandExecution:'运行命令',fileChange:'更新文件',mcpToolCall:'调用工具',webSearch:'搜索网页',collabAgentToolCall:'子 Agent',imageView:'查看图片',dynamicToolCall:'调用工具'};
  if(!types[item.type])return null;
  const agents=item.agentsStates?Object.entries(item.agentsStates).map(([id,v])=>`${id}: ${typeof v==='string'?v:v.status||''}`).join('\n'):'';
  const detail=[item.command,item.query,item.input,item.output,item.server&&item.tool?`${item.server} / ${item.tool}`:item.tool,agents,item.model?`${item.model}${item.reasoningEffort?' / '+item.reasoningEffort:''}`:''].filter(Boolean).map(v=>typeof v==='string'?v:JSON.stringify(v)).join('\n');
  return {key:`${event.kind.startsWith('child/')?'child:':''}${data.threadId||''}:${item.id||event.seq}`,label:item.type==='runtime_tool'?({view_file:'读取文件或图片',list_dir:'查看文件夹',find_by_name:'查找文件',grep_search:'搜索文件内容',run_command:'运行命令',write_to_file:'写入文件',replace_file_content:'修改文件',search_web:'搜索网页',read_url_content:'读取网页',call_mcp_tool:'调用数据连接器',ask_permission:'请求权限'}[item.tool]||item.tool||types[item.type]):types[item.type],detail,status:item.status||(event.kind.endsWith('completed')?'completed':'running'),seq:event.seq,created:event.created};
 }
 if(event.kind==='runtime/switch')return {key:`runtime:${event.seq}`,label:'已准备切换到 '+runtimeName(data.backend),detail:'此前可见历史已准备，将交给下一回合的新原生会话。',status:'completed',seq:event.seq,created:event.created};
 if(event.kind==='thread/providerChanged')return {key:`provider:${event.seq}`,label:'模型服务已切换',detail:data.message||'下一轮使用新的执行上下文。',status:'completed',seq:event.seq,created:event.created};
 if(event.kind==='error')return {key:`error:${event.seq}`,label:'运行提示',detail:data.message||'请求未完成',status:'failed',seq:event.seq,created:event.created};
 return null;
}
const LIVE_ACTIVITY_STATUSES=['running','inProgress','started','pending'];
function activityEntries(){
 let maxSeq=0;for(const key of chat.events.keys())if(key>maxSeq)maxSeq=key;
 const signature=chat.events.size+':'+maxSeq;if(activityEntries.signature===signature&&activityEntries.map===chat.events)return activityEntries.entries;
 const byItem=new Map();for(const event of chat.events.values()){const entry=publicActivity(event);if(entry)byItem.set(entry.key,entry)}
 const entries=[...byItem.values()].sort((a,b)=>a.seq-b.seq);activityEntries.signature=signature;activityEntries.map=chat.events;activityEntries.entries=entries;return entries;
}
function activeActivityEntries(entries){return entries.filter(e=>LIVE_ACTIVITY_STATUSES.includes(e.status))}
function renderActivities(){
 const entries=activityEntries();const signature=JSON.stringify(entries);if(renderActivities.signature===signature)return;const wasHidden=$('chat-activity').hidden;renderActivities.signature=signature;const opened=new Set([...$('activity-list').querySelectorAll('details[open]')].map(e=>e.dataset.activityKey));$('chat-activity').hidden=!entries.length;
 // Fresh appearance (had no activity yet): start collapsed.
 if(wasHidden&&!$('chat-activity').hidden)$('chat-activity').open=false;
 $('activity-count').textContent=entries.length?String(entries.length):'';
 const active=activeActivityEntries(entries);$('activity-title').textContent=active.length?`正在进行 · ${active.at(-1).label}`:'工具与子 Agent 活动';
 $('activity-list').innerHTML=entries.map(e=>`<details class="activity-item" data-activity-key="${esc(e.key)}" ${opened.has(e.key)?'open':''}><summary><span class="activity-indicator ${['failed','declined','error'].includes(e.status)?'failed':(LIVE_ACTIVITY_STATUSES.includes(e.status)?'running':'done')}"></span><strong>${esc(e.label)}</strong><span>${esc(chatStates[e.status]||({inProgress:'正在执行',started:'正在执行',done:'已完成',success:'已完成',declined:'未执行',error:'未完成'}[e.status])||e.status)}</span><time>${messageTime(e.created)}</time></summary>${e.detail?`<pre>${esc(e.detail)}</pre>`:''}</details>`).join('');
}
function reasoningHTML(message){
 if(message.role!=='assistant'||!message.reasoning)return '';
 const running=['streaming','sending'].includes(message.status);
 const lines=(message.reasoning||'').split('\n').map(line=>line.trim()).filter(Boolean);
 const peek=running?(lines.at(-1)||''):(lines[0]||'');
 return `<details class="message-reasoning" data-running="${running?'1':'0'}"><summary><span class="reasoning-icon" aria-hidden="true">✻</span><strong>${running?'思考中':'思考过程'}</strong>${peek?`<span class="reasoning-peek">${esc(peek)}</span>`:''}</summary><div class="reasoning-body">${esc(message.reasoning)}</div></details>`;
}
function liveStatusText(){const active=activeActivityEntries(activityEntries());const e=active.at(-1);return e?`正在${e.label}…`:'正在回复…'}
function typingHTML(){return `<span class="typing-status"><span class="typing-dots" aria-hidden="true"><i></i><i></i><i></i></span>${esc(liveStatusText())}</span>`}
function runtimeFeedback(messages, events){
 const rows=new Map(),users=messages.filter(m=>m.role==='user'&&m.status!=='queued');
 for(const event of [...events].sort((a,b)=>a.seq-b.seq)){
  if(!['error','runtime/status'].includes(event.kind))continue;
  const data=event.data||{};
  const user=users.find(m=>m.id===data.turnId||m.turn_id===data.turnId)||users.filter(m=>m.created<=event.created).at(-1);
  if(!user)continue;
  const key=user.turn_id||user.id,previous=rows.get(key);
  if(data.status==='resumed'){
   if(previous&&previous.retry)rows.set(key,{...previous,retry:false,resumed:true});
   continue;
  }
  rows.set(key,{key,userId:user.id,message:data.message||data.error?.message||'请求未完成',retry:data.status==='retry',attempt:data.attempt,next:data.next,created:event.created});
 }
 return [...rows.values()].map(row=>{
  const answer=messages.filter(m=>m.role==='assistant'&&m.turn_id===row.key).at(-1);
  const user=messages.find(m=>m.id===row.userId);
  const ended=['completed','failed','cancelled','interrupted'].includes(user?.status);
  return {...row,retry:row.retry&&!ended,resumed:row.resumed||(row.retry&&user?.status==='completed'),anchor:answer?.id||row.userId};
 });
}
function renderRuntimeFeedback(){
 for(const row of runtimeFeedback(chat.messages,chat.events.values())){
  const node=document.createElement('article');node.dataset.messageId='runtime:'+row.key;
  node.className='chat-message from-assistant task-notice message-error';node.setAttribute('role','status');
  const label=row.retry?'模型服务正在重试':row.resumed?'宿主已结束重试':'模型服务提示';
  const retry=row.retry?`第 ${Number.isInteger(row.attempt)?row.attempt:'—'} 次重试${Number.isFinite(row.next)?' · 下次尝试 '+clock(row.next):''}。可以等待，也可以点击停止。`:'';
  node.innerHTML=`<div class="message-heading"><strong>${esc(label)}</strong><span>${messageTime(row.created)}</span></div><div class="message-body">${esc(row.message)}</div>${retry?`<p class="help">${esc(retry)}</p>`:''}`;
  const anchor=[...$('chat-messages').children].find(n=>n.dataset.messageId===row.anchor);
  if(anchor)anchor.after(node);else $('chat-messages').append(node);
 }
}
function renderMessages(){
 const scroll=$('chat-scroll'),nearEnd=scroll.scrollHeight-scroll.scrollTop-scroll.clientHeight<140;
 let liveSeq=0;for(const key of chat.events.keys())if(key>liveSeq)liveSeq=key;
 const signature=JSON.stringify(chat.messages)+'|'+liveSeq;if(signature!==renderMessages.signature){renderMessages.signature=signature;
 const nodes=new Map([...$('chat-messages').children].map(n=>[n.dataset.messageId,n]));
 for(const message of chat.messages){
  let node=nodes.get(message.id);if(!node){node=document.createElement('article');node.dataset.messageId=message.id;$('chat-messages').append(node)}nodes.delete(message.id);
  const streaming=['streaming','sending'].includes(message.status);const messageSignature=JSON.stringify(message)+(streaming?('|'+liveSeq):'');if(node.dataset.signature===messageSignature)continue;node.dataset.signature=messageSignature;node.className=`chat-message ${message.role==='user'?'from-user':'from-assistant'} ${message.mode==='notice'?'task-notice':''} ${['failed','interrupted','cancelled'].includes(message.status)?'message-error':''}`;
  const reasoningOpen=!!node.querySelector('.message-reasoning')?.open;
  const files=(message.source_ids||[]).map(id=>({id,name:state?.sources.find(s=>s.id===id)?.name||id}));
  const label=message.role==='user'?'你':(message.mode==='notice'?'任务状态':'BriefLoop');
  node.innerHTML=`<div class="message-heading"><strong>${label}</strong><span>${messageTime(message.created)}</span><span class="message-state">${esc(chatStates[message.status]||message.status)}${message.mode==='steer'&&message.role==='user'?' · 中途补充':''}</span></div>${reasoningHTML(message)}<div class="message-body">${message.text?esc(message.text):(streaming?typingHTML():'')}</div>${files.length?`<div class="message-files">${files.map(file=>`<button type="button" data-message-source="${esc(file.id)}">▤ ${esc(file.name)}</button>`).join('')}</div>`:''}${messageActionsHTML()}`;
  const reasoning=node.querySelector('.message-reasoning');if(reasoning&&reasoningOpen)reasoning.open=true;
  const reqBlock=/```briefloop-requirements\s*([\s\S]*?)```/.exec(message.text||'');if(reqBlock){const apply=document.createElement('button');apply.type='button';apply.className='outline apply-requirements';apply.textContent='应用到材料与需求';apply.onclick=()=>applyRequirements(reqBlock[1].trim());node.append(apply)}
  workspaceProposal(node,message.text);
  bindMessageActions(node,message);
  node.querySelectorAll('[data-message-source]').forEach(button=>button.onclick=()=>action(async()=>showSource(await api('source?id='+encodeURIComponent(button.dataset.messageSource)))));
  if(message.role==='assistant'&&message.status==='completed'&&message.text&&message.mode!=='notice'){api('render',{markdown:message.text}).then(result=>{if(node.isConnected&&node.dataset.signature===messageSignature){node.querySelector('.message-body').innerHTML=result.html;node.querySelector('.message-body').classList.add('rendered-markdown');if(nearEnd)scroll.scrollTop=scroll.scrollHeight}}).catch(()=>{})}
 }
 for(const node of nodes.values())node.remove();renderRuntimeFeedback();if(nearEnd)scroll.scrollTop=scroll.scrollHeight;
 }
}
function homeGreeting(){
 const name=state?.profile?.name,h=new Date().getHours();
 const part=h<6?'凌晨好':h<12?'早上好':h<14?'中午好':h<18?'下午好':'晚上好';
 return name?`${part}，${name}`:part;
}
function homeRecentReports(){
 const seen=new Set(),rows=[];
 for(const b of (state?.briefs||[])){if(seen.has(b.run_id))continue;seen.add(b.run_id);rows.push(b);if(rows.length>=3)break}
 return rows;
}
const scheduledReports=scheduleUI({api,getState:()=>state,refresh:async()=>{await refresh();await refresh();},notice,openReport:id=>{const brief=state.briefs.find(b=>b.id===id);if(brief&&openBrief(brief,{follow:false}))page('report')}});
function svgLineIcon(name,size=18){
 const body=ICONS[name]||ICONS.file||'';
 return `<svg viewBox="0 0 24 24" width="${size}" height="${size}" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${body}</svg>`;
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
 const jobs=(state.jobs||[]).filter(j=>taskLabel(j.kind)&&['queued','running'].includes(j.status));
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
  jobList.querySelectorAll('[data-rail-open-job]').forEach(b=>b.onclick=()=>openTask(jobFor(b.dataset.railOpenJob)));
 }
 // Keep rail recent in sync only when rail is shown with reports too (jobs-only rail may omit recent)
 const recentBlock=$('home-rail-recent'),recentBox=$('home-rail-recent-list');
 if(recentBlock&&recentBox){
  recentBlock.hidden=true;
  recentBox.innerHTML='';
 }
}
function reportIconMeta(b){
 const run=(state.runs||[]).find(r=>r.id===b.run_id),req=run?parse(run.requirements):{};
 const method=req.workflow_snapshot?.id||req.document_workflow?.id;
 const family={business_report:'商业报告',stock_research:'券商研报',meeting_minutes:'会议纪要',general_report:'通用报告'}[method];
 const template=(state.templates||[]).find(t=>t.id===req.template_id);
 return GENRE_META[family||splitTemplateName(template?.name||'').genre]||{icon:'layers',cat:'cat-neutral'};
}
function homeReportRowHTML(b){
 const st=reportStatus(b),desc=reportDescription(b);
 const when=dayTime(b.updated||b.created);
 const meta=reportIconMeta(b);
 return `<button type="button" class="home-report" data-home-report="${esc(b.id)}"><span class="home-report-icon ${meta.cat}" aria-hidden="true">${svgLineIcon(meta.icon,16)}</span><span class="home-report-body"><strong>${esc(parse(b.detail).title||'简报')}</strong>${desc?`<small>${esc(desc)}</small>`:''}</span><span class="home-report-meta"><time>${esc(when)}</time><span class="chip ${st.cls}">${esc(st.label)}</span></span></button>`;
}
function openHomeReport(id){
 const b=(state.briefs||[]).find(x=>x.id===id);
 if(b&&openBrief(b,{follow:false}))page('report');
}
let autoOpenedActivityTurn=null;
function autoOpenActivity(){
 // Each new streaming turn starts with the activity panel collapsed (DESIGN default).
 const activity=$('chat-activity');if(!activity)return;
 const streaming=chat.messages.find(m=>['streaming','sending'].includes(m.status));if(!streaming)return;
 const turn=streaming.turn_id||streaming.id;
 if(autoOpenedActivityTurn===turn)return;
 autoOpenedActivityTurn=turn;
 activity.open=false;
}
function renderChat(){
 const empty=chat.home||(chat.messages.length===0&&!chatActive());
 $('chat').classList.toggle('is-empty',empty);
 if($('chat-home-top'))$('chat-home-top').hidden=!empty;
 if($('chat-home-bottom'))$('chat-home-bottom').hidden=!empty;
 if(empty)renderHome();
 renderHomeTasks();
 $('chat-title').textContent=chat.session?.title||'新对话';const runtime=chat.session?.runtime;const pending=chat.messages.filter(m=>m.role==='user'&&m.status==='queued').length;
 $('chat-status').textContent=`${chatStates[chat.session?.status]||'准备就绪'}${runtime?' · '+modelLabel({model:runtime.model,reasoning_effort:runtime.effort,model_provider:runtime.model_provider}):''}${pending?' · '+pending+' 条消息排队中':''}`;
 renderMessages();renderActivities();autoOpenActivity();renderRequests();renderContext();renderSessions();renderSessionLifecycle();updateComposer();
}
async function selectChat(id){
 if(chat.busy||chat.uploading)return;if(id===chat.id){chat.home=false;renderChat();page('chat');return}rememberDraft();chat.home=false;chat.id=id;chat.session=chat.sessions.find(s=>s.id===id)||null;chat.tokenUsage=null;chat.messages=[];chat.requests=[];chat.events=new Map();chat.after=0;chat.request=null;renderMessages.signature='';localStorage.setItem('briefloop-chat-session',id);chatError();restoreDraft();renderChat();page('chat');await pollChat(true);if(chat.id===id&&!chat.drafts.has(id))restoreDraft();
}
async function openChatHome({resetDraft=false}={}){
 if(chat.busy||chat.uploading)return;
 rememberDraft();chat.home=true;chat.view='active';$('session-view').value='active';chat.sessions=[];chat.id=null;chat.session=null;chat.tokenUsage=null;chat.messages=[];chat.requests=[];chat.events=new Map();chat.after=0;chat.request=null;renderMessages.signature='';
 // Navigation restores the pending home composer; only an explicit new chat resets it.
 if(resetDraft)chat.drafts.delete('new');
 localStorage.removeItem('briefloop-chat-session');restoreDraft({preserveNewDraft:!resetDraft});rememberDraft();chatError();renderChat();page('chat');$('chat-input').focus();await pollChat(true).catch(e=>chatError(e.message));
}
async function newChat(){await openChatHome({resetDraft:true})}
async function showHome(){if(chat.busy||chat.uploading){page('chat');return}await openChatHome()}
async function pollChat(force=false){
 if(chat.pollPromise||chat.pollQueued){
  if(!force)return;
  // Mutations and navigation supersede the in-flight snapshot; coalesce their refreshes.
  chat.pollTicket=(chat.pollTicket||0)+1;
  if(!chat.pollQueued)chat.pollQueued=chat.pollPromise.catch(()=>{}).then(()=>{chat.pollQueued=null;return pollChat(true)});
  return chat.pollQueued;
 }
 const ticket=chat.pollTicket=(chat.pollTicket||0)+1,sid=chat.id,after=chat.after,view=chat.view,events=chat.events;
 const relevant=()=>ticket===chat.pollTicket&&sid===chat.id&&events===chat.events;
 chat.polling=true;
 const pending=Promise.resolve().then(async()=>{
  try{
   // Wait for both reads, even if one fails, before starting a queued refresh.
   const results=await Promise.allSettled([api('harness/sessions?view='+view),sid?api(`harness/session?id=${encodeURIComponent(sid)}&after=${after}&reasoning=1`):Promise.resolve(null)]);
   if(!relevant())return;
   const failure=results.find(result=>result.status==='rejected');if(failure)throw failure.reason;
   const [list,snapshot]=results.map(result=>result.value);
   if(view===chat.view)chat.sessions=list.sessions||[];
   if(snapshot){chat.session=snapshot.session;chat.tokenUsage=snapshot.token_usage||null;chat.messages=snapshot.messages||[];chat.requests=snapshot.requests||[];for(const event of snapshot.events||[]){chat.events.set(event.seq,event);chat.after=Math.max(chat.after,event.seq)}}renderChat();
  }catch(e){if(!relevant())return;if(force)throw e;else if(!$('chat').hidden){if(sessionMissing(e)){chat.id=null;chat.session=null;chat.messages=[];localStorage.removeItem('briefloop-chat-session');chatError();renderChat()}else{$('chat-status').textContent='会话连接中断，正在重连';chatError(e.message)}}}
 }).finally(()=>{if(chat.pollPromise===pending){chat.pollPromise=null;chat.polling=false}});
 chat.pollPromise=pending;return pending;
}
async function sendChat(event){
 event.preventDefault();if(chat.busy||chat.uploading||chat.session&&chat.session.lifecycle&&chat.session.lifecycle!=='active')return;const rawInput=$('chat-input').value.trim();const command=/^\/(\w+)(?:\s+([\s\S]*))?$/.exec(rawInput);if(command){const name=command[1].toLowerCase();if(name==='new'){const panel=commandPanel();if(panel)panel.hidden=true;await newChat();return}if(name==='help'){notice(COMMAND_HELP);$('chat-input').value='';const panel=commandPanel();if(panel)panel.hidden=true;updateComposer();return}}const text=rawInput||(chat.attachments.size?'请查看附件。':'');if(!text)return;const discuss=/^\/discuss\b\s*/i.test(text),displayText=text.replace(/^\/discuss\b\s*/i,'').trim()||'讨论需求',sendText=discuss?(DISCUSS_INSTRUCTION+(displayText!=='讨论需求'?('\n\n用户补充：'+displayText):'')):text;chat.home=false;chat.busy=true;chatError();updateComposer();
 try{
  const runtime=runtimeChoice();if(!chat.id){const result=await api('harness/session',{title:displayText.slice(0,48),runtime});chat.session=result.session||result;chat.id=chat.session.id;if(!chat.id)throw Error('未能创建会话');localStorage.setItem('briefloop-chat-session',chat.id);chat.drafts.delete('new');rememberDraft()}
  const reportOptions=compactReportInstruction();
  const payload={session_id:chat.id,text:sendText+reportOptions,display_text:reportOptions||sendText!==displayText?displayText:undefined,mode:chatActive()?$('chat-mode').value:'queue',source_ids:[...chat.attachments],runtime,allow_web:$('chat-allow-web').checked};const signature=JSON.stringify(payload);
  if(!chat.request||chat.request.signature!==signature)chat.request={signature,message_id:crypto.randomUUID()};
  await api('harness/message',{...payload,message_id:chat.request.message_id});chat.request=null;$('chat-input').value='';chat.attachments.clear();rememberDraft();renderAttachments();
  // The runtime used here is the chosen model; keep the pending-selection state in sync.
  // Per-turn choice does not overwrite the new-conversation default.
  state.settings={...state.settings,model_selection_required:false};
  await pollChat(true);
 }catch(e){rememberDraft();chatError(e.message+'。消息仍保留在输入框中，可修改或再次发送。')}finally{chat.busy=false;updateComposer();$('chat-input').focus()}
}
$('chat-form').onsubmit=sendChat;
$('new-session').onclick=newChat;if($('nav-home'))$('nav-home').onclick=showHome;
$('chat-input').oninput=()=>{rememberDraft();updateComposer();renderCommands()};
$('chat-input').onkeydown=e=>{
 const panel=commandPanel();
 if(panel&&!panel.hidden){
  const items=panel._items||[];
  if(e.key==='ArrowDown'&&items.length){e.preventDefault();commandIndex=(commandIndex+1)%items.length;renderCommands();return}
  if(e.key==='ArrowUp'&&items.length){e.preventDefault();commandIndex=(commandIndex-1+items.length)%items.length;renderCommands();return}
  if(e.key==='Tab'&&items.length){e.preventDefault();acceptCommand();return}
  if(e.key==='Escape'){e.preventDefault();panel.hidden=true;return}
  if(e.key==='Enter'&&!e.shiftKey&&!e.isComposing&&items.length){
   const exact=items.some(c=>('/'+c.name)===e.target.value.trim().toLowerCase());
   if(!exact){e.preventDefault();acceptCommand();return}
  }
 }
 if(e.key==='Enter'&&!e.shiftKey&&!e.isComposing){e.preventDefault();if(!$('chat-send').disabled)$('chat-form').requestSubmit()}
};
$('chat-mode').onchange=updateComposer;
for(const id of ['chat-model','chat-effort','chat-variant','chat-model-provider','chat-service-tier'])$(id).onchange=()=>{if(id==='chat-effort')$('chat-model').value=reasoningModel(chatBackendChoice(),$('chat-model').value,$('chat-effort').value);reasoning.refresh();rememberDraft();chatError();updateComposer()};
$('chat-stop').onclick=async()=>{if(!chat.id||chat.busy)return;chat.busy=true;updateComposer();try{await api('harness/cancel',{session_id:chat.id});await pollChat(true)}catch(e){chatError(e.message)}finally{chat.busy=false;updateComposer()}};
$('chat-attach').onclick=()=>$('chat-upload').click();
$('attach-existing').onclick=()=>{const show=$('existing-sources').hidden;$('existing-sources').hidden=!show;$('attach-existing').setAttribute('aria-expanded',String(show));renderAttachments()};
async function uploadChatFiles(files){
 const incoming=[...(files||[])].filter(Boolean);if(!incoming.length)return;
 if(chat.busy){chatError('消息正在发送，请发送完成后重新添加附件。');return}
 if(chat.session?.lifecycle&&chat.session.lifecycle!=='active'){chatError('请先恢复这条对话，再添加附件。');return}
 chat.uploading++;chatError();updateComposer();
 const chatBackend=chatBackendChoice();
 const canImages=((runtimeCatalog||[]).find(r=>r.id===chatBackend)||{}).capabilities?.images!==false;
 try{preflightSources(incoming,getUploadLimits());for(const raw of incoming){
   const suffix=({ 'image/png':'.png','image/jpeg':'.jpg','image/webp':'.webp' })[raw.type]||'';
   const file=raw.name?raw:new File([raw],`粘贴内容-${Date.now()}${suffix}`,{type:raw.type||'application/octet-stream'});
   // A host that cannot take images must say so at attach time; the turn would
   // otherwise fail after the whole message was queued.
   if(file.type.startsWith('image/')&&!canImages){chatError(`${file.name}：${runtimeName(chatBackend)} 不支持直接读图；请改用支持读图的宿主，或先转成文字材料。`);continue}
   const source=await uploadSource(file);if(source.status==='failed'){chatError(`${file.name} 读取失败：${source.error||'请检查文件后重试'}`);continue}chat.attachments.add(source.id);selected.add(source.id)}await refresh();renderAttachments();rememberDraft()}catch(e){chatError('上传未完成：'+e.message)}finally{chat.uploading--;updateComposer()}
}
$('chat-upload').onchange=event=>{const input=event.target,files=[...input.files];input.value='';uploadChatFiles(files)};
$('chat-input').addEventListener('paste',event=>{
 const pasted=[...(event.clipboardData?.items||[])].filter(item=>item.kind==='file').map(item=>item.getAsFile()).filter(Boolean);
 if(!pasted.length)return;event.preventDefault();uploadChatFiles(pasted);
});
const chatComposer=$('chat-form');
const droppingFiles=event=>[...(event.dataTransfer?.types||[])].includes('Files');
for(const name of ['dragenter','dragover'])chatComposer.addEventListener(name,event=>{if(droppingFiles(event)){event.preventDefault();chatComposer.classList.add('drag-over')}});
for(const name of ['dragleave','dragend'])chatComposer.addEventListener(name,()=>chatComposer.classList.remove('drag-over'));
chatComposer.addEventListener('drop',event=>{const files=[...(event.dataTransfer?.files||[])];chatComposer.classList.remove('drag-over');if(!files.length)return;event.preventDefault();uploadChatFiles(files)});
document.querySelectorAll('[data-prompt]').forEach(b=>b.onclick=()=>{$('chat-input').value=b.dataset.prompt;rememberDraft();updateComposer();$('chat-input').focus()});
async function initChat(signal){
 const list=await api('harness/sessions?view=active');if(signal?.aborted)return false;
 const liveHomeChoice=!!chat.drafts.get('new')?.model;
 // Input typed during a connection retry takes precedence over the stored snapshot.
 try{const saved=JSON.parse(sessionStorage.getItem('briefloop-chat-drafts')||'[]');if(Array.isArray(saved))chat.drafts=new Map([...saved,...chat.drafts])}catch{}
 chat.id=localStorage.getItem('briefloop-chat-session')||null;chat.sessions=list.sessions||[];
 const recoverable=!!chat.id&&chat.sessions.some(s=>s.id===chat.id);
 const settings=state&&state.settings;
 const fresh=chat.sessions.length===0&&!((state&&state.runs)||[]).length&&!((state&&state.briefs)||[]).length;
 const needsModel=!!(settings&&settings.model_selection_required);
 const running=!!((state&&state.jobs)||[]).some(j=>['queued','running'].includes(j.status));
 if(state?.demo&&needsModel&&!running){
  $('welcome').hidden=true;openBrief(state.briefs.find(b=>b.run_id===state.demo.run_id));page('report');
 }else if(!recoverable&&!running&&(fresh||needsModel)){
  // Cold start: no conversation to recover, no host/model chosen, nothing running.
  chat.id=null;localStorage.removeItem('briefloop-chat-session');page('welcome');renderWelcome();
 }else{
  // Home always opens on the landing; conversations are opened from the sidebar.
  chat.home=true;chat.id=null;chat.session=null;chat.messages=[];chat.requests=[];chat.events=new Map();chat.after=0;chat.request=null;renderMessages.signature='';localStorage.removeItem('briefloop-chat-session');
  page('chat');restoreDraft({preserveNewDraft:liveHomeChoice});renderChat();
 }
 renderSessions();
 return true;
}

$('chat-allow-web').onchange=rememberDraft;
let permissionPanel=null,permissionLoad=0;
$('chat-permissions-refresh').onclick=loadPermissionPanel;
$('chat-permissions-close').onclick=()=>$('chat-permissions-dialog').close();
$('chat-permissions-open').onclick=async()=>{const dialog=$('chat-permissions-dialog');if(!dialog.open)dialog.showModal();renderPermissionRequests();await loadPermissionPanel()};
$('settings-chat-web-default').onchange=async()=>{
 const value=$('settings-chat-web-default').checked;
 $('settings-chat-web-default').disabled=true;
 try{await api('settings',{chat_allow_web:value});state.settings.chat_allow_web=value;$('settings-chat-web-status').textContent='已保存，用于之后的新对话。'}
 catch(e){$('settings-chat-web-default').checked=state.settings.chat_allow_web!==false;$('settings-chat-web-status').textContent=e.message}
 finally{$('settings-chat-web-default').disabled=false}
};
$('settings-officecli').onchange=async()=>{
 const value=$('settings-officecli').checked;
 $('settings-officecli').disabled=true;
 try{await api('settings',{officecli_enabled:value});state.settings.officecli_enabled=value;state.office={...(state.office||{}),enabled:value};$('settings-officecli-status').textContent='已保存。'+(value?'导出 Word 后将运行本地质检；质检失败只记录结果，不影响导出。':'导出与预览不再运行 OfficeCLI 质检。')}
 catch(e){$('settings-officecli').checked=state.settings.officecli_enabled===true;$('settings-officecli-status').textContent=e.message}
 finally{$('settings-officecli').disabled=false}
};
function renderPermissionRequests(){
 const pending=chat.requests.filter(r=>r.status==='pending'&&(r.data?.native_options||String(r.method||'').includes('Approval')));
 $('chat-permissions-open').textContent='权限'+(pending.length?' · '+pending.length:'');
 if(!$('chat-permissions-dialog').open)return;
 const box=$('chat-permissions-pending'),signature=JSON.stringify([chat.id,pending]);
 if(box.dataset.signature===signature)return;box.dataset.signature=signature;
 box.innerHTML=pending.length?'<h3>等待你确认的操作</h3>':chatBackendChoice()==='antigravity'?'<p class="help">Antigravity 当前接口不能弹出逐次授权。被拒绝的操作需先在这里明确授权，再重新发送任务。</p>':'<p class="help">当前没有待确认的操作。宿主发起授权请求后会显示在这里。</p>';
 for(const request of pending){
  const section=document.createElement('section');section.className='permission-request';
  const title=document.createElement('p');title.textContent=request.data.questions?.[0]?.question||'宿主请求授权';section.append(title);
  for(const [index,option] of (request.data.questions?.[0]?.options||[]).entries()){
   const b=document.createElement('button');b.type='button';b.textContent=option.label;b.onclick=async()=>{
    section.querySelectorAll('button').forEach(x=>x.disabled=true);
    try{const q=request.data.questions[0];await api('harness/answer',{session_id:chat.id,request_id:request.id,answers:{[q.id]:{answers:[request.data.native_options?.[index]?.optionId||option.label]}}});await pollChat(true)}catch(e){$('chat-permissions-error').textContent=e.message;section.querySelectorAll('button').forEach(x=>x.disabled=false)}
   };section.append(b);
  }
  box.append(section);
 }
}
async function loadPermissionPanel(){
 const backend=chatBackendChoice(),token=++permissionLoad;
 permissionPanel=null;$('chat-permissions-host').textContent=runtimeName(backend)+' · 下一回合';$('chat-permissions-error').textContent='';$('chat-permissions-options').textContent='正在读取宿主权限…';$('chat-permissions-note').textContent='';$('chat-permissions-native').hidden=true;
 try{
  const p=await api('runtime/permissions?backend='+encodeURIComponent(backend));if(token!==permissionLoad||backend!==chatBackendChoice())return;
  permissionPanel=p;$('chat-permissions-note').textContent=p.note;$('chat-permissions-error').textContent=p.diagnostic||'';
  const box=$('chat-permissions-options');box.replaceChildren();$('chat-permissions-native').hidden=p.kind!=='native';
  if(p.kind==='native')return;
  if(p.kind!=='rules'){
   const label=document.createElement('label');label.htmlFor='chat-host-permission-mode';label.textContent='下一回合权限';
   const select=document.createElement('select');select.id='chat-host-permission-mode';
   for(const mode of p.modes)select.add(new Option(mode.name,mode.id));const selected=chat.hostOptions?.mode||p.default_mode||'native';select.value=p.modes.some(mode=>mode.id===selected)?selected:(p.default_mode||'native');
   if(!select.value){select.value='native';$('chat-permissions-error').textContent='原权限模式已不在宿主列表中，请重新选择后再发送。'}
   select.disabled=$('chat-permission').disabled||[...chat.events.values()].some(e=>e.kind==='session/internal');
   select.onchange=()=>{chat.hostOptions={mode:select.value};rememberDraft();$('chat-permissions-error').textContent='已选择，下一回合生效。'};
   box.append(label,select);return;
  }
  const presets=document.createElement('select'),presetLabel=document.createElement('label'),presetHelp=document.createElement('p'),applyPreset=document.createElement('button');
  presets.id='antigravity-preset';presetLabel.htmlFor=presets.id;presetLabel.textContent='权限模式';
  for(const [id,name] of [['default','默认'],['full-machine','全机访问'],['turbo','Turbo'],['custom','自定义']])presets.add(new Option(name,id));
  presets.value=p.preset||'custom';presetHelp.className='help';applyPreset.type='button';applyPreset.textContent='应用权限模式';
  const custom=document.createElement('div');
  const describe=()=>{presetHelp.textContent=({default:'工作区内读写；命令与工作区外访问若需授权，会停止并提示你授权后重发。','full-machine':'允许访问全机文件；命令若需授权，会停止并提示你授权后重发。',turbo:'自动执行文件、命令等操作；已有拒绝、询问规则及沙箱限制继续有效。',custom:'按具体文件、命令或网址管理规则。'})[presets.value];custom.hidden=presets.value!=='custom';applyPreset.hidden=presets.value==='custom';};
  presets.onchange=describe;applyPreset.onclick=()=>saveNativeRule({operation:'preset',preset:presets.value});
  box.append(presetLabel,presets,presetHelp,applyPreset,custom);describe();
  const list=document.createElement('div');list.className='permission-rule-list';
  for(const row of p.rules){
   const line=document.createElement('div'),text=document.createElement('code'),remove=document.createElement('button');
   text.textContent=({allow:'允许',ask:'询问',deny:'拒绝'}[row.decision])+' · '+row.rule;
   remove.type='button';remove.textContent='移除';remove.setAttribute('aria-label','移除 '+text.textContent);
   remove.onclick=()=>saveNativeRule({operation:'remove',decision:row.decision,rule:row.rule});line.append(text,remove);list.append(line);
  }
  custom.append(list);
  const denied=[...chat.events.values()].reverse().map(e=>e.data?.item).find(item=>item?.type==='runtime_tool'&&item.status==='failed'&&typeof item.output==='string'&&item.output.includes('user denied permission for '));
  const match=denied?.output.match(/user denied permission for ((?:read_file|write_file|command|read_url|execute_url|mcp)\([^\r\n()]+\))/);
  if(match){
   const recovery=document.createElement('section'),description=document.createElement('p'),approve=document.createElement('button');
   description.textContent='上次未完成的操作：'+denied.tool+'。授权范围：'+match[1];
   approve.type='button';approve.textContent='允许此操作并保存授权';
   approve.onclick=()=>saveNativeRule({operation:'add',decision:'allow',rule:match[1]});
   recovery.append(description,approve);custom.append(recovery);
  }
  const form=document.createElement('form');form.id='permission-rule-form';form.innerHTML='<h3>授权具体操作</h3><label for="permission-rule-decision">处理方式</label><select id="permission-rule-decision"><option value="allow">允许所选操作</option><option value="deny">拒绝</option></select><label for="permission-rule-tool">操作</label><select id="permission-rule-tool"><option value="read_file">读取文件</option><option value="write_file">写入文件</option><option value="command">执行命令</option><option value="read_url">读取网址</option><option value="execute_url">执行网址操作</option><option value="mcp">MCP 工具</option></select><label for="permission-rule-scope">范围</label><input id="permission-rule-scope" required autocomplete="off" placeholder="填写要访问的完整文件路径或网址"><p class="help">此授权会保存到本机 Antigravity，也会影响其他会话。只填写你愿意授权的具体路径；可在上方撤销。</p><button type="submit">保存规则</button>';
  form.onsubmit=event=>{event.preventDefault();saveNativeRule({operation:'add',decision:$('permission-rule-decision').value,rule:$('permission-rule-tool').value+'('+$('permission-rule-scope').value.trim()+')'})};custom.append(form);
 }catch(e){if(token===permissionLoad){$('chat-permissions-options').textContent='';$('chat-permissions-error').textContent=e.message}}
}
async function saveNativeRule(change){
 const p=permissionPanel;if(!p||p.backend!==chatBackendChoice())return;
 const controls=[...$('chat-permissions-options').querySelectorAll('button,input,select')];controls.forEach(e=>e.disabled=true);
 try{await api('runtime/permissions',{backend:p.backend,revision:p.revision,...change});await loadPermissionPanel();$('chat-permissions-error').textContent='规则已保存。之后发送的任务使用新规则。'}
 catch(e){$('chat-permissions-error').textContent=e.message;controls.forEach(e=>e.disabled=false)}
}

function renderRequests(){
 renderPermissionRequests();
 const requests=chat.requests.filter(r=>r.status==='pending'),signature=JSON.stringify(requests);$('chat-requests').hidden=!requests.length;
 if(renderRequests.signature===signature)return;renderRequests.signature=signature;
 $('chat-requests').innerHTML=requests.map(request=>`<form class="agent-question" data-request-id="${esc(request.id)}"><strong>BriefLoop 需要你的补充</strong>${(request.data?.questions||[]).map((q,i)=>`<fieldset data-question-index="${i}"><legend>${esc(q.question||q.header||'请补充')}</legend>${(q.options||[]).map((option,j)=>`<label class="question-option"><input type="radio" name="answer-${i}" value="${j}"><span><b>${esc(option.label)}</b>${option.description?`<small>${esc(option.description)}</small>`:''}</span></label>`).join('')}<input class="question-text" name="text-${i}" aria-label="${esc(q.question||'补充说明')}" placeholder="${q.options?.length?'或直接输入你的回答':'输入回答'}"></fieldset>`).join('')}<button class="primary" type="submit">提交回答</button><span class="question-error" role="alert"></span></form>`).join('');
 $('chat-requests').querySelectorAll('form').forEach(form=>form.onsubmit=async event=>{
  event.preventDefault();const request=requests.find(r=>r.id===form.dataset.requestId),answers={};
  for(const [i,q] of (request.data?.questions||[]).entries()){const custom=form.elements[`text-${i}`].value.trim(),option=form.querySelector(`input[name="answer-${i}"]:checked`);const value=custom||(option?(request.data?.native_options?.[Number(option.value)]?.optionId||q.options[Number(option.value)].label):'');if(!value){form.querySelector('.question-error').textContent='请回答每一个问题后提交。';return}answers[q.id]={answers:[value]}}
  const button=form.querySelector('button');button.disabled=true;button.textContent='提交中…';form.querySelector('.question-error').textContent='';
  try{await api('harness/answer',{session_id:chat.id,request_id:request.id,answers});await pollChat(true)}catch(e){form.querySelector('.question-error').textContent=e.message}finally{button.disabled=false;button.textContent='提交回答'}
 });
}

function serviceTierOptions(value){return [['','速度：沿用本机'],['default','速度：标准'],['fast','速度：Fast']].map(([id,label])=>`<option value="${id}" ${id===(value||'')?'selected':''}>${label}</option>`).join('')}
function renderRoleModels(){
 const roles=[['evaluator','Evaluator','给产物评分；比较候选产物，判断是否改善'],['maintainer','Wiki Maintainer','将反馈和执行经验整理成 Wiki'],['proposer','Skill Proposer','根据 Wiki 提出或改进 Skill']];
  $('role-model-options').innerHTML=roles.map(([role,name,label])=>{const configured=state.settings.role_models||{},value=(role==='evaluator'?(configured.evaluator||configured.scorer||configured.assessor):configured[role])||{},inherits=!value.model,effort=inherits?settingsEffort(state.settings,backendValue()):(backendValue()==='codex'?effortValue(value,'reasoning_effort'):(value.reasoning_effort||'none'));return `<div class="role-model-row"><span><b>${name}</b><small>${label}</small></span><div class="role-runtime-fields"><div class="role-model-pair"><input id="role-${role}-model" aria-label="${name} 模型 ID" data-role-model="${role}" list="model-suggestions" autocomplete="off" spellcheck="false" value="${esc(value.model||'')}" placeholder="留空继承主链模型" title="${esc(value.model?friendlyModel(value.model):'继承主链模型')}"><select id="role-${role}-effort" class="role-effort-select" aria-label="${name} 推理档位" data-role-effort="${role}" ${inherits?'disabled':''}>${[...new Set(['none','low','medium','high','xhigh','max',effort])].map(e=>`<option value="${e}" ${e===effort?'selected':''}>${e==='none'?'模型默认':e}</option>`).join('')}</select><select id="role-${role}-service-tier" aria-label="${name} 速度" hidden ${inherits?'disabled':''}>${serviceTierOptions(value.service_tier)}</select></div><label class="role-provider-field"><span>Provider</span><input id="role-${role}-provider" aria-label="${name} Codex provider" value="${esc(value.model_provider||'')}" placeholder="${inherits?'继承主链全部配置':'留空沿用本机 Codex 配置'}" autocomplete="off" spellcheck="false" ${inherits?'disabled':''}></label><label class="role-variant-field" hidden><span>推理强度</span><input id="role-${role}-variant" aria-label="${name} 推理档位（Opencode variant）" data-role-variant="${role}" list="effort-suggestions" value="${esc((backendValue()==='mimo'?value.reasoning_effort:value.model_variant)||'')}" placeholder="模型默认，如 high / max" autocomplete="off" spellcheck="false" ${inherits?'disabled':''}></label></div></div>`}).join('');
  $('role-model-options').querySelectorAll('input,select').forEach(input=>input.onchange=()=>{if(input.dataset.roleModel){reasoning.refresh();renderBackend()}return saveRoleModels()});renderBackend();
 setupModelPickers($('role-model-options'));
}
async function saveRoleModels(){
  await Promise.all(['evaluator','maintainer','proposer'].map(role=>{const cfg=fastControlConfig('role-'+role);return cfg.backend==='codex'&&cfg.model?loadFastCapability(cfg):null}));
  const op=['opencode','briefloop-native'].includes(backendValue());const role_models={};for(const input of $('role-model-options').querySelectorAll('[data-role-model]')){const role=input.dataset.roleModel,effortValue=$(`role-${role}-effort`).value,model=reasoningModel(backendValue(),input.value.trim(),effortValue),effort=$(`role-${role}-effort`),provider=$(`role-${role}-provider`),variant=$(`role-${role}-variant`);effort.disabled=!model;provider.disabled=!model;variant.disabled=!model;if(model)role_models[role]=op?{model,model_variant:variant.value.trim()||null}:{model,reasoning_effort:backendValue()==='mimo'?(variant.value.trim()||null):effort.value,model_provider:provider.value.trim()||null,service_tier:selectedServiceTier(`role-${role}-service-tier`,fastControlConfig('role-'+role),true)}}
  const controls=[...$('role-model-options').querySelectorAll('input,select')];controls.forEach(input=>input.disabled=true);$('role-model-status').textContent='保存中…';
  try{await api('settings',{role_models});state.settings.role_models=role_models;$('role-model-status').textContent='已保存，下一次启动时生效。'}catch(e){$('role-model-status').textContent='未保存：'+e.message;renderRoleModels()}finally{for(const input of $('role-model-options').querySelectorAll('[data-role-model]')){input.disabled=false;const inherits=!input.value.trim();$(`role-${input.dataset.roleModel}-effort`).disabled=inherits;$(`role-${input.dataset.roleModel}-provider`).disabled=inherits;$(`role-${input.dataset.roleModel}-variant`).disabled=inherits;$(`role-${input.dataset.roleModel}-service-tier`).disabled=inherits}renderBackend()}
}

function workspaceProposal(node,text){
 const wsBlock=/```briefloop-workspace\s*([\s\S]*?)```/.exec(text||'');if(!wsBlock)return;
 let spec=null;try{spec=JSON.parse(wsBlock[1].trim())}catch{}
 const name=String(spec?.name||'').trim();if(!name)return;
 const button=document.createElement('button');button.type='button';button.className='outline workspace-proposal';button.textContent='新建并切换工作区：'+name;
 button.onclick=async()=>{
  try{
   const inventory=workspaceInventory&&workspaceInventory.current?workspaceInventory:await api('workspaces');workspaceInventory=inventory;
   const base=String(inventory.current?.path||'');const target=base?base.replace(/[\\/][^\\/]*$/,'')+'/'+name:name;
   if(!confirm(`将在同级目录新建并切换工作区：\n${target}\n\n当前未保存的编辑会先保存。继续？`))return;
   await switchWorkspace(name,true);
  }catch(e){notice(e.message,true)}
 };
 node.append(button);
}
let workspaceInventory=null,workspaceSwitching=false;
function workspaceListHTML(result,current,attr){
 const list=result.workspaces||[];
 return list.length?list.map((w,i)=>`<div class="workspace-row"><button type="button" class="workspace-choice" ${attr}="${i}" ${w.path===current.path?'disabled':''}><span><strong>${esc(w.name||w.path)}</strong><small>${esc(w.path)}</small></span><em>${w.path===current.path?'当前':(w.running?'运行中 · 打开 ↗':'打开 ↗')}</em></button>${w.path!==current.path&&w.running?`<button type="button" class="workspace-stop" data-stop-workspace="${esc(w.path)}" title="停止该工作区服务">停止</button>`:''}</div>`).join(''):'<p class="help">还没有其他工作区。</p>';
}
function wireWorkspaceList(box,result,attr){
 if(!box)return;
 box.querySelectorAll('['+attr+']').forEach(b=>b.onclick=()=>switchWorkspace(result.workspaces[Number(b.getAttribute(attr))].path,false).catch(()=>{}));
 box.querySelectorAll('[data-stop-workspace]').forEach(b=>b.onclick=e=>{e.stopPropagation();stopWorkspace(b.dataset.stopWorkspace)});
}
async function stopWorkspace(path){
 if(!window.confirm('停止该工作区服务？未完成的任务会中断；数据、来源和任务记录都会保留。'))return;
 try{const result=await api('workspaces/stop',{path});notice(result.message||'已处理');await refreshWorkspaces();if(typeof renderSettingsWorkspaces==='function'&&$('settings-view-workspaces')&&!$('settings-view-workspaces').hidden)await renderSettingsWorkspaces()}catch(e){notice(e.message,true)}
}
function workspaceStatus(text,error=false){for(const id of ['workspace-switch-status','settings-workspace-status']){const el=$(id);if(!el)continue;el.textContent=text;el.classList.toggle('error',error)}}
function workspaceChoices(box,result,attr){
 if(!box)return;
 const workspaces=result.workspaces||[],current=result.current||{},key=attr.replace(/-([a-z])/g,(m,c)=>c.toUpperCase());
 box.innerHTML=(workspaces.some(w=>w.path!==current.path))?workspaces.map((w,i)=>`<button type="button" class="workspace-choice" data-${attr}="${i}" ${w.path===current.path?'disabled':''}><span><strong>${esc(w.name||w.path)}</strong><small>${esc(w.path)}</small></span><em>${w.path===current.path?'当前':'打开 ↗'}</em></button>`).join(''):'<p class="help">还没有其他工作区。</p>';
 box.querySelectorAll(`[data-${attr}]`).forEach(button=>button.onclick=()=>switchWorkspace(workspaces[Number(button.dataset[key])].path,false).catch(()=>{}));
}
async function refreshWorkspaces(){
 const result=await api('workspaces');workspaceInventory=result;const current=result.current||{};
 $('workspace-name').textContent=current.name||'本地工作区';$('workspace-switch').title=current.path||'选择工作区';$('workspace-current-name').textContent=current.name||'当前工作区';$('workspace-current-path').textContent=current.path||'';
 $('workspace-list').innerHTML=workspaceListHTML(result,current,'data-workspace-index');
 wireWorkspaceList($('workspace-list'),result,'data-workspace-index');
 return result;}
async function showWorkspacePicker(){
 $('workspace-switch-status').textContent='';$('workspace-switch-status').classList.remove('error');$('workspace-dialog').showModal();
 if(!workspaceInventory)$('workspace-list').innerHTML='<p class="help">正在读取工作区…</p>';
 try{await refreshWorkspaces()}catch(e){$('workspace-switch-status').textContent='无法读取工作区：'+e.message;$('workspace-switch-status').classList.add('error')}
}
function workspaceControls(){
 const controls=[...$('workspace-dialog').querySelectorAll('button,input')];
 for(const id of ['workspace-switch','settings-workspace-path','settings-workspace-open','settings-workspace-new','settings-workspace-create']){const control=$(id);if(control)controls.push(control)}
 return controls;
}
function openWorkspaceFrom(inputId,create){switchWorkspace($(inputId).value,create).catch(()=>{})}
async function switchWorkspace(path,create){
 if(workspaceSwitching){workspaceStatus('正在切换工作区，请稍候…');return}
 if(chat.busy||chat.uploading){workspaceStatus('请等待消息发送或附件上传完成后再切换。',true);return}
 workspaceSwitching=true;workspaceStatus('正在打开工作区…');
 const controls=workspaceControls();const originalDisabled=new Map(controls.map(c=>[c,c.disabled]));controls.forEach(c=>c.disabled=true);
 try{
  rememberDraft();clearTimeout(saveTimer);const deadline=Date.now()+15000;while(saving&&Date.now()<deadline)await new Promise(resolve=>setTimeout(resolve,60));
  if(saving)throw Error('当前简报仍在保存，请稍后重试。');if(dirty){workspaceStatus('正在保存当前简报…');await save();if(dirty)throw Error('当前简报尚未保存，请先完成保存。')}
  if(window.briefloopDesktop?.openWorkspace){const requested=path.trim();const result=await window.briefloopDesktop.openWorkspace({path:requested,create});if(result?.cancelled)workspaceStatus('已保留当前工作区。');return !result?.cancelled}
  const result=await api('workspaces/open',{path:path.trim(),create});if(!result.url)throw Error('工作区服务尚未准备好，请重试。');
 workspaceStatus(result.path?'已打开 '+result.path+'，正在切换…':'已打开，正在切换…');location.assign(result.url);return true;
 }catch(e){workspaceStatus(e.message,true);throw e} finally{workspaceSwitching=false;controls.forEach(c=>c.disabled=originalDisabled.get(c))}
}
$('workspace-switch').onclick=showWorkspacePicker;
$('close-workspace').onclick=()=>$('workspace-dialog').close();
$('workspace-open-form').onsubmit=event=>{event.preventDefault();openWorkspaceFrom('workspace-path',false)};
$('workspace-create-form').onsubmit=event=>{event.preventDefault();openWorkspaceFrom('workspace-new-name',true)};
function renderContext(){
 const events=[...chat.events.values()].reverse(),usageEvent=events.find(e=>e.kind==='thread/tokenUsage/updated'),providerChange=events.find(e=>e.kind==='thread/providerChanged');
 const usage=providerChange&&(!usageEvent||providerChange.seq>usageEvent.seq)?null:(chat.tokenUsage||usageEvent?.data?.tokenUsage),last=usage?.last||{},windowSize=usage?.modelContextWindow;
 const finite=value=>typeof value==='number'&&Number.isFinite(value)&&value>=0;const count=value=>finite(value)?new Intl.NumberFormat('zh-CN').format(value):'未知';
 const hasInput=finite(last.inputTokens),hasWindow=finite(windowSize)&&windowSize>0;
 $('context-summary').textContent=usage?`上下文 · ${hasInput?count(last.inputTokens):'未知'} / ${hasWindow?count(windowSize):'上限未知'}`:'上下文 · 待统计';
 $('context-details').innerHTML=usage?`<strong>最近一次模型请求</strong><dl><div><dt>输入 Token</dt><dd>${count(last.inputTokens)}</dd></div><div><dt>其中缓存</dt><dd>${count(last.cachedInputTokens)}</dd></div><div><dt>输出 Token</dt><dd>${count(last.outputTokens)}</dd></div><div><dt>模型窗口</dt><dd>${hasWindow?count(windowSize):'未知'}</dd></div></dl>${hasInput&&hasWindow?`<meter min="0" max="${windowSize}" value="${Math.min(last.inputTokens,windowSize)}" aria-label="最近输入与上下文窗口的比例"></meter><p>最近输入占窗口 ${(last.inputTokens/windowSize*100).toFixed(1)}%。</p>`:''}<p>输入量来自最近一次请求，累计用量不作为上下文占用。</p>`:'<p>开始执行后，按后端返回的真实数据更新；暂无用量。</p>';
}
$('chat-permission').onchange=()=>{rememberDraft();updateComposer()};
function showSettings(){$('settings-chat-web-default').checked=state.settings.chat_allow_web!==false;$('timeout-minutes').value=state.settings.timeout_minutes;$('hard-timeout-minutes').value=state.settings.hard_timeout_minutes||0;moveSearchSettings('settings');page('settings-dialog');$('settings-dialog').scrollIntoView({block:'start'});refreshRuntimeDiscovery();office.syncSettingsToggle();updateLearningPause().catch(()=>{});refreshTavilySettings().catch(()=>{})}
$('settings-open').onclick=showSettings;$('settings-close').onclick=()=>page('chat');
{
 const modelPanel=document.querySelector('.model-settings');const shortcut=document.createElement('div');shortcut.className='setup-settings-shortcut';shortcut.innerHTML='<div><span>生成模型</span><strong id="setup-model-summary">Luna / high</strong></div><button type="button" class="outline">模型与角色设置</button>';shortcut.querySelector('button').onclick=showSettings;modelPanel.before(shortcut);$('settings-model-block').append(modelPanel);
 const providerRow=document.querySelector('.chat-provider-row');providerRow.querySelector('label').textContent='当前对话 Provider';providerRow.querySelector('span').textContent='用于当前对话下一次执行';$('settings-model-block').append(providerRow);
 const learningControl=document.querySelector('.learning-control'),learningHelp=learningControl.nextElementSibling,autoLearnLabel=$('auto-learn').closest('label');$('settings-learning-block').append(learningControl,learningHelp,autoLearnLabel);
 const link=document.createElement('button');link.type='button';link.className='outline feedback-settings';link.textContent='学习设置';link.onclick=showSettings;$('learn-now').before(link);
}

// DESIGN §7.2 default row: 附件 | 模型 | 参数 … 发送split. Do not park model in send-controls.
{
 const options=document.querySelector('.composer-options');
 const attach=$('chat-attach');
 const model=$('chat-model');
 if(options&&attach&&model&&model.parentElement!==options){
  attach.after(model);
 }
 const panel=$('composer-params-panel');
 const grid=panel?.querySelector('.composer-params-grid');
 if(grid){
  for(const id of ['chat-effort','chat-service-tier']){
   const el=$(id);
   if(el&&!grid.contains(el))grid.append(el.closest('label')||el);
  }
 }
 const help=$('composer-help');if(help&&help.parentElement!==$('chat-form').parentElement)$('chat-form').after(help);
 const settingsButton=$('settings-open');
 if(settingsButton&&!settingsButton.querySelector('svg')){
  settingsButton.innerHTML='<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M9 3h6l1 3 3 1 2 5-2 5-3 1-1 3H9l-1-3-3-1-2-5 2-5 3-1 1-3Z"/><circle cx="12" cy="12" r="3"/></svg><span>设置</span>';
 }
}

function autoSizeChatInput(){const input=$('chat-input');input.style.height='auto';input.style.height=Math.min(210,Math.max(36,input.scrollHeight))+'px'}
{
 const inputHandler=$('chat-input').oninput;$('chat-input').oninput=event=>{inputHandler?.(event);autoSizeChatInput()};
}
function learningAuthorizationNote(){
 const auth=state?.learning_authorization;
 if(auth?.state==='needs_confirmation')return '自动学习等待确认：开启会在后台调用模型做学习验证，需要先确认一次调用上限。反馈照常保存，已启用的技能照常用于报告。';
 if(auth?.state==='rounds_exceed')return `学习轮数已高于确认时的 ${auth.authorized_rounds} 轮，自动学习已暂停；重新勾选即可确认新的上限。`;
 if(auth?.state==='plan_changed')return '执行后端或模型在确认之后发生了变化，自动学习已暂停；重新勾选即可按新的配置确认上限。';
 return '';
}
async function updateLearningPause(){
 const runtime=await api('runtime'),paused=runtime.automatic_learning_paused===true,note=$('settings-paused-note'),waiting=learningAuthorizationNote();
 note.textContent=paused?'此工作区的自动学习已暂停。勾选下方选项后启用。':waiting;note.hidden=!note.textContent;
 $('auto-learn').checked=!paused&&state.learning_authorization?.state==='authorized';
}
$('auto-learn').onchange=async event=>{const enabled=event.target.checked;event.target.disabled=true;try{await setAutoLearn(enabled);await refresh();await updateLearningPause()}catch(e){notice(e.message,true)}finally{event.target.disabled=false;syncCompactReportControls()}};

function sourceOriginalLink(result){
 const provenance=result.provenance||{};
 return {label:provenance.original_kind==='provider_response'?'下载 Tavily 返回内容 ↗':'下载原件 ↗',href:result.original_url||''};
}
function applySourceLinks(result,els){
 const source=result.source||{};
 if(els.link){els.link.textContent=source.url||'';els.link.href=source.url||'#';els.link.hidden=!source.url}
 if(els.original){const original=sourceOriginalLink(result);els.original.textContent=original.label;els.original.hidden=!original.href;if(original.href){els.original.href=original.href;els.original.download=source.name||''}else els.original.removeAttribute('href')}
}
function sourceProvenanceRows(result){
 const provenance=result.provenance||{},rows=[];
 if(provenance.fetched_at)rows.push(['抓取时间',moment(provenance.fetched_at)||String(provenance.fetched_at)])
 if(provenance.content_type)rows.push(['内容类型',String(provenance.content_type)]);
 return rows;
}
function showSourceProvenance(result,el){if(!el)return;const rows=sourceProvenanceRows(result);el.innerHTML=rows.map(([label,value])=>`<div><dt>${esc(label)}</dt><dd>${esc(value)}</dd></div>`).join('');el.hidden=!rows.length}
function showSource(result){
 applySourceLinks(result,{link:$('source-link'),original:$('source-original')});
 showSourceMedia(result);
 $('source-title').textContent=(result.source||{}).name||'来源';$('source-body').textContent=(result.source||{}).error||result.text||'';
 showSourceProvenance(result,$('source-provenance'));
 $('source-dialog').showModal();
}

let sourceMediaId=null;
const dialogSourceMediaView=()=>({media:$('source-media'),images:$('source-images'),controls:$('source-page-controls'),note:$('source-media-note'),pages:$('source-pages'),render:$('source-pages-render')});
const drawerSourceMediaView=()=>({media:$('source-drawer-media'),images:$('source-drawer-images'),controls:$('source-drawer-page-controls'),note:$('source-drawer-media-note'),pages:$('source-drawer-pages'),render:$('source-drawer-pages-render')});
function resetSourceMedia(view){view=view||dialogSourceMediaView();sourceMediaId=null;if(!view.media)return;view.media.hidden=true;view.images.replaceChildren();if(view.render){view.render.disabled=false;view.render.hidden=false}const label=view.controls?.querySelector?.('label');if(label)label.textContent='查看 PDF 页码';const officeButton=officeMediaButton(view);if(officeButton)officeButton.hidden=true}
function officeMediaButton(view){
 // The drawer has a static control; the source dialog gets an equivalent one lazily.
 if(!view.media)return null;
 if(view.media.id==='source-drawer-media')return $('source-drawer-office-render');
 let button=$('source-office-render');
 if(!button){button=document.createElement('button');button.type='button';button.className='outline';button.id='source-office-render';button.hidden=true;button.textContent='OfficeCLI 查看页面（本地渲染，不调用模型）';view.controls?.after?.(button)}
 return button;
}
function showSourceMedia(result,view){
 view=view||dialogSourceMediaView();sourceMediaId=null;
 const media=result.attachment;
 if(!media||result.source?.status==='failed'){resetSourceMedia(view);return}
 const isPDF=media.media_type==='application/pdf',isImage=media.media_type?.startsWith('image/');
 // OfficeCLI page preview is an optional enhancement: without the switch an
 // Office original renders exactly like today, with no extra controls.
 const isOfficeFile=typeof office!=='undefined'&&office.officeEnabled()&&/\.(docx|xlsx|pptx)$/i.test(result.source?.name||'');
 if(!isPDF&&!isImage&&!isOfficeFile){resetSourceMedia(view);return}
 resetSourceMedia(view);sourceMediaId=result.source.id;
 view.media.hidden=false;
 if(isOfficeFile){
  view.controls.hidden=false;view.pages.value='1';
  const label=view.controls?.querySelector?.('label');if(label)label.textContent='查看页码（1–4）';
  if(view.render)view.render.hidden=true;
  const officeButton=officeMediaButton(view);if(officeButton){officeButton.hidden=false;officeButton.onclick=()=>action(()=>office.previewSourceInto(sourceMediaId,view,officeButton))}
  view.note.textContent='Office 原件可用 OfficeCLI 本地渲染页面查看；本地渲染，不调用模型，预览失败不影响文件本身。';
  return;
 }
 view.controls.hidden=!isPDF;view.pages.value='1';
 view.note.textContent=isPDF?`PDF 原件可用${media.pages?'，共 '+media.pages+' 页':''}。正文提取不覆盖所有图表，可按页查看。`:'原图已保存；发送给支持视觉的模型时会作为图片输入，不冒充 OCR 文本。';
 if(isImage&&result.image_url){const img=document.createElement('img');img.src=result.image_url;img.alt=result.source.name||'来源图片';img.className='source-preview-image';view.images.append(img)}
 if(view.render)view.render.onclick=()=>action(()=>renderSourcePages(sourceMediaId,view));
}
async function renderSourcePages(id,view){
 if(!id||!view)return;const value=view.pages.value.trim();if(!/^\d+(?:\s*[,，]\s*\d+)*$/.test(value))throw Error('请输入页码，例如 1 或 1,3');
 const pages=[...new Set(value.split(/[,，]/).map(Number))];if(pages.some(p=>p<1)||pages.length>4)throw Error('每次请选择 1–4 页，页码从 1 开始');
 view.render.disabled=true;
 try{const result=await api('source-pages',{source_id:id,pages});if(sourceMediaId!==id)return;view.images.replaceChildren();
  for(const page of result.pages){const figure=document.createElement('figure');const caption=document.createElement('figcaption');caption.textContent='第 '+page.page+' 页';const img=document.createElement('img');img.className='source-preview-image';img.alt=caption.textContent;img.src=page.url;figure.append(caption,img);view.images.append(figure)}
 }finally{if(sourceMediaId===id)view.render.disabled=false}
}
$('source-dialog').addEventListener('close',()=>resetSourceMedia());

const SEARCH_LABELS={native:'宿主自带搜索',tavily:'Tavily',duckduckgo:'DuckDuckGo',bocha:'博查',zhipu:'智谱搜索'};
function readSearchPolicy(){return {primary_provider:$('search-provider').value,supplemental_providers:[...$('search-supplements').querySelectorAll('input[value]:checked')].map(e=>e.value).filter(v=>v!==$('search-provider').value),zhipu_engine:$('zhipu-engine').value,native_search_enabled:$('search-native').checked,coverage_mode:$('search-coverage').value,market_scope:$('search-market').value,platform_scope:[]}}
function loadSearchPolicy(){const p=state.settings.search_policy||{primary_provider:state.settings.search_provider,native_search_enabled:state.settings.search_provider==='tavily'};$('search-provider').value=p.primary_provider||'native';$('search-coverage').value=p.coverage_mode||'coverage';$('search-market').value=p.market_scope||'';$('zhipu-engine').value=p.zhipu_engine||'search_std';$('search-native').checked=!!p.native_search_enabled;$('search-supplements').querySelectorAll('input[value]').forEach(e=>e.checked=(p.supplemental_providers||[]).includes(e.value));refreshBochaSettings();refreshZhipuSettings()}
function nativeSearchName(){return SEARCH_LABELS[$('search-provider').value]||'宿主自带搜索'}
function renderSearchProvider(){const p=readSearchPolicy(),all=[p.primary_provider,...p.supplemental_providers];$('tavily-settings').hidden=!all.includes('tavily');$('bocha-settings').hidden=!all.includes('bocha');$('zhipu-settings').hidden=!all.includes('zhipu');$('search-supplements').disabled=p.coverage_mode==='primary_only';$('search-supplements').querySelectorAll('input[value]').forEach(e=>{e.disabled=e.value===p.primary_provider;if(e.disabled)e.checked=false});$('search-native').disabled=p.primary_provider==='native';$('search-native-note').textContent=(state.settings.agent_backend==='briefloop-native'?'内置引擎不提供宿主自带搜索；请使用已配置的受控搜索渠道。当前开关在切换其他宿主后适用。':runtimeName(state.settings.agent_backend||'codex')+'：能否搜索由宿主实际工具和授权决定。原生调用次数未知，不纳入受控 API 次数；发现的正文仍须保存。');$('setup-search-summary').textContent='优先 '+nativeSearchName()+(p.coverage_mode==='primary_only'?' · 仅首选':(p.supplemental_providers.length?' · 补充 '+p.supplemental_providers.map(x=>SEARCH_LABELS[x]).join('、'):'')+(p.native_search_enabled&&p.primary_provider!=='native'?' · 允许宿主搜索':''));renderBudgetProviderScope()}
async function saveSearchPolicy(){const button=$('search-policy-save');button.disabled=true;const policy=readSearchPolicy();$('search-settings-status').textContent='保存中…';try{await api('settings',{search_provider:policy.primary_provider,search_policy:policy});state.settings.search_provider=policy.primary_provider;state.settings.search_policy=policy;$('search-settings-status').textContent='已保存。新任务采用此策略，运行中的报告保持原配置。';await Promise.all([refreshTavilySettings(),refreshBochaSettings(),refreshZhipuSettings()])}catch(e){$('search-settings-status').textContent='未保存：'+e.message}finally{button.disabled=false;renderSearchProvider()}}
async function refreshBochaSettings(){try{const r=await api('bocha');$('bocha-key-status').textContent=r.configured?'已配置 · '+(r.source==='environment'?'环境变量':'本机配置'):'尚未配置，博查搜索需填写 API Key';$('bocha-key-remove').hidden=r.source!=='file'}catch{$('bocha-key-status').textContent='无法读取博查配置，请重试'}}
async function refreshZhipuSettings(){try{const r=await api('zhipu-search');$('zhipu-key-status').textContent=r.configured?'已配置 · '+(r.source==='environment'?'环境变量':'本机配置'):'尚未配置，智谱搜索需填写 API Key';$('zhipu-key-remove').hidden=r.source!=='file'}catch{$('zhipu-key-status').textContent='无法读取智谱搜索配置，请重试'}}
$('search-policy-save').onclick=saveSearchPolicy;
for(const id of ['search-supplements','search-coverage','search-market','zhipu-engine'])$(id).onchange=()=>{renderSearchProvider();$('search-settings-status').textContent='设置已修改，点击“保存搜索策略”生效。'};
for(const action of ['save','remove'])$('bocha-key-'+action).onclick=async()=>{const button=$('bocha-key-'+action),key=$('bocha-key');button.disabled=true;try{await api('bocha',action==='remove'?{remove:true}:{api_key:key.value.trim()});await refreshBochaSettings();$('search-settings-status').textContent=action==='remove'?'本机密钥已移除。':'博查密钥已保存，未调用搜索。'}catch(e){$('search-settings-status').textContent=e.message}finally{key.value='';button.disabled=false}};
for(const action of ['save','remove'])$('zhipu-key-'+action).onclick=async()=>{const button=$('zhipu-key-'+action),key=$('zhipu-key');button.disabled=true;try{await api('zhipu-search',action==='remove'?{remove:true}:{api_key:key.value.trim()});await refreshZhipuSettings();$('search-settings-status').textContent=action==='remove'?'本机密钥已移除。':'智谱搜索密钥已保存，未调用搜索。'}catch(e){$('search-settings-status').textContent=e.message}finally{key.value='';button.disabled=false}};
async function refreshTavilySettings(){
 try{const result=await api('tavily');const where={environment:'环境变量',file:'本机配置'}[result.source]||'本机配置';$('tavily-key-status').textContent=result.configured?`已配置 · ${where} · 所有工作区可用`:'尚未配置 Tavily 密钥';$('tavily-key-remove').hidden=result.source!=='file';return result}
 catch{$('tavily-key-status').textContent='暂时无法读取本机配置，请稍后重试。';$('tavily-key-remove').hidden=true}
}
$('search-provider').onchange=()=>{renderSearchProvider();$('search-settings-status').textContent='设置已修改，点击“保存搜索策略”生效。'};
async function saveTavilyKey(event){
 event.preventDefault();const input=$('tavily-key'),key=input.value.trim();if(!key){$('search-settings-status').textContent='请先粘贴 Tavily API Key。';return}
 $('tavily-key-save').disabled=true;input.disabled=true;$('search-settings-status').textContent='正在保存本机配置…';
 try{await api('tavily',{api_key:key});$('search-settings-status').textContent='密钥已保存，未发起验证或搜索。';await refreshTavilySettings()}catch{$('search-settings-status').textContent='密钥未能保存，请检查本地服务后重试。'}finally{input.value='';input.disabled=false;$('tavily-key-save').disabled=false}
};
$('tavily-key-remove').onclick=async()=>{
 const button=$('tavily-key-remove');button.disabled=true;$('tavily-key').value='';
 try{await api('tavily',{remove:true});$('search-settings-status').textContent='本机保存的密钥已移除。';await refreshTavilySettings()}catch{$('search-settings-status').textContent='未能移除本机配置，请稍后重试。'}finally{button.disabled=false}
};
$('settings-dialog').addEventListener('close',()=>{$('tavily-key').value='';$('bocha-key').value='';$('zhipu-key').value='';$('zhipu-key').value='';if(!$('setup').hidden)moveSearchSettings('setup')});
let candidatesPolling=false;
async function refreshCandidates(){
 if(candidatesPolling)return;candidatesPolling=true;
 try{
  const result=await api('learning-candidates'),candidates=result.candidates||[],signature=JSON.stringify(candidates);if(refreshCandidates.signature===signature)return;refreshCandidates.signature=signature;
  const opened=new Set([...$('learning-candidates').querySelectorAll('details[open]')].map(d=>d.dataset.candidateKey));
  const labels={pending_validation:'待验证',validation_running:'正在验证',accepted:'已接受',revision_required:'待修改 · 要求保留',rejected:'未采用',paused:'验证已暂停',failed:'验证未完成',no_action:'未提出候选'};
  const pausedJobs=new Set(['paused','interrupted','cancelled','failed']);
  $('learning-candidates').innerHTML=candidates.length?candidates.map((candidate,i)=>{const key=`${candidate.job_id}:${candidate.title}:${i}`,paused=pausedJobs.has(candidate.job_status),canResume=paused&&!['accepted','rejected','no_action'].includes(candidate.status),label=labels[candidate.status]||'待处理';return `<details class="candidate-item" data-candidate-key="${esc(key)}" ${opened.has(key)?'open':''}><summary><strong>${esc(candidate.title||'候选技能')}</strong><span class="candidate-status">${esc(label)}${paused&&candidate.status==='pending_validation'?' · 已暂停':''}</span></summary>${candidate.reason?`<p class="candidate-reason">${esc(candidate.reason)}</p>`:''}${candidate.markdown?`<pre class="candidate-markdown">${esc(candidate.markdown)}</pre>`:'<p class="help">候选正文暂不可读取。</p>'}${canResume?`<button class="outline" data-resume-candidate="${esc(candidate.job_id)}">继续验证（调用模型）</button>`:''}</details>`}).join(''):'<p class="help">暂无候选技能。候选生成后会显示在这里。</p>';
  $('learning-candidates').querySelectorAll('[data-resume-candidate]').forEach(button=>button.onclick=()=>action(async()=>{button.disabled=true;try{await api('resume',{job_id:button.dataset.resumeCandidate});await refreshCandidates()}finally{button.disabled=false}},'已提交继续验证'));
 }catch{$('learning-candidates').innerHTML='<p class="help">暂时无法读取候选技能，稍后会自动重试。</p>';refreshCandidates.signature=null}finally{candidatesPolling=false}
}

$('tavily-key-save').onclick=saveTavilyKey;
$('tavily-key').onkeydown=event=>{if(event.key==='Enter'){event.preventDefault();event.stopPropagation();if(!$('tavily-key-save').disabled)saveTavilyKey(event)}};
function moveSearchSettings(place){
 const slot=$(place==='setup'?'setup-search-inline':'settings-search-slot');slot.append($('settings-search-block'));
 if(place==='settings')renderSearchProvider();else if(!$('setup-search-inline').hidden)$('tavily-settings').hidden=false;
}
$('setup-search-config').onclick=()=>{
 const container=$('setup-search-inline'),show=container.hidden;container.hidden=!show;$('setup-search-config').setAttribute('aria-expanded',String(show));
 if(show){moveSearchSettings('setup');$('tavily-settings').hidden=false;refreshTavilySettings().catch(()=>{})}else $('tavily-key').value='';
};

$('chat-model').oninput=updateComposer;
for(const id of ['model-select','model-provider'])$(id).addEventListener('input',()=>renderFastControls());
$('chat-model-provider').oninput=()=>{rememberDraft();updateComposer()};
for(const id of ['chat-model','chat-model-provider','model-select','model-provider'])$(id).addEventListener('keydown',event=>{if(event.key==='Enter'){event.preventDefault();event.stopPropagation();$(id).blur()}});

let lengthEdited=false;
const LENGTH_PRESETS={quick:[350,500],compact:[800,1000],balanced:[1500,2000],detailed:[2000,2500]};
function initializeLengthInputs(requirements){
 const preset=LENGTH_PRESETS[$('length-preset').value]||LENGTH_PRESETS.balanced;
 $('target-words').value=requirements.target_words??preset[0];$('max-words').value=requirements.max_words??preset[1];validateLengthInputs();
}
function validateLengthInputs(){
 const target=Number($('target-words').value),maximum=Number($('max-words').value);
 $('max-words').setCustomValidity(Number.isInteger(target)&&target>0&&Number.isInteger(maximum)&&maximum>0&&maximum<target?'字数上限不能小于目标字数。':'');
}
$('length-preset').onchange=()=>{lengthEdited=true;const [target,maximum]=LENGTH_PRESETS[$('length-preset').value];$('target-words').value=target;$('max-words').value=maximum;validateLengthInputs()};
for(const id of ['target-words','max-words'])$(id).oninput=()=>{lengthEdited=true;validateLengthInputs()};
function renderBriefLength(){
 renderReportDataButton();
 const element=$('brief-length');if(!current){element.hidden=true;return}element.hidden=false;
 const stats=state.briefs.find(brief=>brief.id===current.id)?.length_stats||current.length_stats;
 element.classList.remove('over-limit');if(!stats||!Number.isFinite(stats.count)){element.textContent='字数信息暂不可用';return}
 const count=value=>new Intl.NumberFormat('zh-CN').format(value),parts=[`${dirty?'上次保存':'正文'} ${count(stats.count)} 字`];
 if(stats.target_words==null&&stats.max_words==null)parts.push('字数目标与上限未设置');else {parts.push(stats.target_words==null?'目标未设置':`目标 ${count(stats.target_words)}`);parts.push(stats.max_words==null?'上限未设置':`上限 ${count(stats.max_words)}`)}
 if(stats.over_limit===true&&stats.max_words!=null){parts.push(`超出 ${count(stats.count-stats.max_words)} 字`);element.classList.add('over-limit')}
 if(dirty)parts.push('保存后更新');element.textContent=parts.join(' · ');
}

let sessionMenuTarget=null;
function closeSessionMenu(){$('session-actions-menu').hidden=true;sessionMenuTarget=null}
function renderSessionMenu(){
 const session=chat.sessions.find(s=>s.id===sessionMenuTarget)||(chat.session?.id===sessionMenuTarget?chat.session:null);if(!session){closeSessionMenu();return}
 const busy=sessionBusy(session)||chat.busy,lifecycle=session.lifecycle||chat.view,actions=lifecycle==='active'?[['archive','归档'],['delete','移到回收站']]:lifecycle==='archived'?[['restore','恢复到最近对话'],['delete','移到回收站']]:[['restore','恢复对话']];
 $('session-actions-menu').innerHTML=actions.map(([action,label])=>`<button type="button" role="menuitem" data-session-action="${action}" ${busy?'disabled':''}>${label}</button>`).join('')+`<p>${busy?'请先停止当前任务，再整理对话。':'报告、来源和 Wiki 保留不变。'}</p>`;
 $('session-actions-menu').querySelectorAll('[data-session-action]').forEach(button=>button.onclick=()=>mutateSession(button.dataset.sessionAction,session.id));
}
function openSessionMenu(id,anchor){
 if(sessionMenuTarget===id&&!$('session-actions-menu').hidden){closeSessionMenu();return}sessionMenuTarget=id;renderSessionMenu();if(!sessionMenuTarget)return;const menu=$('session-actions-menu');menu.hidden=false;const rect=anchor.getBoundingClientRect();menu.style.left=Math.max(8,Math.min(rect.right-menu.offsetWidth,window.innerWidth-menu.offsetWidth-8))+'px';menu.style.top=Math.max(8,Math.min(rect.bottom+4,window.innerHeight-menu.offsetHeight-8))+'px';menu.querySelector('button:not(:disabled)')?.focus();
}
async function mutateSession(action,id){
 const session=chat.sessions.find(s=>s.id===id)||(chat.session?.id===id?chat.session:null);if(sessionBusy(session)||chat.busy){notice('请先停止当前任务，再整理对话。',true);return}
 closeSessionMenu();if(id===chat.id)rememberDraft();
 try{await api('harness/'+action,{session_id:id});await pollChat(true);notice({archive:'对话已归档。',delete:'对话已移到回收站，可恢复。',restore:'对话已恢复。'}[action])}catch(e){notice(e.message,true)}
}
function renderSessionLifecycle(){
 const lifecycle=chat.session?.lifecycle||'active',readonly=lifecycle!=='active';$('chat-lifecycle-note').hidden=!readonly;
 $('chat-lifecycle-text').textContent=lifecycle==='deleted'?'这条对话在回收站中，恢复后可继续。':'这条对话已归档，恢复后可继续。';$('restore-current-session').disabled=chat.busy||sessionBusy(chat.session);
}
$('restore-current-session').onclick=()=>{if(chat.id)mutateSession('restore',chat.id)};
$('settings-runtime-tests').onclick=async()=>{closeSessionMenu();chat.view='tests';chat.sessions=[];page('chat');renderSessions();await pollChat(true).catch(e=>notice(e.message,true))};
$('session-view').onchange=async event=>{closeSessionMenu();chat.view=event.target.value;chat.sessions=[];renderSessions();await pollChat(true).catch(e=>notice(e.message,true))};
$('archive-completed').onclick=async()=>{
 if(chat.busy)return;closeSessionMenu();const button=$('archive-completed');button.disabled=true;
 try{rememberDraft();const result=await api('harness/archive-completed',{});await pollChat(true);notice(result.count?`已归档 ${result.count} 条已结束对话。`:'没有可归档的已结束对话。')}catch(e){notice(e.message,true)}finally{button.disabled=chat.busy||!chat.sessions.some(s=>!sessionBusy(s))}
};
document.addEventListener('click',event=>{if(!event.target.closest('#session-actions-menu')&&!event.target.closest('[data-manage-session]'))closeSessionMenu()});
document.addEventListener('keydown',event=>{if(event.key==='Escape')closeSessionMenu()});

const RESEARCH_BUDGET_PRESETS={weekly:{search_requests:30,candidate_urls:150,source_pages:60},monthly:{search_requests:80,candidate_urls:400,source_pages:150}};
const RESEARCH_TIERS={quick:{search_requests:6,candidate_urls:30,source_pages:12},standard:{search_requests:30,candidate_urls:150,source_pages:60},deep:{search_requests:80,candidate_urls:400,source_pages:150}};
const BUDGET_FIELDS={search_requests:'budget-search-requests',candidate_urls:'budget-candidate-urls',source_pages:'budget-source-pages'};
function readResearchBudget(){return Object.fromEntries(Object.entries(BUDGET_FIELDS).map(([key,id])=>[key,Number($(id).value)]))}
function reflectBudgetPreset(){const current=readResearchBudget();$('budget-preset').value=Object.keys(RESEARCH_BUDGET_PRESETS).find(name=>Object.keys(BUDGET_FIELDS).every(key=>current[key]===RESEARCH_BUDGET_PRESETS[name][key]))||'custom'}
function initializeResearchBudget(requirements){const budget=requirements.research_budget||RESEARCH_BUDGET_PRESETS.weekly;for(const [key,id] of Object.entries(BUDGET_FIELDS))$(id).value=budget[key]??RESEARCH_BUDGET_PRESETS.weekly[key];reflectBudgetPreset();renderBudgetProviderScope()}
function renderBudgetProviderScope(){const p=readSearchPolicy(),native=p.primary_provider==='native'||(p.coverage_mode!=='primary_only'&&p.native_search_enabled);$('budget-provider-scope').textContent='受控 API 渠道共享搜索与候选预算；正文按唯一 URL 计量。'+(native?'宿主原生搜索次数未知，单独显示，不计入受控 API 硬上限。':'')}
$('budget-preset').onchange=()=>{const budget=RESEARCH_BUDGET_PRESETS[$('budget-preset').value];if(budget)for(const [key,id] of Object.entries(BUDGET_FIELDS))$(id).value=budget[key]};
for(const id of Object.values(BUDGET_FIELDS))$(id).oninput=reflectBudgetPreset;
$('research-tier').onchange=()=>{if($('research-tier').value==='deep'&&!lengthEdited){$('target-words').value=10000;$('max-words').value=12000;validateLengthInputs()}const budget=RESEARCH_TIERS[$('research-tier').value];if(!budget)return;for(const [key,id] of Object.entries(BUDGET_FIELDS))$(id).value=budget[key];reflectBudgetPreset();renderBudgetProviderScope()};

let budgetPolling=false;
function researchBudgetTarget(){
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

// Content methods and Word layout are separate choices. Switching an export
// template never enters this requirements form or modifies a saved run.
function readWorkflowChoice(){
 const [workflow_id,workflow_variant]=($('workflow-choice').value||'').split('/');
 return {workflow_id:workflow_id||null,workflow_variant:workflow_variant||null};
}
function initializeWorkflowChoice(req,partial=false){
 const has=key=>Object.prototype.hasOwnProperty.call(req,key);
 if(partial&&!['workflow_id','workflow_variant','report_profile'].some(has))return;
 const legacy=!req.workflow_id&&req.report_profile==='industry_periodic';
 const id=req.workflow_id||(legacy?'business_report':partial&&!has('workflow_id')&&!has('report_profile')?readWorkflowChoice().workflow_id:'');
 const variant=req.workflow_variant||(legacy?'industry_periodic':state?.workflows?.find(w=>w.id===id)?.default_variant||'');
 $('workflow-choice').value=id?`${id}/${variant}`:'';
}
function renderWorkflowChoices(first=false){
 const el=$('workflow-choice');if(!el||!state)return;
 const catalog=state.workflows||[];
 const signature=JSON.stringify(catalog);
 if(first||el.dataset.catalog!==signature){
  const chosen=el.value;
  el.innerHTML='<option value="">使用建议用途</option>'+catalog.map(w=>`<optgroup label="${esc(w.label)}">${w.variants.map(v=>`<option value="${esc(w.id+'/'+v.id)}">${esc(w.label)} · ${esc(v.label)}</option>`).join('')}</optgroup>`).join('');
  el.dataset.catalog=signature;el.value=chosen;
  if(first)initializeWorkflowChoice(state.requirements||{});
 }
 const choice=readWorkflowChoice();
 const template=state.templates?.find(t=>t.id===$('template-select').value);
 const workflow=catalog.find(w=>w.id===(choice.workflow_id||template?.workflow_hint||'general_report'));
 const variant=workflow?.variants.find(v=>v.id===(choice.workflow_variant||workflow.default_variant));
 $('workflow-hint').textContent=workflow?`${choice.workflow_id?'已选':'本轮建议'}：${workflow.label} · ${variant?.label||''}。用于规划、写作和评价；Word 版式由报告模板决定。`:'文档方法目录暂不可用，请刷新后再选择。';
 const meeting=workflow?.id==='meeting_minutes';
 $('source-requirement').textContent=meeting?'需要本次会议记录':'可选';
 $('source-input-hint').textContent=meeting?'请添加并选择本次会议转写或笔记。只有议程时可整理框架，不能生成未发生的讨论或决议。':'可选：图片、PDF、Word、Excel、Markdown、文本、CSV。也可以直接输入目标，让 Agent 联网研究。';
 $('source-web-hint').textContent=meeting?'会议内容来自已选转写或笔记；公开检索只能补充另行要求的背景，不能替代会中记录。':'开启后，无需先上传文件，Agent 会围绕目标查找并保存公开来源。关闭时仍可讨论问题或使用已有材料。';
}
function syncWorkflowProfile(changeLength=true){
 const choice=readWorkflowChoice();
 const next=choice.workflow_id==='business_report'&&choice.workflow_variant==='industry_periodic'?'industry_periodic':'brief';
 const previous=$('report-profile').value;$('report-profile').value=next;
 if(changeLength&&previous!==next)$('report-profile').dispatchEvent(new Event('change'));
 else $('industry-profile-options').hidden=next!=='industry_periodic';
 renderWorkflowChoices();
}
$('workflow-choice').onchange=()=>syncWorkflowProfile();

// Reference reports are explicitly separated from this period's evidence.
const INDUSTRY_TASK_OUTLINE='撰写行业定期报告，围绕核心摘要、行业指标、供需竞争、重点专题与组织启示展开。可根据行业与读者需要调整章节。数据标明日期、单位、口径及比较期，实际与预测分开。分析从事实出发，解释传导机制、适用条件与下一步观察项。正文围绕本期变化、对组织的影响及有依据的行动展开；核查与待补信息单独保存。';
let briefLengthChoice=null,industryLengthChoice=[5000,5500];
function industryProfileActive(){return $('report-profile').value==='industry_periodic'}
function renderReferenceSources(){
 const el=$('reference-source-list');if(!el||!state)return;
 el.innerHTML=state.sources.length?state.sources.map(s=>`<label class="check"><input type="checkbox" data-reference-source="${esc(s.id)}" ${referenceSelected.has(s.id)?'checked':''} ${s.status==='failed'?'disabled':''}><span>${esc(s.name)}</span></label>`).join(''):'<p class="help">上传历史报告后，可在这里选择。参考报告不是必需材料。</p>';
 el.querySelectorAll('[data-reference-source]').forEach(input=>input.onchange=()=>{const id=input.dataset.referenceSource;if(input.checked){referenceSelected.add(id);selected.delete(id);const evidence=$('source-list').querySelector(`[data-check="${CSS.escape(id)}"]`);if(evidence)evidence.checked=false}else referenceSelected.delete(id)});
}
function initializeReportProfile(requirements){
 referenceSelected=new Set(requirements.reference_source_ids||[]);
 for(const id of referenceSelected)selected.delete(id);
 $('industry-profile-options').hidden=!industryProfileActive();$('length-preset').disabled=industryProfileActive();$('length-preset').closest('label').hidden=industryProfileActive();
 if(industryProfileActive()){
  $('target-words').value=requirements.target_words??5000;$('max-words').value=requirements.max_words??5500;
  industryLengthChoice=[$('target-words').value,$('max-words').value];
 }
 validateLengthInputs();renderReferenceSources();
}
$('report-profile').onchange=()=>{
 if(industryProfileActive()){
  briefLengthChoice=[$('target-words').value,$('max-words').value];
  [$('target-words').value,$('max-words').value]=industryLengthChoice;
  for(const id of referenceSelected){selected.delete(id);const evidence=$('source-list').querySelector(`[data-check="${CSS.escape(id)}"]`);if(evidence)evidence.checked=false}
 }else{
  industryLengthChoice=[$('target-words').value,$('max-words').value];
  [$('target-words').value,$('max-words').value]=briefLengthChoice||LENGTH_PRESETS[$('length-preset').value]||LENGTH_PRESETS.balanced;

 }
 $('industry-profile-options').hidden=!industryProfileActive();$('length-preset').disabled=industryProfileActive();$('length-preset').closest('label').hidden=industryProfileActive();validateLengthInputs();
};
$('industry-task-outline').onclick=()=>{const field=$('requirements').elements.objective;if(!field.value.includes(INDUSTRY_TASK_OUTLINE))field.value=(field.value.trim()?field.value.trim()+'\n\n':'')+INDUSTRY_TASK_OUTLINE;field.focus()};

function renderReportDataButton(){
 const run=current&&state?.runs.find(item=>item.id===current.run_id);
 $('report-data-control').hidden=!run;
}
function reportDataText(result){
 const data=result.data,records=data?.records||[],calculations=data?.calculations||[],gaps=[...new Set([...(result.gaps||[]),...(data?.gaps||[])])];
 const categories={actual:'实际值',forecast:'预测值',guidance:'管理层指引',consensus:'市场一致预期'};
 const value=v=>v==null?'未提供':String(v);
 const lines=['查看已保存内容，不调用模型。',result.note||'',data?'以下为本稿保存的结构化数据。':'本稿未保存结构化数据；请结合正文与来源查看，已有缺口如下。','', '数据缺口',gaps.length?gaps.map(gap=>'• '+gap).join('\n'):'未记录具体缺口；这不表示数据完整或已全部核验。'];
 if(data&&!records.length)lines.push('','尚无指标记录。');
 records.forEach((row,index)=>{
  const calculation=calculations.find(item=>item.record_index===index);
  lines.push('',`${index+1}. ${[row.metric,row.product,row.region].filter(Boolean).join(' / ')}`,
   `本期值：${value(row.current)} ${row.unit||''}　指标期间：${value(row.current_date)}　资料截至日：${value(row.as_of)}`,
   `类别：${categories[row.category]||row.category||'未注明'}　税口径：${row.tax_basis||'未注明'}`,
   `来源：${row.source_id||'未提供'}　定位：${row.locator||'未注明'}`);
  if(row.previous!=null||row.previous_date){lines.push(`比较值：${value(row.previous)} ${row.previous_unit||row.unit||''}　指标期间：${value(row.previous_date)}　资料截至日：${value(row.previous_as_of)}`,
   `比较类别：${categories[row.previous_category]||row.previous_category||'未注明'}　税口径：${row.previous_tax_basis||'未注明'}`,
   `比较来源：${row.previous_source_id||row.source_id||'未提供'}　定位：${row.previous_locator||'未注明'}`)}
  lines.push('计算变化：'+(calculation?.gap||((calculation?.change!=null)?`${value(calculation.change)} ${calculation.change_unit||''}`:'未计算或未设置比较')));
  if(row.notes)lines.push('备注：'+row.notes);
 });
 return lines.filter(line=>line!==null).join('\n');
}
$('report-data-open').onclick=()=>action(async()=>{
 if(!current)return;const result=await api('report-data?version='+encodeURIComponent(current.id));
 $('source-title').textContent='数据与缺口';$('source-link').hidden=true;$('source-link').textContent='';$('source-original').hidden=true;$('source-provenance').hidden=true;
 $('source-body').textContent=reportDataText(result);$('source-dialog').showModal();
});


// An explicit select lists every suggestion; the text input stays unrestricted.
function emptyCatalogLabel(backend,catalog){const why=String(catalog?.diagnostic||'').trim()||'没有读取到模型列表';return `${runtimeName(backend)}：${why}（可直接输入模型 ID）`}
function refreshInlineModelPickers(){
 const chatBackend=modelTargetBackend('chat-model');
 if(!modelCatalogs.has(chatBackend))fetchModelCatalog(false,chatBackend).then(()=>refreshInlineModelPickers()).catch(e=>{modelCatalogs.set(chatBackend,{backend:chatBackend,at:0,models:[],diagnostic:e.message||'模型目录读取失败'});refreshInlineModelPickers()});
 document.querySelectorAll('.model-picker-select').forEach(select=>{
  const input=select.parentElement.querySelector('input');select.replaceChildren(new Option('▾',''));
  const backend=modelTargetBackend(input?.id),catalog=modelCatalogs.get(backend),models=catalog?.models||[];
  for(const model of models)select.add(new Option(model.name+' · '+model.id,model.id));
  if(!models.length){const warn=new Option(emptyCatalogLabel(backend,catalog),'__empty__');warn.disabled=true;select.add(warn)}
  select.add(new Option('输入其他模型 ID…','__custom__'));
  select.add(new Option('搜索全部模型…','__browse__'));
  if(input?.dataset.roleModel)select.add(new Option('继承主链模型','__inherit__'));
 });
}
function setupModelPickers(root=document){
 for(const input of root.querySelectorAll('input[list="model-suggestions"]')){
  input.removeAttribute('list');
  const wrap=document.createElement('span');wrap.className='model-picker';input.before(wrap);wrap.append(input);
  const select=document.createElement('select');select.className='model-picker-select';select.setAttribute('aria-label',input.id==='chat-model'?'选择模型':'选择'+(input.getAttribute('aria-label')||'模型'));select.title='常用模型建议；也可直接在左侧输入其他模型 ID';
  select.add(new Option('▾',''));
  const models=modelCatalogs.get(modelTargetBackend(input?.id))?.models||[];
  for(const model of models)select.add(new Option(model.name+' · '+model.id,model.id));
  select.add(new Option('输入其他模型 ID…','__custom__'));
  select.add(new Option('搜索全部模型…','__browse__'));
  if(input.dataset.roleModel)select.add(new Option('继承主链模型','__inherit__'));
  select.onchange=()=>{
   const model=select.value;select.value='';if(input.disabled)return;
   if(model==='__custom__'){input.focus();input.select();return}
   if(model==='__browse__'){openModelPicker(input.id);return}
   if(!model)return;input.value=model==='__inherit__'?'':model;
   input.dispatchEvent(new Event('input',{bubbles:true}));input.dispatchEvent(new Event('change',{bubbles:true}));input.focus();
  };
  wrap.append(select);
  new MutationObserver(()=>{select.disabled=input.disabled}).observe(input,{attributes:true,attributeFilter:['disabled']});select.disabled=input.disabled;
 }
}
setupModelPickers();




$('text-color').oninput=e=>editor?.chain().focus().setMark('textStyle',{color:e.target.value}).run();
$('cell-color').oninput=e=>editor?.chain().focus().setCellAttribute('backgroundColor',e.target.value).run();
$('paragraph-align').onchange=e=>{if(!editor)return;const type=editor.isActive('heading')?'heading':'paragraph';editor.chain().focus().updateAttributes(type,{textAlign:e.target.value}).run()};

function renderWordExports(){
 const box=$('word-exports');if(!box||!state)return;
 const scope=current?.id||'';box.dataset.version=scope;
 const fileKinds={export_docx:'工作稿 Word',release:'正式 Word',audit_bundle:'审计包'};
 const jobs=state.jobs.filter(j=>fileKinds[j.kind]&&(!current||parse(j.payload).run_id===current.run_id));
 box.hidden=!jobs.length;
 box.innerHTML=jobs.slice(0,6).map(j=>{const result=parse(j.result),payload=parse(j.payload);const url=j.kind==='release'?'/api/release-file?id='+encodeURIComponent(payload.release_id):j.kind==='audit_bundle'?'/api/audit-file?job='+encodeURIComponent(j.id):result.download_url;const officeSummary=j.status==='complete'&&result.office&&typeof office!=='undefined'?esc(office.officeCheckSummary(result.office)):'';const previewButton=j.status==='complete'&&url&&typeof office!=='undefined'&&office.officeEnabled()?`<button type="button" data-office-preview="${esc(j.id)}">预览</button>`:'';return `<div class="job"><span>${fileKinds[j.kind]} · ${j.status==='complete'?'已制作':statuses[j.status]||esc(j.status)}</span><small>${payload.version_id===current?.id?'当前稿件版本':'历史稿件版本'}</small>${j.status==='complete'&&url?`<a href="${esc(url)}" download>下载${fileKinds[j.kind]}</a>`:`<span>${esc(j.error||'使用提交时固定的版本，可继续编辑')}</span>`}${officeSummary?`<span>${officeSummary}</span>`:''}${previewButton}</div>`}).join('');
 if(typeof office!=='undefined')box.querySelectorAll('[data-office-preview]').forEach(b=>b.onclick=()=>office.openPreview({job_id:b.dataset.officePreview}));
 // Detected but not enabled: one dismissible hint, never auto-enabled.
 if(typeof office!=='undefined'&&!renderWordExports.officeHintClosed&&state.office?.installed&&!state.office.enabled){const hint=document.createElement('p');hint.className='help';hint.textContent='检测到 OfficeCLI，可在设置中开启导出质检；不开启不影响现有导出。';const close=document.createElement('button');close.type='button';close.className='outline';close.textContent='知道了';close.onclick=()=>{renderWordExports.officeHintClosed=true;hint.remove()};hint.append(close);box.append(hint)}
 const active=jobs.find(j=>j.status==='running');
 if(active)api('events?job='+active.id).then(events=>{const last=[...events].reverse().find(e=>e.kind==='export_progress');if(last&&box.isConnected&&box.dataset.version===scope){const p=parse(last.data);const meter=document.createElement('div');meter.textContent=p.message;const progress=document.createElement('progress');if(Number.isFinite(p.total)&&Number.isFinite(p.step)){progress.max=p.total;progress.value=p.step}progress.setAttribute('aria-label',p.message||'文件制作中');meter.append(progress);box.append(meter)}}).catch(()=>{});
}

$('research-notes').onclick=()=>action(async()=>{const version=await savedVersion();const record=await api('research-notes?version='+version);resetSourceMedia();$('source-title').textContent='核查与待补';$('source-original').hidden=true;$('source-provenance').hidden=true;$('source-link').textContent='';$('source-body').textContent=[...record.gaps,...record.notes.map(n=>JSON.stringify(n,null,2)),'引用核查',...record.citations.map(c=>c.source_name+' · '+(c.locator||'')+(c.excerpt?'\n'+c.excerpt:''))].join('\n\n');$('source-dialog').showModal()});

function readTemplateSections(){
 const templateId=$('template-select').value,template=state?.templates?.find(t=>t.id===templateId),original=parse(template?.spec).sections||[];
 // Never submit a report whose chosen template is not ready; falling back to the
 // general layout would silently change the deliverable.
 if(template&&template.status&&template.status!=='ready')throw Error('所选模板尚未就绪：'+template.name+'；请改用通用模板，或等模板准备完成后再提交');
 const saved=templateId&&state.requirements?.template_id===templateId?state.requirements.sections||[]:[];
 return [...$('template-sections').querySelectorAll('[data-section-id]')].map(row=>{
  const base=original.find(s=>s.section_id===row.dataset.sectionId)||{},requirement=saved.find(s=>s.section_id===row.dataset.sectionId);
  return {section_id:row.dataset.sectionId,title:row.querySelector('[data-title]').value,purpose:row.querySelector('[data-purpose]')?.value??requirement?.purpose??base.purpose??'',mode:row.querySelector('select').value,placeholder:'待填充'};
 });
}
function templateSections(){
 const selected=state?.templates?.find(t=>t.id===$('template-select').value),sections=parse(selected?.spec).sections||[];
 $('template-sections').innerHTML=sections.map(s=>`<div class="row" data-section-id="${esc(s.section_id)}"><input data-title aria-label="章节标题" value="${esc(s.title)}"><select aria-label="章节职责"><option value="required">必写</option><option value="optional">按资料选用</option><option value="manual">人工填写</option></select></div>`).join('');
}
function applyTemplateSectionEdits(){
 const templateId=$('template-select').value;
 if(!templateId||state?.requirements?.template_id!==templateId)return;
 for(const section of state.requirements.sections||[]){const row=[...$('template-sections').querySelectorAll('[data-section-id]')].find(r=>r.dataset.sectionId===section.section_id);if(row){row.querySelector('[data-title]').value=section.title;row.querySelector('select').value=section.mode||'required'}}
}
function unreadyTemplate(id){const template=(state?.templates||[]).find(t=>t.id===id);return template&&template.status&&template.status!=='ready'?template:null}
function renderTemplates(first=false){
 const select=$('template-select');if(!select||!state)return;
 // Rebuild only when the template list or the picked template changes, so edits in
 // the section rows survive unrelated state refreshes.
 const signature=JSON.stringify([select.value,(state.templates||[]).map(t=>[t.id,t.status,t.revision])]);if(!first&&signature===renderTemplates.signature)return;renderTemplates.signature=signature;
 const chosen=first?(state.requirements?.template_id||state.settings.default_template_id||''):select.value;
 const blocked=unreadyTemplate(chosen);
 const ready=(state.templates||[]).filter(t=>t.status==='ready').sort((a,b)=>(a.origin==='builtin'?0:1)-(b.origin==='builtin'?0:1));
 const option=t=>`<option value="${esc(t.id)}">${esc(t.name)}${t.origin==='builtin'?'（内置）':''} · v${t.revision}</option>`;
 const groups={};
 for(const t of ready.filter(t=>t.origin==='builtin'&&t.name.includes('·'))){
  const i=t.name.lastIndexOf('·');(groups[t.name.slice(0,i)]=groups[t.name.slice(0,i)]||[]).push(t);
 }
 select.innerHTML='<option value="">通用模板</option>'
  +Object.entries(groups).map(([genre,items])=>`<optgroup label="${esc(genre)}（内置）">${items.map(option).join('')}</optgroup>`).join('')
  +ready.filter(t=>t.origin!=='builtin').map(option).join('')
  +(blocked?`<option value="${esc(blocked.id)}">${esc(blocked.name)} · 尚未就绪</option>`:'');
 select.value=chosen;
 $('template-status').textContent=[(state.templates||[]).filter(t=>t.status!=='ready').map(t=>t.name+'：'+(t.error||'模板准备中，可在任务列表查看或恢复')).join('；'),
  blocked?'当前选中的模板尚未就绪，请改用通用模板或等它准备完成。':''].filter(Boolean).join(' ');
 templateSections();
 applyTemplateSectionEdits();
}
$('template-select').onchange=()=>{templateSections();applyTemplateSectionEdits();renderWorkflowChoices()};
$('template-import-button').onclick=()=>$('template-file').click();
$('template-file').onchange=e=>action(async()=>{const file=e.target.files[0];if(!file)return;await api('template-import',await uploadPayload(file,getUploadLimits()));e.target.value='';notice('模板已上传，BriefLoop 将准备章节和版式，完成后可在我的模板中选择')});

$('company-mode').onchange=e=>action(()=>api('settings',{company_context_enabled:e.target.value==='ask'?null:e.target.value==='on'}));
$('company-open').onclick=()=>action(async()=>{const data=await api('company-context');resetSourceMedia();$('source-title').textContent='企业背景知识库';$('source-original').hidden=true;$('source-provenance').hidden=true;$('source-link').textContent='';$('source-body').textContent=data.facts.map(f=>f.fact_key+'\n'+f.value+'\n截至 '+f.effective_date+' · '+f.source_id+(f.locator?' · '+f.locator:'')).join('\n\n')||'尚未保存企业背景。企业内部周报开始前，BriefLoop 可以提议维护。';for(const pending of data.pending){const row=document.createElement('div');row.textContent='待确认：'+pending.fact_key+' — '+pending.value;for(const [label,accept] of [['采用新资料',true],['保留原记录',false]]){const button=document.createElement('button');button.textContent=label;button.onclick=()=>action(async()=>{await api('company-resolve',{fact_id:pending.id,accept});row.textContent='已处理'});row.append(button)}$('source-body').append(row)}$('source-dialog').showModal()});
$('auto-revision').onchange=e=>action(()=>api('settings',{auto_revision:e.target.checked}));

function updateFormattingTools(){if(!editor||editor.isDestroyed)return;document.querySelectorAll('[data-table-command]').forEach(b=>b.hidden=!editor.isActive('table'));document.querySelectorAll('[data-image-command]').forEach(b=>b.hidden=!editor.isActive('image'));$('cell-color').closest('label').hidden=!editor.isActive('table');for(const name of ['bold','italic'])$('toolbar').querySelector('[data-command='+name+']')?.setAttribute('aria-pressed',String(editor.isActive(name)))}

$('word-import-button').onclick=()=>$('word-import-file').click();
$('word-import-file').onchange=e=>action(async()=>{const file=e.target.files[0];if(!file)return;const version=await savedVersion();const result=await api('import-revision',await uploadPayload(file,getUploadLimits(),{base_version:version}));e.target.value='';if(result.status==='imported'){await refresh();openBrief(result.version);notice('Word 修订已保存，原稿保留')}else{resetSourceMedia();$('source-title').textContent='Word 修订待对齐';$('source-original').hidden=true;$('source-provenance').hidden=true;$('source-link').textContent='';$('source-body').textContent=result.message+'\n\n'+result.extracted_markdown;$('source-dialog').showModal();notice('原文件已保留，可请 BriefLoop 协助对齐到当前报告',true)}});

$('template-default').onclick=()=>action(()=>api('settings',{default_template_id:$('template-select').value||null}),'已保存工作区默认模板');

let savedProviderConfigurations=[];
function providerEndpoint(){return $('provider-engine').value==='briefloop-native'?'native':'opencode'}
$('provider-engine').onchange=()=>{$('custom-api-key').value='';$('custom-provider').value='';$('provider-open').click()};
$('provider-open').onclick=async()=>{$('provider-result').textContent='';$('custom-supports-images').value='';$('provider-use').hidden=true;settingsView('models');settingsModelTab('api');
 try{const r=await api(providerEndpoint()+'/providers');savedProviderConfigurations=r.configurations||[];
 $('custom-saved').innerHTML='<option value="">新建配置</option>'+savedProviderConfigurations.map((p,i)=>`<option value="${i}">${esc(p.name)} · ${esc(p.model)}</option>`).join('');
 if(!$('custom-provider').value.trim()&&savedProviderConfigurations.length){
  const previous=localStorage.getItem('briefloop-provider-id');const index=Math.max(0,savedProviderConfigurations.findIndex(p=>p.provider===previous));
  $('custom-saved').value=String(index);$('custom-saved').dispatchEvent(new Event('change'));
 }else if(savedProviderConfigurations.some(p=>p.provider===$('custom-provider').value.trim()))loadProviderCatalog();
 }catch(e){$('provider-result').textContent='已有配置读取失败：'+e.message}
};
$('custom-saved').onchange=()=>{
 const p=savedProviderConfigurations[Number($('custom-saved').value)];if($('custom-saved').value===''||!p)return;
 for(const [id,key] of [['custom-provider','provider'],['custom-name','name'],['custom-model','model'],['custom-protocol','protocol'],['custom-base-url','base_url'],['custom-context-limit','context_limit'],['custom-output-limit','output_limit'],['custom-supports-images','supports_images']])$(id).value=p[key]==null?'':String(p[key]);
 $('custom-api-key').value='';$('provider-use').hidden=true;localStorage.setItem('briefloop-provider-id',p.provider);loadProviderCatalog();
};
$('provider-close').onclick=()=>{$('custom-api-key').value='';if(welcomeFromProvider){welcomeFromProvider=false;page('welcome');renderWelcome();$('provider-close').textContent='返回 Agent CLI';$('provider-close').setAttribute('aria-label','返回 Agent CLI')}else settingsModelTab('cli')};
$('provider-dialog').addEventListener('close',()=>{$('custom-api-key').value=''});
$('provider-form').onsubmit=async event=>{
 event.preventDefault();const button=$('provider-save');button.disabled=true;$('provider-result').textContent='正在保存到本机…';
 const body={protocol:$('custom-protocol').value,name:$('custom-name').value.trim(),context_limit:$('custom-context-limit').value?Number($('custom-context-limit').value):null,output_limit:$('custom-output-limit').value?Number($('custom-output-limit').value):null,provider:$('custom-provider').value.trim(),base_url:$('custom-base-url').value.trim(),model:$('custom-model').value.trim(),api_key:$('custom-api-key').value,supports_images:$('custom-supports-images').value===''?null:$('custom-supports-images').value==='true'};
 $('custom-api-key').value='';
 try{
  const result=await api(providerEndpoint()+'/provider',body);body.api_key='';
  $('provider-use').hidden=false;$('provider-use').dataset.model=result.model;$('provider-use').dataset.backend=$('provider-engine').value;
  $('provider-result').textContent='已保存 '+result.model+'。当前模型选择保持原值。';
 await loadProviderCatalog();
 }catch(e){$('provider-result').textContent=e.message}finally{body.api_key='';button.disabled=false}
};

let providerCatalogRequest=0;
async function loadProviderCatalog(){
 const provider=$('custom-provider').value.trim();if(!provider)return;
 const engine=providerEndpoint(),request=++providerCatalogRequest;$('provider-model-options').innerHTML='';
 $('provider-result').textContent='正在读取模型目录…';
 try{
  const r=await api(engine+'/provider-catalog',{provider});
  if(request!==providerCatalogRequest||engine!==providerEndpoint()||provider!==$('custom-provider').value.trim())return;
  $('provider-model-options').innerHTML=(r.models||[]).map(id=>`<option value="${esc(id)}"></option>`).join('');
  const labels={reachable:'目录已更新',auth_failed:'认证失败',insufficient_balance:'余额不足',forbidden:'无权访问',catalog_unavailable:'目录接口不可用，可手填模型',rate_limited:'请求限流',upstream_unavailable:'服务商暂不可用',connection_failed:'连接失败，可手填模型',invalid_catalog:'响应不是可识别的模型目录',http_error:'接口返回错误'};
  $('provider-result').textContent=(labels[r.status]||r.status)+' · '+(r.models||[]).length+' 个模型。未调用模型。';
 }catch(e){if(request===providerCatalogRequest)$('provider-result').textContent='目录读取失败：'+e.message+'；可手动输入模型 ID。'}
}
$('provider-catalog').onclick=loadProviderCatalog;
$('provider-test-model').onclick=()=>action(async()=>{
 $('provider-result').textContent='正在提交短工具调用…';
 const r=providerEndpoint()==='native'?await api('runtime-test',{backend:'briefloop-native',model:$('custom-provider').value.trim()+'/'+$('custom-model').value.trim()}):await api('opencode/provider-test',{provider:$('custom-provider').value.trim(),model:$('custom-model').value.trim()});
 chat.view='tests';await selectChat(r.session_id);
},'已提交模型工具测试；进展见对话，可随时停止');
$('provider-use').onclick=()=>action(async()=>{
 const model=$('provider-use').dataset.model,backend=$('provider-use').dataset.backend;
 if(backend!==backendValue())$('model-variant').value='';
 $('agent-backend').value=backend;$('model-select').value=model;
 await saveModel();await refresh();renderBackend();await refreshModelSuggestions(true);
 if(!chatActive()){chat.nextBackend=backend;chat.hostOptions={};$('chat-model').value=model;$('chat-model-provider').value='';$('chat-service-tier').value='';$('chat-variant').value=$('model-variant').value;renderChatRuntimePermissions();rememberDraft();updateComposer()}
 $('provider-result').textContent='已选用 '+model+'，下一次任务生效。';
},'模型已选用');
$('custom-protocol').onchange=()=>{
 const base=$('custom-base-url').value.replace(/\/+$/,'');
 if(['https://api.deepseek.com','https://api.deepseek.com/v1','https://api.deepseek.com/anthropic','https://api.deepseek.com/anthropic/v1'].includes(base))
 $('custom-base-url').value=$('custom-protocol').value==='anthropic-messages'?'https://api.deepseek.com/anthropic/v1':'https://api.deepseek.com';
};
$('custom-preset').onchange=()=>{
 const presets={deepseek:['DeepSeek','deepseek','chat-completions','https://api.deepseek.com'],openai:['OpenAI','openai-custom','responses','https://api.openai.com/v1'],anthropic:['Anthropic','anthropic-custom','anthropic-messages','https://api.anthropic.com/v1']};
 const p=presets[$('custom-preset').value];if(!p)return;
 ['custom-name','custom-provider','custom-protocol','custom-base-url'].forEach((id,i)=>$(id).value=p[i]);
};

$('timeout-minutes').onchange=()=>action(async()=>{
 const minutes=Number($('timeout-minutes').value);
 if(!Number.isInteger(minutes)||minutes<0||minutes>240)throw Error('目标用时请输入 0–240 的整数，0 表示不设目标');
 await api('settings',{timeout_minutes:minutes});
},'后续报告的目标用时已更新，超出目标不会自动停止');
$('hard-timeout-minutes').onchange=()=>action(async()=>{
 const minutes=Number($('hard-timeout-minutes').value);
 if(!Number.isInteger(minutes)||minutes<0||minutes>1440)throw Error('最长运行保护请输入 0–1440 的整数');
 await api('settings',{hard_timeout_minutes:minutes});
},'后续任务的最长运行保护已更新，当前任务保持原设置');
async function chooseCompanyContext(enabled){
 await api('settings',{company_context_enabled:enabled});state.settings.company_context_enabled=enabled;
 $('company-mode').value=enabled?'on':'off';$('company-choice-dialog').close();$('requirements').requestSubmit();
}
$('company-choice-enable').onclick=()=>action(()=>chooseCompanyContext(true));
$('company-choice-skip').onclick=()=>action(()=>chooseCompanyContext(false));
$('company-choice-cancel').onclick=()=>$('company-choice-dialog').close();

async function showEvidence(blockId=null){
 const version=await savedVersion();if(!version)return;
 const data=await api('evidence?version='+encodeURIComponent(version));
 $('evidence-summary').textContent=data.note;
 const states={unreviewed:'语义待审阅',needs_review:'正文已改，需复核',anchor_missing:'正文锚点已失效',source_changed:'来源已变，需复核',premise_changed:'前提依据已变，需复核',premise_cycle:'前提关联异常'};
 const rows=blockId?data.bindings.filter(b=>b.block_id===blockId):data.bindings;
 $('evidence-list').innerHTML=rows.length?rows.map(b=>`<article class="evidence-card"><span class="tag">${esc(states[b.status]||b.status)}</span><h3>${esc(b.claim.data.statement)}</h3><p>正文：${esc(b.quote)}</p>${b.claim.data.reasoning?`<p>推断依据：${esc(b.claim.data.reasoning)}</p>`:''}${b.evidence.map(e=>`<details open><summary>${esc(e.source_name)} · ${esc(evidenceLocation(e.data.locator))}</summary><p>支持范围：${esc(e.supports_quote)}</p><blockquote>${esc(e.data.excerpt)}</blockquote><p class="help">${esc(e.data.extraction_method)} · ${esc(e.data.location_status)}${e.intact?'':' · 原件或文本已变化'}</p><button type="button" data-evidence-source="${esc(e.source_id)}">查看原始来源</button></details>`).join('')}${premiseCards(b.premises||[])}</article>`).join(''):'<p>当前范围尚未登记证据绑定，不能据此判断已经核验。</p>';
 $('evidence-list').querySelectorAll('[data-evidence-source]').forEach(button=>button.onclick=()=>{ $('evidence-dialog').close();action(async()=>showSource(await api('source?id='+encodeURIComponent(button.dataset.evidenceSource)))) });
 $('evidence-dialog').showModal();
}
$('evidence-open').onclick=()=>action(()=>showEvidence());
$('evidence-close').onclick=()=>$('evidence-dialog').close();
$('evidence-selection').onclick=()=>action(()=>{
 const selection=editor?.state.selection;let id=selection?.node?.attrs.blockId;
 if(!id&&selection)for(let depth=selection.$from.depth;depth>0;depth--){id=selection.$from.node(depth).attrs.blockId;if(id)break}
 return showEvidence(id||null);
});
function evidenceLocation(locator){
 if(locator.kind==='text')return `第 ${locator.start_line}–${locator.end_line} 行`;
 if(locator.kind==='pdf')return `第 ${locator.page} 页`;
 if(locator.kind==='xlsx')return `${locator.sheet} · ${locator.cells}`;
 return locator.region?'图像指定区域':'图像原件';
}

$('review-start').onclick=()=>action(async()=>{const version=await savedVersion();if(version)await api('review',{version_id:version})},'已提交独立只读审阅');
$('review-close').onclick=()=>$('review-dialog').close();
$('review-open').onclick=()=>action(async()=>{
 const version=await savedVersion();if(!version)return;const data=await api('review-status?version='+encodeURIComponent(version));
 const states={queued:'等待审阅',running:'审阅中',complete:'已返回审阅结果',incomplete:'审阅未完成',cancelled:'已停止',open:'待处理',addressed_pending_review:'已回应，待复核',resolved:'已复核解决',dismissed_with_evidence:'有依据排除'};
 $('review-list').innerHTML=(data.conflicts||[]).filter(c=>c.status!=='resolved').map(c=>`<article class="evidence-card"><span class="tag error">来源分歧 · ${esc(states[c.status]||c.status)}</span><p>${esc(c.data.description)}</p><p>已提醒不等于已解决，需由独立Reviewer核对双方依据。</p></article>`).join('')+(data.reviews||[]).map(r=>reviewResultHTML(r,states)).join('')+(data.reviews.length?'':'<p>当前版本尚未审阅，不能视为已通过。</p>')+data.findings.map(f=>`<article class="evidence-card"><span class="tag">${esc(states[f.status]||f.status)} · ${f.data.severity==='major'?'重要问题':'一般问题'}</span><h3>${esc(f.data.description)}</h3><blockquote>${esc(f.data.report_quote)}</blockquote><p>依据：${esc(f.data.evidence)}</p><p>${esc(f.data.suggested_action)}</p><p class="help">目标版本：${esc(f.version_id)}</p>${['open','addressed_pending_review'].includes(f.status)?`<form data-finding-response="${f.id}"><select name="action"><option value="corrected">已修改当前稿</option><option value="removed">已移除相关主张</option><option value="disagree">提出有依据的异议</option></select><textarea name="reason" required placeholder="说明修改位置或异议依据"></textarea><button type="submit">提交处理说明，等待复核</button></form>`:''}</article>`).join('');
 $('review-list').querySelectorAll('[data-finding-response]').forEach(form=>form.onsubmit=e=>{e.preventDefault();action(async()=>{const target=await savedVersion();await api('review-response',{finding_id:form.dataset.findingResponse,version_id:target,action:form.elements.action.value,reason:form.elements.reason.value});$('review-dialog').close()},'处理说明已保存；只有独立复核才能关闭问题')});
 $('review-dialog').showModal();
});

$('review-revise').onclick=()=>action(async()=>{const version=await savedVersion();if(version)await api('revise-findings',{version_id:version})},'已安排一次针对性修订及独立复核');

function premiseCards(premises){return premises.map(p=>`<details><summary>间接依据／前提：${esc(p.claim?.data.statement||p.claim_id)}${p.status==='unreviewed'?'':' · 依据需复核'}</summary>${(p.evidence||[]).map(e=>`<p>${esc(e.source_name)} · ${esc(evidenceLocation(e.data.locator))}</p><blockquote>${esc(e.data.excerpt)}</blockquote><button type="button" data-evidence-source="${esc(e.source_id)}">查看原始来源</button>`).join('')}${premiseCards(p.premises||[])}</details>`).join('')}

const delivery=deliveryUI({api,notice,action,page,openBrief,savedVersion,statuses,getState:()=>state,getCurrent:()=>current,isDirty:()=>dirty,isSaving:()=>saving,office});
delivery.init();
// Optional OfficeCLI preview dialog; markup ships in index.html, module logic in office-tools.js.
$('office-preview-close').onclick=()=>$('office-preview-dialog').close();
$('source-updates-close').onclick=()=>$('source-updates-dialog').close();
$('source-updates-open').onclick=()=>action(async()=>{
 const version=await savedVersion(),data=await api('source-update-state?version='+encodeURIComponent(version)),changes=Array.isArray(data)?data:data.changes||[];
 const run=state.runs.find(item=>item.id===current.run_id),requirements=parse(run?.requirements),allowed=new Set(parse(run?.source_ids||'[]'));
 const sources=state.sources.filter(source=>allowed.has(source.id));
 $('source-refresh-source').innerHTML=sources.map(source=>`<option value="${esc(source.id)}">${esc(source.name)}</option>`).join('');
 const localTime=new Date();localTime.setMinutes(localTime.getMinutes()-localTime.getTimezoneOffset());$('source-refresh-cutoff').value=localTime.toISOString().slice(0,16);
 $('source-refresh-scope').textContent=requirements.allow_web?'按本轮已允许的联网范围和剩余预算复查；不会自动增加预算。':'本轮仅使用已有材料；在线复查不会执行，本地材料更新需上传独立的新文件。';
 $('source-refresh-submit').disabled=!sources.length;
 const availabilityLabel={after_cutoff:'本轮信息截止后披露',available_by_cutoff:'本轮信息截止前可得',availability_unknown:'可得时间未确认',overlapping_date_precision:'披露日期与截止日期重叠，需核对'};
 const sourceName=id=>state.sources.find(s=>s.id===id)?.name||id;
 $('source-updates-list').innerHTML=changes.map(change=>`<article class="evidence-card"><span class="tag">${change.review_status==='resolved'?'已复核处理':'待独立复核'} · ${esc(changeTypeLabel[change.data?.kind]||'变化性质待核对')}</span><h3>${esc(change.data?.description)}</h3><p>${esc(sourceName(change.old_source_id))} → ${esc(sourceName(change.new_source_id))}</p><p>适用范围：${esc(change.data?.scope)}</p><p>${esc(availabilityLabel[change.data?.new_availability]||'可得时间未确认')}</p><p class="help">关联 ${change.current_impacts?.versions?.length||0} 个稿件版本、${change.current_impacts?.releases?.length||0} 份正式件；历史文件保留。</p><button type="button" data-update-source="${esc(change.old_source_id)}">查看原来源</button><button type="button" data-update-source="${esc(change.new_source_id)}">查看新来源</button></article>`).join('')||'<p>当前版本没有已登记的来源更新。此状态不表示来源已全部复查。</p>';
 const refreshes=state.jobs.filter(job=>job.kind==='source_refresh'&&parse(job.payload).run_id===run?.id);
 if(refreshes.length)$('source-updates-list').insertAdjacentHTML('afterbegin','<h3>最近复查</h3>'+refreshes.slice(0,5).map(job=>{const result=parse(job.result),payload=parse(job.payload);return `<article class="evidence-card"><strong>${esc(sourceName(payload.source_id))}</strong><p>${esc(job.error||sourceRefreshOutcome(result.outcome)||statuses[job.status]||job.status)}</p>${result.new_source_id?`<button type="button" data-update-source="${esc(result.new_source_id)}">查看本次取得的快照</button>`:''}<p class="help">${esc(displayDate(job.created))}</p></article>`}).join(''));
 $('source-updates-list').querySelectorAll('[data-update-source]').forEach(button=>button.onclick=()=>{$('source-updates-dialog').close();action(async()=>showSource(await api('source?id='+encodeURIComponent(button.dataset.updateSource))))});
 $('source-updates-dialog').showModal();
});

function reviewResultHTML(review,states){
 const result=review.result||{},items=review.requirement_items||[],clauses=review.clause_items||[];
 const labels={covered:'已完成',manual:'用户安排人工填写',partial:'部分完成',missing:'未完成',unverified:'未核验',not_applicable:'不适用'};
 const kinds={reader_content:'读者内容',research_method:'研究方法',writing_preference:'写作偏好',manual_assignment:'人工安排',objective:'目标',question:'必答问题',writing:'写作偏好',manual:'人工填写'};
 const clauseChecks=result.clause_checks||[],requirementChecks=result.requirement_checks||[];
 const clauseHTML=clauseChecks.map(check=>{
  const item=clauses.find(item=>item.clause_id===check.clause_id);
  return `<li><strong>${esc(labels[check.status]||check.status)}</strong> · ${item?`<span class="tag">${esc(kinds[item.kind]||item.kind)}</span><p>${esc(item.source_quote)}</p>${item.instruction&&item.instruction!==item.source_quote?`<p>执行要求：${esc(item.instruction)}</p>`:''}`:`未知条款 ID：${esc(check.clause_id)}`}<p>理由：${esc(check.reason)}</p>${check.basis?.length?`<p>依据：</p><ul>${check.basis.map(text=>`<li>${esc(text)}</li>`).join('')}</ul>`:''}</li>`;
 }).join('');
 const requirementHTML=requirementChecks.map(check=>{
  const item=items.find(item=>item.requirement_id===check.requirement_id);
  return `<li><strong>${esc(labels[check.status]||check.status)}</strong> · ${item?`${item.kind?`<span class="tag">${esc(kinds[item.kind]||item.kind)}</span> `:''}${esc(item.text)}`:`未知要求 ID：${esc(check.requirement_id)}`}<p>理由：${esc(check.reason)}</p></li>`;
 }).join('');
 const unchecked=[...(result.unchecked_items||[]),...(result.unchecked||[]).map(description=>({description,importance:'unknown'}))];
 return `<article class="review-version"><strong>${esc(states[review.status]||review.status)}</strong><p>${esc(result.summary||'当前没有完整审阅结果')}</p><p class="help">${result.coverage_scan_complete?'已检查正文是否遗漏重要主张绑定':'重要主张覆盖尚未完成检查'}；审阅完成不代表全部要求已满足，正式交付另按当前版本的条件判断。</p>${review.requirement_index_error?`<p class="help">${esc(review.requirement_index_error)}</p>`:''}${unchecked.length?`<details class="review-checks" open><summary>尚未核验 · ${unchecked.length} 项</summary><ul>${unchecked.map(item=>`<li><span class="tag">${item.importance==='core'?'核心事项':item.importance==='supporting'?'非核心事项':'重要性未确定'}</span> ${esc(item.description)}</li>`).join('')}</ul></details>`:''}${clauseChecks.length?`<details class="review-checks" open><summary>条款核查结果 · ${clauseChecks.length} 项</summary><ul>${clauseHTML}</ul></details>`:''}${requirementChecks.length?`<details class="review-checks" open><summary>原始要求核查结果 · ${requirementChecks.length} 项</summary><ul>${requirementHTML}</ul></details>`:''}${!clauseChecks.length&&!requirementChecks.length?'<p class="help">尚无逐项要求核查结果。</p>':''}</article>`;
}

$('source-refresh-form').onsubmit=event=>{event.preventDefault();action(async()=>{
 const source=$('source-refresh-source').value,cutoff=$('source-refresh-cutoff').value;
 if(!source||!cutoff)throw Error('请选择来源与本轮信息截止时间');
 const version=await savedVersion();await api('source-refresh',{version_id:version,source_id:source,information_cutoff:new Date(cutoff).toISOString()});
 $('source-updates-dialog').close();notice('来源复查已排队，可在任务记录查看结果');
})};

function sourceRefreshOutcome(outcome){return {not_authorized:'本轮未允许联网，未执行在线复查',local_source_requires_upload:'本地来源更新需上传独立的新文件',budget_exhausted:'本轮预算已用尽，未获取新快照',fetch_failed:'新快照读取未成功，保留原来源',unchanged_snapshot:'实际取得的快照未变化',changed_needs_review:'取得的快照有变化，待判断影响并独立复核'}[outcome]||''}

let connectorPanel=null;
function settingsView(name){
 for(const view of ['models','execution','learning','workspaces','connectors','updates'])$('settings-view-'+view).hidden=view!==name;
 document.querySelectorAll('[data-settings-view]').forEach(b=>{b.classList.toggle('active',b.dataset.settingsView===name);b.setAttribute('aria-current',b.dataset.settingsView===name?'page':'false')});
 if(name==='workspaces')return renderSettingsWorkspaces();
 if(name==='updates'){activity?.readCategory('updates');return appUpdates.refreshAppUpdates();}
 if(name==='connectors'){
  connectorPanel ||= connectorSettings($('settings-view-connectors'),api);
  return connectorPanel.refresh();
 }
}
async function renderSettingsWorkspaces(){
 const box=$('settings-workspace-list');if(!box)return;
 box.innerHTML='<p class="help">正在读取工作区…</p>';
 try{await refreshWorkspaces()}catch(e){box.innerHTML='<p class="help">无法读取工作区：'+esc(e.message)+'</p>';return}
 workspaceChoices(box,workspaceInventory||{workspaces:[],current:{}},'settings-workspace');
}
function settingsModelTab(name){
 $('settings-cli').hidden=name!=='cli';$('settings-api').hidden=name!=='api';
 for(const tab of ['cli','api'])$('settings-tab-'+tab).setAttribute('aria-selected',String(tab===name));
}
{
 document.querySelector('main').append($('settings-dialog'));
 $('settings-api').append($('provider-dialog'));
 document.querySelectorAll('[data-settings-view]').forEach(b=>b.onclick=()=>settingsView(b.dataset.settingsView));
 if($('settings-workspace-open'))$('settings-workspace-open').onclick=()=>openWorkspaceFrom('settings-workspace-path',false);
 if($('settings-workspace-create'))$('settings-workspace-create').onclick=()=>openWorkspaceFrom('settings-workspace-new',true);
 $('settings-tab-cli').onclick=()=>settingsModelTab('cli');
 $('settings-tab-api').onclick=()=>{if($('provider-engine').value!=='briefloop-native'){$('provider-engine').value='briefloop-native';$('provider-engine').onchange()}else $('provider-open').click()};
 $('agent-backend').closest('label').classList.add('runtime-select-legacy');
 document.querySelector('.model-settings legend').textContent='当前模型与角色';
}
/* ===== Report workspace redesign (see DESIGN.md) ===== */
const REPORT_TABS=['assistant','sources','checks'];
function setReportTab(name){
 const panel=$('report-panel');if(!panel)return;
 if(!REPORT_TABS.includes(name))name='assistant';
 panel.querySelectorAll('[data-pane]').forEach(p=>{p.hidden=p.dataset.pane!==name});
 document.querySelectorAll('#report-panel [data-report-tab],#report-tabs [data-report-tab]').forEach(b=>b.classList.toggle('active',b.dataset.reportTab===name));
 expandReportPanel();
}
function outlineHeadings(){
 const out=[];
 if(editor&&editor.state&&editor.state.doc)editor.state.doc.descendants(node=>{if(node.type.name==='heading')out.push({level:Number(node.attrs.level)||2,text:node.textContent,blockId:node.attrs.blockId||null})});
 if(out.length)return out;
 let fenced=false;for(const line of String((current&&current.markdown)||'').split('\n')){if(/^\s*(```|~~~)/.test(line)){fenced=!fenced;continue}if(fenced)continue;const m=/^(#{1,3})\s+(.+?)\s*$/.exec(line);if(m)out.push({level:m[1].length,text:m[2],blockId:null})}
 return out;
}
function setReportView(view){
 if(!['edit','outline'].includes(view))view='edit';
 const grid=$('report-grid'),outline=$('report-outline');
 document.querySelectorAll('#report-tabs [data-report-view]').forEach(b=>b.classList.toggle('active',b.dataset.reportView===view));
 if(outline)outline.hidden=view!=='outline';
 if(grid)grid.hidden=view==='outline';
 if(view==='outline')renderOutline();
}
function outlineText(){return outlineHeadings().map(h=>'#'.repeat(h.level)+' '+h.text).join('\n')}
function jumpToOutline(index){
 setReportView('edit');const item=outlineHeadings()[index];if(!item)return;
 const root=(editor&&editor.view&&editor.view.dom)||$('editor');if(!root)return;
 const headings=[...root.querySelectorAll('h1,h2,h3')];
 const hit=(item.blockId&&headings.find(h=>h.getAttribute('data-block-id')===item.blockId))||headings[index];
 if(hit)hit.scrollIntoView({block:'center',behavior:'smooth'});
}
let outlineDraft={key:null,text:null};
function renderOutline(){
 const box=$('report-outline');if(!box)return;const items=outlineHeadings();const key=(current&&current.id)||'';const base=outlineText();const text=(outlineDraft.key===key&&outlineDraft.text!=null)?outlineDraft.text:base;
 const list=items.length?`<ul class="outline-list">${items.map((h,i)=>`<li class="outline-lv${h.level}"><button type="button" data-outline-index="${i}">${esc(h.text)}</button></li>`).join('')}</ul>`:'<p class="help">这份报告还没有小标题。</p>';
 box.innerHTML=`<p class="help">点击章节定位到正文；下面每行一个章节，用 # / ## 表示层级（1–3 级）。可增删或调整顺序，然后把它带到「材料与需求」作为下一份简报的章节。</p>${list}<textarea id="outline-text" class="outline-text" rows="12" spellcheck="false" placeholder="## 核心摘要&#10;## 需求与竞争">${esc(text)}</textarea><div class="outline-actions"><button type="button" id="outline-apply" class="primary">用它做下一份报告 →</button><button type="button" id="outline-reset" class="outline">从当前稿件还原</button></div>`;
 box.querySelectorAll('[data-outline-index]').forEach(b=>b.onclick=()=>jumpToOutline(Number(b.dataset.outlineIndex)));
 const t=$('outline-text');if(t)t.oninput=()=>{outlineDraft={key,text:t.value}};
 const reset=$('outline-reset');if(reset)reset.onclick=()=>{outlineDraft={key:null,text:null};if(t)t.value=base};
 const apply=$('outline-apply');if(apply)apply.onclick=()=>applyOutlineToSetup();
}
function applyOutlineToSetup(){
 const value=($('outline-text')?.value||'').trim();
 const titles=value?value.split('\n').map(l=>l.replace(/^#{1,6}\s*/,'').trim()).filter(Boolean):[];
 if(!titles.length){notice('大纲为空',true);return}
 applyRequirements(JSON.stringify({manual_sections:titles}));
}
function expandReportPanel(){const grid=$('report-grid');if(grid)grid.classList.remove('panel-collapsed');try{localStorage.setItem('briefloop-report-panel','open')}catch{}}
function collapseReportPanel(){const grid=$('report-grid');if(grid)grid.classList.add('panel-collapsed');try{localStorage.setItem('briefloop-report-panel','closed')}catch{}}
function toggleReportPanel(){const grid=$('report-grid');if(!grid)return;grid.classList.contains('panel-collapsed')?expandReportPanel():collapseReportPanel()}
function setReportChatOpen(open){
 const chatEl=$('chat');
 if(open){if(!chatEl)return;chatEl.hidden=false;document.body.classList.add('report-chat-open')}
 else{document.body.classList.remove('report-chat-open');if(chatEl)chatEl.hidden=true}
 const close=$('report-chat-close');if(close)close.hidden=!open;
 const backdrop=$('report-chat-backdrop');if(backdrop)backdrop.hidden=!open;
}
function openReportChat(sessionId){
 if(!$('chat'))return;
 setReportChatOpen(true);
 if(sessionId&&chat.sessions.some(s=>s.id===sessionId)&&chat.id!==sessionId)selectChat(sessionId).catch(()=>{});
 const input=$('chat-input');if(input)setTimeout(()=>input.focus(),0);
}
function closeReportChat(){setReportChatOpen(false)}
function expandReportChat(sessionId){openReportChat(sessionId)}
function renderReportStatus(){
 const box=$('report-status');if(!box)return;if(!current){box.innerHTML='';return}
 const chips=[];
 const a=current&&state.assessments.find(x=>x.version_id===current.id);
 if(a){const d=parse(a.data);chips.push(d.status==='complete'?`<span class="chip ok">已评分${d.overall?' · '+esc(d.overall):''}</span>`:'<span class="chip">评分中</span>')}
 else{const pending=!!(current&&reviewPending(current,state.jobs));chips.push(pending?'<span class="chip">评分中</span>':'<span class="chip warn">未评分</span>')}
 const conflicts=runConflicts(current.run_id).length;if(conflicts)chips.push(`<span class="chip danger">来源分歧 ${conflicts}</span>`);
 box.innerHTML=chips.join('');
}
function renderAssistantSummary(){
 const box=$('assistant-summary');if(!box)return;
 if(!current){box.innerHTML='';return}
 const run=(state.runs||[]).find(r=>r.id===current.run_id),req=run?parse(run.requirements):{};
 const conflicts=runConflicts(current.run_id).length;
 const tc=req.time_context;
 const kv=[['系统核对日期',tc?`${tc.today} · ${tc.timezone}`:'旧任务未记录'],['时间范围',tc?`${tc.start} 至 ${tc.end_exclusive}（不含结束时刻）`:req.period],['读者',req.audience],['已登记来源',runSourceCount(current.run_id)+' 个']].filter(([,v])=>v);
 const cards=[];
 if(kv.length)cards.push(`<dl class="assistant-card">${kv.map(([k,v])=>`<div class="kv"><dt>${esc(k)}</dt><dd>${esc(String(v))}</dd></div>`).join('')}</dl>`);
 cards.push(`<div class="assistant-card"><h3>需要关注</h3>${conflicts?`<div class="attention"><span class="badge danger">数据冲突</span><span>有 ${conflicts} 项来源分歧待处理</span></div>`:'<p>暂未发现待处理冲突；评分与审阅完成后会显示在这里。</p>'}</div>`);
 box.innerHTML=cards.join('');
}
function sendReportQuestion(text){
 const q=(text||'').trim();if(!q)return;
 const input=$('assistant-input');if(input)input.value='';
 openReportChat();
 const chatInput=$('chat-input');if(chatInput)chatInput.value=q;
 if(typeof rememberDraft==='function')rememberDraft();
 const form=$('chat-form');if(form)form.requestSubmit();
}
document.querySelectorAll('#report-panel [data-report-tab]').forEach(b=>b.onclick=()=>setReportTab(b.dataset.reportTab));
document.querySelectorAll('#report-tabs [data-report-tab]').forEach(b=>b.onclick=()=>{setReportView('edit');setReportTab(b.dataset.reportTab)});
document.querySelectorAll('#report-tabs [data-report-view]').forEach(b=>b.onclick=()=>setReportView(b.dataset.reportView));
if($('report-panel-toggle'))$('report-panel-toggle').onclick=toggleReportPanel;
document.querySelectorAll('.menu-wrap').forEach(wrap=>{const toggle=wrap.querySelector('button[aria-haspopup="menu"]'),pop=wrap.querySelector('.popover');if(!toggle||!pop)return;toggle.onclick=e=>{e.stopPropagation();const open=pop.hidden;document.querySelectorAll('.popover').forEach(p=>p.hidden=true);document.querySelectorAll('[aria-haspopup="menu"]').forEach(b=>b.setAttribute('aria-expanded','false'));pop.hidden=!open;toggle.setAttribute('aria-expanded',String(open))}});
document.addEventListener('click',e=>{if(e.target.closest?.('.export-template-row')||e.target.closest?.('.composer-params'))return;document.querySelectorAll('.popover').forEach(p=>p.hidden=true);document.querySelectorAll('[aria-haspopup="menu"]').forEach(b=>b.setAttribute('aria-expanded','false'))});
document.addEventListener('keydown',e=>{if(e.key==='Escape'){document.querySelectorAll('.popover').forEach(p=>p.hidden=true);document.querySelectorAll('[aria-haspopup="menu"]').forEach(b=>b.setAttribute('aria-expanded','false'))}});
if($('assistant-form'))$('assistant-form').onsubmit=e=>{e.preventDefault();sendReportQuestion($('assistant-input').value)};
document.querySelectorAll('[data-assistant-prompt]').forEach(b=>b.onclick=()=>{const input=$('assistant-input');if(input){input.value=b.dataset.assistantPrompt;input.focus()}});
try{if(localStorage.getItem('briefloop-report-panel')==='closed')collapseReportPanel()}catch{}
/* ===== Object pages: reports / sources / templates ===== */
function reportStatus(b){
 const a=(state.assessments||[]).find(x=>x.version_id===b.id);
 if(a){const d=parse(a.data);if(d.status==='complete')return {key:'scored',label:'已评分'+(d.overall?' · '+d.overall:''),cls:'ok'}}
 if((state.jobs||[]).some(j=>j.kind==='release'&&j.status==='complete'&&parse(j.payload).version_id===b.id))return {key:'released',label:'已正式交付',cls:'ok'};
 if((state.jobs||[]).some(j=>['generate','revise','assess','review'].includes(j.kind)&&['queued','running'].includes(j.status)&&(()=>{const p=parse(j.payload);return p.run_id===b.run_id||p.version_id===b.id})()))return {key:'running',label:'处理中',cls:''};
 return {key:'draft',label:'草稿',cls:''};
}
function runSourceCount(runId){return runSourceIds((state.runs||[]).find(r=>r.id===runId)).length}
function runConflicts(runId){
 const run=(state.runs||[]).find(r=>r.id===runId);let ids=[];
 try{ids=run?(Array.isArray(run.all_source_ids)?run.all_source_ids:JSON.parse(run.source_ids||'[]')):[]}catch{}
 const scoped=new Set(ids);
 return (state.conflicts||[]).filter(c=>{
  if(c.run_id)return c.run_id===runId;
  try{return (JSON.parse(c.data).source_ids||[]).some(id=>scoped.has(id))}catch{return false}
 });
}
function reportDescription(b){const run=(state.runs||[]).find(r=>r.id===b.run_id);const req=run?parse(run.requirements):{};if(req.objective)return req.objective;const md=(b.markdown||b.excerpt||'').replace(/[#>*`\[\]]/g,' ').replace(/\s+/g,' ').trim();return md.slice(0,120)}
function renderReports(){
 const box=$('reports-list');if(!box||!state)return;
 const seen=new Set(),all=[];
 for(const b of (state.briefs||[])){if(seen.has(b.run_id))continue;seen.add(b.run_id);all.push(b)}
 const q=($('reports-search')?.value||'').trim().toLowerCase();
 const fStatus=$('reports-filter-status')?.value||'',fTime=$('reports-filter-time')?.value||'',fSource=$('reports-filter-source')?.value||'';
 const view=renderReports.view||'list';
 const rows=all.filter(b=>{
  const st=reportStatus(b),sources=runSourceCount(b.run_id);
  if(fStatus&&st.key!==fStatus)return false;
  if(fTime){const days=(Date.now()-new Date(b.updated||b.created).getTime())/86400000;if(days>Number(fTime))return false}
  if(fSource==='yes'&&!sources)return false;
  if(fSource==='no'&&sources)return false;
  if(q){const hay=((parse(b.detail).title||'')+' '+reportDescription(b)).toLowerCase(),found=renderReports.found;if(!hay.includes(q)&&!(found?.q===q&&found.runs.has(b.run_id)))return false}
  return true;
 });
 box.className='report-list'+(view==='grid'?' grid':'');
 const makingFirst=!state.briefs.length&&state.jobs.some(j=>j.kind==='generate'&&['queued','running'].includes(j.status));
 const sig=JSON.stringify([view,q,fStatus,fTime,fSource,makingFirst,all.length,rows.map(b=>{const st=reportStatus(b);return [b.id,b.status,b.updated,st.label,runSourceCount(b.run_id)]})]);if(renderReports.sig===sig)return;renderReports.sig=sig;
 const end=$('reports-end');if(end)end.hidden=!rows.length;
 box.innerHTML=rows.length?rows.map(b=>{const st=reportStatus(b),sources=runSourceCount(b.run_id),desc=reportDescription(b);const when=dayTime(b.updated||b.created),meta=reportIconMeta(b);return `<article class="report-card"><span class="report-card-icon ${meta.cat}" aria-hidden="true">${svgLineIcon(meta.icon,24)}</span><div class="report-card-body"><h3 class="report-card-title">${esc(parse(b.detail).title||'简报')}</h3>${desc?`<p class="report-card-desc">${esc(desc)}</p>`:''}<div class="report-card-meta"><span>${sources} 个来源</span><span>${esc(when)} 最后编辑</span></div></div><div class="report-card-side"><span class="chip ${st.cls}">${esc(st.label)}</span><button type="button" class="primary" data-report-open="${esc(b.id)}">${st.key==='draft'?'继续编辑':'打开'}</button><div class="menu-wrap report-card-menu"><button type="button" class="ghost" data-report-menu aria-haspopup="menu" aria-expanded="false" aria-label="更多操作">⋯</button><div class="popover" role="menu" hidden><button type="button" role="menuitem" data-report-open="${esc(b.id)}">打开</button><a role="menuitem" href="/api/download?version=${encodeURIComponent(b.id)}">下载 Markdown</a><button type="button" role="menuitem" data-report-release="${esc(b.id)}">正式交付与审计包</button></div></div></div></article>`}).join(''):(makingFirst?'<div class="empty-inline"><strong>首份报告正在制作</strong><p class="help">初稿保存后会显示在这里。</p></div>':all.length?'<div class="empty-inline"><p class="help">没有符合筛选条件的报告，请调整搜索或筛选。</p></div>':'<div class="empty-inline"><p class="help">还没有报告。使用左侧「新建报告」开始制作。</p></div>');
 box.querySelectorAll('[data-report-open]').forEach(el=>el.onclick=()=>{const b=state.briefs.find(x=>x.id===el.dataset.reportOpen);if(b&&openBrief(b,{follow:false}))page('report')});
 box.querySelectorAll('[data-page="setup"]').forEach(el=>el.onclick=()=>page('setup'));
 box.querySelectorAll('.report-card-menu').forEach(wrap=>{const toggle=wrap.querySelector('[data-report-menu]'),pop=wrap.querySelector('.popover');if(!toggle||!pop)return;toggle.onclick=e=>{e.stopPropagation();const open=pop.hidden;document.querySelectorAll('.popover').forEach(p=>p.hidden=true);document.querySelectorAll('[aria-haspopup="menu"]').forEach(b=>b.setAttribute('aria-expanded','false'));pop.hidden=!open;toggle.setAttribute('aria-expanded',String(open))}});
 box.querySelectorAll('[data-report-release]').forEach(el=>el.onclick=()=>{const b=state.briefs.find(x=>x.id===el.dataset.reportRelease);if(b)return action(()=>delivery.openReleaseDialog(b))});
}
function sourceState(s){return s.status==='failed'?'failed':s.needs_visual?'visual':'ready'}
function sourceStatusChip(st){return `<span class="chip ${st==='ready'?'ok':st==='visual'?'warn':'danger'}">${st==='ready'?'可用':st==='visual'?'需视觉读取':'获取失败'}</span>`}
function sourceIsWeb(s){return !!(s&&s.url)}
function sourceHost(s){try{return new URL(s.url||s.name).hostname.replace(/^www\./,'')}catch{return ''}}
function sourceTitle(s){const n=(s&&s.name)||'';let label;if(n&&!/^https?:\/\//i.test(n))label=n;else{const u=(s&&s.url)||n;try{const p=new URL(u);let seg=decodeURIComponent((p.pathname.split('/').filter(Boolean).pop()||''));seg=seg.replace(/\.[a-z0-9]{1,5}$/i,'').replace(/[-_]+/g,' ').trim();label=seg||p.hostname.replace(/^www\./,'')}catch{label=n||(s&&s.id)||'来源'}}return label.length>160?label.slice(0,159)+'…':label}
function prettifyUrl(url){if(!url)return '';let out=url;try{const p=new URL(url);out=p.hostname.replace(/^www\./,'')+decodeURI(p.pathname)+(p.search||'')}catch{}return out.length>110?out.slice(0,107)+'…':out}
function runSourceIds(run){if(!run)return[];if(Array.isArray(run.all_source_ids))return run.all_source_ids;try{const ids=parse(run.source_ids);return Array.isArray(ids)?ids:[]}catch{return[]}}
function sourceUsage(){const map=new Map();for(const run of(state.runs||[])){const ids=runSourceIds(run);const brief=(state.briefs||[]).find(b=>b.run_id===run.id);const title=brief?(parse(brief.detail).title||'简报'):'报告';for(const id of ids){if(!map.has(id))map.set(id,[]);map.get(id).push({run_id:run.id,title})}}return map}
function usageHTML(id,usage){const u=((usage||sourceUsage()).get(id))||[];return u.length?`<ul class="source-usage">${u.map(x=>`<li><button type="button" data-open-report="${esc(x.run_id)}">${esc(x.title)}<span aria-hidden="true">→</span></button></li>`).join('')}</ul>`:'<p class="help">还没有报告使用这个来源。</p>'}
function wireUsage(pane){if(!pane)return;pane.querySelectorAll('[data-open-report]').forEach(b=>b.onclick=()=>{const brief=(state.briefs||[]).find(x=>x.run_id===b.dataset.openReport);if(brief&&openBrief(brief,{follow:false})){closeSourceDrawer();page('report')}})}
$('sources-channel-filter').onchange=()=>{renderSourcesPage.sig=null;renderSourcesPage()};
const sourceLibrarySearch=createSourceLibrarySearch({api,onChange:()=>{renderSourcesPage.sig=null;renderSourcesPage()}});
const sourceSearchReasons={failed:'获取失败',visual_partial:'仅检索提取文字，图片未检索',empty_text:'没有可读正文',too_large:'正文超过2 MiB，未检索',changed:'文件已被改动，未检索',unreadable:'正文无法读取',metadata_unreadable:'读取状态无法确认，未检索'};
function renderSourcesPage(){
 const box=$('sources-page-list');if(!box||!state)return;
 const all=state.sources||[],usage=sourceUsage();
 const q=($('sources-search')?.value||'').trim();
 const type=$('sources-type-filter')?.value||'';
 const channel=$('sources-channel-filter')?.value||'';
 const status=renderSourcesPage.status||'';
 sourceLibrarySearch.update({query:q,type,channel,status,workspace:state.workspace_id,
  sources:all.map(s=>[s.id,s.hash,s.status,s.needs_visual,s.name,s.url,s.discovery_providers])});
 const search=sourceLibrarySearch.state,matches=new Map(search.items.map(item=>[item.source_id,item]));
 const failed=all.filter(s=>sourceState(s)==='failed');
 const rows=all.filter(s=>{
  if(type&&(type==='web'?!sourceIsWeb(s):sourceIsWeb(s)))return false;
  if(channel&&(channel==='unrecorded'?(s.discovery_providers||[]).length:!(s.discovery_providers||[]).includes(channel)))return false;
  if(status&&sourceState(s)!==status)return false;
  if(q&&!matches.has(s.id))return false;
  return true;
 });
 if($('sources-page-count'))$('sources-page-count').textContent=q?
  `已检查 ${search.scanned}/${search.scanned||search.exhausted?search.total:'…'} 个候选，找到 ${search.items.length} 个来源`+(search.unsearchedCount?` · ${search.unsearchedCount} 份正文未完整检索`:'')+(search.exhausted&&!search.unsearchedCount?' · 搜索完成':''):
  `共 ${all.length} 个来源`+(failed.length?` · ${failed.length} 个获取失败`:'');
 const retry=$('sources-retry-all');if(retry){retry.hidden=!failed.length;retry.disabled=!failed.length}
 const sig=JSON.stringify([q,type,status,channel,search,rows.map(s=>{const u=usage.get(s.id)||[];return [s.id,s.status,s.needs_visual,s.name,s.url,s.created,u.length,s.discovery_providers]})]);if(renderSourcesPage.sig===sig)return;renderSourcesPage.sig=sig;
 box.innerHTML=rows.length?rows.map(s=>{const host=sourceHost(s),web=sourceIsWeb(s),u=usage.get(s.id)||[],st=sourceState(s);const when=day(s.created);return `<div class="sources-row" data-src-row="${esc(s.id)}"><div class="src-name"><span class="src-title">${esc(sourceTitle(s))}</span><small>${esc(host||'本地文件')} · ${esc(when)} 更新${(s.discovery_providers||[]).length?' · 发现：'+esc(s.discovery_providers.map(p=>SEARCH_LABELS[p]||p).join('、')):''}</small></div><div class="src-type">${web?'网站':'文件'}</div><div class="src-status">${sourceStatusChip(st)}</div><div class="src-usage">${u.length?esc(u.length+' 份报告'):'—'}</div><div class="src-actions"><div class="menu-wrap"><button type="button" class="ghost" data-src-menu aria-haspopup="menu" aria-expanded="false" aria-label="更多">⋯</button><div class="popover" role="menu" hidden>${st!=='ready'?'<button type="button" role="menuitem" data-sources-retry="'+esc(s.id)+'">重新读取</button>':''}${web?'<button type="button" role="menuitem" data-sources-copy="'+esc(s.url||'')+'">复制链接</button><a role="menuitem" href="'+esc(s.url)+'" target="_blank" rel="noreferrer">打开原文</a>':''}</div></div></div></div>`}).join(''):'<p class="help">没有匹配的来源。</p>';
 if(q){
  if(!rows.length)box.innerHTML=`<p class="help">${search.loading?'正在检索本地材料…':search.error?'搜索未完成。':search.exhausted?(search.unsearchedCount?'已检索正文未找到匹配，仍有正文未能读取。':'没有匹配的来源。'):'本页未命中，仍有材料未检索。'}</p>`;
  box.querySelectorAll('[data-src-row]').forEach(row=>{const match=matches.get(row.dataset.srcRow),name=row.querySelector('.src-name');for(const hit of match?.hits||[]){const context=document.createElement('p');context.className='source-search-context';context.textContent=`第 ${hit.start_line} 行 · ${hit.context}`;name.append(context)}});
  const footer=document.createElement('div');footer.className='source-search-footer';
  if(search.unsearchedCount){const details=document.createElement('details'),summary=document.createElement('summary');summary.textContent=`${search.unsearchedCount} 份正文未完整检索`;details.append(summary);for(const item of search.unsearched){const line=document.createElement('p');line.textContent=`${item.name}：${sourceSearchReasons[item.reason]||'未检索'}`;details.append(line)}footer.append(details)}
  if(search.error){const error=document.createElement('p');error.textContent=search.error;footer.append(error);const restart=document.createElement('button');restart.type='button';restart.className='outline';restart.textContent='重新搜索';restart.onclick=()=>{sourceLibrarySearch.update({});renderSourcesPage.sig=null;renderSourcesPage()};footer.append(restart)}
  else if(search.next||search.loading){const more=document.createElement('button');more.type='button';more.className='outline';more.textContent=search.loading?'正在检索…':'继续搜索';more.disabled=search.loading;more.onclick=()=>sourceLibrarySearch.more();footer.append(more)}
  box.append(footer);
 }
 box.querySelectorAll('[data-src-row]').forEach(row=>row.onclick=e=>{if(e.target.closest('.menu-wrap'))return;openSourceDrawer(row.dataset.srcRow,usage,matches.get(row.dataset.srcRow)).catch(err=>notice(err.message,true))});
 box.querySelectorAll('[data-sources-retry]').forEach(b=>b.onclick=e=>{e.stopPropagation();action(async()=>{const src=await api('retry-source',{source_id:b.dataset.sourcesRetry});notice(src.status==='ready'?'来源已重新读取':src.error,src.status!=='ready')})});
 box.querySelectorAll('[data-sources-copy]').forEach(b=>b.onclick=async e=>{e.stopPropagation();try{await copyText(b.dataset.sourcesCopy);notice('链接已复制')}catch{notice('复制失败',true)}});
 box.querySelectorAll('.menu-wrap').forEach(wrap=>{const t=wrap.querySelector('[data-src-menu]'),pop=wrap.querySelector('.popover');if(!t||!pop)return;t.onclick=e=>{e.stopPropagation();const open=pop.hidden;document.querySelectorAll('.popover').forEach(x=>x.hidden=true);pop.hidden=!open;t.setAttribute('aria-expanded',String(open))}});
}
function renderSourceOverview(s,result,usage){
 const pane=document.querySelector('[data-source-pane="overview"]');if(!pane)return;
 const st=sourceState(s),host=sourceHost(s);
 const date=dateTime(s.created);
 const statusText=st==='ready'?'正文已成功解析':st==='visual'?'正文提取不完整，需要视觉读取':'未能解析正文';
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
 drawer.querySelectorAll('[data-source-tab]').forEach(b=>b.classList.toggle('active',b.dataset.sourceTab===name));
}
async function openSourceDrawer(id,usage,match){
 const s=(state.sources||[]).find(x=>x.id===id);if(!s)return;
 const drawer=$('source-drawer'),backdrop=$('source-drawer-backdrop');if(!drawer)return;
 const workspace=state.workspace_id;
 drawer.dataset.sourceId=id;
 const request=String((Number(drawer.dataset.request)||0)+1);drawer.dataset.request=request;
 const host=sourceHost(s);
 const brand=$('source-drawer-brand');if(brand)brand.textContent=host||'本地文件';
 const title=$('source-drawer-title');if(title)title.textContent=sourceTitle(s);
 const domain=$('source-drawer-domain');if(domain)domain.textContent=s.url?prettifyUrl(s.url):'';
 const chips=$('source-drawer-chips');if(chips)chips.innerHTML=`<span class="chip">${sourceIsWeb(s)?'网站':'文件'}</span>${sourceStatusChip(sourceState(s))}`;
 const original=$('source-drawer-original');if(original){original.hidden=true;original.removeAttribute('href')}
 const link=$('source-drawer-source');if(link){link.hidden=true;link.removeAttribute('href')}
 const retry=$('source-drawer-retry');if(retry)retry.onclick=()=>action(async()=>{const r=await api('retry-source',{source_id:s.id});notice(r.status==='ready'?'来源已重新读取':r.error,r.status!=='ready')});
 renderSourceOverview(s,null,usage);
 const up=document.querySelector('[data-source-pane="usage"]');if(up){up.innerHTML=usageHTML(s.id,usage);wireUsage(up)}
 const body=$('source-drawer-body');if(body)body.textContent='正在读取…';showSourceMedia({source:s,attachment:null},drawerSourceMediaView());
 drawer.hidden=false;if(backdrop)backdrop.hidden=false;
 setSourceDrawerTab('overview');
 let result=null;
 try{result=await api('source?id='+encodeURIComponent(id))}catch(e){if(drawer.dataset.request===request&&state.workspace_id===workspace&&body)body.textContent='读取失败：'+e.message}
 if(!result||drawer.dataset.request!==request||state.workspace_id!==workspace)return;
 applySourceLinks(result,{link:$('source-drawer-source'),original:$('source-drawer-original')});
 renderSourceOverview(s,result,usage);
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
function closeSourceDrawer(){const d=$('source-drawer'),b=$('source-drawer-backdrop');if(d){d.hidden=true;d.dataset.request=String((Number(d.dataset.request)||0)+1)}if(b)b.hidden=true}
const templatesPage=templatesUI({api,notice,action,page,renderWorkflowChoices,templateSections,getState:()=>state,setSettings:next=>state.settings=next});
if($('new-report'))$('new-report').onclick=()=>page('setup');
if($('sources-upload'))$('sources-upload').onchange=e=>action(async()=>{preflightSources(e.target.files,getUploadLimits());for(const f of e.target.files){await uploadSource(f)}e.target.value=''},'来源已保存');
if($('sources-add-url'))$('sources-add-url').onclick=()=>action(async()=>{const s=await api('source-url',{url:$('sources-url').value});$('sources-url').value='';const row=$('sources-add-url-row');if(row)row.hidden=true;notice(s.status==='ready'?'网页已读取':'来源已保存，但读取失败：'+s.error,s.status!=='ready')});
if($('templates-upload'))$('templates-upload').onchange=e=>action(async()=>{const file=e.target.files[0];if(!file)return;await api('template-import',await uploadPayload(file,getUploadLimits()));e.target.value='';notice('模板已上传，BriefLoop 将准备章节和版式')});
if($('sources-search'))$('sources-search').oninput=()=>{renderSourcesPage.sig='';renderSourcesPage()};
if($('sources-type-filter'))$('sources-type-filter').onchange=()=>{renderSourcesPage.sig='';renderSourcesPage()};
document.querySelectorAll('[data-sources-status]').forEach(b=>b.onclick=()=>{renderSourcesPage.status=b.dataset.sourcesStatus;document.querySelectorAll('[data-sources-status]').forEach(x=>x.classList.toggle('active',x===b));renderSourcesPage.sig='';renderSourcesPage()});
if($('sources-retry-all'))$('sources-retry-all').onclick=()=>action(async()=>{const list=(state.sources||[]).filter(s=>s.status==='failed');if(!list.length)return;for(const s of list){try{await api('retry-source',{source_id:s.id})}catch(e){}}notice(`已重试 ${list.length} 个失败来源`)});
if($('sources-add-file'))$('sources-add-file').onclick=()=>$('sources-upload').click();
if($('sources-add-url-open'))$('sources-add-url-open').onclick=()=>{const row=$('sources-add-url-row');if(row){row.hidden=false;const u=$('sources-url');if(u)u.focus()}};
if($('sources-add-url-cancel'))$('sources-add-url-cancel').onclick=()=>{const row=$('sources-add-url-row');if(row)row.hidden=true};
if($('source-drawer-close'))$('source-drawer-close').onclick=()=>closeSourceDrawer();
if($('source-drawer-backdrop'))$('source-drawer-backdrop').onclick=()=>closeSourceDrawer();
document.querySelectorAll('[data-source-tab]').forEach(b=>b.onclick=()=>setSourceDrawerTab(b.dataset.sourceTab));
if($('reports-search'))$('reports-search').oninput=()=>{renderReports.sig='';renderReports();const q=$('reports-search').value.trim().toLowerCase();clearTimeout(renderReports.searchTimer);if(q)renderReports.searchTimer=setTimeout(()=>api('report-search?q='+encodeURIComponent(q)).then(r=>{if($('reports-search').value.trim().toLowerCase()!==q)return;renderReports.found={q,runs:new Set(r.run_ids)};renderReports.sig='';renderReports()}).catch(()=>{}),250)};
['reports-filter-status','reports-filter-time','reports-filter-source'].forEach(id=>{const el=$(id);if(el)el.onchange=()=>{renderReports.sig='';renderReports()}});
document.querySelectorAll('[data-reports-view]').forEach(b=>b.onclick=()=>{renderReports.view=b.dataset.reportsView;document.querySelectorAll('[data-reports-view]').forEach(x=>x.classList.toggle('active',x===b));renderReports.sig='';renderReports()});
if($('report-tasks-all'))$('report-tasks-all').onclick=()=>{renderTasks.showAll=!renderTasks.showAll;renderTasks();renderTaskGraph()};
if($('report-chat-expand'))$('report-chat-expand').onclick=()=>openReportChat();
if($('report-chat-close'))$('report-chat-close').onclick=()=>closeReportChat();
if($('report-chat-backdrop'))$('report-chat-backdrop').onclick=()=>closeReportChat();

const reportMcpSelection=mcpSelection($('report-mcp-selection'),api);

// Desktop hosts request an acknowledged flush; ordinary web pages keep their unload behavior.
if(window.briefloopDesktop?.onPrepareClose){
 window.briefloopDesktop.onResume(()=>{document.body.inert=false});
 window.briefloopDesktop.onPrepareClose(async()=>{
  try{
   if(chat.busy||chat.uploading)return {status:'failed',error:'消息正在发送或附件正在上传，请完成后再关闭。'};
   document.body.inert=true;rememberDraft();clearTimeout(saveTimer);
   if(current)await savedVersion();
   return {status:'saved',version_id:current?.id||null};
  }catch(error){document.body.inert=false;return {status:'failed',error:error.message||'修改尚未保存，请保留窗口。'}}
 });
}

const appUpdates=appUpdatesUI({api,notice});
appUpdates.init();

activity=activityCenter({api,getState:()=>state,page,openBrief,showSettings,settingsView});

async function showSearchActivity(runId){
 const dialog=$('search-activity-dialog');dialog.showModal();$('search-activity-body').textContent='正在读取…';
 try{const result=await api('search-activity?run='+encodeURIComponent(runId));const p=result.policy,native=p.primary_provider==='native'||(p.coverage_mode!=='primary_only'&&p.native_search_enabled);
 $('search-activity-body').innerHTML=`<p>优先 ${esc(SEARCH_LABELS[p.primary_provider])}${native?' · 宿主原生搜索次数：未知':''}</p>`+result.records.map(r=>`<article class="panel"><strong>${esc(SEARCH_LABELS[r.provider]||r.provider)} · ${esc(r.outcome==='success'?'已返回':'失败')}</strong><p>${esc(r.query||'')}</p><p class="help">${esc(r.reason||r.purpose||'')} · ${esc(r.elapsed_ms??'未知')} ms${r.http_status?' · 状态 '+esc(r.http_status):''}${r.failure_kind?' · '+esc(r.failure_kind):''} · ${r.admitted_urls?.length||0} 个已接纳候选</p></article>`).join('');
 if(!result.records.length)$('search-activity-body').innerHTML+='<p class="help">暂无受控 API 搜索记录，不能据此认定宿主没有搜索。</p>';
 }catch(e){$('search-activity-body').textContent=e.message}
}
$('search-activity-close').onclick=()=>$('search-activity-dialog').close();

// Compact report controls: same component in the composer and creation form.
function compactReportInstruction(){
 const row=document.querySelector('[data-report-options="chat"]');if(!row)return '';
 const tier=row.querySelector('[data-option="tier"]').value;
 const fact=row.querySelector('[data-option="fact"]').checked&&$('chat-allow-web').checked;
 return `\n\n本轮报告选项（仅当用户要求生成报告时使用，不因此自动生成）：research_tier=${tier}，fact_check=${fact}。生成时传入 requirements；${tier==='deep'?'深度研究建议 8000–12000 字，默认 target_words=10000、max_words=12000；':''}用户正文另有明确选择则按正文。`;
}
function compactReportControls(where){
 const row=document.createElement('div');row.className='compact-report-options';row.dataset.reportOptions=where;
 row.innerHTML='<label>研究 <select data-option="tier" aria-label="研究深度"><option value="quick">快速</option><option value="standard" selected>标准</option><option value="deep">深度研究</option></select></label><label title="生成后联网补查关键主张，增加时间与用量"><input type="checkbox" data-option="fact">事实核查</label><label title="改稿或评论后在后台启动学习验证，会调用模型；已启用的技能不受这个开关影响"><input type="checkbox" data-option="learn">自动学习</label><label><input type="number" min="1" max="20" value="1" data-option="rounds" aria-label="自动学习最多轮数">轮</label><details><summary aria-label="更多报告选项">更多 ⋯</summary><div class="compact-options-menu"><label>目标用时 <select data-option="timeout"><option value="10">约 10 分钟</option><option value="30">30 分钟</option><option value="60" selected>60 分钟</option><option value="120">120 分钟</option><option value="0">不设目标</option></select></label><label>Scout 并发 <input data-option="scouts" type="number" min="1" max="16" value="4"></label></div></details>';
 row.addEventListener('change',event=>action(async()=>{
  const key=event.target.dataset.option;if(!key)return;const value=event.target.type==='checkbox'?event.target.checked:event.target.value;
  if(key==='learn'){try{await setAutoLearn(value);await refresh()}finally{syncCompactReportControls()}$('auto-learn').checked=state.learning_authorization?.state==='authorized';return}
  if(key==='tier'){$('research-tier').value=value;$('research-tier').dispatchEvent(new Event('change'))}
  if(key==='fact')$('requirements').elements.fact_check.checked=value;
  const field={tier:'research_tier',fact:'fact_checker',learn:'auto_learn',rounds:'k',timeout:'timeout_minutes',scouts:'max_parallel'}[key];
  const setting=['rounds','timeout','scouts'].includes(key)?Number(value):value;
  await api('settings',{[field]:setting});state.settings[field]=setting;
  if(key==='learn')$('auto-learn').checked=value;if(key==='rounds')$('rounds').value=value;if(key==='timeout')$('timeout-minutes').value=value;
  syncCompactReportControls();
 }));return row;
}
function syncCompactReportControls(){
 if(!state?.settings)return;
 document.querySelectorAll('[data-report-options]').forEach(row=>{
  const web=row.dataset.reportOptions==='chat'?$('chat-allow-web').checked:$('requirements').elements.allow_web.checked;
  for(const [key,value] of Object.entries({tier:row.dataset.reportOptions==='setup'?$('research-tier').value:state.settings.research_tier||'standard',fact:row.dataset.reportOptions==='setup'?$('requirements').elements.fact_check.checked:state.settings.fact_checker,learn:state.learning_authorization?.state==='authorized',rounds:state.settings.k,timeout:state.settings.timeout_minutes,scouts:state.settings.max_parallel})){
   const input=row.querySelector(`[data-option="${key}"]`);if(document.activeElement===input)continue;if(input.type==='checkbox')input.checked=!!value;else input.value=value;
  }
  const fact=row.querySelector('[data-option="fact"]');fact.disabled=!web;if(!web)fact.checked=false;
 });
 const reports=$('max-reports');if(reports&&document.activeElement!==reports&&!reports.dataset.editing)reports.value=state.settings.max_reports||4;
}
function mountCompactReportControls(){
 const paramsPanel=$('composer-params-panel')||$('chat-input').closest('form');
 paramsPanel.append(compactReportControls('chat'));
 const setup=compactReportControls('setup');const tier=$('research-tier').closest('label');tier.before(setup);tier.classList.add('compact-legacy-option');tier.hidden=true;
 if(tier.nextElementSibling?.classList.contains('help'))tier.nextElementSibling.hidden=true;
 const fact=$('requirements').elements.fact_check.closest('label');fact.classList.add('compact-legacy-option');fact.hidden=true;if(fact.nextElementSibling?.classList.contains('help'))fact.nextElementSibling.hidden=true;
 const label=document.createElement('label');label.textContent='同时生成报告数 ';const input=document.createElement('input');input.id='max-reports';input.type='number';input.min='1';input.max='16';input.value='4';label.append(input);$('settings-view-execution').append(label);
 input.oninput=()=>{input.dataset.editing='1'};input.onchange=()=>{const value=Number(input.value);return action(async()=>{const result=await api('settings',{max_reports:value});state.settings.max_reports=result.max_reports;delete input.dataset.editing;notice('并发数已保存；已运行报告继续，新任务按空位开始')})};
 const save=document.createElement('button');save.type='button';save.className='outline';save.textContent='保存并发数';save.onclick=()=>input.onchange();label.append(save);
 for(const control of [$('chat-allow-web'),$('requirements').elements.allow_web])control.addEventListener('change',syncCompactReportControls);
 syncCompactReportControls();
}
mountCompactReportControls();
// Params popover: progressive disclosure (DESIGN §6.5 §7.2).
(function wireComposerParams(){
 const trigger=$('composer-params'),panel=$('composer-params-panel');
 if(!trigger||!panel)return;
 const close=()=>{panel.hidden=true;trigger.setAttribute('aria-expanded','false')};
 trigger.onclick=e=>{e.stopPropagation();const open=panel.hidden;document.querySelectorAll('.popover').forEach(p=>{p.hidden=true});panel.hidden=!open;trigger.setAttribute('aria-expanded',String(open))};
 document.addEventListener('click',e=>{if(!panel.hidden&&!e.target.closest('.composer-params'))close()});
 document.addEventListener('keydown',e=>{if(e.key==='Escape')close()});
})();
