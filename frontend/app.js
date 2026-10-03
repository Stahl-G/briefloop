import {versionInformationHTML} from './report-version-info.js';
import {reasoningControls,settingsEffort,reasoningModel} from './reasoning-controls.js';
import {$,esc} from './dom.js';
import {clock,dateTimeSeconds,moment} from './time.js';
import {api,uploadSource,setToken,getUploadLimits,setSourceUploadHost} from './api.js';
import {createReportBrowsing} from './report-browsing.js';
import {createProviderCapabilities} from './provider-capabilities.js';
import {createModelCatalog,createProviderCatalog} from './model-catalog.js';
import {createRuntimeModelPicker} from './runtime-model-picker.js';
import {createRuntimePickerBindings} from './runtime-picker-bindings.js';
const providerCapabilities=createProviderCapabilities({$});
const reasoning=reasoningControls({api});
import {createAssessmentPanel} from './assessment-panel.js';
import {createQuickReport} from './quick-report.js';
import {createReviewControls} from './review-controls.js';
import {compactReportControlsUI} from './compact-report-controls.js';
import {evidenceReviewDialogsUI} from './evidence-review-dialogs.js';
import {renderVersionDiff} from './version-diff.js';
import {DOMSerializer} from '@tiptap/pm/model';
import {beginPanel,updatePanel} from './report-panels.js';
import {jobExecutionLabel,jobResumeLabel,createLocalFileProgress,isLocalFileJob} from './job-presentation.js';
import {taskProgressCard} from './task-progress.js';
import {researchProcessHTML,processDisclosureKey} from './research-process.js';
import {CitationPresentation,createCitationSources} from './citation-sources.js';
import {copyText} from './clipboard.js';
import {scheduleUI} from './schedules.js';
import {adaptivePoll} from './polling.js';
import {preflightSources,uploadPayload,sourceStatusLabel} from './uploads.js';
import {runtimeCard,runtimeModelSummary,runtimeIcon} from './runtime-cards.js';
import {createNativeRequests,requestKind} from './native-requests.js';
import {createPermissionDirectory,permissionSelection} from './runtime-permission-modes.js';
import {createRuntimePermissionPanel} from './runtime-permission-panel.js';
import {createComposerOptions} from './composer-options.js';
import {anchoredPopover} from './anchored-popover.js';
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
import {revisionQuestionsUI} from './revision-questions.js';
import {createFeedbackList} from './feedback-list.js';
import {readersUI} from './readers.js';
import {verificationBadge} from './skill-verification.js';
import {previousReportUI} from './previous-report.js';
import {nextReportUI} from './next-report.js';
import {learningRetry} from './learning-retry.js';
import {templatesUI} from './templates.js';
import {createTemplateOutput} from './template-output.js';
import {deliveryUI,changeTypeLabel,displayDate} from './delivery.js';
import {reportExportUI} from './report-export.js';
import {excelExportUI} from './excel-export.js';
import {reportLanguageUI,reportLanguage,LENGTH_PRESETS as LANGUAGE_LENGTHS,lengthUnit,runLanguage} from './report-language.js';
import {createMarketConvention} from './market-convention.js';
import {MarketDataColors} from './market-decorations.js';
import {createLengthControls} from './length-controls.js';
import {createReportFormDefaults} from './report-form-defaults.js';
import {sessionBudgetUI} from './session-budget.js';
import {createReportSources} from './report-sources.js';
import {searchSettingsUI} from './search-settings.js';
import {researchBudgetUI} from './research-budget.js';
import {sourceViewerUI} from './source-viewer.js';
import {svgLineIcon,homeUI} from './home.js';
import {runSourceIds,sourceState,sourceStatusChip,sourceTitle,sourceHost,sourcesPageUI} from './sources-page.js';
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
const reportSources=createReportSources({$,esc,getState:()=>state,getCurrent:()=>current,runSourceIds,sourceState,sourceStatusChip,sourceTitle,sourceHost,getEditor:()=>editor,reveal:()=>setReportTab('sources'),openSource:id=>openSourceDrawer(id,sourceUsage()).catch(err=>notice(err.message,true))});
const citationSources=createCitationSources({element:$('editor'),getSources:()=>state?.sources||[],getCurrent:()=>current,focusSource:id=>reportSources.focus(id),openSource:id=>openSourceDrawer(id,sourceUsage()).catch(err=>notice(err.message,true))});
const sessionBudget=sessionBudgetUI({$,api,action,notice,getState:()=>state});
const reportBrowsing=createReportBrowsing({api,getState:()=>state,getCurrent:()=>current,openBrief,page,notice,reportStatus,reportDescription,reportIconMeta:b=>reportIconMeta(b),svgLineIcon,runSourceCount,openRelease:b=>action(()=>delivery.openReleaseDialog(b)),onUsageOpen:()=>closeSourceDrawer(),onContext:()=>{assessment();citations();renderBriefLength();renderReportStatus();renderAssistantSummary()}});
const revisionQuestions=revisionQuestionsUI({api,action:(...args)=>action(...args)});
const feedbackList=createFeedbackList({$,esc,getState:()=>state,openLearning:()=>page('learning')});
const retryLearning=learningRetry({api,getPlan:()=>state?.learning_authorization?.plan,confirm:text=>confirm(text),describePlan:learningPlanText});
const readerProfiles=readersUI({api,action:(...args)=>action(...args)});
readerProfiles.bind();
const previousReport=previousReportUI({api,uploadPayload,getUploadLimits,notice:(...args)=>notice(...args),saveReader:reader=>action(async()=>{const saved=await api('reader-save',reader);await refresh();const select=$('reader-select');if(select){select.value=saved.id;select.dispatchEvent(new Event('change'))}},'读者档案已保存，下一份报告可复用'),openChat:async({text,source_id})=>{await openChatHome();chat.attachments.add(source_id);renderAttachments();$('chat-input').value=text;rememberDraft();updateComposer();$('chat-input').focus()}});
previousReport.bind();
function notice(s,error=false){$('notice').textContent=s;$('notice').classList.toggle('error',error);$('notice').hidden=false;clearTimeout(notice.timer);notice.timer=setTimeout(()=>$('notice').hidden=true,error?12000:4500)}
function page(name){if(document.body.classList.contains('report-chat-open'))setReportChatOpen(false);if(((name==='chat'&&!chat.id)||name==='setup')&&(state?.settings?.model_selection_required||!state?.settings?.model)){notice('请先选择 Agent 和模型');name='welcome'}if(name!=='settings-dialog'&&$('custom-api-key'))$('custom-api-key').value='';for(const id of ['chat','report','reports','sources','templates','setup','learning','settings-dialog','welcome'])$(id).hidden=id!==name;document.querySelectorAll('nav [data-page]').forEach(b=>b.classList.toggle('active',b.dataset.page===name));if(name==='learning')refreshCandidates();if(name==='setup'){if(typeof reportMcpSelection!=='undefined')reportMcpSelection.refresh();moveSearchSettings('setup');applyPendingSetupFields()}else if($('tavily-key')){$('tavily-key').value='';$('bocha-key').value='';$('zhipu-key').value=''}if(name==='reports'){renderTasks();renderTaskGraph();renderReports()}if(['reports','templates','learning'].includes(name))activity?.readCategory(name)}
document.querySelectorAll('[data-page]').forEach(b=>b.onclick=()=>page(b.dataset.page));
async function action(fn,message){try{await fn();if(message)notice(message);await refresh()}catch(e){notice(e.message,true)}}
// Optional OfficeCLI enhancement; every surface it adds hides itself while the
// switch is off, so behaviour matches a machine without the binary.
const office=createOfficeTools({api,action,$,esc,parse,getState:()=>state,notice});
const searchSettings=searchSettingsUI({api,getState:()=>state,runtimeName,renderBudgetProviderScope:()=>renderBudgetProviderScope()});
const {readSearchPolicy,loadSearchPolicy,renderSearchProvider,refreshTavilySettings,moveSearchSettings}=searchSettings;
const researchBudget=researchBudgetUI({api,parse,getState:()=>state,getCurrent:()=>current,readSearchPolicy,selectTierBudget:budget=>reportFormDefaults.selectTierBudget(budget)});
const {readResearchBudget,reflectBudgetPreset,initializeResearchBudget,renderBudgetProviderScope,refreshReportBudget}=researchBudget;
const sourceViewer=sourceViewerUI({api,action,office});
const {showSource,resetSourceMedia}=sourceViewer;
const sourcesPage=sourcesPageUI({api,action,notice,page,openBrief,markTab,reportBrowsing,getState:()=>state,
 showSourceMedia:sourceViewer.showSourceMedia,drawerSourceMediaView:sourceViewer.drawerSourceMediaView,applySourceLinks:sourceViewer.applySourceLinks,sourceProvenanceRows:sourceViewer.sourceProvenanceRows});
const {sourceUsage,renderSourcesPage,setSourceDrawerTab,openSourceDrawer,closeSourceDrawer}=sourcesPage;
const compactControls=compactReportControlsUI({api,action,refresh,notice,page,showSettings,settingsView,setAutoLearn,chatBackendChoice,backendValue,runtimeName,sessionBudget,
 getState:()=>state,getComposerOptions:()=>composerOptions,pendingReview:()=>reviewControls.pending()});
const {compactReportInstruction,compactFactAvailability,syncCompactReportControls,mountCompactReportControls}=compactControls;
setSourceUploadHost(()=>{const visible=['chat','setup','sources','report'].map($).find(el=>el&&!el.hidden)||$('sources');let host=visible.querySelector('[data-source-uploads]');if(!host){host=document.createElement('div');host.dataset.sourceUploads='';visible.prepend(host)}return host},()=>refresh().catch(error=>notice(error.message,true)));
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
// Every snapshot carries the server clock. That stamp alone must not rebuild the
// page, which would reset focus and selections on each poll; only the timed task
// cards and the result banner follow the clock.
async function refreshState(first=false,signal){try{const next=await api(typeof reportBrowsing==='undefined'?'state':reportBrowsing.stateRoute(current,pendingRun));if(signal?.aborted)return false;$('connection').textContent='本地已连接';const {system_clock,...stable}=next;const signature=JSON.stringify(stable);if(state?.workspace_id&&state.workspace_id!==next.workspace_id)templateOutput.reset();state=typeof reportBrowsing==='undefined'?next:reportBrowsing.acceptState(next,current);scheduledReports.render();activity?.render();renderWordExports();if($('release-dialog')?.open)delivery.refreshReleaseState().catch(e=>notice(e.message,true));const initialize=first&&!refresh.initialized;if(initialize||signature!==refresh.signature){render(initialize);refresh.signature=signature;if(initialize)refresh.initialized=true}else{renderTasks();renderTaskBanner()}await refreshProgress();if(first||!$('learning').hidden)await refreshCandidates();if(first||!$('report').hidden)await refreshReportBudget();return !signal?.aborted}catch(e){if(signal?.aborted)return false;$('connection').textContent='连接中断';if(first)throw e}}
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
const toEditor=(md,version=current?.id)=>{const ids=[];return mapFigureImages(md.replace(/\\?\[@(src\\?_[a-zA-Z0-9_-]+)\\?\]/g,(_,raw)=>{const id=raw.replaceAll('\\','');if(!ids.includes(id))ids.push(id);return `[${ids.indexOf(id)+1}](#source-${id})`}),true,version)};
const fromEditor=md=>mapFigureImages(md.replace(/\[([^\]]+)\]\(#source-(src_[a-zA-Z0-9_-]+)\)/g,(_,label,id)=>`${/^(?:\d+|\?)$/.test(label)?'':label}[@${id}]`),false);
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
const nextReport=nextReportUI({$,api,savedVersion,getTemplate:id=>{const t=state?.templates?.find(t=>t.id===id);return t?{...t,sections:parse(t.spec).sections||[]}:null},getCurrent:()=>current,applyRequirements,refreshTime:()=>previewReportTime(),notice,clearSources:()=>{selected.clear();referenceSelected.clear();document.querySelectorAll('[data-check],[data-reference-source]').forEach(input=>input.checked=false);renderReferenceSources()}});
nextReport.bind();
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
 // Language first: its change event resets untouched lengths before explicit numbers apply.
 if(data.language!=null){const language=reportLanguage(data.language);if(language)set('language',language)}
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
 if('length_mode' in data||'length_requirement' in data)lengthControls.restore(data,{fromDiscussion:true});else lengthControls.render();
 validateLengthInputs();
 if(Array.isArray(data.writing_preferences))writingPreferencesOverride=[...data.writing_preferences];
 page('setup');notice('已填入材料与需求，请检查后生成');
}
// The backend names every task kind (task_labels.py) and says which ones a
// user sees as their own; the page renders that map and keeps no copy.
const taskLabel=kind=>state?.task_labels?.[kind];
const localFileProgress=createLocalFileProgress({element:$('run-progress'),api,action,taskLabel});
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
const taskSnapshots=new Map(),taskExpanded=new Map(),taskMaterials=new Set(),taskActions=new Map();
let taskGraphRequest=null;
function visibleReportTasks(){
 const all=(state.jobs||[]).filter(j=>taskLabel(j.kind)).sort((a,b)=>new Date(b.created)-new Date(a.created));
 const activeParents=new Set(all.filter(j=>['queued','running'].includes(j.status)).map(j=>j.id));
 return (renderTasks.showAll?all:all.filter(j=>['queued','running','failed','interrupted','cancelled'].includes(j.status)&&!activeParents.has(parse(j.payload).parent_job_id))).slice(0,15);
}
function taskCardHTML(j){const cached=taskSnapshots.get(j.id);return taskProgressCard(j,cached?.data,{label:bannerTitle(j)||taskLabel(j.kind),kindLabel:taskLabel(j.kind),expanded:taskExpanded.get(processDisclosureKey(j,cached?.data||{})),materials:taskMaterials.has(j.id),error:cached?.error,busy:taskActions.get(j.id)})}
function bindTaskCards(){
 const box=$('report-task-cards');
 const mutate=async(id,route,label)=>{if(taskActions.has(id))return;taskActions.set(id,label);renderTasks();try{await action(()=>api(route,{job_id:id}))}finally{taskActions.delete(id);renderTasks()}};
 box.querySelectorAll('[data-task-detail]').forEach(d=>d.ontoggle=()=>{taskExpanded.set(d.dataset.processState,d.open)});
 box.querySelectorAll('[data-task-materials]').forEach(d=>d.ontoggle=()=>{if(d.open)taskMaterials.add(d.dataset.taskMaterials);else taskMaterials.delete(d.dataset.taskMaterials)});
 box.querySelectorAll('[data-task-open]').forEach(b=>b.onclick=()=>openTask(taskFor(b.dataset.taskOpen)));
 box.querySelectorAll('[data-task-stop]').forEach(b=>b.onclick=()=>mutate(b.dataset.taskStop,'stop','正在停止任务…'));
 box.querySelectorAll('[data-task-resume]').forEach(b=>b.onclick=()=>mutate(b.dataset.taskResume,'resume','正在恢复任务…'));
 box.querySelectorAll('[data-task-dismiss]').forEach(b=>b.onclick=()=>mutate(b.dataset.taskDismiss,'task-dismiss','正在清除任务…'));
 box.querySelectorAll('[data-progress-version]').forEach(b=>b.onclick=()=>{const brief=state.briefs.find(x=>x.id===b.dataset.progressVersion)||{id:b.dataset.progressVersion};if(brief&&openBrief(brief,{follow:false})){pendingRun=null;page('report')}});
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
 $('welcome-model-name').textContent=model==='default'?(chosen==='claude'?'跟随 Claude Code 默认':'跟随宿主默认'):model||'选择或搜索模型';$('welcome-model').disabled=!selected||welcomeBusy;
 $('welcome-start').disabled=!welcomeReady(settings,runtimeCatalog,welcomeMode,welcomeBusy);
 $('welcome-choice').textContent=welcomeBusy?'正在保存选择…':!selected?'请先选择一个 Agent。':!model?'请选择模型，再开始普通对话。':'已保存选择，可以开始普通对话。';
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
 if(first){$('settings-chat-web-default').checked=state.settings.chat_allow_web!==false;$('chat-allow-web').checked=state.settings.chat_allow_web!==false;$('timeout-minutes').value=state.settings.timeout_minutes;$('hard-timeout-minutes').value=state.settings.hard_timeout_minutes||0;$('company-mode').value=state.settings.company_context_enabled==null?'ask':state.settings.company_context_enabled?'on':'off';$('auto-revision').checked=state.settings.auto_revision!==false;state.sources.filter(s=>s.status==='ready'&&s.usage!=='previous_report').forEach(s=>selected.add(s.id));$('rounds').value=state.settings.k;$('auto-learn').checked=state.learning_authorization?.state==='authorized';$('model-select').value=state.settings.model_selection_required?'':state.settings.model||'';assignEffort('effort-select',settingsEffort(state.settings,state.settings.agent_backend||'codex'));$('model-provider').value=state.settings.model_provider||'';$('service-tier').value=state.settings.service_tier||'';$('agent-backend').value=state.settings.agent_backend||'codex';$('model-variant').value=state.settings.agent_backend==='mimo'?(state.settings.runtime_efforts?.mimo||''):(state.settings.model_variant||'');updateModelLabel();renderRoleModels();renderReviewRuntime();renderBackend();loadSearchPolicy();renderSearchProvider();refreshRuntimeDiscovery();office.syncSettingsToggle();if(state.requirements)for(const [k,v] of Object.entries(state.requirements)){const e=$('requirements').elements[k];if(e)e.type==='checkbox'?e.checked=v:e.value=v}$('requirements').elements.key_questions_text.value=(state.requirements?.key_questions||[]).join('\n');$('requirements').elements.manual_sections_text.value=(state.requirements?.manual_sections||[]).join('\n');reportLanguageForm.restore(state.requirements?.language);reportMarket.restore();initializeLengthInputs(state.requirements||{});initializeResearchBudget(state.requirements||{});initializeReportProfile(state.requirements||{});if(!state.requirements||state.requirements.fact_check==null)$('requirements').elements.fact_check.checked=!!state.settings.fact_checker;syncFactCheckControl();quickReport.sync();syncWorkflowProfile(false);previewReportTime()}
 $('source-count').textContent=state.sources.length+' 份';$('source-list').innerHTML=state.sources.map(s=>`<div class="source-item"><input type="checkbox" data-check="${s.id}" ${selected.has(s.id)?'checked':''} ${s.status!=='ready'||s.usage==='previous_report'?'disabled':''} aria-label="选择 ${esc(s.name)}"><button data-source="${s.id}">${esc(s.name)}</button><span class="tag ${s.status==='failed'?'error':''}">${esc(s.usage==='previous_report'?'往期报告 · 参考用途':sourceStatusLabel(s))}</span>${['failed','cancelled','interrupted'].includes(s.status)?`<button data-retry-source="${s.id}">重试</button>`:''}</div>`).join('');
 document.querySelectorAll('[data-retry-source]').forEach(b=>b.onclick=()=>action(async()=>{const s=await api('retry-source',{source_id:b.dataset.retrySource});selected.delete(b.dataset.retrySource);if(s.status==='ready')selected.add(s.id);notice(sourceStatusLabel(s),['failed','cancelled','interrupted'].includes(s.status))}));
 document.querySelectorAll('[data-check]').forEach(b=>b.onchange=()=>{if(b.checked){selected.add(b.dataset.check);referenceSelected.delete(b.dataset.check);renderReferenceSources()}else selected.delete(b.dataset.check)});renderReferenceSources();
 renderVersionSelect();

 tryOpenPending();if(!current&&!pendingRun&&!openBrief.request&&state.briefs.length)openBrief(state.briefs[0],{follow:true});if(current&&followUpdates&&!dirty&&!saving){const latest=state.briefs.find(b=>b.run_id===current.run_id);if(latest?.parent_id===current.id&&latest.author==='agent')openBrief(latest,{follow:true})}if(current){$('version-select').value=current.id;assessment();citations();renderBriefLength()}
 syncPendingReport();
 $('jobs').innerHTML=state.jobs.filter(j=>j.status!=='dismissed').map(j=>`<div class="job"><span class="tag ${j.status==='failed'?'error':''}">${statuses[j.status]}</span><div class="job-main">${esc(taskLabel(j.kind)||j.kind)}<small>${esc(jobExecutionLabel(j,jobModelLabel))} · ${j.progress?`第 ${j.progress.round}/${j.progress.k} 轮 · ${{maintainer:'整理经验',proposer:'提出候选',validation:'验证候选'}[j.progress.phase]||j.progress.phase} · `:''}${esc(j.error||(j.kind==='source_refresh'?sourceRefreshOutcome(parse(j.result).outcome):'')||moment(j.created))}</small></div>${j.kind==='learn'?`<button data-details="${j.id}">查看比较</button>`:''}${['queued','running'].includes(j.status)?`<button data-stop="${j.id}">停止</button>`:''}${['failed','interrupted','cancelled'].includes(j.status)?`<button data-resume="${j.id}">${esc(jobResumeLabel(j))}</button>${['review','learn'].includes(j.kind)?`<button data-retry-current="${j.id}">${j.kind==='learn'?'确认预算后重试':'按当前模型重试'}</button>`:''}`:''}</div>`).join('');
 document.querySelectorAll('[data-stop]').forEach(b=>b.onclick=()=>action(()=>api('stop',{job_id:b.dataset.stop})));document.querySelectorAll('[data-resume]').forEach(b=>b.onclick=()=>action(()=>api('resume',{job_id:b.dataset.resume})));document.querySelectorAll('[data-retry-current]').forEach(b=>b.onclick=()=>action(()=>retryLearning(state.jobs.find(j=>j.id===b.dataset.retryCurrent))));renderTasks();renderTaskGraph();renderTaskBanner();renderAssistantSummary();renderReportStatus();renderReports();renderSourcesPage();templatesPage.render();if($('welcome')&&!$('welcome').hidden)renderWelcome();
 document.querySelectorAll('[data-details]').forEach(b=>b.onclick=()=>action(async()=>{const d=await api('learning-details?job='+b.dataset.details);$('source-title').textContent='技能比较与依据';$('source-original').hidden=true;$('source-provenance').hidden=true;$('source-link').textContent='';$('source-body').textContent=d.rounds.length?d.rounds.map((r,i)=>`第 ${i+1} 轮\n${r.result?.reason||'比较尚未完成'}\n${(r.result?.pairs||[]).map(p=>({better:'候选更好',tie:'差不多，保留原技能',worse:'原稿更好'}[p.verdict])+': '+p.reason).join('\n')}\n\n`+r.cases.map(c=>`任务：${c.requirements.title}\n\n旧版\n${gradeSummary(c.baseline.assessment)}\n${c.baseline.reader_markdown||c.baseline.markdown}\n\n候选\n${gradeSummary(c.candidate.assessment)}\n${c.candidate.reader_markdown||c.candidate.markdown}`).join('\n\n')).join('\n\n'):d.job.error||'比较尚未开始；先整理 Wiki 和提出候选。';$('source-dialog').showModal()}));
 revisionQuestions.render(state.revision_edits);feedbackList.render();
 readerProfiles.render(state.readers,{readerId:state.requirements?.reader_id});
 $('skills').innerHTML=`<div class="skill">${state.active_skill?'当前启用 '+esc(state.active_skill):'当前使用基础任务提示词'}${state.active_skill?'<button data-rollback="">回到基础版本</button>':''}</div>`+state.skills.map(s=>`<div class="skill"><strong>${esc(s.id)}</strong>${verificationBadge(state.skill_verifications?.[s.id])}<p>${esc(s.reason)}</p>${s.reader_scope_ids?.length?'<span class="tag">历史读者专属版本 · 暂不启用</span>':s.id===state.active_skill?'<span class="tag">正在使用</span>':`<button data-rollback="${s.id}" class="outline">使用这个版本</button>`}</div>`).join('');document.querySelectorAll('[data-rollback]').forEach(b=>b.onclick=()=>action(()=>api('rollback',{skill_id:b.dataset.rollback||null}),'下一轮将使用所选技能'));
 if(state.wiki!==render.wiki){render.wiki=state.wiki;if(state.wiki)api('render',{markdown:state.wiki}).then(r=>$('wiki').innerHTML=r.html);else $('wiki').innerHTML='<h2>还没有学习经验</h2><p class="muted">生成简报后直接改稿，或留下评论。Maintainer 会在这里整理观察、方法与适用条件。</p>'}bindSources();
}
function renderVersionSelect(){
 const visible=[];
 for(const runId of [...new Set(state.briefs.map(b=>b.run_id))]){
  const versions=state.briefs.filter(b=>b.run_id===runId),latest=versions[0],original=[...versions].reverse().find(b=>['agent','example','import'].includes(b.author));
  if(latest)visible.push({brief:latest,label:latest.author==='example'?'合成示例':latest.author==='import'?'往期原稿':latest.author==='user'?'当前编辑稿':latest.parent_id?quickReport.versionLabel(latest):'生成原稿'});
  if(original&&original.id!==latest?.id)visible.push({brief:original,label:original.author==='example'?'合成示例':original.author==='import'?'往期原稿':'生成原稿'});
 }
 if(current&&!visible.some(v=>v.brief.id===current.id))visible.push({brief:current,label:'正在查看历史快照'});
 const pendingOptions=state.jobs.filter(j=>j.kind==='generate'&&['queued','running'].includes(j.status)&&!state.briefs.some(b=>b.run_id===parse(j.payload).run_id)).map(j=>{const rid=parse(j.payload).run_id,r=state.runs.find(r=>r.id===rid);return `<option value="run:${esc(rid)}">${esc(parse(r?.requirements).title||'新报告')} · ${j.status==='queued'?'排队中':'正在生成'}</option>`}).join('');
 // The report title is already the page heading; its own versions show only their label.
 $('version-select').innerHTML=pendingOptions+visible.map(({brief:b,label})=>`<option value="${b.id}">${b.run_id===current?.run_id?label:esc(parse(b.detail).title||'简报')+' · '+label}</option>`).join('');
 const edits=current?(state.briefs.find(b=>b.run_id===current.run_id)?.user_version_count??state.briefs.filter(b=>b.run_id===current.run_id&&b.author==='user').length):0;
 if($('version-history')){$('version-history').textContent=`编辑历史（${edits}）`;$('version-history').hidden=!edits}
 if(current)$('version-select').value=current.id;else if(pendingRun)$('version-select').value='run:'+pendingRun;
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
 for(const id of ['download-word','export-menu-toggle','more-menu-toggle','version-diff'])if($(id))$(id).hidden=waiting;
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
async function loadBrief(b){if(typeof reportBrowsing!=='undefined')return reportBrowsing.loadBrief(b);if(!b||'markdown' in b)return b;const bodies=openBrief.bodies||(openBrief.bodies=new Map()),cached=bodies.get(b.id);if(cached&&cached.hash===b.hash&&(cached.execution_revision??0)===(b.execution_revision??0))return cached;const full=await api('brief?id='+encodeURIComponent(b.id));bodies.set(full.id,full);return full}
function openBrief(b,{follow=false}={}){if(!b)return false;if(dirty||saving){notice('请先保存当前修改，再切换版本',true);return false}if(!('markdown' in b)){const cached=openBrief.bodies?.get(b.id);if(!cached||cached.hash!==b.hash||(cached.execution_revision??0)!==(b.execution_revision??0)){/* Polled state lists versions only. Choosing one records the request; it opens (and clears pending reports) only when its body arrives and is still the latest choice. */if(openBrief.request?.id===b.id&&openBrief.request.hash===b.hash&&(openBrief.request.execution_revision??0)===(b.execution_revision??0))return true;const request=openBrief.request={id:b.id,hash:b.hash,execution_revision:b.execution_revision};request.promise=loadBrief(b).then(full=>{if(openBrief.request!==request)return false;openBrief.request=null;return openBrief(full,{follow})}).catch(e=>{if(openBrief.request===request){openBrief.request=null;notice(e.message,true)}return false});return true}b=cached}openBrief.request=null;if(typeof reportBrowsing!=='undefined')reportBrowsing.adopt(b);(openBrief.bodies||(openBrief.bodies=new Map())).set(b.id,b);while(openBrief.bodies.size>6)openBrief.bodies.delete(openBrief.bodies.keys().next().value);pendingRun=null;followUpdates=follow;current=b;if(typeof citationSources!=='undefined')citationSources.hide();renderVersionSelect();syncPendingReport();renderWordExports();$('report-title').textContent=parse(b.detail).title||'简报';updateDownloads(b);if(editor)editor.destroy();highlightQuotes=[];editor=new Editor({element:$('editor'),editable:(state.briefs.find(x=>x.run_id===b.run_id)?.id||b.latest_version_id)===b.id,extensions:[StarterKit.configure({link:{openOnClick:false},trailingNode:false}),ReportTrailingParagraph,MarketDataColors,TableKit,ReportImage.configure({HTMLAttributes:{class:'briefloop-figure'},allowBase64:false}),TextStyle,Layout,Citation,CitationPresentation.configure({getSources:()=>state?.sources||[]}),Markdown,MustFixHighlight],content:b.editor_document?editorDocument(parse(b.editor_document),b.id):toEditor(b.markdown),...(b.editor_document?{}:{contentType:'markdown'}),onUpdate:changed,onSelectionUpdate:updateFormattingTools});$('markdown-source').value=b.markdown;const historical=(state.briefs.find(x=>x.run_id===b.run_id)?.id||b.latest_version_id)!==b.id;$('markdown-source').readOnly=historical;$('toolbar').querySelectorAll('button,input,select').forEach(x=>x.disabled=historical);$('save-state').textContent=historical?'历史记录（只读）':b.author==='example'?'合成示例已保存':b.author==='user'?'当前编辑稿已自动保存':'原稿已保存';$('version-select').value=b.id;assessment();citations();renderBriefLength();setReportView('edit');renderReportStatus();renderAssistantSummary();return true}
function changed(){reportSources.render();followUpdates=false;dirty=true;assessmentPanel.bumpDeliveryTicket();const check=$('assessment').querySelector('.delivery-checks');if(check)updatePanel(check,'有未保存修改；保存后重新检查。');renderBriefLength();$('save-state').textContent='有未保存修改';clearTimeout(saveTimer);saveTimer=setTimeout(save,1400)}
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
const reportExport=reportExportUI({api,notice,refresh,savedVersion,parse,getState:()=>state,getCurrent:()=>current});
reportExport.init();
const excelExport=excelExportUI({api,notice,refresh,savedVersion,parse,getState:()=>state});
excelExport.init();
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
const quickReport=createQuickReport({$,api,action,notice,savedVersion,getCurrent:()=>current,esc,syncSourceHints:renderWorkflowChoices,getBackend:()=>state.settings?.agent_backend||'codex'});
quickReport.init();
const assessmentPanel=createAssessmentPanel({
 api,action,notice,$,esc,parse,savedVersion,
 renderCompletion:()=>quickReport.render(),
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
const reviewControls=createReviewControls({api,action,$,getState:()=>state,backendValue,friendlyModel,savedVersion,notice,runtimeName,onAvailabilityChange:syncCompactReportControls});
const {syncFactCheckControl,renderReviewRuntime}=reviewControls;
reviewControls.init();
$('requirements').elements.allow_web.addEventListener('change',syncFactCheckControl);
$('requirements').elements.writing_mode.addEventListener('change',syncFactCheckControl);
$('agent-backend').addEventListener('change',syncFactCheckControl);
let reportTimePreviewTicket=0;
async function previewReportTime(){
 const ticket=++reportTimePreviewTicket;
 const form=$('requirements'),box=$('report-system-clock');
 if(form.elements.previous_report_version_id?.value&&!['period','period_start','period_end'].some(key=>form.elements[key].value.trim())){box.textContent='请填写并确认本期时间范围；不会沿用上期日期。';return}
 box.textContent='正在核对本期时间范围…';
 try{const requirements={title:form.elements.title.value||'预览',objective:form.elements.objective.value||'预览'};for(const key of ['period','period_start','period_end','report_timezone'])requirements[key]=form.elements[key].value;const t=await api('report-time-preview',{requirements});if(ticket!==reportTimePreviewTicket)return;reportFormDefaults.setWindow(t,requirements);box.textContent=`系统日期：${t.today} · ${t.timezone}；报告范围：${t.start} 至 ${t.end_exclusive}（不含结束时刻）。提交时冻结。`;}catch(e){if(ticket===reportTimePreviewTicket)box.textContent=e.message}
}
for(const key of ['period','period_start','period_end','report_timezone'])$('requirements').elements[key].addEventListener('change',previewReportTime);
$('requirements').onsubmit=e=>{e.preventDefault();action(async()=>{const f=new FormData(e.target),req=Object.fromEntries(f.entries());if(!['fast','fast_web'].includes(req.completion_mode)&&req.writing_mode==='internal_report'&&state.settings.company_context_enabled==null){$('company-choice-dialog').showModal();return}req.allow_web=f.has('allow_web');req.fact_check=f.has('fact_check');req.target_words=Number(req.target_words);req.max_words=Number(req.max_words);Object.assign(req,lengthControls.read(),quickReport.read());req.research_budget=readResearchBudget();reportFormDefaults.prepare(req);reportMarket.prepare(req);req.search_policy=readSearchPolicy();Object.assign(req,readWorkflowChoice());req.reference_source_ids=[...referenceSelected];req.template_id=req.template_id||null;req.sections=readTemplateSections();req.key_questions=(req.key_questions_text||'').split('\n').map(x=>x.trim()).filter(Boolean);delete req.key_questions_text;req.manual_sections=(req.manual_sections_text||'').split('\n').map(x=>x.trim()).filter(Boolean);for(const title of req.manual_sections){const found=req.sections.find(s=>s.title===title);if(found){found.mode='manual';found.placeholder='待填充'}}delete req.manual_sections_text;preserveWritingPreferences(req,state.requirements,writingPreferencesOverride);if(!req.reader_id)delete req.reader_id;req.raw_input=req.objective;delete req.runtime_model;delete req.runtime_effort;await saveModel();if(current)await savedVersion();const connector_selection=reportMcpSelection.selection();const job=await api('generate',{...(connector_selection?{connector_selection}:{}),requirements:req,session_id:chat.id||undefined,source_ids:[...selected].filter(id=>!req.reference_source_ids.includes(id))});nextReport.clear();showPendingReport(parse(job.payload).run_id);page('report');notice('任务已排队，后台会生成简报')})};
$('upload').onchange=e=>action(async()=>{preflightSources(e.target.files,getUploadLimits());for(const f of e.target.files){const s=await uploadSource(f);if(s.status==='ready')selected.add(s.id)}e.target.value=''},'来源已保存');
$('add-url').onclick=()=>action(async()=>{const s=await api('source-url',{url:$('source-url').value});if(s.status==='ready')selected.add(s.id);$('source-url').value='';notice(s.status==='ready'?'网页已读取':'来源已保存，但读取失败：'+s.error,s.status!=='ready')});
$('rescore').onclick=()=>action(async()=>{await savedVersion();await api('assess',{version_id:current.id,session_id:chat.id||undefined})},'已提交评分');
$('comment-submit').onclick=()=>action(async()=>{const text=$('comment').value,required=$('comment-required').checked;const version=await savedVersion();await api('comment',{version_id:version,text,learning_intent:required?'explicit_requirement':'feedback'});if($('comment').value===text&&$('comment-required').checked===required){$('comment').value='';$('comment-required').checked=false;}scheduleLearning()},'反馈已保存');
// Saving feedback is free; starting a learning validation calls models (#727).
function learningPlanText(plan){
 return `每轮最多用 ${plan.cases} 份历史报告，每份基线和候选各试写一次（最多 ${plan.trial_generations_per_round} 次，可复用的基线不重写），每批还会最多启动 ${plan.triage_turns_per_batch} 个独立改动分类会话，每轮另有整理经验、提出候选和一次成对比较；`+
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



function gradeSummary(a){return a?`评分：证据 ${a.evidence??'—'}/5 · 覆盖 ${a.coverage??'—'}/5 · 分析 ${a.analysis??'—'}/5 · 表达 ${a.expression??'—'}/5\n${a.summary}\n`:'尚未评分\n'}

function effectiveReportJobs(){
 const runId=pendingRun||current?.run_id;
 const version=current?.run_id===runId?current:state.briefs.find(b=>b.run_id===runId);
 const superseded=new Set(state.jobs.map(j=>parse(j.payload).previous_job_id).filter(Boolean));
 const latestChecks=new Set();
 return state.jobs.filter(j=>{
  if(['export_docx','release','audit_bundle','source_extract'].includes(j.kind)||superseded.has(j.id))return false;
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
const runProcessChoices=new Map();
function runProcessHTML(job,p){return researchProcessHTML(job,p,{expanded:runProcessChoices.get(processDisclosureKey(job,p))})}
function bindRunProcess(){
 const detail=$('run-progress').querySelector?.('[data-process-state]');
 if(detail)detail.ontoggle=()=>runProcessChoices.set(detail.dataset.processState,detail.open);
}
let progressRequest=null;
function jobModelLabel(job,started={}){const payload=parse(job.payload),runtime=payload.runtime||started.runtime;const backend=runtime?.agent_backend||runtime?.backend||payload.agent_backend||payload.backend||started.agent_backend||started.backend;return modelLabel({...runtime,agent_backend:backend})}
function progressSelection(){
 const relevant=withoutSupersededRetries(effectiveReportJobs());
 const job=relevant.find(j=>j.status==='running')||relevant.find(j=>j.status==='queued');
 const paused=job?null:relevant.find(j=>['cancelled','interrupted','failed'].includes(j.status));
 const finished=!job&&!paused&&current?relevant.find(j=>j.status==='complete'&&['generate','revise','assess','review','fact_check'].includes(j.kind)):null;
 return {job,paused,finished,key:JSON.stringify([current?.id,pendingRun,(job||paused||finished)?.id,(job||paused||finished)?.status])};
}
async function refreshProgress(){
 if(!state)return;
 const {job,paused,finished,key}=progressSelection();
 if(progressRequest?.key===key)return;
 const request={key};progressRequest=request;
 // A stop, retry, completion or report switch supersedes old network replies.
 // Let the new selection refresh immediately, even if the old fetch is pending.
 const isCurrent=()=>progressRequest===request&&progressSelection().key===key;
 try{
 if(typeof isLocalFileJob!=='undefined'&&isLocalFileJob(job||paused)){await localFileProgress.render(job||paused,isCurrent);return;}
 if(!job){
 if(finished){const progress=await api('task-progress?job='+encodeURIComponent(finished.id));if(!isCurrent())return;$('run-progress').hidden=false;$('run-progress').innerHTML=runProcessHTML(finished,progress);bindRunProcess();return}
 $('run-progress').hidden=!paused;
 if(paused){const service=await api('runtime');if(!isCurrent())return;$('run-progress').innerHTML=`<div class="section-title"><h2>${paused.status==='failed'?'任务未完成':'任务已暂停'}</h2><button id="paused-resume" class="primary">恢复任务（沿用原模型）</button></div><p>当前没有继续执行这个任务。已有来源和产物保留。</p><p class="help">本地服务 PID ${service.server_pid||'—'}（页面与任务管理） · ${service.pid?'模型进程 PID '+service.pid:'本工作区没有模型进程'}</p><p class="help">${esc(paused.error||'')}</p><p class="help">恢复会沿用原来的模型与后端。审阅和学习也可按当前模型重试，旧任务记录会保留。</p>${['review','learn'].includes(paused.kind)?`<button id="paused-retry-current" class="outline">${paused.kind==='learn'?'确认预算后重试':'按当前模型重试'}</button>`:''}<button id="paused-settings" class="outline">修改模型与要求</button><button id="paused-dismiss" class="outline">清除这个任务</button>`;if($('paused-retry-current'))$('paused-retry-current').onclick=()=>action(()=>retryLearning(paused));$('paused-settings').onclick=()=>page('setup');$('paused-resume').onclick=()=>action(()=>api('resume',{job_id:paused.id}),'已按页面显示的模型提交');$('paused-dismiss').onclick=()=>action(()=>api('task-dismiss',{job_id:paused.id}),'已清除这个未完成任务')}
 return
}
  if(job.kind==='source_refresh'){
   const payload=parse(job.payload),source=state.sources.find(s=>s.id===payload.source_id);
   $('run-progress').hidden=false;$('run-progress').innerHTML=`<div class="section-title"><h2>${job.status==='queued'?'来源复查已排队':'正在复查来源'}</h2><button class="outline" id="progress-stop">停止任务</button></div><p>${esc(source?.name||'当前来源')}</p><p class="help">按本轮联网范围和预算获取新快照；已有来源与报告保留。复查完成后，来源变化仍需判断和复核。</p>`;
   $('progress-stop').onclick=()=>action(()=>api('stop',{job_id:job.id}));return;
  }
  const [events,live,publicProgress]=await Promise.all([api('events?job='+job.id),api('runtime?job_id='+encodeURIComponent(job.id)),api('task-progress?job='+encodeURIComponent(job.id))]);
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
  const stageLabel=typeof p.stage==='string'?p.stage:(p.stages||[]).find(item=>item.status==='active')?.label;
  const elapsed=Math.max(0,Math.floor((Date.now()-new Date(p.started||job.created))/1000));
  const mins=Math.floor(elapsed/60),secs=elapsed%60;
  // Independent review/check jobs need not own the main runtime's PID.
  const running=job.status==='running';
  $('run-progress').hidden=false;
  $('run-progress').innerHTML=`<div class="section-title"><div><p class="eyebrow">${taskLabel(job.kind)||'简报生成'} · ${running?'后台正在运行':'等待后台执行'}</p><h2>${esc(pendingRequests.length?'等待你的确认':stageLabel||(job.status==='queued'?'任务已排队':'正在启动 BriefLoop'))}</h2></div><button class="outline" id="progress-stop">停止任务</button></div><p><strong>${esc(jobModelLabel(job,start))}</strong> · 模型进程 PID ${live.pid||'—'}${live.server_pid?' · 本地服务 PID '+live.server_pid:''}</p><p class="help">${job.status==='queued'?'排队等待':p.started?'已执行':'起始时间待确认'} ${job.status==='queued'||p.started?`${mins} 分 ${secs} 秒`:''} · ${req.target_minutes?'目标约 '+req.target_minutes+' 分钟'+(running&&p.started&&elapsed>req.target_minutes*60?' · 已超过目标，继续执行':''):'未设目标用时'}${req.hard_timeout_minutes?' · 单轮最长运行 '+req.hard_timeout_minutes+' 分钟':''} <button id="progress-timeout" class="subtle-button">后续任务设置</button>${run?` · ${run.initial_source_count??parse(run.source_ids||'[]').length} 份初始来源 · ${req.allow_web?'允许联网':'仅本地来源'}`:''}</p>${runProcessHTML(job,{...publicProgress,stage:pendingRequests.length?'等待你的确认':publicProgress.stage||stageLabel,started:publicProgress.started||p.started})}<p class="help">${p.draft_ready?'正文已可查看，评分独立完成。':'正文保存后会自动显示；等待子 agent 时可能暂时没有新消息。'}${p.last_activity?' 最近活动：'+clock(p.last_activity):''}</p>`;
  bindRunProcess();
  if(pendingRequests.length){
   $('run-progress').insertAdjacentHTML('beforeend',`<div class="agent-question" role="status"><strong>有 ${pendingRequests.length} 项操作等待确认</strong><p>打开任务对话，查看具体操作并选择允许、拒绝或补充信息。回答后任务会继续。</p><button id="progress-requests" class="primary">查看并处理</button></div>`);
   $('progress-requests').onclick=()=>selectChat(taskSession);
  }
  $('progress-timeout').onclick=showSettings;
  $('progress-stop').onclick=()=>action(()=>api('stop',{job_id:job.id}));
 }catch(e){if(isCurrent()){$('run-progress').hidden=false;$('run-progress').textContent='进度连接暂时中断，任务没有重新提交。'}}
 finally{if(progressRequest===request)progressRequest=null}
}

function friendlyModel(model,backend){return model==='default'?(backend==='claude'?'跟随 Claude Code 默认':'跟随宿主默认'):model}
function effortValue(runtime,key){return Object.prototype.hasOwnProperty.call(runtime,key)?(runtime[key]||'none'):'none'}
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
function modelLabel(cfg){if(!cfg?.model)return '未指定模型';const backend=cfg.agent_backend||cfg.backend;const prefix=backend?runtimeName(backend)+' · ':'';if(cfg.model_variant!=null||['opencode','briefloop-native'].includes(backend)){const variant=cfg.model_variant||'模型默认';return prefix+friendlyModel(cfg.model,backend)+' / '+variant}if(backend&&!['codex','opencode'].includes(backend)&&!Object.hasOwn(cfg,'reasoning_effort'))return prefix+friendlyModel(cfg.model,backend);const effort=cfg.reasoning_effort,effortLabel=Object.prototype.hasOwnProperty.call(cfg,'reasoning_effort')?(!effort||effort==='none'?'模型默认':effort):'未记录';return prefix+friendlyModel(cfg.model,backend)+' / '+effortLabel+(cfg.model_provider?' · '+cfg.model_provider:'')+(cfg.service_tier==='fast'?' · Fast（请求）':cfg.service_tier==='default'?' · 标准':'')}
function backendValue(){return ($('agent-backend')&&$('agent-backend').value)||state.settings.agent_backend||'codex'}
function renderBackend(){
 const backend=backendValue(),variant=['opencode','briefloop-native','mimo'].includes(backend),codex=backend==='codex';
 $('variant-field').hidden=!variant;document.querySelector('.main-provider-field').style.display=codex?'':'none';
 $('effort-select').hidden=variant;$('effort-select').closest('label').hidden=variant;
 reasoning.configure($(variant?'model-variant':'effort-select'),backend,$('model-select').value.trim(),{variant});
 $('model-select').placeholder=['opencode','briefloop-native'].includes(backend)?'输入 provider/model':'输入任意模型 ID';
 document.querySelectorAll('.role-variant-field').forEach(e=>e.hidden=!variant);
 document.querySelectorAll('.role-provider-field').forEach(e=>e.style.display=codex?'':'none');
 document.querySelectorAll('.role-effort-select').forEach(e=>{e.style.display=variant?'none':'';reasoning.configure(variant?$(`role-${e.dataset.roleEffort}-variant`):e,backend,$(`role-${e.dataset.roleEffort}-model`).value.trim()||$('model-select').value.trim(),{variant})});
 renderSearchProvider();updateModelLabel();renderFastControls();
}
function updateModelLabel(){refreshRuntimeModelSummaries();const op=['opencode','briefloop-native'].includes(backendValue());const cfg=op?{model:$('model-select').value.trim(),model_variant:$('model-variant').value.trim()||null,agent_backend:backendValue()}:{model:$('model-select').value.trim(),reasoning_effort:backendValue()==='mimo'?($('model-variant').value.trim()||null):$('effort-select').value,model_provider:$('model-provider').value.trim(),service_tier:selectedServiceTier('service-tier',fastControlConfig('main')),agent_backend:backendValue()};$('execution-choice').textContent='即将使用：'+modelLabel(cfg);if($('setup-model-summary'))$('setup-model-summary').textContent=modelLabel(cfg);$('generate-button').textContent='使用 '+modelLabel(cfg)+' 生成简报 →';$('model-select').title=cfg.model?friendlyModel(cfg.model,cfg.agent_backend)+' · '+cfg.model:'输入模型 ID'}
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
  root.querySelectorAll('[data-runtime-select]').forEach(button=>button.onclick=()=>{if(button.dataset.runtimeSelect===backendValue())return;select.value=button.dataset.runtimeSelect;select.dispatchEvent(new Event('change'))});
 }
 renderSettingsSessionNote();
}
async function refreshRuntimeDiscovery(force=false){
 const select=$('agent-backend');if(!select.value){const backend=state.settings.agent_backend||'codex';if(!Array.from(select.options).some(o=>o.value===backend))select.add(new Option(backend,backend));select.value=backend;}
 if(!$('runtime-discovery-status')){const box=document.createElement('section');box.className='runtime-discovery';box.innerHTML='<div class="section-title"><strong>本机 Agent CLI</strong><button type="button" id="runtime-discovery-refresh" class="outline">重新检测</button></div><p id="runtime-discovery-status" class="help" role="status"></p><div id="runtime-discovery-details" class="help"></div><p id="runtime-model-status" class="help" role="status"></p>';$('settings-runtime-list').append(box);$('runtime-discovery-refresh').onclick=()=>refreshRuntimeDiscovery(true)}
 $('runtime-discovery-status').textContent='正在检测执行引擎…';$('runtime-discovery-refresh').disabled=true;
 try{const data=await api('runtimes'+(force?'?refresh=1':''));runtimeCatalog=data.runtimes||[];renderRuntimeDiscovery();if(typeof office!=='undefined'&&data.capabilities?.officecli)office.renderSettingsCapability(data.capabilities.officecli);$('runtime-discovery-status').textContent=`检测到 ${runtimeCatalog.filter(r=>r.installed).length} 个本机 CLI，其中 ${runtimeCatalog.filter(r=>r.available).length} 个可选择；检测未验证账号与模型调用；选择模型后可在普通对话中使用。`+(data.diagnostic?` ${data.diagnostic}`:'');await refreshModelSuggestions(force)}catch(e){$('runtime-discovery-status').textContent='检测失败：'+e.message}finally{$('runtime-discovery-refresh').disabled=false;runtimeScanned=true;if($('welcome')&&!$('welcome').hidden)renderWelcome()}
}
$('agent-backend').onchange=()=>action(async()=>{const backend=backendValue(),dropped=Object.keys(state.settings.role_models||{}).length;await api('settings',{agent_backend:backend,model_selection_required:true,role_models:{}});state.settings.agent_backend=backend;state.settings.model_selection_required=true;state.settings.role_models={};assignEffort('effort-select',settingsEffort(state.settings,backend));$('model-variant').value=backend==='mimo'?(state.settings.runtime_efforts?.mimo||''):(state.settings.model_variant||'');reasoning.refresh();$('model-select').value='';$('chat-model').value='';renderRuntimeDiscovery();renderRoleModels();renderBackend();updateComposer();await refreshModelSuggestions();notice(dropped?'宿主已切换；原宿主的角色模型已清空，留空即继承主链模型':'Runtime 已保存；请选择或输入模型')});$('model-variant').onchange=()=>action(saveModel,'Variant 已保存；下一次启动生效');
const modelDirectory=createModelCatalog({api,$,getBackend:backendValue,getChatBackend:chatBackendChoice,runtimeName,runtimeIcon,onCatalogChange:refreshRuntimeModelSummaries,openExecutionPicker:target=>runtimeModelPicker.open(target)});
const {catalogs:modelCatalogs,fetchModelCatalog,openModelPicker,refreshCurrentModelPicker,renderModelPicker,pickModel,refreshInlineModelPickers,setupModelPickers}=modelDirectory;
const runtimeModelPicker=createRuntimeModelPicker({api,$,directory:modelDirectory,getRuntimes:()=>runtimeCatalog,...createRuntimePickerBindings({$,api,getState:()=>state,getChat:()=>chat,getBackend:backendValue,getChatBackend:chatBackendChoice,assignEffort,setChatPermission,rememberDraft,refreshChat:()=>{reasoning.refresh();chatError();updateComposer();refreshInlineModelPickers()},refreshSettings:()=>{reasoning.refresh();renderRuntimeDiscovery();renderRoleModels();renderBackend();refreshInlineModelPickers();renderWelcome();renderSettingsSessionNote()}})});
async function refreshModelSuggestions(force=false){if(force)fastCapabilities.clear();renderFastControls();await modelDirectory.refreshModelSuggestions()}
$('model-picker-close').onclick=()=>$('model-picker').close();
$('model-picker-search').oninput=()=>renderModelPicker();
$('model-picker-search').onkeydown=event=>{if(event.key==='Enter'&&!event.isComposing){event.preventDefault();$('model-picker-list').querySelector('[data-model-pick]')?.focus()}};
$('model-picker-refresh').onclick=()=>refreshCurrentModelPicker();
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

$('version-history').onclick=()=>action(async()=>{await savedVersion();if(current)await reportBrowsing.showHistory(current)});
$('close-history').onclick=()=>$('history-dialog').close();
$('version-diff').onclick=()=>action(async()=>{
 await savedVersion();if(!current)return;
 const target=current;
 const versions=state.briefs.filter(b=>b.run_id===target.run_id),index=versions.findIndex(b=>b.id===target.id);
 const history=typeof reportBrowsing==='undefined'?{items:versions.slice(index+1)}:await reportBrowsing.comparisonVersions(target);if(current!==target)return;
 const older=history.items;let nextCursor=history.next_cursor;
 if(!older.length){notice('这是第一稿，还没有可比较的上一版本');return}
 const label=b=>moment(b.created)+' · '+(b.author==='agent'?'AI 稿件':b.author==='user'?'用户修改':b.author==='import'?'往期原稿':'原稿');
 function fillComparison(){ $('diff-base').innerHTML=older.map((b,i)=>`<option value="${esc(b.id)}">${i===0?'上一稿 · ':!b.parent_id?'第一稿 · ':''}${esc(label(b))}</option>`).join('')+(nextCursor?'<option value="more">加载更多历史版本…</option>':'')}fillComparison();
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
 $('diff-base').onchange=async()=>{try{if($('diff-base').value==='more'){++comparison;const result=await reportBrowsing.comparisonVersions(target,nextCursor);if(current!==target)return;for(const b of result.items)if(!older.some(old=>old.id===b.id))older.push(b);older.sort((a,b)=>b.position-a.position);nextCursor=result.next_cursor;fillComparison();$('diff-base').value=result.items[0]?.id||older[0].id}const ticket=comparison+1;compare().catch(e=>{if(ticket===comparison){$('diff-body').replaceChildren();$('diff-next').disabled=true;$('diff-summary').textContent='比较版本读取失败：'+e.message}})}catch(e){notice(e.message,true)}};
 await compare();$('diff-dialog').showModal();
});
$('close-diff').onclick=()=>$('diff-dialog').close();


// Interactive agent conversations. Artifact editors keep their existing state.
const chat = {view:'active',home:true,sessions:[],id:null,session:null,messages:[],requests:[],events:new Map(),after:0,busy:false,uploading:0,polling:false,drafts:new Map(),attachments:new Set(),request:null};
const nativeRequests=createNativeRequests({api,getSessionId:()=>chat.id,refresh:()=>pollChat(true)});
const composerOptions=createComposerOptions({$,getChat:()=>chat,getState:()=>state,availability:()=>compactFactAvailability('chat'),rememberDraft,openSettings:name=>{showSettings();settingsView(name)},openReview:()=>{page('setup');const panel=document.querySelector('.review-runtime-settings');panel.open=true;panel.scrollIntoView({block:'center'})}});
const permissionDirectory=createPermissionDirectory({api,getModel:()=>($('chat-model')?.value||'').trim(),onChange:backend=>{if(backend===chatBackendChoice())renderChatRuntimePermissions()}});
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
 chat.hostOptions=previous?.host_options||{};setChatPermission(previous?.permission||'');renderChatRuntimePermissions();
 chat.request=null;rememberDraft();updateComposer();refreshInlineModelPickers();
};
function rememberDraft(){chat.drafts.set(chat.id||'new',{text:$('chat-input').value,sources:[...chat.attachments],backend:chatBackendChoice(),model:$('chat-model').value,model_provider:$('chat-model-provider').value.trim()||null,effort:$('chat-effort').value,variant:($('chat-variant')?.value||'').trim(),service_tier:(chatBackendChoice())==='codex'?($('chat-service-tier').value||null):null,allow_web:$('chat-allow-web').checked,permission:$('chat-permission').value,host_options:chat.hostOptions||{},report_options:chat.reportOptions||{}});try{sessionStorage.setItem('briefloop-chat-drafts',JSON.stringify([...chat.drafts].slice(-30)))}catch{}}
function restoreDraft({preserveNewDraft=false}={}){
 const d=chat.drafts.get(chat.id||'new'),sessionRuntime=chat.session?.runtime;
 const backend=((chat.id||preserveNewDraft)&&d?.backend)||sessionRuntime?.backend||state.settings.agent_backend||'codex';
 chat.nextBackend=backend;
 const saved=d&&(chat.id||d.backend===backend||(!d.backend&&sessionRuntime))?d:null;
 const fallback={model:state.settings.model_selection_required?'':state.settings.model,backend,effort:settingsEffort(state.settings,backend),variant:backend==='mimo'?state.settings.runtime_efforts?.mimo:state.settings.model_variant,model_provider:state.settings.model_provider,service_tier:state.settings.service_tier};
 const runtime=saved||sessionRuntime||fallback;
 // Searching is the expected default for a fresh chat; a saved draft keeps the user's own choice.
 $('chat-input').value=d?.text||'';chat.attachments=new Set(d?.sources||[]);$('chat-allow-web').checked=d&&('allow_web' in d)?!!d.allow_web:(chat.id?[...(chat.messages||[])].reverse().find(m=>m.role==='user'&&m.purpose!=='runtime_test')?.allow_web??(state.settings.chat_allow_web!==false):state.settings.chat_allow_web!==false);chat.hostOptions=runtime.host_options||{};chat.reportOptions=d?.report_options||{};
 $('chat-model').value=runtime.model||'';assignEffort('chat-effort',Object.hasOwn(runtime,'effort')?effortValue(runtime,'effort'):'none');if($('chat-variant'))$('chat-variant').value=runtime.variant||(runtime.backend==='mimo'?runtime.effort:'')||'';
 $('chat-model-provider').value=runtime.model_provider||'';$('chat-service-tier').value=runtime.service_tier||'';setChatPermission(runtime.permission||'');
 renderAttachments();updateComposer();autoSizeChatInput();refreshInlineModelPickers();
}
function setChatPermission(value){const select=$('chat-permission');select.value=value;if(select.value!==value){select.add(new Option(value,value));select.value=value}}
function renderChatRuntimePermissions(){
 const backend=chatBackendChoice(),select=$('chat-permission');
 // The select remains a hidden draft carrier; visible choices come only from
 // the current runtime catalog in the permission panel.
 select.hidden=true;$('chat-permissions-native').hidden=true;
 if(typeof permissionDirectory!=='undefined'&&!permissionDirectory.get(backend))permissionDirectory.load(backend).catch(()=>{});
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
function runtimeChoice(){
 const model=$('chat-model').value.trim();if(!model)throw Error('请输入模型 ID');
 const backend=chatBackendChoice(),managed=['codex','opencode','briefloop-native'].includes(backend);
 let permission=managed?$('chat-permission').value:'';
 if(typeof permissionDirectory!=='undefined'){
  const catalog=permissionDirectory.get(backend),chosen=managed?permission:chat.hostOptions?.mode;
  if((managed||chosen)&&!catalog)throw Error('权限目录尚未读取完成，请稍后发送或打开权限面板刷新。');
  if(catalog){
   const choice=permissionSelection(catalog,chosen);
   if(choice.unavailable)throw Error('原权限模式已不可用，请重新选择后发送。');
   if(managed){permission=choice.selected;if(!permission)throw Error('未取得可用的默认权限，请打开权限面板重新选择。')}
  }
 }
 if(!managed)return {model:reasoningModel(backend,model,$('chat-effort').value),backend,effort:backend==='mimo'?($('chat-variant').value.trim()||null):($('chat-effort').value==='none'?null:$('chat-effort').value),permission:'runtime-native',host_options:chat.hostOptions||{}};
 if(['opencode','briefloop-native'].includes(backend)){if(!model.includes('/'))throw Error('模型必须是 provider/model 形式');return {model,backend,variant:($('chat-variant')?.value||'').trim()||null,permission}}
 return {model,backend,model_provider:$('chat-model-provider').value.trim()||null,effort:$('chat-effort').value,service_tier:selectedServiceTier('chat-service-tier',fastControlConfig('chat'),true),permission};
}
function messageTime(value){return clock(value)}
function chatError(text=''){$('chat-error').textContent=text;$('chat-error').hidden=!text}
function sessionMissing(error){return /会话或消息不存在|会话不存在/.test(String(error&&error.message||error||''))}
function updateComposer(){syncCompactReportControls();renderChatBackendChoice();renderChatRuntimePermissions();const readonly=chat.session&&chat.session.lifecycle&&chat.session.lifecycle!=='active';const active=chatActive(),steering=active&&$('chat-mode').value==='steer';if(steering){const runtime=activeChatRuntime();setChatPermission(runtime.permission||'workspace-write');$('chat-model').value=runtime.model||'';assignEffort('chat-effort',effortValue(runtime,'effort'));if($('chat-variant'))$('chat-variant').value=runtime.variant||(runtime.backend==='mimo'?runtime.effort:'')||'';$('chat-model-provider').value=runtime.model_provider||'';$('chat-service-tier').value=runtime.service_tier||'';const activeMessage=chat.messages.find(m=>m.role==='user'&&m.turn_id===chat.session?.turn_id);$('chat-allow-web').checked=!!activeMessage?.allow_web} renderFastControls();$('chat-allow-web').disabled=readonly||steering||chat.busy;for(const id of ['chat-model','chat-effort','chat-variant','chat-model-provider','chat-service-tier'])if($(id)){$(id).disabled=readonly||steering||chat.busy;if($(id+'-choices'))$(id+'-choices').disabled=$(id).disabled;}$('chat-permission').disabled=readonly||steering||chat.busy;$('new-session').disabled=chat.busy||chat.uploading>0;$('chat-input').readOnly=chat.busy||readonly;document.querySelectorAll('[data-chat-session]').forEach(b=>b.disabled=chat.busy||chat.uploading>0);$('chat-send').disabled=readonly||chat.busy||chat.uploading>0||(!$('chat-input').value.trim()&&!chat.attachments.size)||!$('chat-model').value.trim();const sendLabel=chat.busy?'发送中…':active?($('chat-mode').value==='steer'?'立即补充':'排队发送'):'发送消息';$('chat-send').textContent=chat.busy?'…':'发送';$('chat-send').setAttribute('aria-label',sendLabel);$('chat-send').title=sendLabel;$('chat-stop').hidden=!sessionBusy(chat.session);$('chat-stop').disabled=chat.busy;$('chat-mode').disabled=!active||chat.busy;$('chat-attach').disabled=readonly||chat.busy||chat.uploading>0;$('attach-existing').disabled=readonly||chat.busy;$('chat-attach').querySelector('span').textContent=chat.uploading?'上传中…':'附件';const model=$('chat-model').value.trim(),label=friendlyModel(model,chatBackendChoice())||'选择模型';$('chat-model').title=model?friendlyModel(model)+' · '+model:'输入模型 ID';if(!model)$('chat-send').title='请先选择模型';const chatBackend=chatBackendChoice();const speed=$('chat-service-tier').hidden?'':($('chat-service-tier').value==='fast'?' · Fast（请求）':$('chat-service-tier').value==='default'?' · 标准':'');const effort=['opencode','briefloop-native','mimo'].includes(chatBackend)?(($('chat-variant')?.value||'').trim()||'模型默认'):($('chat-effort').value==='none'?'模型默认':$('chat-effort').value);$('composer-help').textContent=`Enter 发送 · Shift + Enter 换行 · ${label}${' / '+effort}${speed}${active?' · 立即补充沿用当前联网与模型设置；更改设置请排队到下一回合':''}${model?'':' · 请先从模型列表选择或输入模型 ID'}`;if(typeof modelDirectory!=='undefined')modelDirectory.refreshModelLabels()}
function sessionBusy(session){if(!session)return false;if(typeof session.busy==='boolean')return session.busy;return ['starting','running','stopping'].includes(session.status)||!!session.turn_id||(session.id===chat.id&&(chat.messages.some(m=>['queued','sending','delivered','streaming'].includes(m.status))||chat.requests.some(r=>r.status==='pending')))}
function renderSessions(){
 const signature=JSON.stringify([chat.view,chat.id,chat.sessions]);if(renderSessions.signature===signature)return;renderSessions.signature=signature;$('session-view').value=chat.view;
 $('archive-completed').hidden=chat.view!=='active';$('archive-completed').disabled=chat.busy||!chat.sessions.some(s=>!sessionBusy(s));
 const empty={active:'对话会保存在这里。随时回来继续。',archived:'暂无归档对话。',deleted:'回收站为空。'}[chat.view];
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
 $('existing-sources').innerHTML=sources.length?sources.map(s=>`<label><input type="checkbox" data-chat-source="${esc(s.id)}" ${chat.attachments.has(s.id)?'checked':''} ${s.status!=='ready'?'disabled':''}><span>${esc(s.name)}</span><small>${s.status==='ready'&&!s.needs_visual?'':esc(sourceStatusLabel(s))}</small></label>`).join(''):'<p class="help">还没有已保存来源。可以上传文件，也可以开启联网检索。</p>';
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
  const reqBlock=/```briefloop-requirements\s*([\s\S]*?)```/.exec(message.text||'');if(reqBlock){const apply=document.createElement('button');apply.type='button';apply.className='outline apply-requirements';apply.textContent='应用到材料与需求';apply.onclick=()=>applyRequirements(reqBlock[1].trim());node.append(apply)}previousReport.appendReaderAction(node,message.text);
  workspaceProposal(node,message.text);
  bindMessageActions(node,message);
  node.querySelectorAll('[data-message-source]').forEach(button=>button.onclick=()=>action(async()=>showSource(await api('source?id='+encodeURIComponent(button.dataset.messageSource)))));
  if(message.role==='assistant'&&message.status==='completed'&&message.text&&message.mode!=='notice'){api('render',{markdown:message.text}).then(result=>{if(node.isConnected&&node.dataset.signature===messageSignature){node.querySelector('.message-body').innerHTML=result.html;node.querySelector('.message-body').classList.add('rendered-markdown');if(nearEnd)scroll.scrollTop=scroll.scrollHeight}}).catch(()=>{})}
 }
 for(const node of nodes.values())node.remove();renderRuntimeFeedback();if(nearEnd)scroll.scrollTop=scroll.scrollHeight;
 }
}
const scheduledReports=scheduleUI({api,getState:()=>state,refresh:async()=>{await refresh();await refresh();},notice,openReport:id=>{const brief=state.briefs.find(b=>b.id===id)||{id};if(brief&&openBrief(brief,{follow:false}))page('report')}});
const homePage=homeUI({api,action,page,openBrief,openTask,taskFor,taskLabel,statuses,parse,reportStatus,reportDescription,scheduledReports,getState:()=>state});
const {bannerTitle,renderTaskBanner,renderHome,renderHomeTasks,reportIconMeta}=homePage;
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
   const source=await uploadSource(file);if(source.status!=='ready'){chatError(`${file.name}：${sourceStatusLabel(source)}${source.error?' · '+source.error:''}`);continue}chat.attachments.add(source.id);selected.add(source.id)}await refresh();renderAttachments();rememberDraft()}catch(e){chatError('上传未完成：'+e.message)}finally{chat.uploading--;updateComposer()}
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
const permissionPanel=createRuntimePermissionPanel({
 $,api,directory:permissionDirectory,getBackend:chatBackendChoice,getModel:()=>($('chat-model').value||'').trim(),runtimeName,
 getSelection:catalog=>['codex','opencode','briefloop-native'].includes(catalog.backend)?$('chat-permission').value:chat.hostOptions?.mode,
 onSelect:(mode,catalog)=>{
  if(catalog.kind==='native')setChatPermission(mode)
  else chat.hostOptions={...chat.hostOptions,mode};
  rememberDraft();updateComposer();
 },
 isLocked:()=>$('chat-permission').disabled||[...chat.events.values()].some(event=>event.kind==='session/internal')
});
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
 const pending=chat.requests.filter(request=>request.status==='pending'&&requestKind(request)==='permission');
 $('chat-permissions-open').textContent='权限'+(pending.length?' · '+pending.length:'');
 if(!$('chat-permissions-dialog').open)return;
 nativeRequests.render($('chat-permissions-pending'),chat.requests,{permissionsOnly:true,empty:'<p class="help">当前没有待确认的操作。执行授权与补充需求分别处理。</p>'});
}
function loadPermissionPanel(){return permissionPanel.load()}

function renderRequests(){
 renderPermissionRequests();
 $('chat-requests').hidden=!chat.requests.some(request=>request.status==='pending');
 nativeRequests.render($('chat-requests'),chat.requests);
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
 const modelPanel=document.querySelector('.model-settings');const shortcut=document.createElement('div');shortcut.className='setup-settings-shortcut';shortcut.innerHTML='<div><span>生成模型</span><strong id="setup-model-summary">未指定模型</strong></div><button type="button" class="outline">选择运行时与模型</button>';shortcut.querySelector('button').onclick=()=>openModelPicker('model-select');modelPanel.before(shortcut);$('settings-model-block').append(modelPanel);
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

sourceViewer.init();

searchSettings.init();
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

$('chat-model').oninput=updateComposer;
for(const id of ['model-select','model-provider'])$(id).addEventListener('input',()=>renderFastControls());
$('chat-model-provider').oninput=()=>{rememberDraft();updateComposer()};
for(const id of ['chat-model','chat-model-provider','model-select','model-provider'])$(id).addEventListener('keydown',event=>{if(event.key==='Enter'){event.preventDefault();event.stopPropagation();$(id).blur()}});

const lengthControls=createLengthControls({$,notice,language:()=>reportLanguage($('report-language').value)||'zh'});
lengthControls.init();
const reportFormDefaults=createReportFormDefaults({$,onChange:()=>{validateLengthInputs();reflectBudgetPreset()}});
reportFormDefaults.init();
// Automatic lengths follow language, purpose, research depth and report period.
const reportMarket=createMarketConvention({$,api,notice,getState:()=>state,getCurrent:()=>current});
reportMarket.init();
const reportLanguageForm=reportLanguageUI({notice,onChange:()=>{reportFormDefaults.sync();reportMarket.syncLanguage()}});
reportLanguageForm.init();
function presetLengths(extent){const presets=LANGUAGE_LENGTHS[reportLanguageForm.current()];return presets[extent]||presets.balanced}
function initializeLengthInputs(requirements){
 reportFormDefaults.restore(requirements);
 const preset=presetLengths($('length-preset').value);
 $('target-words').value=requirements.target_words??preset[0];$('max-words').value=requirements.max_words??preset[1];lengthControls.restore(requirements);validateLengthInputs();
}
function validateLengthInputs(){
 const target=Number($('target-words').value),maximum=Number($('max-words').value);
 $('max-words').setCustomValidity(Number.isInteger(target)&&target>0&&Number.isInteger(maximum)&&maximum>0&&maximum<target?'篇幅上沿不能小于建议目标，请调整其中一个数字。':'');
}
$('length-preset').onchange=()=>{const [target,maximum]=presetLengths($('length-preset').value);$('target-words').value=target;$('max-words').value=maximum;validateLengthInputs()};
for(const id of ['target-words','max-words'])$(id).oninput=validateLengthInputs;
function renderBriefLength(){
 renderReportDataButton();
 const element=$('brief-length');if(!current){element.hidden=true;return}element.hidden=false;
 const stats=state.briefs.find(brief=>brief.id===current.id)?.length_stats||current.length_stats;
 lengthControls.renderBrief(element,stats,{dirty,language:runLanguage(state,current.run_id)});
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
$('session-view').onchange=async event=>{closeSessionMenu();chat.view=event.target.value;chat.sessions=[];renderSessions();await pollChat(true).catch(e=>notice(e.message,true))};
$('archive-completed').onclick=async()=>{
 if(chat.busy)return;closeSessionMenu();const button=$('archive-completed');button.disabled=true;
 try{rememberDraft();const result=await api('harness/archive-completed',{});await pollChat(true);notice(result.count?`已归档 ${result.count} 条已结束对话。`:'没有可归档的已结束对话。')}catch(e){notice(e.message,true)}finally{button.disabled=chat.busy||!chat.sessions.some(s=>!sessionBusy(s))}
};
document.addEventListener('click',event=>{if(!event.target.closest('#session-actions-menu')&&!event.target.closest('[data-manage-session]'))closeSessionMenu()});
document.addEventListener('keydown',event=>{if(event.key==='Escape')closeSessionMenu()});

researchBudget.init();

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
 const meeting=workflow?.id==='meeting_minutes',fast=$('completion-mode').value==='fast',fastWeb=$('completion-mode').value==='fast_web'&&!meeting;
 $('source-requirement').textContent=fast?'需要已读取的文本':meeting?'需要本次会议记录':'可选';
 $('source-input-hint').textContent=fastWeb?'可直接输入研究目标，无需先上传材料。会挑选并保存公开网页原文；你选中的已有材料也会用于写作。':fast?'添加并选择可读文本材料后直接写作。扫描图片需先完成文字读取；此模式不代为检索资料。':meeting?'请添加并选择本次会议转写或笔记。只有议程时可整理框架，不能生成未发生的讨论或决议。':'可选：图片、PDF、Word、Excel、Markdown、文本、CSV。也可以直接输入目标，让 Agent 联网研究。';
 $('source-web-hint').textContent=fastWeb?'快速联网最多 3 次搜索、读取 6 篇原文；受所选渠道与额度约束，失败和覆盖缺口会保留。':fast?'快速模式只使用已选材料；切回完整流程可恢复联网研究。':meeting?'会议内容来自已选转写或笔记；公开检索只能补充另行要求的背景，不能替代会中记录。':'开启后，无需先上传文件，Agent 会围绕目标查找并保存公开来源。关闭时仍可讨论问题或使用已有材料。';
}
function syncWorkflowProfile(changeLength=true){
 const choice=readWorkflowChoice();
 const next=choice.workflow_id==='business_report'&&choice.workflow_variant==='industry_periodic'?'industry_periodic':'brief';
 const previous=$('report-profile').value;$('report-profile').value=next;
 if(changeLength&&previous!==next)$('report-profile').dispatchEvent(new Event('change'));
 else $('industry-profile-options').hidden=next!=='industry_periodic';
 reportFormDefaults.sync();renderWorkflowChoices();
}
$('workflow-choice').onchange=()=>syncWorkflowProfile();

// Reference reports are explicitly separated from this period's evidence.
const INDUSTRY_TASK_OUTLINE='撰写行业定期报告，围绕核心摘要、行业指标、供需竞争、重点专题与组织启示展开。可根据行业与读者需要调整章节。数据标明日期、单位、口径及比较期，实际与预测分开。分析从事实出发，解释传导机制、适用条件与下一步观察项。正文围绕本期变化、对组织的影响及有依据的行动展开；核查与待补信息单独保存。';
function industryProfileActive(){return $('report-profile').value==='industry_periodic'}
function renderReferenceSources(){
 const el=$('reference-source-list');if(!el||!state)return;
 el.innerHTML=state.sources.length?state.sources.map(s=>`<label class="check"><input type="checkbox" data-reference-source="${esc(s.id)}" ${referenceSelected.has(s.id)?'checked':''} ${s.status!=='ready'?'disabled':''}><span>${esc(s.name)}</span></label>`).join(''):'<p class="help">上传历史报告后，可在这里选择。参考报告不是必需材料。</p>';
 el.querySelectorAll('[data-reference-source]').forEach(input=>input.onchange=()=>{const id=input.dataset.referenceSource;if(input.checked){referenceSelected.add(id);selected.delete(id);const evidence=$('source-list').querySelector(`[data-check="${CSS.escape(id)}"]`);if(evidence)evidence.checked=false}else referenceSelected.delete(id)});
}
function initializeReportProfile(requirements){
 referenceSelected=new Set(requirements.reference_source_ids||[]);
 for(const id of referenceSelected)selected.delete(id);
 $('industry-profile-options').hidden=!industryProfileActive();$('length-preset').disabled=industryProfileActive();$('length-preset').closest('label').hidden=industryProfileActive();
 validateLengthInputs();renderReferenceSources();
}
$('report-profile').onchange=()=>{
 if(industryProfileActive()){
  for(const id of referenceSelected){selected.delete(id);const evidence=$('source-list').querySelector(`[data-check="${CSS.escape(id)}"]`);if(evidence)evidence.checked=false}
 }
 $('industry-profile-options').hidden=!industryProfileActive();$('length-preset').disabled=industryProfileActive();$('length-preset').closest('label').hidden=industryProfileActive();reportFormDefaults.sync();
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


setupModelPickers();




$('text-color').oninput=e=>editor?.chain().focus().setMark('textStyle',{color:e.target.value}).run();
$('cell-color').oninput=e=>editor?.chain().focus().setCellAttribute('backgroundColor',e.target.value).run();
$('paragraph-align').onchange=e=>{if(!editor)return;const type=editor.isActive('heading')?'heading':'paragraph';editor.chain().focus().updateAttributes(type,{textAlign:e.target.value}).run()};

function renderWordExports(){
 const box=$('word-exports');if(!box||!state)return;
 const scope=current?.id||'';box.dataset.version=scope;
 const fileKinds={export_docx:'工作稿 Word',release:'正式 Word',audit_bundle:'审计包',export_xlsx:'工作稿 Excel'};
 const jobs=state.jobs.filter(j=>fileKinds[j.kind]&&(!current||parse(j.payload).run_id===current.run_id));
 box.hidden=!jobs.length;
 box.innerHTML=jobs.slice(0,6).map(j=>{const result=parse(j.result),payload=parse(j.payload);const url=j.kind==='release'?'/api/release-file?id='+encodeURIComponent(payload.release_id):j.kind==='audit_bundle'?'/api/audit-file?job='+encodeURIComponent(j.id):result.download_url;const officeSummary=j.status==='complete'&&result.office&&typeof office!=='undefined'?esc(office.officeCheckSummary(result.office)):'';const previewButton=j.status==='complete'&&url&&['export_docx','export_xlsx','release'].includes(j.kind)&&typeof office!=='undefined'&&office.officeEnabled()?`<button type="button" data-office-preview="${esc(j.id)}">预览</button>`:'';return `<div class="job"><span>${fileKinds[j.kind]} · ${j.status==='complete'?'已制作':statuses[j.status]||esc(j.status)}</span><small>${payload.version_id===current?.id?'当前稿件版本':'历史稿件版本'}</small>${j.status==='complete'&&url?`<a href="${esc(url)}" download>下载${fileKinds[j.kind]}</a>`:`<span>${esc(j.error||'使用提交时固定的版本，可继续编辑')}</span>`}${officeSummary?`<span>${officeSummary}</span>`:''}${previewButton}</div>`}).join('');
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
 const reused=nextReport.requirementsForTemplate(templateId);
 const saved=reused?.sections||(templateId&&state.requirements?.template_id===templateId?state.requirements.sections||[]:[]);
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
 const reused=nextReport.requirementsForTemplate(templateId);
 if(!templateId||(!reused&&state?.requirements?.template_id!==templateId))return;
 for(const section of (reused||state.requirements).sections||[]){const row=[...$('template-sections').querySelectorAll('[data-section-id]')].find(r=>r.dataset.sectionId===section.section_id);if(row){row.querySelector('[data-title]').value=section.title;row.querySelector('select').value=section.mode||'required'}}
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
$('template-select').onchange=()=>{templateSections();applyTemplateSectionEdits();renderWorkflowChoices();reportLanguageForm.syncTemplate(state.templates?.find(t=>t.id===$('template-select').value))};
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
$('provider-engine').onchange=()=>{providerDirectory.invalidate();$('custom-api-key').value='';$('custom-provider').value='';$('provider-open').click()};
$('provider-open').onclick=async()=>{$('provider-result').textContent='';$('custom-supports-images').value='';providerCapabilities.reset(providerEndpoint());$('provider-use').hidden=true;settingsView('models');settingsModelTab('api');
 try{const r=await api(providerEndpoint()+'/providers');savedProviderConfigurations=r.configurations||[];
 $('custom-saved').innerHTML='<option value="">新建配置</option>'+savedProviderConfigurations.map((p,i)=>`<option value="${i}">${esc(p.name)} · ${esc(p.model)}</option>`).join('');
 const selected=savedProviderConfigurations.findIndex(p=>p.provider===$('custom-provider').value.trim()&&p.model===$('custom-model').value.trim());
 if(selected>=0){
  $('custom-saved').value=String(selected);$('custom-saved').dispatchEvent(new Event('change'));
 }else if(!$('custom-provider').value.trim()&&savedProviderConfigurations.length){
  const previous=localStorage.getItem('briefloop-provider-id');const index=Math.max(0,savedProviderConfigurations.findIndex(p=>p.provider===previous));
  $('custom-saved').value=String(index);$('custom-saved').dispatchEvent(new Event('change'));
 }else{$('custom-saved').value='';if(savedProviderConfigurations.some(p=>p.provider===$('custom-provider').value.trim()))loadProviderCatalog()}
 }catch(e){$('provider-result').textContent='已有配置读取失败：'+e.message}
};
$('custom-saved').onchange=()=>{
 const p=savedProviderConfigurations[Number($('custom-saved').value)];if($('custom-saved').value===''||!p){providerCapabilities.reset(providerEndpoint());return}
 for(const [id,key] of [['custom-provider','provider'],['custom-name','name'],['custom-model','model'],['custom-protocol','protocol'],['custom-base-url','base_url'],['custom-context-limit','context_limit'],['custom-output-limit','output_limit'],['custom-supports-images','supports_images']])$(id).value=p[key]==null?'':String(p[key]);
 providerCapabilities.load(p,providerEndpoint());
 $('custom-api-key').value='';$('provider-use').hidden=true;localStorage.setItem('briefloop-provider-id',p.provider);loadProviderCatalog();
};
$('provider-close').onclick=()=>{$('custom-api-key').value='';if(welcomeFromProvider){welcomeFromProvider=false;page('welcome');renderWelcome();$('provider-close').textContent='返回 Agent CLI';$('provider-close').setAttribute('aria-label','返回 Agent CLI')}else settingsModelTab('cli')};
$('provider-dialog').addEventListener('close',()=>{$('custom-api-key').value=''});
$('provider-form').onsubmit=async event=>{
 event.preventDefault();const button=$('provider-save');button.disabled=true;$('provider-result').textContent='正在保存到本机…';
 const engine=providerEndpoint(),backend=engine==='native'?'briefloop-native':'opencode';
 const body={protocol:$('custom-protocol').value,name:$('custom-name').value.trim(),context_limit:$('custom-context-limit').value?Number($('custom-context-limit').value):null,output_limit:$('custom-output-limit').value?Number($('custom-output-limit').value):null,provider:$('custom-provider').value.trim(),base_url:$('custom-base-url').value.trim(),model:$('custom-model').value.trim(),api_key:$('custom-api-key').value,supports_images:$('custom-supports-images').value===''?null:$('custom-supports-images').value==='true',...providerCapabilities.read(engine)};
 $('custom-api-key').value='';
 try{
  const result=await api(engine+'/provider',body);body.api_key='';
  modelDirectory.invalidate(backend);providerDirectory.invalidate(engine,body.provider);
  const refreshed=modelDirectory.fetchModelCatalog(true,backend);
  if(engine!==providerEndpoint()||body.provider!==$('custom-provider').value.trim()){await refreshed;return}
  $('provider-use').hidden=false;$('provider-use').dataset.model=result.model;$('provider-use').dataset.backend=backend;
  $('provider-result').textContent='已保存 '+result.model+'。当前模型选择保持原值。';
  await Promise.all([refreshed,loadProviderCatalog()]);
 }catch(e){$('provider-result').textContent=e.message}finally{body.api_key='';button.disabled=false}
};

const providerDirectory=createProviderCatalog({api,$,getEndpoint:providerEndpoint,modelDirectory});
const loadProviderCatalog=()=>providerDirectory.load();
$('provider-catalog').onclick=loadProviderCatalog;
$('custom-model').onfocus=loadProviderCatalog;
$('custom-provider').oninput=()=>providerDirectory.invalidate();
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
 const p=presets[$('custom-preset').value];if(!p)return;providerDirectory.invalidate();
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

evidenceReviewDialogsUI({api,action,savedVersion,showSource,getEditor:()=>editor,reviewControls}).init();

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
// Tab rows show the choice with .active; aria-selected gives assistive tech the same state.
function markTab(button,selected){button.classList.toggle('active',selected);button.setAttribute('aria-selected',String(selected))}
const REPORT_TABS=['assistant','sources','checks'];
function setReportTab(name){
 const panel=$('report-panel');if(!panel)return;
 if(!REPORT_TABS.includes(name))name='assistant';
 panel.querySelectorAll('[data-pane]').forEach(p=>{p.hidden=p.dataset.pane!==name});
 document.querySelectorAll('#report-panel [data-report-tab]').forEach(b=>markTab(b,b.dataset.reportTab===name));
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
 document.querySelectorAll('#report-tabs [data-report-view]').forEach(b=>markTab(b,b.dataset.reportView===view));
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
function expandReportPanel(){const grid=$('report-grid');if(grid)grid.classList.remove('panel-collapsed');if($('report-panel-reopen'))$('report-panel-reopen').hidden=true;try{localStorage.setItem('briefloop-report-panel','open')}catch{}}
function collapseReportPanel(){const grid=$('report-grid');if(grid)grid.classList.add('panel-collapsed');if($('report-panel-reopen'))$('report-panel-reopen').hidden=false;try{localStorage.setItem('briefloop-report-panel','closed')}catch{}}
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
 else{const pending=!!(current&&reviewPending(current,state.jobs));chips.push(pending?'<span class="chip">评分中</span>':'<span class="chip">未评分</span>')}
 const conflicts=runConflicts(current.run_id).length;if(conflicts)chips.push(`<span class="chip danger">来源分歧 ${conflicts}</span>`);
 box.innerHTML=chips.join('');
}
function renderAssistantSummary(){
 reportSources.render();
 reportMarket.render();
 const box=$('assistant-summary');if(!box)return;
 if(!current){box.innerHTML='';return}
 const run=(state.runs||[]).find(r=>r.id===current.run_id),req=run?parse(run.requirements):{};
 const conflicts=runConflicts(current.run_id).length;
 const tc=req.time_context;
 const kv=[['系统核对日期',tc?`${tc.today} · ${tc.timezone}`:'旧任务未记录'],['时间范围',tc?`${tc.start} 至 ${tc.end_exclusive}（不含结束时刻）`:req.period],['读者',req.audience],['已登记来源',runSourceCount(current.run_id)+' 个']].filter(([,v])=>v);
 const cards=[versionInformationHTML(current,{esc})];
 if(kv.length)cards.push(`<dl class="assistant-card">${kv.map(([k,v])=>`<div class="kv"><dt>${esc(k)}</dt><dd>${esc(String(v))}</dd></div>`).join('')}</dl>`);
 // An empty "needs attention" card is a placeholder; show it only when something needs attention.
 if(conflicts)cards.push(`<div class="assistant-card"><h3>需要关注</h3><div class="attention"><span class="badge danger">数据冲突</span><span>有 ${conflicts} 项来源分歧待处理</span></div></div>`);
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
if($('report-panel-reopen'))$('report-panel-reopen').onclick=expandReportPanel;
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
 if(b.report_status){const labels={scored:'已评分',released:'已正式交付',running:'处理中',draft:'草稿'};return {key:b.report_status,label:labels[b.report_status]+(b.report_status==='scored'&&b.assessment_overall?' · '+b.assessment_overall:''),cls:b.report_status==='released'?'delivered':b.report_status==='scored'?'ok':''}}
 const a=(state.assessments||[]).find(x=>x.version_id===b.id);
 if(a){const d=parse(a.data);if(d.status==='complete')return {key:'scored',label:'已评分'+(d.overall?' · '+d.overall:''),cls:'ok'}}
 if((state.jobs||[]).some(j=>j.kind==='release'&&j.status==='complete'&&parse(j.payload).version_id===b.id))return {key:'released',label:'已正式交付',cls:'delivered'};
 if((state.jobs||[]).some(j=>['generate','revise','assess','review'].includes(j.kind)&&['queued','running'].includes(j.status)&&(()=>{const p=parse(j.payload);return p.run_id===b.run_id||p.version_id===b.id})()))return {key:'running',label:'处理中',cls:''};
 return {key:'draft',label:'草稿',cls:''};
}
function runSourceCount(runId){const run=(state.runs||[]).find(r=>r.id===runId);return run?.source_count??runSourceIds(run).length}
function runConflicts(runId){
 const run=(state.runs||[]).find(r=>r.id===runId);let ids=[];
 try{ids=run?(Array.isArray(run.all_source_ids)?run.all_source_ids:JSON.parse(run.source_ids||'[]')):[]}catch{}
 const scoped=new Set(ids);
 return (state.conflicts||[]).filter(c=>{
  if(c.run_id)return c.run_id===runId;
  try{return (JSON.parse(c.data).source_ids||[]).some(id=>scoped.has(id))}catch{return false}
 });
}
function reportDescription(b){if(b.objective)return b.objective;const run=(state.runs||[]).find(r=>r.id===b.run_id);const req=run?parse(run.requirements):{};if(req.objective)return req.objective;const md=(b.markdown||b.excerpt||'').replace(/[#>*`\[\]]/g,' ').replace(/\s+/g,' ').trim();return md.slice(0,120)}
function renderReports(){reportBrowsing.render()}
const templateOutput=createTemplateOutput({api,notice,refresh,getState:()=>state,getCurrent:()=>current,savedVersion,
 openBrief:brief=>{const opened=openBrief(brief);if(opened)page('report');return opened},uploadPayload,getUploadLimits});
const templatesPage=templatesUI({api,notice,action,page,renderWorkflowChoices,templateSections,getState:()=>state,
 syncTemplateLanguage:template=>reportLanguageForm.syncTemplate(template),openTemplateOutput:(id,mode)=>templateOutput.open(id,mode)});
if($('new-report'))$('new-report').onclick=()=>page('setup');
sourcesPage.init();
if($('templates-upload'))$('templates-upload').onchange=e=>action(async()=>{const file=e.target.files[0];if(!file)return;await api('template-import',await uploadPayload(file,getUploadLimits()));e.target.value='';notice('模板已上传，BriefLoop 将准备章节和版式')});
reportBrowsing.init();
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

activity=activityCenter({api,getState:()=>state,page,openBrief,showSettings,settingsView,selectChat});

mountCompactReportControls();
// Research options follow the visible viewport, including zoom and short windows.
anchoredPopover({trigger:$('composer-params'),panel:$('composer-params-panel')});
