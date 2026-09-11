import {Editor,Extension} from '@tiptap/core';
import {Plugin,PluginKey} from 'prosemirror-state';
import {Decoration,DecorationSet} from 'prosemirror-view';
import StarterKit from '@tiptap/starter-kit';
import {TableKit} from '@tiptap/extension-table';
import Image from '@tiptap/extension-image';
import {Markdown} from '@tiptap/markdown';
import {TextStyle,Layout,ReportImage,Citation,editorDocument,savedDocument,readerHighlights} from './rich-document.js';
// Reader-appropriateness marks are editor decorations: they never enter the saved
// document, Word export or Markdown. Hover shows the violation and its requirement.
let highlightQuotes=[],showSuggestionMarks=false,highlightFindings=new Map(),highlightKinds=new Map();
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
function findingTitle(f){return [(f.requirement?'要求：'+f.requirement:''),f.description||'',(f.suggestion?'建议：'+f.suggestion:'')].filter(Boolean).join('\n')}
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
const $=id=>document.getElementById(id),esc=s=>String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c])),parse=s=>JSON.parse(s||'{}');
let followUpdates=true;
let token='',state,current,pendingRun=null,editor,dirty=false,saving=false,saveTimer,learnTimer,markdownMode=false,selected=new Set(),referenceSelected=new Set();
function notice(s,error=false){$('notice').textContent=s;$('notice').classList.toggle('error',error);$('notice').hidden=false;clearTimeout(notice.timer);notice.timer=setTimeout(()=>$('notice').hidden=true,error?12000:4500)}
async function api(path,data,retried=false){const r=await fetch('/api/'+path,data===undefined?{}:{method:'POST',headers:{'Content-Type':'application/json','X-BriefLoop-Token':token},body:JSON.stringify(data)});const b=await r.json();if(r.status===403&&data!==undefined&&!retried){token=(await api('session')).token;return api(path,data,true)}if(!r.ok)throw Error(b.error||'操作失败');return b}
function page(name){if(name!=='settings-dialog'&&$('custom-api-key'))$('custom-api-key').value='';for(const id of ['chat','report','setup','learning','settings-dialog','welcome'])$(id).hidden=id!==name;document.querySelectorAll('nav [data-page]').forEach(b=>b.classList.toggle('active',b.dataset.page===name));if(name==='learning')refreshCandidates();if(name==='setup'){moveSearchSettings('setup');applyPendingSetupFields()}else if($('tavily-key'))$('tavily-key').value=''}
document.querySelectorAll('[data-page]').forEach(b=>b.onclick=()=>page(b.dataset.page));
async function action(fn,message){try{await fn();if(message)notice(message);await refresh()}catch(e){notice(e.message,true)}}
async function refresh(first=false){try{const next=await api('state');$('connection').textContent='本地已连接';const signature=JSON.stringify(next);state=next;renderWordExports();if($('release-dialog')?.open)refreshReleaseState().catch(e=>notice(e.message,true));if(first||signature!==refresh.signature){refresh.signature=signature;render(first)}await refreshProgress();if(first||!$('learning').hidden)await refreshCandidates();if(first||!$('report').hidden)await refreshReportBudget()}catch(e){$('connection').textContent='连接中断';if(first)notice(e.message,true)}}
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
const fromEditor=md=>mapFigureImages(md.replace(/\[([^\]]+)\]\(#source-(src_[a-zA-Z0-9]+)\)/g,(_,label,id)=>`[@${id}]`),false);
// END_FIGURE_EDITOR_MAPPING
function updateDownloads(brief){
 const query='version='+encodeURIComponent(brief.id);$('download').href='/api/download?'+query;$('download-docx').href='/api/download?format=docx&'+query;
 const bundle=$('download-bundle');if(bundle){bundle.href='/api/download?format=bundle&'+query;bundle.hidden=!/briefloop-figure:[A-Za-z0-9_-]+/.test(brief.markdown)}
}

const statuses={queued:'等待运行',running:'正在运行',complete:'已完成',failed:'未完成',interrupted:'已中断',cancelled:'已停止'};
const DISCUSS_INSTRUCTION='（讨论模式：现在不要生成报告，也不要启动生成任务。）请先和我逐条确认这份简报的需求：目的与要回答的问题、读者、时间范围、必答问题、人工填写章节、篇幅与格式偏好。确认清楚后，在回复的最后单独给出一个 ```briefloop-requirements 代码块，内容是 JSON：{"title":"","objective":"","audience":"","period":"","key_questions":[],"manual_sections":[],"writing_preferences":[],"report_profile":"brief","writing_mode":"internal_report","target_words":1500,"max_words":2000}。只讨论和确认，不写文件、不生成报告。';
function applyRequirements(text){
 let data;try{data=JSON.parse(text)}catch(e){notice('要求清单无法解析：'+e.message,true);return}
 const form=$('requirements');if(!form)return;
 const set=(name,value)=>{const el=form.elements[name];if(el&&value!=null){el.value=value;el.dispatchEvent(new Event('change',{bubbles:true}))}};
 set('title',data.title);set('objective',data.objective);set('audience',data.audience);set('period',data.period);
 if(Array.isArray(data.key_questions))set('key_questions_text',data.key_questions.join('\n'));
 if(Array.isArray(data.manual_sections))set('manual_sections_text',data.manual_sections.join('\n'));
 if(data.report_profile)set('report_profile',data.report_profile);
 if(data.writing_mode)set('writing_mode',data.writing_mode);
 if(data.target_words)set('target_words',data.target_words);
 if(data.max_words)set('max_words',data.max_words);
 if(Array.isArray(data.writing_preferences)&&data.writing_preferences.length){const box=form.elements.objective;if(box&&!String(box.value).includes(data.writing_preferences[0]))box.value=String(box.value||'')+'\n写作偏好：'+data.writing_preferences.join('；')}
 page('setup');notice('已填入材料与需求，请检查后生成');
}
const TASK_LABELS={generate:'生成简报',assess:'重新评分',review:'独立审阅',revise:'按审阅修订',learn:'WikiSkill 学习',export_docx:'生成工作稿 Word',release:'制作正式 Word',audit_bundle:'制作审计包',source_refresh:'复查来源',prepare_template:'准备模板'};
// Generic message actions: every entry renders as a small icon button under the message.
const MESSAGE_ACTIONS=[
 {id:'copy',label:'复制回复',run:copyMessage,icon:'<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"><rect x="9" y="9" width="11" height="11" rx="2"/><path d="M5 15V6a2 2 0 0 1 2-2h9"/></svg>'},
];
async function copyMessage(message){try{await navigator.clipboard.writeText(message.text||'')}catch(e){notice('复制失败：'+e.message,true);return}notice('已复制消息文本')}
function messageActionsHTML(){return '<div class="message-actions-row">'+MESSAGE_ACTIONS.map(a=>`<button type="button" class="message-action" data-action="${a.id}" data-tip="${esc(a.label)}" aria-label="${esc(a.label)}">${a.icon}</button>`).join('')+'</div>'}
function bindMessageActions(node,message){node.querySelectorAll('.message-action').forEach(b=>{const action=MESSAGE_ACTIONS.find(a=>a.id===b.dataset.action);if(action)b.onclick=()=>Promise.resolve(action.run(message)).catch(e=>notice(e.message,true))})}
function taskFor(id){return state.jobs.find(j=>j.id===id)}
function openTask(job){
 if(!job)return;
 const payload=parse(job.payload);
 const brief=state.briefs.find(b=>b.id===payload.version_id)||state.briefs.find(b=>b.run_id===payload.run_id);
 if(brief)openBrief(brief,{follow:true});
 page('report');
}
function renderTasks(){
 const box=$('task-list');if(!box)return;
 const open=['queued','running','failed','interrupted','cancelled'];
 const tasks=state.jobs.filter(j=>TASK_LABELS[j.kind]&&open.includes(j.status)).slice(0,15);
 box.innerHTML=tasks.length?tasks.map(j=>{
  const running=['queued','running'].includes(j.status);
  const dot=['failed','interrupted','cancelled'].includes(j.status)?'error':running?'running':'';
  const milestone=j.progress?` · 第 ${j.progress.round}/${j.progress.k} 轮`:'';
  return `<div class="task-item"><button type="button" class="task-main" data-task-open="${j.id}" title="打开任务"><i class="task-dot ${dot}"></i><span class="task-text"><strong>${TASK_LABELS[j.kind]}</strong><small>${esc(statuses[j.status]||j.status)}${milestone}${j.error?' · '+esc(j.error):''}</small></span></button>${running?`<button type="button" class="task-icon" data-task-stop="${j.id}" title="停止">■</button>`:''}${['failed','interrupted','cancelled'].includes(j.status)?`<button type="button" class="task-icon" data-task-resume="${j.id}" title="恢复（沿用原模型）">↻</button>`:''}</div>`;
 }).join(''):'<p class="help">暂无未完成的任务</p>';
 box.querySelectorAll('[data-task-open]').forEach(b=>b.onclick=()=>openTask(taskFor(b.dataset.taskOpen)));
 box.querySelectorAll('[data-task-stop]').forEach(b=>b.onclick=()=>action(()=>api('stop',{job_id:b.dataset.taskStop})));
 box.querySelectorAll('[data-task-resume]').forEach(b=>b.onclick=()=>action(()=>api('resume',{job_id:b.dataset.taskResume})));
}
function renderArtifacts(){
 const box=$('artifact-list');if(!box||!state)return;
 const rows=[],seen=new Set();
 for(const brief of state.briefs){if(seen.has(brief.run_id))continue;seen.add(brief.run_id);rows.push({brief})}
 const fileKinds={export_docx:'工作稿 Word',release:'正式 Word',audit_bundle:'审计包'};
 for(const j of state.jobs){
  if(!fileKinds[j.kind]||j.status!=='complete')continue;
  const payload=parse(j.payload),result=parse(j.result);
  const url=j.kind==='release'?'/api/release-file?id='+encodeURIComponent(payload.release_id):j.kind==='audit_bundle'?'/api/audit-file?job='+encodeURIComponent(j.id):result.download_url;
  rows.push({label:fileKinds[j.kind],url});
 }
 const items=rows.slice(0,8);
 box.innerHTML=items.length?items.map(it=>it.brief
  ?`<button type="button" class="artifact-item" data-artifact-brief="${esc(it.brief.id)}"><span>${esc(parse(it.brief.detail).title||'简报草稿')}</span><small>打开稿件</small></button>`
  :`<a class="artifact-item" href="${esc(it.url||'#')}" download><span>${esc(it.label)}</span><small>下载</small></a>`).join(''):'<p class="help">暂无产物</p>';
 box.querySelectorAll('[data-artifact-brief]').forEach(b=>b.onclick=()=>{const brief=state.briefs.find(x=>x.id===b.dataset.artifactBrief);if(brief){openBrief(brief,{follow:false});page('report')}});
}
const WELCOME_PURPOSES=[
 {id:'internal',label:'内部简报',prompt:'/discuss 我要做一份面向管理层的内部简报，请先和我确认目的、读者、必答问题、篇幅与格式。',fields:{writing_mode:'internal_report',report_profile:'brief'}},
 {id:'public',label:'公开研究',prompt:'请围绕我的研究目标查找公开资料，写一份有依据的简报。',fields:{writing_mode:'general',report_profile:'brief',allow_web:true}},
 {id:'industry',label:'行业报告',prompt:'请做一份行业定期报告，先确认行业、目标组织、报告日期与覆盖期间。',fields:{writing_mode:'general',report_profile:'industry_periodic'}},
];
let welcomeIndex=0,welcomePurpose=null;
function welcomeAvailable(){return (runtimeCatalog||[]).filter(r=>r.available)}
function chooseWelcomeHost(id){const select=$('agent-backend');if(select&&select.value!==id){select.value=id;select.dispatchEvent(new Event('change'))}renderWelcome()}
function applyWelcomePurpose(id){const purpose=WELCOME_PURPOSES.find(p=>p.id===id);if(!purpose)return;welcomePurpose=id;$('chat-input').value=purpose.prompt;rememberDraft();if(purpose.fields)sessionStorage.setItem('briefloop-welcome-fields',JSON.stringify(purpose.fields));renderWelcome()}
function applyPendingSetupFields(){let fields=null;try{fields=JSON.parse(sessionStorage.getItem('briefloop-welcome-fields')||'null')}catch{}if(!fields)return;sessionStorage.removeItem('briefloop-welcome-fields');const form=$('requirements');if(!form)return;for(const [name,value] of Object.entries(fields)){const el=form.elements[name];if(!el)continue;if(el.type==='checkbox')el.checked=!!value;else el.value=value;el.dispatchEvent(new Event('change',{bubbles:true}))}}
function renderWelcome(){
 const box=$('welcome-runtimes');if(!box)return;
 const avail=welcomeAvailable(),chosen=state?.settings?.agent_backend||'codex';
 box.innerHTML=avail.length?avail.map(r=>`<button type="button" class="welcome-host ${r.id===chosen?'selected':''}" data-welcome-host="${esc(r.id)}"><strong>${esc(r.name)}</strong><small>${esc(r.version||'版本未确认')}${r.id===chosen?' · 当前选择':''}</small></button>`).join(''):'<p class="help">未检测到可用宿主。请先安装并登录一个 CLI，或在设置里配置 API 提供商。</p>';
 box.querySelectorAll('[data-welcome-host]').forEach(b=>b.onclick=()=>{welcomeIndex=Math.max(0,avail.findIndex(r=>r.id===b.dataset.welcomeHost));chooseWelcomeHost(b.dataset.welcomeHost)});
 const model=state?.settings?.model;
 $('welcome-choice').textContent=`将使用：${runtimeName(chosen)} · ${model?friendlyModel(model):'待选择模型'}`;
 $('welcome-start').disabled=!model;
 const pbox=$('welcome-purposes');
 pbox.innerHTML='<span class="help">这次想做什么？可选。</span>'+WELCOME_PURPOSES.map(p=>`<button type="button" class="welcome-purpose ${welcomePurpose===p.id?'selected':''}" data-welcome-purpose="${p.id}">${esc(p.label)}</button>`).join('');
 pbox.querySelectorAll('[data-welcome-purpose]').forEach(b=>b.onclick=()=>applyWelcomePurpose(b.dataset.welcomePurpose));
}
$('welcome-model').onclick=()=>openModelPicker('model-select');
$('welcome-cycle').onclick=()=>{const avail=welcomeAvailable();if(avail.length<2)return;welcomeIndex=(welcomeIndex+1)%avail.length;chooseWelcomeHost(avail[welcomeIndex].id)};
$('welcome-start').onclick=()=>{if(!state?.settings?.model){notice('请先选择模型',true);return}page('chat');$('chat-input').focus();rememberDraft()};
$('welcome-skip').onclick=()=>page('chat');
function render(first){
 renderTemplates(first);
 if($('review-open'))$('review-open').textContent='审阅与需处理'+(state.conflicts?.length?' · '+state.conflicts.length+' 项来源分歧':'');
 if(first){$('timeout-minutes').value=state.settings.timeout_minutes;$('company-mode').value=state.settings.company_context_enabled==null?'ask':state.settings.company_context_enabled?'on':'off';$('auto-revision').checked=state.settings.auto_revision!==false;state.sources.forEach(s=>selected.add(s.id));$('rounds').value=state.settings.k;$('auto-learn').checked=state.settings.auto_learn;$('model-select').value=state.settings.model_selection_required?'':state.settings.model||'gpt-5.6-luna';assignEffort('effort-select',effortValue(state.settings,'reasoning_effort'));$('model-provider').value=state.settings.model_provider||'';$('agent-backend').value=state.settings.agent_backend||'codex';$('model-variant').value=state.settings.model_variant||'';updateModelLabel();renderRoleModels();renderBackend();$('search-provider').value=state.settings.search_provider==='tavily'?'tavily':'native';renderSearchProvider();refreshRuntimeDiscovery();if(state.requirements)for(const [k,v] of Object.entries(state.requirements)){const e=$('requirements').elements[k];if(e)e.type==='checkbox'?e.checked=v:e.value=v}$('requirements').elements.key_questions_text.value=(state.requirements?.key_questions||[]).join('\n');$('requirements').elements.manual_sections_text.value=(state.requirements?.manual_sections||[]).join('\n');initializeLengthInputs(state.requirements||{});initializeResearchBudget(state.requirements||{});initializeReportProfile(state.requirements||{})}
 $('source-count').textContent=state.sources.length+' 份';$('source-list').innerHTML=state.sources.map(s=>`<div class="source-item"><input type="checkbox" data-check="${s.id}" ${selected.has(s.id)?'checked':''} aria-label="选择 ${esc(s.name)}"><button data-source="${s.id}">${esc(s.name)}</button><span class="tag ${s.status==='failed'?'error':''}">${s.status==='failed'?'读取失败':s.needs_visual?'需视觉读取':'可读取'}</span>${s.status==='failed'?`<button data-retry-source="${s.id}">重试</button>`:''}</div>`).join('');
 document.querySelectorAll('[data-retry-source]').forEach(b=>b.onclick=()=>action(async()=>{const s=await api('retry-source',{source_id:b.dataset.retrySource});selected.delete(b.dataset.retrySource);selected.add(s.id);notice(s.status==='ready'?'来源已重新读取':s.error,s.status!=='ready')}));
 document.querySelectorAll('[data-check]').forEach(b=>b.onchange=()=>{if(b.checked){selected.add(b.dataset.check);referenceSelected.delete(b.dataset.check);renderReferenceSources()}else selected.delete(b.dataset.check)});renderReferenceSources();
 const visible=[];
 for(const runId of [...new Set(state.briefs.map(b=>b.run_id))]){
  const versions=state.briefs.filter(b=>b.run_id===runId),latest=versions[0],original=[...versions].reverse().find(b=>b.author==='agent');
  if(latest)visible.push({brief:latest,label:latest.author==='user'?'当前编辑稿':latest.parent_id?'图表修订稿':'生成原稿'});
  if(original&&original.id!==latest?.id)visible.push({brief:original,label:'生成原稿'});
 }
 if(current&&!visible.some(v=>v.brief.id===current.id))visible.push({brief:current,label:'正在查看历史快照'});
 $('version-select').innerHTML=visible.map(({brief:b,label})=>`<option value="${b.id}">${esc(parse(b.detail).title||'简报')} · ${label}</option>`).join('');
 if($('version-history'))$('version-history').textContent='编辑历史'+(current?'（'+state.briefs.filter(b=>b.run_id===current.run_id&&b.author==='user').length+'）':'');

 tryOpenPending();if(!current&&state.briefs.length)openBrief(state.briefs[0],{follow:true});if(current&&followUpdates&&!dirty&&!saving){const latest=state.briefs.find(b=>b.run_id===current.run_id);if(latest?.parent_id===current.id&&latest.author==='agent')openBrief(latest,{follow:true})}if(current){$('version-select').value=current.id;assessment();citations();renderBriefLength()}
 $('empty').hidden=!!current||state.jobs.length>0;$('document-area').hidden=!current;
 $('jobs').innerHTML=state.jobs.filter(j=>j.status!=='dismissed').map(j=>`<div class="job"><span class="tag ${j.status==='failed'?'error':''}">${statuses[j.status]}</span><div class="job-main">${{generate:'生成简报',assess:'重新评分',review:'独立审阅',revise:'按审阅修订',learn:'WikiSkill 学习',export_docx:'生成工作稿 Word',release:'制作正式 Word',audit_bundle:'制作审计包',source_refresh:'复查来源',prepare_template:'准备模板'}[j.kind]}<small>${['export_docx','release','audit_bundle'].includes(j.kind)?'本地脚本':j.kind==='source_refresh'?'来源工具':parse(j.payload).runtime?esc(modelLabel(parse(j.payload).runtime)):'旧任务：沿用当时本机配置'} · ${j.progress?`第 ${j.progress.round}/${j.progress.k} 轮 · ${{maintainer:'整理经验',proposer:'提出候选',validation:'验证候选'}[j.progress.phase]||j.progress.phase} · `:''}${esc(j.error||(j.kind==='source_refresh'?sourceRefreshOutcome(parse(j.result).outcome):'')||new Date(j.created).toLocaleString())}</small></div>${j.kind==='learn'?`<button data-details="${j.id}">查看比较</button>`:''}${['queued','running'].includes(j.status)?`<button data-stop="${j.id}">停止</button>`:''}${['failed','interrupted','cancelled'].includes(j.status)?`<button data-resume="${j.id}">恢复</button>`:''}</div>`).join('');
 document.querySelectorAll('[data-stop]').forEach(b=>b.onclick=()=>action(()=>api('stop',{job_id:b.dataset.stop})));document.querySelectorAll('[data-resume]').forEach(b=>b.onclick=()=>action(()=>api('resume',{job_id:b.dataset.resume})));renderTasks();renderArtifacts();if($('welcome')&&!$('welcome').hidden)renderWelcome();
 document.querySelectorAll('[data-details]').forEach(b=>b.onclick=()=>action(async()=>{const d=await api('learning-details?job='+b.dataset.details);$('source-title').textContent='技能比较与依据';$('source-original').hidden=true;$('source-provenance').hidden=true;$('source-link').textContent='';$('source-body').textContent=d.rounds.length?d.rounds.map((r,i)=>`第 ${i+1} 轮\n${r.result?.reason||'比较尚未完成'}\n${(r.result?.pairs||[]).map(p=>({better:'候选更好',tie:'差不多，保留原技能',worse:'原稿更好'}[p.verdict])+': '+p.reason).join('\n')}\n\n`+r.cases.map(c=>`任务：${c.requirements.title}\n\n旧版\n${gradeSummary(c.baseline.assessment)}\n${c.baseline.reader_markdown||c.baseline.markdown}\n\n候选\n${gradeSummary(c.candidate.assessment)}\n${c.candidate.reader_markdown||c.candidate.markdown}`).join('\n\n')).join('\n\n'):d.job.error||'比较尚未开始；先整理 Wiki 和提出候选。';$('source-dialog').showModal()}));
 $('skills').innerHTML=`<div class="skill">${state.active_skill?'当前启用 '+esc(state.active_skill):'当前使用基础任务提示词'}${state.active_skill?'<button data-rollback="">回到基础版本</button>':''}</div>`+state.skills.map(s=>`<div class="skill"><strong>${esc(s.id)}</strong><p>${esc(s.reason)}</p>${s.id===state.active_skill?'<span class="tag">正在使用</span>':`<button data-rollback="${s.id}" class="outline">使用这个版本</button>`}</div>`).join('');document.querySelectorAll('[data-rollback]').forEach(b=>b.onclick=()=>action(()=>api('rollback',{skill_id:b.dataset.rollback||null}),'下一轮将使用所选技能'));
 if(state.wiki!==render.wiki){render.wiki=state.wiki;if(state.wiki)api('render',{markdown:state.wiki}).then(r=>$('wiki').innerHTML=r.html);else $('wiki').innerHTML='<h2>还没有学习经验</h2><p class="muted">生成简报后直接改稿，或留下评论。Maintainer 会在这里整理观察、方法与适用条件。</p>'}bindSources();
}
function tryOpenPending(){
 if(!pendingRun||dirty||saving)return false;
 const incoming=state?.briefs.find(b=>b.run_id===pendingRun);
 if(incoming&&openBrief(incoming,{follow:true})){pendingRun=null;return true}
 return false;
}
function openBrief(b,{follow=false}={}){if(dirty||saving){notice('请先保存当前修改，再切换版本',true);return false}followUpdates=follow;current=b;$('report-title').textContent=parse(b.detail).title||'简报';updateDownloads(b);if(editor)editor.destroy();highlightQuotes=[];editor=new Editor({element:$('editor'),editable:state.briefs.find(x=>x.run_id===b.run_id)?.id===b.id,extensions:[StarterKit.configure({link:{openOnClick:false}}),TableKit,ReportImage.configure({HTMLAttributes:{class:'briefloop-figure'},allowBase64:false}),TextStyle,Layout,Citation,Markdown,MustFixHighlight],content:b.editor_document?editorDocument(parse(b.editor_document),b.id):toEditor(b.markdown),...(b.editor_document?{}:{contentType:'markdown'}),onUpdate:changed,onSelectionUpdate:updateFormattingTools});$('markdown-source').value=b.markdown;const historical=state.briefs.find(x=>x.run_id===b.run_id)?.id!==b.id;$('markdown-source').readOnly=historical;$('toolbar').querySelectorAll('button,input,select').forEach(x=>x.disabled=historical);$('save-state').textContent=historical?'历史记录（只读）':b.author==='user'?'当前编辑稿已自动保存':'原稿已保存';$('version-select').value=b.id;assessment();citations();renderBriefLength();return true}
async function renderDeliveryChecks(){
 const ticket=(renderDeliveryChecks.ticket||0)+1;renderDeliveryChecks.ticket=ticket;
 if(!current||!$('assessment'))return;const vid=current.id;
 const old=$('assessment').querySelector('.delivery-checks');if(old)old.remove();
 const box=document.createElement('div');box.className='delivery-checks';
 box.textContent=dirty?'有未保存修改；下列检查仅针对已保存版本。':'正在检查已保存版本…';
 $('assessment').prepend(box);
 let c;try{c=await api('version-checks?version='+encodeURIComponent(vid))}catch(e){if(box.isConnected)box.textContent='检查暂不可用，请稍后重试；未判定通过。';return}
 if(!current||current.id!==vid||ticket!==renderDeliveryChecks.ticket||!box.isConnected)return;
 const parts=[];
 if(dirty)parts.push('<span class="tag">有未保存修改；仅检查已保存版本</span>');
 parts.push(c.broken_refs.length?`<span class="tag error">断链引用 ${c.broken_refs.length} 处：${c.broken_refs.map(esc).join('、')}</span>`:'<span class="tag">正文引用可定位；支持关系仍需评价</span>');
 const n=c.numbers;
 parts.push(`<span class="tag">${n.status==='not_checked'?'未做数值核对':n.status==='partial'?'部分绑定已检查':'已检查提交的绑定'}：提交 ${n.total} 项，已检查 ${n.checked} 项，匹配 ${n.matched} 项</span>`);
 if(n.unmatched.length)parts.push(`<span class="tag error">绑定数值不一致 ${n.unmatched.length} 项：${n.unmatched.map(r=>esc(r.label||r.expected)).join('、')}</span>`);
 if(n.skipped.length)parts.push(`<span class="tag">未检查 ${n.skipped.length} 项：${n.skipped.map(r=>esc((r.label||'未命名')+'：'+r.reason)).join('；')}</span>`);
 if(c.export.escaped_bold)parts.push('<span class="tag error">存在转义加粗，请检查排版</span>');
 if(c.export.figure_error)parts.push('<span class="tag error">图表资源不可用：'+esc(c.export.figure_error)+'</span>');
 else if(c.export.figure_markers.length)parts.push('<span class="tag">含图表：独立交付请下载 Word 或含图片的 Markdown 包</span>');
 if(c.assessment_overall)parts.push(`<span class="tag">模型评分：${esc(c.assessment_overall)}</span>`);
 box.innerHTML='<strong>已保存版本检查</strong> '+parts.join(' ')+'<p class="help">数值检查仅覆盖已提交并成功定位的绑定，不代表正文数字已全部核验；事实含义、研究覆盖与交付质量仍需评价。</p>';
}

function changed(){followUpdates=false;dirty=true;renderDeliveryChecks.ticket=(renderDeliveryChecks.ticket||0)+1;const check=$('assessment').querySelector('.delivery-checks');if(check)check.textContent='有未保存修改；保存后重新检查。';renderBriefLength();$('save-state').textContent='有未保存修改';clearTimeout(saveTimer);saveTimer=setTimeout(save,1400)}
$('version-select').onchange=e=>openBrief(state.briefs.find(b=>b.id===e.target.value));
let savePromise=null,lastSaveError=null;
function save(){
 if(savePromise)return savePromise;
 if(!dirty||!current)return Promise.resolve();
 saving=true;lastSaveError=null;$('save-state').textContent='保存中…';
 const text=markdownMode?$('markdown-source').value:JSON.stringify(savedDocument(editor.getJSON()));
 const base=current.id;
 savePromise=(async()=>{try{
  current=await api('save',{base_version:base,markdown:markdownMode?text:'',editor_document:markdownMode?null:JSON.parse(text)});
  dirty=(markdownMode?$('markdown-source').value:JSON.stringify(savedDocument(editor.getJSON())))!==text;
  $('save-state').textContent=dirty?'有新的修改':'已保存';updateDownloads(current);await refresh();
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
for(const id of ['download','download-docx','download-bundle']){
 const link=$(id);if(!link)continue;
 link.onclick=async e=>{e.preventDefault();try{const version=await savedVersion();if(id==='download-docx'){await api('export',{version_id:version});notice('Word 已排队制作');await refresh();return}const format=id==='download-docx'?'docx':id==='download-bundle'?'bundle':null;window.location.assign('/api/download?version='+encodeURIComponent(version)+(format?'&format='+format:''))}catch(e){notice('下载未开始：'+e.message,true)}};
}

function scheduleLearning(){ /* Worker consumes the durable feedback after inactivity. */ }
function assessment(){if(!current)return;queueMicrotask(renderDeliveryChecks);const r=state.assessments.find(a=>a.version_id===current.id);if(!r){highlightQuotes=[];applyHighlights();$('assessment').innerHTML='<p class="muted">尚未评分</p><p class="help">你可以先阅读和修改。评分针对这个版本独立运行。</p>';return}const d=parse(r.data),findings=d.findings||[];highlightQuotes=readerHighlights(findings,{expression:d.expression,showSuggestions:showSuggestionMarks}).map(item=>({quote:item.quote,kind:item.kind,title:findingTitle(item.finding),findingIndex:findings.indexOf(item.finding)}));highlightFindings=new Map(findings.map((f,i)=>[String(i),f]));highlightKinds=new Map(highlightQuotes.map(q=>[String(q.findingIndex),q.kind]));applyHighlights();const toggle=`<label class="finding-toggle help"><input type="checkbox" id="show-suggestions" ${showSuggestionMarks?'checked':''}> 显示建议标记（黄）；必须修正句始终标红</label>`;$('assessment').innerHTML=`<div class="judgment">${esc(d.overall)}</div><p>${esc(d.summary)}</p><div class="grades">${[['evidence','证据与准确性'],['coverage','覆盖与取舍'],['analysis','分析有效性'],['expression','表达与可用性']].map(([k,l])=>`<div class="grade"><span>${l}</span><strong>${d[k]??'—'}</strong><small>${d[k]?' / 5':''}</small></div>`).join('')}</div><p class="help">等级是本轮要求完成程度，评分可有不同意见。</p>${(typeof d.expression==='number'&&d.expression<=2&&d.overall==='达到要求')?'<p class="help">表达分偏低但总体仍判为「达到要求」，两者不一致；系统会按此安排一次修订，实际以正文和独立审阅为准。</p>':''}${findings.length?toggle:''}${findings.map((f,i)=>`<details class="finding" data-finding="${i}"><summary>${f.severity==='major'?'●':'○'} ${esc(f.description)}</summary>${f.report_quote?`<blockquote>${esc(f.report_quote)}</blockquote>`:''}<p>${esc(f.requirement)}</p><p>${esc(f.evidence)}</p>${f.source_id?`<button data-source="${esc(f.source_id)}">查看来源 · ${esc(f.locator)}</button>`:''}<p>${esc(f.suggestion)}</p></details>`).join('')}`;bindSources();const toggleEl=$('show-suggestions');if(toggleEl)toggleEl.onchange=()=>{showSuggestionMarks=toggleEl.checked;assessment()}}
function citations(){const refs=(parse(current.detail).citations||[]).filter(r=>toEditor(current.markdown).includes('#source-'+r.source_id));$('citations').innerHTML=refs.length?'引用来源 '+refs.map(r=>`<button data-source="${esc(r.source_id)}">${esc(state.sources.find(s=>s.id===r.source_id)?.name||r.source_id)} · ${esc(r.locator)}</button>`).join(''):'尚无引用记录';bindSources()}
function bindSources(){document.querySelectorAll('[data-source]').forEach(b=>b.onclick=()=>action(async()=>{const r=await api('source?id='+b.dataset.source);showSource(r)}))}
$('close-source').onclick=()=>$('source-dialog').close();
$('requirements').onsubmit=e=>{e.preventDefault();action(async()=>{const f=new FormData(e.target),req=Object.fromEntries(f.entries());if(req.writing_mode==='internal_report'&&state.settings.company_context_enabled==null){$('company-choice-dialog').showModal();return}req.allow_web=f.has('allow_web');req.target_words=Number(req.target_words);req.max_words=Number(req.max_words);req.research_budget=readResearchBudget();req.reference_source_ids=req.report_profile==='industry_periodic'?[...referenceSelected]:[];req.template_id=req.template_id||null;req.sections=readTemplateSections();req.key_questions=(req.key_questions_text||'').split('\n').map(x=>x.trim()).filter(Boolean);delete req.key_questions_text;req.manual_sections=(req.manual_sections_text||'').split('\n').map(x=>x.trim()).filter(Boolean);for(const title of req.manual_sections){const found=req.sections.find(s=>s.title===title);if(found){found.mode='manual';found.placeholder='待填充'}}delete req.manual_sections_text;req.raw_input=req.objective;delete req.runtime_model;delete req.runtime_effort;await saveModel();if(current)await savedVersion();const job=await api('generate',{requirements:req,session_id:chat.id||undefined,source_ids:[...selected].filter(id=>!req.reference_source_ids.includes(id))});pendingRun=parse(job.payload).run_id;page('report');notice('任务已排队，后台会生成简报')})};
$('upload').onchange=e=>action(async()=>{for(const f of e.target.files){const buf=new Uint8Array(await f.arrayBuffer());let b='';for(let i=0;i<buf.length;i+=8192)b+=String.fromCharCode(...buf.subarray(i,i+8192));const s=await api('upload',{name:f.name,data:btoa(b)});selected.add(s.id)}e.target.value=''},'来源已保存');
$('add-url').onclick=()=>action(async()=>{const s=await api('source-url',{url:$('source-url').value});selected.add(s.id);$('source-url').value='';notice(s.status==='ready'?'网页已读取':'来源已保存，但读取失败：'+s.error,s.status!=='ready')});
$('rescore').onclick=()=>action(async()=>{await savedVersion();await api('assess',{version_id:current.id,session_id:chat.id||undefined})},'已提交评分');
$('comment-submit').onclick=()=>action(async()=>{const text=$('comment').value;const version=await savedVersion();await api('comment',{version_id:version,text});if($('comment').value===text)$('comment').value='';scheduleLearning()},'反馈已保存');
$('learn-now').onclick=()=>action(async()=>{await savedVersion();clearTimeout(learnTimer);const result=await api('learn',{});notice(result.message||'已提交反馈学习')});
async function setK(v){const k=Math.max(1,Math.min(20,Number(v)||1));$('rounds').value=k;await action(()=>api('settings',{k}),'轮数已保存，下一批生效')}
$('rounds').onchange=e=>setK(e.target.value);$('k-minus').onclick=()=>setK(Number($('rounds').value)-1);$('k-plus').onclick=()=>setK(Number($('rounds').value)+1);$('auto-learn').onchange=e=>action(()=>api('settings',{auto_learn:e.target.checked}),'学习偏好已保存');
$('toolbar').querySelectorAll('button').forEach(b=>b.onclick=()=>{if(!editor)return;const c=editor.chain().focus();({bold:()=>c.toggleBold().run(),italic:()=>c.toggleItalic().run(),heading:()=>c.toggleHeading({level:2}).run(),bullet:()=>c.toggleBulletList().run(),table:()=>c.insertTable({rows:3,cols:3,withHeaderRow:true}).run(),undo:()=>c.undo().run(),redo:()=>c.redo().run(),addRow:()=>c.addRowAfter().run(),deleteRow:()=>c.deleteRow().run(),addColumn:()=>c.addColumnAfter().run(),deleteColumn:()=>c.deleteColumn().run(),mergeCells:()=>c.mergeCells().run(),splitCell:()=>c.splitCell().run(),imageCaption:()=>{const a=editor.getAttributes('image');if(!a.src)return;const caption=window.prompt('图注',a.caption||'');if(caption!==null)c.updateAttributes('image',{caption}).run()},imageWidth:()=>{const a=editor.getAttributes('image');if(!a.src)return;const raw=window.prompt('图像宽度（像素）',String(a.width||480));if(raw===null)return;const width=Number(raw);if(Number.isInteger(width)&&width>0&&width<=10000)c.updateAttributes('image',{width,height:null}).run();else notice('请输入有效宽度',true)}})[b.dataset.command]()});
$('markdown-toggle').onclick=()=>$('markdown-import').click();
$('markdown-import').onchange=e=>action(async()=>{const file=e.target.files[0];if(!file)return;await savedVersion();editor.commands.setContent(toEditor(await file.text()),{contentType:'markdown',emitUpdate:true});await savedVersion();e.target.value='';notice('已导入为新的富文档版本')});
$('markdown-source').oninput=changed;window.addEventListener('beforeunload',e=>{if(dirty){e.preventDefault();e.returnValue=''}});
(async()=>{try{token=(await api('session')).token;await refresh(true);await initChat();refreshWorkspaces().catch(()=>{});setInterval(()=>refresh(),3000)}catch(e){notice(e.message,true)}})();

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
let progressRequest=false;
async function refreshProgress(){
 if(progressRequest||!state)return;
 const relevant=effectiveReportJobs();
 const job=relevant.find(j=>j.status==='running')||relevant.find(j=>j.status==='queued');
 if(!job){
 const paused=relevant.find(j=>['cancelled','interrupted','failed'].includes(j.status));
 $('run-progress').hidden=!paused;
 if(paused){const service=await api('runtime');$('run-progress').innerHTML=`<div class="section-title"><h2>${paused.status==='failed'?'任务未完成':'任务已暂停'}</h2><button id="paused-resume" class="primary">恢复任务（沿用原模型）</button></div><p>当前没有继续执行这个任务。已有来源和产物保留。</p><p class="help">本地服务 PID ${service.server_pid||'—'}（页面与任务管理） · ${service.pid?'模型进程 PID '+service.pid:'本工作区没有模型进程'}</p><p class="help">${esc(paused.error||'')}</p><p class="help">恢复会沿用该任务原来的模型与后端；要改用当前设置，请新建任务。</p><button id="paused-settings" class="outline">修改模型与要求</button>`;$('paused-settings').onclick=()=>page('setup');$('paused-resume').onclick=()=>action(()=>api('resume',{job_id:paused.id}),'已按页面显示的模型提交')}
 return
}
 progressRequest=true;
 try{
  if(job.kind==='source_refresh'){
   const payload=parse(job.payload),source=state.sources.find(s=>s.id===payload.source_id);
   $('run-progress').hidden=false;$('run-progress').innerHTML=`<div class="section-title"><h2>${job.status==='queued'?'来源复查已排队':'正在复查来源'}</h2><button class="outline" id="progress-stop">停止任务</button></div><p>${esc(source?.name||'当前来源')}</p><p class="help">按本轮联网范围和预算获取新快照；已有来源与报告保留。复查完成后，来源变化仍需判断和复核。</p>`;
   $('progress-stop').onclick=()=>action(()=>api('stop',{job_id:job.id}));return;
  }
  const [events,live]=await Promise.all([api('events?job='+job.id),api('runtime')]);
  const last=[...events].reverse().find(e=>e.kind==='runtime_progress');
  const p=last?parse(last.data):{};const started=[...events].reverse().find(e=>e.kind==='runtime_started');const start=started?parse(started.data):{};
  const run=state.runs.find(r=>r.id===parse(job.payload).run_id);
  const req=run?parse(run.requirements):{};
  const agents=p.agents||[];
  const elapsed=Math.max(0,Math.floor((Date.now()-new Date(job.created))/1000));
  const mins=Math.floor(elapsed/60),secs=elapsed%60;
  const running=live.worker_alive&&live.pid&&live.returncode===null;
  const labels={running:'进行中',pending_init:'启动中',completed:'已完成',done:'已完成',closed:'已结束',failed:'失败',errored:'失败'};
  $('run-progress').hidden=false;
  $('run-progress').innerHTML=`<div class="section-title"><div><p class="eyebrow">${job.kind==='learn'?'技能学习':'简报生成'} · ${running?'后台正在运行':'等待后台执行'}</p><h2>${esc(p.stage||(job.status==='queued'?'任务已排队':'正在启动 BriefLoop'))}</h2></div><button class="outline" id="progress-stop">停止任务</button></div><p><strong>${esc(modelLabel(parse(job.payload).runtime||start.runtime))}</strong> · 模型进程 PID ${live.pid||'—'}${live.server_pid?' · 本地服务 PID '+live.server_pid:''}</p><p class="help">已用 ${mins} 分 ${secs} 秒 · 单次执行上限 ${state.settings.timeout_minutes} 分钟 <button id="progress-timeout" class="subtle-button">调整时限</button>${run?` · ${JSON.parse(run.source_ids).length} 份初始来源 · ${req.allow_web?'允许联网':'仅本地来源'}`:''}</p>${p.message?`<p class="progress-message">${esc(p.message)}</p>`:''}<div class="agent-progress">${agents.map(a=>`<div><strong>${esc(a.role)}</strong><span>${labels[a.status]||esc(a.status)}</span>${a.task?`<p>${esc(a.task)}</p>`:''}</div>`).join('')}</div><p class="help">${p.draft_ready?'正文已可查看，评分独立完成。':'正文保存后会自动显示；等待子 agent 时可能暂时没有新消息。'}${p.last_activity?' 最近活动：'+new Date(p.last_activity).toLocaleTimeString():''}</p>`;
  $('progress-timeout').onclick=showSettings;
  $('progress-stop').onclick=()=>action(()=>api('stop',{job_id:job.id}));
 }catch(e){$('run-progress').hidden=false;$('run-progress').textContent='进度连接暂时中断，任务没有重新提交。'+e.message}
 finally{progressRequest=false}
}

function friendlyModel(model){return ({'default':'宿主默认模型','gpt-5.6-luna':'Luna','gpt-5.6-terra':'Terra','gpt-5.6-sol':'Sol','gpt-6-astra':'Astra'}[model]||model)}
function effortValue(runtime,key){return Object.prototype.hasOwnProperty.call(runtime,key)?(runtime[key]||'none'):'high'}
function assignEffort(id,value){const input=$(id);if(![...input.options].some(o=>o.value===value))input.add(new Option(value,value));input.value=value}
function activeChatRuntime(){return chat.messages.find(m=>m.role==='user'&&m.turn_id===chat.session?.turn_id&&m.runtime)?.runtime||chat.session?.runtime||{}}
function modelLabel(cfg){if(!cfg?.model)return '未指定模型';const prefix=cfg.agent_backend?runtimeName(cfg.agent_backend)+' · ':'';if(cfg.agent_backend&&!['codex','opencode'].includes(cfg.agent_backend))return prefix+friendlyModel(cfg.model);if(cfg.model_variant!=null||cfg.agent_backend==='opencode'){const variant=cfg.model_variant||'模型默认';return prefix+friendlyModel(cfg.model)+' / '+variant}const effort=cfg.reasoning_effort,effortLabel=Object.prototype.hasOwnProperty.call(cfg,'reasoning_effort')?(!effort||effort==='none'?'模型默认':effort):'未记录';return prefix+friendlyModel(cfg.model)+' / '+effortLabel+(cfg.model_provider?' · '+cfg.model_provider:'')}
function backendValue(){return ($('agent-backend')&&$('agent-backend').value)||state.settings.agent_backend||'codex'}
function renderBackend(){const op=backendValue()==='opencode',codex=backendValue()==='codex';$('variant-field').hidden=!op;document.querySelector('.main-provider-field').style.display=codex?'':'none';$('effort-select').hidden=!codex;$('effort-select').closest('label').hidden=!codex;$('model-select').placeholder=op?'如 opencode-go/gpt-5.6-luna':'输入任意模型 ID';document.querySelectorAll('.role-variant-field').forEach(e=>e.hidden=!op);document.querySelectorAll('.role-provider-field').forEach(e=>e.style.display=codex?'':'none');document.querySelectorAll('.role-effort-select').forEach(e=>e.style.display=codex?'':'none');renderSearchProvider();updateModelLabel()}
function updateModelLabel(){const op=backendValue()==='opencode';const cfg=op?{model:$('model-select').value.trim(),model_variant:$('model-variant').value.trim()||null,agent_backend:'opencode'}:{model:$('model-select').value.trim(),reasoning_effort:$('effort-select').value,model_provider:$('model-provider').value.trim(),agent_backend:backendValue()};$('execution-choice').textContent='即将使用：'+modelLabel(cfg);if($('setup-model-summary'))$('setup-model-summary').textContent=modelLabel(cfg);$('generate-button').textContent='使用 '+modelLabel(cfg)+' 生成简报 →';$('model-select').title=cfg.model?friendlyModel(cfg.model)+' · '+cfg.model:'输入模型 ID'}
async function saveModel(){const model=$('model-select').value.trim();if(!model)throw Error('请输入模型 ID');const op=backendValue()==='opencode';if(op){if(!model.includes('/'))throw Error('Opencode 模型必须是 provider/model 形式');await api('settings',{agent_backend:'opencode',model_selection_required:false,model,model_variant:$('model-variant').value.trim()||null})}else if(backendValue()!=='codex')await api('settings',{agent_backend:backendValue(),model_selection_required:false,model,model_provider:null,model_variant:null});else await api('settings',{agent_backend:backendValue(),model_selection_required:false,model,reasoning_effort:$('effort-select').value,model_provider:$('model-provider').value.trim()||null});updateModelLabel();if($('welcome')&&!$('welcome').hidden)renderWelcome()}
let runtimeCatalog=[];
function runtimeName(id){return runtimeCatalog.find(r=>r.id===id)?.name||id}
function renderSettingsSessionNote(){
 const box=$('settings-session-note');if(!box)return;
 const session=chat.session,backend=session?.runtime?.backend,chosen=backendValue();
 if(!session){box.hidden=false;box.innerHTML='当前没有打开的会话。本页的模型与执行引擎设置用于新会话。';return}
 const model=session.runtime?.model;
 const head=`当前会话：<strong>${esc(runtimeName(backend))} · ${esc(model?friendlyModel(model):'宿主默认')}</strong>`;
 box.hidden=false;
 if(chosen&&chosen!==backend){
  box.innerHTML=head+`。本页把执行引擎设为 <strong>${esc(runtimeName(chosen))}</strong>，与当前会话不同；换引擎需要新开会话。 <button type="button" class="outline" id="settings-new-session">用以上设置开新会话</button>`;
  const button=$('settings-new-session');if(button)button.onclick=()=>newChat();
 }else{
  box.innerHTML=head+'。本页改动用于新会话；当前会话的模型可在对话里的模型选择器调整。';
 }
}
function renderRuntimeDiscovery(){
 const select=$('agent-backend'),chosen=select.value||state.settings.agent_backend||'codex';
 select.replaceChildren();
 for(const runtime of runtimeCatalog){const option=new Option(runtime.name+(runtime.available?'':(runtime.installed?' · 尚未支持':' · 未安装')),runtime.id);option.disabled=!runtime.available;select.add(option)}
 if(![...select.options].some(o=>o.value===chosen))select.add(new Option(chosen+' · 未检测到',chosen));select.value=chosen;
 const modelBlock=$('settings-model-block');
 const installed=runtimeCatalog.filter(r=>r.installed).sort((a,b)=>Number(b.id===chosen)-Number(a.id===chosen)),missing=runtimeCatalog.filter(r=>!r.installed);
 const row=r=>`<article data-runtime-card="${esc(r.id)}" class="runtime-card ${r.id===chosen?'selected':''}"><div class="runtime-card-head"><span class="runtime-monogram" aria-hidden="true">${esc(r.name.slice(0,1))}</span><div><h3>${esc(r.name)}${r.id===chosen?'<span class="runtime-selected">当前使用</span>':''}</h3><p>${esc(r.version||'版本未确认')}</p></div><button type="button" class="outline" data-runtime-select="${esc(r.id)}" ${r.available?'':'disabled'}>${r.id===chosen?'已选择':r.available?'选择':(r.installed?'尚未支持':'未安装')}</button>${r.available?`<button type="button" class="outline" data-runtime-test="${esc(r.id)}" ${r.id!==chosen?'disabled':''}>测试</button>`:''}</div>${r.id===chosen?`<p class="runtime-current-model">模型 <strong>${esc(state.settings.model_selection_required?'待选择':state.settings.model||'宿主默认')}</strong></p>`:''}<details><summary>安装与能力</summary><p class="runtime-path">${esc(r.path||'未安装')}</p><p>${esc(r.diagnostic||'已找到本机 CLI，实际能力以运行结果为准。')}</p></details></article>`;
 $('runtime-discovery-details').innerHTML=installed.map(row).join('')+`<details class="runtime-uninstalled"><summary>未安装的 CLI · ${missing.length}</summary><p>${missing.map(r=>esc(r.name)).join(' · ')}</p></details>`;
 const selectedCard=[...$('runtime-discovery-details').querySelectorAll('[data-runtime-card]')].find(c=>c.dataset.runtimeCard===chosen);
 if(selectedCard)selectedCard.append(modelBlock);else $('settings-cli').append(modelBlock);
 $('runtime-discovery-details').querySelectorAll('[data-runtime-test]').forEach(button=>button.onclick=()=>action(async()=>{
 const model=$('model-select').value.trim();if(!model)throw Error('请先选择或输入模型 ID');
 const r=await api('runtime-test',{backend:button.dataset.runtimeTest,model});await selectChat(r.session_id);
 },'已提交模型测试；进展见对话，可随时停止'));
 $('runtime-discovery-details').querySelectorAll('[data-runtime-select]').forEach(button=>button.onclick=()=>{if(button.dataset.runtimeSelect===backendValue())return;select.value=button.dataset.runtimeSelect;select.dispatchEvent(new Event('change'))});
 renderSettingsSessionNote();
}
async function refreshRuntimeDiscovery(force=false){
 const select=$('agent-backend');if(!select.value){const backend=state.settings.agent_backend||'codex';if(!Array.from(select.options).some(o=>o.value===backend))select.add(new Option(backend,backend));select.value=backend;}
 if(!$('runtime-discovery-status')){const box=document.createElement('section');box.className='runtime-discovery';box.innerHTML='<div class="section-title"><strong>本机 Runtime</strong><button type="button" id="runtime-discovery-refresh" class="outline">重新检测</button></div><p id="runtime-discovery-status" class="help" role="status"></p><div id="runtime-discovery-details" class="help"></div><p id="runtime-model-status" class="help" role="status"></p>';$('settings-runtime-list').append(box);$('runtime-discovery-refresh').onclick=()=>refreshRuntimeDiscovery(true)}
 $('runtime-discovery-status').textContent='正在检测本机 CLI…';$('runtime-discovery-refresh').disabled=true;
 try{const data=await api('runtimes'+(force?'?refresh=1':''));runtimeCatalog=data.runtimes||[];renderRuntimeDiscovery();$('runtime-discovery-status').textContent=`已接入 ${runtimeCatalog.filter(r=>r.available).length} 个本机 CLI；账号与模型可通过短测试验证。`;await refreshModelSuggestions(force)}catch(e){$('runtime-discovery-status').textContent='检测失败：'+e.message}finally{$('runtime-discovery-refresh').disabled=false}
}
$('agent-backend').onchange=()=>action(async()=>{const backend=backendValue(),dropped=Object.keys(state.settings.role_models||{}).length;await api('settings',{agent_backend:backend,model_selection_required:true,role_models:{}});state.settings.agent_backend=backend;state.settings.model_selection_required=true;state.settings.role_models={};$('model-select').value='';$('chat-model').value='';modelCatalog={backend:null,at:0,models:[]};renderRuntimeDiscovery();renderRoleModels();renderBackend();updateComposer();await refreshModelSuggestions();notice(dropped?'宿主已切换；原宿主的角色模型已清空，留空即继承主链模型':'Runtime 已保存；请选择或输入模型')});if($('welcome')&&!$('welcome').hidden)renderWelcome();$('model-variant').onchange=()=>action(saveModel,'Variant 已保存；下一次启动生效');
let modelCatalog={backend:null,at:0,models:[]};
const modelCatalogs=new Map();
function modelTargetBackend(target){return target==='chat-model'?chat.session?.runtime?.backend||backendValue():backendValue()}
async function fetchModelCatalog(force=false,backend=backendValue()){
 const now=Date.now(),cached=modelCatalogs.get(backend);
 if(!force&&cached&&now-cached.at<3600000){if(backend===backendValue())modelCatalog=cached;return cached.models;}
 const data=await api('models?backend='+encodeURIComponent(backend)+(force?'&refresh=1':''));
 const catalog={backend,at:now,models:(data.models||[]).map(m=>typeof m==='string'?{id:m,name:friendlyModel(m)}:{...m,name:m.name||m.label||friendlyModel(m.id),provider:m.provider||runtimeName(backend)}),diagnostic:data.diagnostic||data.error||'',source:data.source||''};modelCatalogs.set(backend,catalog);if(backend===backendValue())modelCatalog=catalog;return catalog.models;
}
async function refreshModelSuggestions(force=false){
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
  $('model-picker-list').innerHTML=html||(error?('<p class="help">读取失败：'+esc(error)+'</p>'):(models.length?'<p class="help">没有匹配的模型。可直接输入完整模型 ID 后按回车选用。</p>':'<p class="help">'+esc(emptyCatalogLabel(backend,modelCatalogs.get(backend)))+'</p>'));
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
$('model-picker-refresh').onclick=()=>action(async()=>{await refreshModelSuggestions(true);await renderModelPicker()},'模型目录已刷新');
$('model-select').onchange=()=>action(saveModel,'模型已保存；下一次启动生效');$('effort-select').onchange=()=>action(saveModel,'推理档位已保存；下一次启动生效');$('model-provider').onchange=()=>action(saveModel,'Provider 已保存；下一次启动生效');$('model-browse').onclick=()=>openModelPicker('model-select');
$('model-apply-session').onclick=()=>{
 const session=chat.session;
 if(!session){notice('当前没有打开的会话，请先新建或选择对话',true);return}
 if(chatActive()){notice('会话正在运行，请等待或停止后再应用',true);return}
 if(session.runtime?.backend!==backendValue()){notice('执行引擎不同，不能应用到当前会话；请用「用以上设置开新会话」',true);return}
 const model=$('model-select').value.trim();if(!model){notice('请先选择或输入模型 ID',true);return}
 $('chat-model').value=model;updateComposer();renderSettingsSessionNote();notice('已应用到当前会话，下一条消息生效（执行引擎按会话固定）');
};

$('version-history').onclick=()=>action(async()=>{
 await savedVersion();if(!current)return;
 const versions=state.briefs.filter(b=>b.run_id===current.run_id);
 $('history-list').innerHTML=versions.map((b,i)=>`<button class="history-row" data-history-version="${b.id}"><strong>${b.author==='agent'?'生成原稿':i===0?'当前编辑稿':'自动保存快照'}</strong><span>${new Date(b.created).toLocaleString('zh-CN',{hour12:false,year:'numeric',month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit',second:'2-digit'})}</span></button>`).join('');
 $('history-list').querySelectorAll('[data-history-version]').forEach(button=>button.onclick=()=>{const b=state.briefs.find(x=>x.id===button.dataset.historyVersion);openBrief(b);render(false);$('history-dialog').close()});
 $('history-dialog').showModal();
});
$('close-history').onclick=()=>$('history-dialog').close();

// Interactive agent conversations. Artifact editors keep their existing state.
const chat = {view:'active',sessions:[],id:null,session:null,messages:[],requests:[],events:new Map(),after:0,busy:false,uploading:0,polling:false,drafts:new Map(),attachments:new Set(),request:null};
const chatStates={idle:'准备就绪',starting:'正在启动',running:'正在处理',complete:'已完成',completed:'已完成',failed:'运行失败',interrupted:'已中断',cancelled:'已停止',queued:'已排队',sending:'发送中',delivered:'已发送',streaming:'正在回复'};
const chatActive=()=>['running','starting'].includes(chat.session?.status);
function rememberDraft(){chat.drafts.set(chat.id||'new',{text:$('chat-input').value,sources:[...chat.attachments],backend:chat.session?.runtime?.backend||state.settings.agent_backend||'codex',model:$('chat-model').value,model_provider:$('chat-model-provider').value.trim()||null,effort:$('chat-effort').value,allow_web:$('chat-allow-web').checked,permission:$('chat-permission').value});try{sessionStorage.setItem('briefloop-chat-drafts',JSON.stringify([...chat.drafts].slice(-30)))}catch{}}
function restoreDraft(){
 const d=chat.drafts.get(chat.id||'new'),sessionRuntime=chat.session?.runtime;
 const backend=sessionRuntime?.backend||state.settings.agent_backend||'codex';
 const saved=d&&(d.backend===backend||(!d.backend&&sessionRuntime))?d:null;
 const fallback={model:state.settings.model_selection_required?'':state.settings.model,backend,effort:state.settings.reasoning_effort};
 const runtime=saved||sessionRuntime||fallback;
 // Searching is the expected default for a fresh chat; a saved draft keeps the user's own choice.
 $('chat-input').value=d?.text||'';chat.attachments=new Set(d?.sources||[]);$('chat-allow-web').checked=d&&('allow_web' in d)?!!d.allow_web:true;
 $('chat-model').value=runtime.model||'';assignEffort('chat-effort',effortValue(runtime,'effort'));
 $('chat-model-provider').value=runtime.model_provider||'';$('chat-permission').value=runtime.permission||'workspace-write';
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
 const modes=(caps?.permission_modes||(['codex','opencode'].includes(runtime)?['workspace-write','read-only']:['runtime-native'])).filter(mode=>PERMISSION_MODES[mode]);
 return modes.length?modes:['workspace-write'];
}
function renderChatRuntimePermissions(){
 const backend=chat.session?.runtime?.backend||state.settings.agent_backend||'codex',select=$('chat-permission'),modes=permissionModes(backend);
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
 const canSteer=declaredCaps?declaredCaps.steer!==false:backend==='codex';
 for(const option of $('chat-mode').options){if(option.value==='steer'){option.hidden=!canSteer;option.disabled=!canSteer}}
 if(!canSteer&&$('chat-mode').value==='steer')$('chat-mode').value='queue';
 $('chat-effort').hidden=backend!=='codex';document.querySelector('.chat-provider-row').hidden=backend!=='codex';
}
function runtimeChoice(){const model=$('chat-model').value.trim();if(!model)throw Error('请输入模型 ID');const backend=chat.session?.runtime?.backend||state.settings.agent_backend||'codex';if(!['codex','opencode'].includes(backend)){return {model,backend,permission:'runtime-native'}}if(backend==='opencode'){if(!model.includes('/'))throw Error('Opencode 模型必须是 provider/model 形式，例如 opencode-go/gpt-5.6-luna');return {model,backend,variant:state.settings.model_variant||null,permission:$('chat-permission').value}}return {model,backend,model_provider:$('chat-model-provider').value.trim()||null,effort:$('chat-effort').value,permission:$('chat-permission').value}}
function messageTime(value){const date=new Date(value);return Number.isNaN(date.getTime())?'':date.toLocaleTimeString('zh-CN',{hour:'2-digit',minute:'2-digit',hour12:false})}
function chatError(text=''){$('chat-error').textContent=text;$('chat-error').hidden=!text}
function sessionMissing(error){return /会话或消息不存在|会话不存在/.test(String(error&&error.message||error||''))}
function updateComposer(){renderChatRuntimePermissions();const readonly=chat.session&&chat.session.lifecycle&&chat.session.lifecycle!=='active';const active=chatActive(),steering=active&&$('chat-mode').value==='steer';if(steering){const runtime=activeChatRuntime();$('chat-permission').value=runtime.permission||'workspace-write';$('chat-model').value=runtime.model||'';assignEffort('chat-effort',effortValue(runtime,'effort'));$('chat-model-provider').value=runtime.model_provider||'';const activeMessage=chat.messages.find(m=>m.role==='user'&&m.turn_id===chat.session?.turn_id);$('chat-allow-web').checked=!!activeMessage?.allow_web} $('chat-allow-web').disabled=readonly||steering||chat.busy;for(const id of ['chat-model','chat-effort','chat-model-provider'])$(id).disabled=readonly||steering||chat.busy;$('chat-permission').disabled=readonly||steering||chat.busy;$('new-session').disabled=chat.busy||chat.uploading>0;$('chat-input').readOnly=chat.busy||readonly;document.querySelectorAll('[data-chat-session]').forEach(b=>b.disabled=chat.busy||chat.uploading>0);$('chat-send').disabled=readonly||chat.busy||chat.uploading>0||(!$('chat-input').value.trim()&&!chat.attachments.size)||!$('chat-model').value.trim();const sendLabel=chat.busy?'发送中…':active?($('chat-mode').value==='steer'?'立即补充':'排队发送'):'发送消息';$('chat-send').textContent=chat.busy?'…':'↑';$('chat-send').setAttribute('aria-label',sendLabel);$('chat-send').title=sendLabel;$('chat-stop').hidden=!active;$('chat-stop').disabled=chat.busy;$('chat-mode').disabled=!active||chat.busy;$('chat-attach').disabled=readonly||chat.busy||chat.uploading>0;$('attach-existing').disabled=readonly||chat.busy;$('chat-attach').querySelector('span').textContent=chat.uploading?'上传中…':'文件';const model=$('chat-model').value.trim(),label=friendlyModel(model)||'输入模型 ID';$('chat-model').title=model?friendlyModel(model)+' · '+model:'输入模型 ID';const chatBackend=chat.session?.runtime?.backend||state.settings.agent_backend||'codex';$('chat-runtime-label')&&($('chat-runtime-label').textContent=runtimeName(chatBackend));const effort=chatBackend==='opencode'?(chat.session?.runtime?.variant||state.settings.model_variant||'模型默认'):($('chat-effort').value==='none'?'模型默认':$('chat-effort').value);$('composer-help').textContent=`Enter 发送 · Shift + Enter 换行 · ${label}${['codex','opencode'].includes(state.settings.agent_backend||'codex')?' / '+effort:''}${active?' · 立即补充沿用当前联网与模型设置；更改设置请排队到下一回合':''}`}
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
 $('existing-sources').innerHTML=sources.length?sources.map(s=>`<label><input type="checkbox" data-chat-source="${esc(s.id)}" ${chat.attachments.has(s.id)?'checked':''} ${s.status==='failed'?'disabled':''}><span>${esc(s.name)}</span><small>${s.status==='failed'?'读取失败':s.needs_visual?'需视觉读取':''}</small></label>`).join(''):'<p class="help">还没有已保存来源。可以上传文件，也可以开启联网检索。</p>';
 $('existing-sources').querySelectorAll('[data-chat-source]').forEach(b=>b.onchange=()=>{b.checked?chat.attachments.add(b.dataset.chatSource):chat.attachments.delete(b.dataset.chatSource);renderAttachments();rememberDraft();updateComposer()});
}
function publicActivity(event){
 const data=event.data||{},item=data.item;
 if(item){
  const types={runtime_tool:'调用工具',opencode_tool:'调用工具',commandExecution:'运行命令',fileChange:'更新文件',mcpToolCall:'调用工具',webSearch:'搜索网页',collabAgentToolCall:'子 Agent',imageView:'查看图片',dynamicToolCall:'调用工具'};
  if(!types[item.type])return null;
  const agents=item.agentsStates?Object.entries(item.agentsStates).map(([id,v])=>`${id}: ${typeof v==='string'?v:v.status||''}`).join('\n'):'';
  const detail=[item.command,item.query,item.server&&item.tool?`${item.server} / ${item.tool}`:item.tool,agents,item.model?`${item.model}${item.reasoningEffort?' / '+item.reasoningEffort:''}`:''].filter(Boolean).map(v=>typeof v==='string'?v:JSON.stringify(v)).join('\n');
  return {key:`${event.kind.startsWith('child/')?'child:':''}${data.threadId||''}:${item.id||event.seq}`,label:types[item.type],detail,status:item.status||(event.kind.endsWith('completed')?'completed':'running'),seq:event.seq,created:event.created};
 }
 if(event.kind==='thread/providerChanged')return {key:`provider:${event.seq}`,label:'模型服务已切换',detail:data.message||'下一轮使用新的执行上下文。',status:'completed',seq:event.seq,created:event.created};
 if(event.kind==='error')return {key:`error:${event.seq}`,label:'运行提示',detail:data.message||'请求未完成',status:'failed',seq:event.seq,created:event.created};
 return null;
}
function renderActivities(){
 const byItem=new Map();for(const event of chat.events.values()){const entry=publicActivity(event);if(entry)byItem.set(entry.key,entry)}
 const entries=[...byItem.values()].sort((a,b)=>a.seq-b.seq);const signature=JSON.stringify(entries);if(renderActivities.signature===signature)return;renderActivities.signature=signature;const opened=new Set([...$('activity-list').querySelectorAll('details[open]')].map(e=>e.dataset.activityKey));$('chat-activity').hidden=!entries.length;$('activity-count').textContent=entries.length?String(entries.length):'';
 const active=entries.filter(e=>['running','inProgress','started','pending'].includes(e.status));$('activity-title').textContent=active.length?`正在进行 · ${active.at(-1).label}`:'工具与子 Agent 活动';
 $('activity-list').innerHTML=entries.map(e=>`<details class="activity-item" data-activity-key="${esc(e.key)}" ${opened.has(e.key)?'open':''}><summary><span class="activity-indicator ${['failed','declined','error'].includes(e.status)?'failed':(['running','inProgress','started','pending'].includes(e.status)?'running':'done')}"></span><strong>${esc(e.label)}</strong><span>${esc(chatStates[e.status]||({inProgress:'正在执行',started:'正在执行',done:'已完成',success:'已完成',declined:'未执行',error:'未完成'}[e.status])||e.status)}</span><time>${messageTime(e.created)}</time></summary>${e.detail?`<pre>${esc(e.detail)}</pre>`:''}</details>`).join('');
}
function renderMessages(){
 const scroll=$('chat-scroll'),nearEnd=scroll.scrollHeight-scroll.scrollTop-scroll.clientHeight<140;
 const signature=JSON.stringify(chat.messages);if(signature!==renderMessages.signature){renderMessages.signature=signature;
 const nodes=new Map([...$('chat-messages').children].map(n=>[n.dataset.messageId,n]));
 for(const message of chat.messages){
  let node=nodes.get(message.id);if(!node){node=document.createElement('article');node.dataset.messageId=message.id;$('chat-messages').append(node)}nodes.delete(message.id);
  const messageSignature=JSON.stringify(message);if(node.dataset.signature===messageSignature)continue;node.dataset.signature=messageSignature;node.className=`chat-message ${message.role==='user'?'from-user':'from-assistant'} ${message.mode==='notice'?'task-notice':''} ${['failed','interrupted','cancelled'].includes(message.status)?'message-error':''}`;
  const files=(message.source_ids||[]).map(id=>({id,name:state?.sources.find(s=>s.id===id)?.name||id}));
  const label=message.role==='user'?'你':(message.mode==='notice'?'任务状态':'BriefLoop');
  node.innerHTML=`<div class="message-heading"><strong>${label}</strong><span>${messageTime(message.created)}</span><span class="message-state">${esc(chatStates[message.status]||message.status)}${message.mode==='steer'&&message.role==='user'?' · 中途补充':''}</span></div><div class="message-body">${esc(message.text||(['streaming','sending'].includes(message.status)?'…':''))}</div>${files.length?`<div class="message-files">${files.map(file=>`<button type="button" data-message-source="${esc(file.id)}">▤ ${esc(file.name)}</button>`).join('')}</div>`:''}${messageActionsHTML()}`;
  const reqBlock=/```briefloop-requirements\s*([\s\S]*?)```/.exec(message.text||'');if(reqBlock){const apply=document.createElement('button');apply.type='button';apply.className='outline apply-requirements';apply.textContent='应用到材料与需求';apply.onclick=()=>applyRequirements(reqBlock[1].trim());node.append(apply)}
  bindMessageActions(node,message);
  node.querySelectorAll('[data-message-source]').forEach(button=>button.onclick=()=>action(async()=>showSource(await api('source?id='+encodeURIComponent(button.dataset.messageSource)))));
  if(message.role==='assistant'&&message.status==='completed'&&message.text&&message.mode!=='notice'){api('render',{markdown:message.text}).then(result=>{if(node.isConnected&&node.dataset.signature===messageSignature){node.querySelector('.message-body').innerHTML=result.html;node.querySelector('.message-body').classList.add('rendered-markdown');if(nearEnd)scroll.scrollTop=scroll.scrollHeight}}).catch(()=>{})}
 }
 for(const node of nodes.values())node.remove();if(nearEnd)scroll.scrollTop=scroll.scrollHeight;
 }
 $('chat-empty').hidden=chat.messages.length>0;
}
function renderChat(){
 $('chat').classList.toggle('is-empty',chat.messages.length===0&&!chatActive());
 const cta=$('chat-setup-cta');if(cta)cta.hidden=!(state&&(state.settings?.model_selection_required||!state.settings?.model)&&chat.messages.length===0);
 $('chat-title').textContent=chat.session?.title||'新对话';const runtime=chat.session?.runtime;const pending=chat.messages.filter(m=>m.role==='user'&&m.status==='queued').length;
 $('chat-status').textContent=`${chatStates[chat.session?.status]||'准备就绪'}${runtime?' · '+modelLabel({model:runtime.model,reasoning_effort:runtime.effort,model_provider:runtime.model_provider}):''}${pending?' · '+pending+' 条消息排队中':''}`;
 renderMessages();renderActivities();renderRequests();renderContext();renderSessions();renderSessionLifecycle();updateComposer();
}
async function selectChat(id){
 if(chat.busy||chat.uploading)return;if(id===chat.id){page('chat');return}rememberDraft();chat.id=id;chat.session=chat.sessions.find(s=>s.id===id)||null;chat.tokenUsage=null;chat.messages=[];chat.requests=[];chat.events=new Map();chat.after=0;chat.request=null;renderMessages.signature='';localStorage.setItem('briefloop-chat-session',id);chatError();restoreDraft();renderChat();page('chat');await pollChat(true);if(!chat.drafts.has(id))restoreDraft();
}
async function newChat(){
 if(chat.busy||chat.uploading)return;rememberDraft();chat.view='active';$('session-view').value='active';chat.sessions=[];chat.id=null;chat.session=null;chat.tokenUsage=null;chat.messages=[];chat.requests=[];chat.events=new Map();chat.after=0;chat.request=null;renderMessages.signature='';chat.drafts.delete('new');localStorage.removeItem('briefloop-chat-session');restoreDraft();chatError();renderChat();page('chat');$('chat-input').focus();await pollChat(true).catch(e=>chatError(e.message));
}
async function pollChat(force=false){
 if(chat.polling&&!force)return;chat.polling=true;const sid=chat.id,after=chat.after,view=chat.view;
 try{
  const [list,snapshot]=await Promise.all([api('harness/sessions?view='+view),sid?api(`harness/session?id=${encodeURIComponent(sid)}&after=${after}`):Promise.resolve(null)]);
  if(view===chat.view)chat.sessions=list.sessions||[];if(sid===chat.id&&snapshot){chat.session=snapshot.session;chat.tokenUsage=snapshot.token_usage||null;chat.messages=snapshot.messages||[];chat.requests=snapshot.requests||[];for(const event of snapshot.events||[]){chat.events.set(event.seq,event);chat.after=Math.max(chat.after,event.seq)}}renderChat();
 }catch(e){if(force)throw e;else if(!$('chat').hidden){if(sessionMissing(e)){chat.id=null;chat.session=null;chat.messages=[];localStorage.removeItem('briefloop-chat-session');chatError();renderChat()}else{$('chat-status').textContent='会话连接中断，正在重连';chatError(e.message)}}}finally{chat.polling=false}
}
async function sendChat(event){
 event.preventDefault();if(chat.busy||chat.uploading||chat.session&&chat.session.lifecycle&&chat.session.lifecycle!=='active')return;const text=$('chat-input').value.trim()||(chat.attachments.size?'请查看附件。':'');if(!text)return;const discuss=/^\/discuss\b\s*/i.test(text),displayText=text.replace(/^\/discuss\b\s*/i,'').trim()||'讨论需求',sendText=discuss?(DISCUSS_INSTRUCTION+(displayText!=='讨论需求'?('\n\n用户补充：'+displayText):'')):text;chat.busy=true;chatError();updateComposer();
 try{
  const runtime=runtimeChoice();if(!chat.id){const result=await api('harness/session',{title:displayText.slice(0,48),runtime});chat.session=result.session||result;chat.id=chat.session.id;if(!chat.id)throw Error('未能创建会话');localStorage.setItem('briefloop-chat-session',chat.id);rememberDraft()}
  const payload={session_id:chat.id,text:sendText,display_text:sendText===displayText?undefined:displayText,mode:chatActive()?$('chat-mode').value:'queue',source_ids:[...chat.attachments],runtime,allow_web:$('chat-allow-web').checked};const signature=JSON.stringify(payload);
  if(!chat.request||chat.request.signature!==signature)chat.request={signature,message_id:crypto.randomUUID()};
  await api('harness/message',{...payload,message_id:chat.request.message_id});chat.request=null;$('chat-input').value='';chat.attachments.clear();rememberDraft();renderAttachments();
  // The runtime used here is the chosen model; keep the pending-selection state in sync.
  state.settings={...state.settings,model_selection_required:false,model:runtime.model,agent_backend:runtime.backend||state.settings.agent_backend};
  $('model-select').value=runtime.model;renderRuntimeDiscovery();updateModelLabel();
  await pollChat(true);
 }catch(e){rememberDraft();chatError(e.message+'。消息仍保留在输入框中，可修改或再次发送。')}finally{chat.busy=false;updateComposer();$('chat-input').focus()}
}
$('chat-form').onsubmit=sendChat;
$('new-session').onclick=newChat;
$('chat-input').oninput=()=>{rememberDraft();updateComposer()};
$('chat-input').onkeydown=e=>{if(e.key==='Enter'&&!e.shiftKey&&!e.isComposing){e.preventDefault();if(!$('chat-send').disabled)$('chat-form').requestSubmit()}};
$('chat-mode').onchange=updateComposer;
for(const id of ['chat-model','chat-effort','chat-model-provider'])$(id).onchange=()=>{rememberDraft();chatError();updateComposer()};
$('chat-stop').onclick=async()=>{if(!chat.id||chat.busy)return;chat.busy=true;updateComposer();try{await api('harness/cancel',{session_id:chat.id});await pollChat(true)}catch(e){chatError(e.message)}finally{chat.busy=false;updateComposer()}};
$('chat-attach').onclick=()=>$('chat-upload').click();
$('attach-existing').onclick=()=>{const show=$('existing-sources').hidden;$('existing-sources').hidden=!show;$('attach-existing').setAttribute('aria-expanded',String(show));renderAttachments()};
$('chat-upload').onchange=async event=>{
 const input=event.target,files=[...input.files];input.value='';if(!files.length)return;chat.uploading++;chatError();updateComposer();
 const chatBackend=chat.session?.runtime?.backend||state.settings.agent_backend||'codex';
 const canImages=((runtimeCatalog||[]).find(r=>r.id===chatBackend)||{}).capabilities?.images!==false;
 try{for(const file of files){
   // A host that cannot take images must say so at attach time; the turn would
   // otherwise fail after the whole message was queued.
   if(file.type.startsWith('image/')&&!canImages){chatError(`${file.name}：${runtimeName(chatBackend)} 不支持直接读图；请改用支持读图的宿主，或先转成文字材料。`);continue}
   const bytes=new Uint8Array(await file.arrayBuffer());let binary='';for(let i=0;i<bytes.length;i+=8192)binary+=String.fromCharCode(...bytes.subarray(i,i+8192));const source=await api('upload',{name:file.name,data:btoa(binary)});if(source.status==='failed'){chatError(`${file.name} 读取失败：${source.error||'请检查文件后重试'}`);continue}chat.attachments.add(source.id);selected.add(source.id)}await refresh();renderAttachments();rememberDraft()}catch(e){chatError('上传未完成：'+e.message)}finally{chat.uploading--;updateComposer()}
};
document.querySelectorAll('[data-prompt]').forEach(b=>b.onclick=()=>{$('chat-input').value=b.dataset.prompt;rememberDraft();updateComposer();$('chat-input').focus()});
async function initChat(){
 try{const saved=JSON.parse(sessionStorage.getItem('briefloop-chat-drafts')||'[]');if(Array.isArray(saved))chat.drafts=new Map(saved)}catch{}
 chat.id=localStorage.getItem('briefloop-chat-session')||null;
 try{const list=await api('harness/sessions?view=active');chat.sessions=list.sessions||[]}catch{chat.sessions=[]}
 const recoverable=!!chat.id&&chat.sessions.some(s=>s.id===chat.id);
 const settings=state&&state.settings;
 const hasModel=!!(settings&&settings.model)&&!settings.model_selection_required;
 const running=!!((state&&state.jobs)||[]).some(j=>['queued','running'].includes(j.status));
 if(!recoverable&&!hasModel&&!running){
  // Cold start: no conversation to recover, no host/model chosen, nothing running.
  chat.id=null;localStorage.removeItem('briefloop-chat-session');page('welcome');renderWelcome();
 }else{
  page('chat');restoreDraft();
  try{await pollChat(true)}
  catch(e){
   // A saved conversation can be gone after a workspace reset or delete. Drop it and
   // show the empty new-conversation state instead of a raw "会话或消息不存在".
   chatError(sessionMissing(e)?'':e.message);
   chat.id=null;chat.session=null;chat.messages=[];localStorage.removeItem('briefloop-chat-session');
   await pollChat().catch(()=>{});
  }
  if(chat.session)restoreDraft();
 }
 setInterval(()=>pollChat(),1300);
}

$('chat-allow-web').onchange=rememberDraft;
function renderRequests(){
 const requests=chat.requests.filter(r=>r.status==='pending'),signature=JSON.stringify(requests);$('chat-requests').hidden=!requests.length;
 if(renderRequests.signature===signature)return;renderRequests.signature=signature;
 $('chat-requests').innerHTML=requests.map(request=>`<form class="agent-question" data-request-id="${esc(request.id)}"><strong>BriefLoop 需要你的补充</strong>${(request.data?.questions||[]).map((q,i)=>`<fieldset data-question-index="${i}"><legend>${esc(q.question||q.header||'请补充')}</legend>${(q.options||[]).map((option,j)=>`<label class="question-option"><input type="radio" name="answer-${i}" value="${j}"><span><b>${esc(option.label)}</b>${option.description?`<small>${esc(option.description)}</small>`:''}</span></label>`).join('')}<input class="question-text" name="text-${i}" aria-label="${esc(q.question||'补充说明')}" placeholder="${q.options?.length?'或直接输入你的回答':'输入回答'}"></fieldset>`).join('')}<button class="primary" type="submit">提交回答</button><span class="question-error" role="alert"></span></form>`).join('');
 $('chat-requests').querySelectorAll('form').forEach(form=>form.onsubmit=async event=>{
  event.preventDefault();const request=requests.find(r=>r.id===form.dataset.requestId),answers={};
  for(const [i,q] of (request.data?.questions||[]).entries()){const custom=form.elements[`text-${i}`].value.trim(),option=form.querySelector(`input[name="answer-${i}"]:checked`);const value=custom||(option?q.options[Number(option.value)].label:'');if(!value){form.querySelector('.question-error').textContent='请回答每一个问题后提交。';return}answers[q.id]={answers:[value]}}
  const button=form.querySelector('button');button.disabled=true;button.textContent='提交中…';form.querySelector('.question-error').textContent='';
  try{await api('harness/answer',{session_id:chat.id,request_id:request.id,answers});await pollChat(true)}catch(e){form.querySelector('.question-error').textContent=e.message}finally{button.disabled=false;button.textContent='提交回答'}
 });
}

function renderRoleModels(){
 const roles=[['evaluator','Evaluator','给产物评分；比较候选产物，判断是否改善'],['maintainer','Wiki Maintainer','将反馈和执行经验整理成 Wiki'],['proposer','Skill Proposer','根据 Wiki 提出或改进 Skill']];
  $('role-model-options').innerHTML=roles.map(([role,name,label])=>{const configured=state.settings.role_models||{},value=(role==='evaluator'?(configured.evaluator||configured.scorer||configured.assessor):configured[role])||{},inherits=!value.model,effort=effortValue(inherits?state.settings:value,'reasoning_effort');return `<div class="role-model-row"><span><b>${name}</b><small>${label}</small></span><div class="role-runtime-fields"><div class="role-model-pair"><input id="role-${role}-model" aria-label="${name} 模型 ID" data-role-model="${role}" list="model-suggestions" autocomplete="off" spellcheck="false" value="${esc(value.model||'')}" placeholder="留空继承主链模型" title="${esc(value.model?friendlyModel(value.model):'继承主链模型')}"><select id="role-${role}-effort" class="role-effort-select" aria-label="${name} 推理档位" data-role-effort="${role}" ${inherits?'disabled':''}>${[...new Set(['none','low','medium','high','xhigh','max',effort])].map(e=>`<option value="${e}" ${e===effort?'selected':''}>${e==='none'?'模型默认':e}</option>`).join('')}</select></div><label class="role-provider-field"><span>Provider</span><input id="role-${role}-provider" aria-label="${name} Codex provider" value="${esc(value.model_provider||'')}" placeholder="${inherits?'继承主链全部配置':'留空沿用本机 Codex 配置'}" autocomplete="off" spellcheck="false" ${inherits?'disabled':''}></label><label class="role-variant-field" hidden><span>Variant</span><input id="role-${role}-variant" aria-label="${name} Opencode variant" data-role-variant="${role}" value="${esc(value.model_variant||'')}" placeholder="如 high / max，留空默认" autocomplete="off" spellcheck="false" ${inherits?'disabled':''}></label></div></div>`}).join('');
  $('role-model-options').querySelectorAll('input,select').forEach(input=>input.onchange=saveRoleModels);renderBackend();
 setupModelPickers($('role-model-options'));
}
async function saveRoleModels(){
  const op=backendValue()==='opencode';const role_models={};for(const input of $('role-model-options').querySelectorAll('[data-role-model]')){const role=input.dataset.roleModel,model=input.value.trim(),effort=$(`role-${role}-effort`),provider=$(`role-${role}-provider`),variant=$(`role-${role}-variant`);effort.disabled=!model;provider.disabled=!model;variant.disabled=!model;if(model)role_models[role]=op?{model,model_variant:variant.value.trim()||null}:{model,reasoning_effort:effort.value,model_provider:provider.value.trim()||null}}
  const controls=[...$('role-model-options').querySelectorAll('input,select')];controls.forEach(input=>input.disabled=true);$('role-model-status').textContent='保存中…';
  try{await api('settings',{role_models});state.settings.role_models=role_models;$('role-model-status').textContent='已保存，下一次启动时生效。'}catch(e){$('role-model-status').textContent='未保存：'+e.message;renderRoleModels()}finally{for(const input of $('role-model-options').querySelectorAll('[data-role-model]')){input.disabled=false;const inherits=!input.value.trim();$(`role-${input.dataset.roleModel}-effort`).disabled=inherits;$(`role-${input.dataset.roleModel}-provider`).disabled=inherits;$(`role-${input.dataset.roleModel}-variant`).disabled=inherits}}
}

let workspaceInventory=null,workspaceSwitching=false;
async function refreshWorkspaces(){
 const result=await api('workspaces');workspaceInventory=result;const current=result.current||{};
 $('workspace-name').textContent=current.name||'本地工作区';$('workspace-switch').title=current.path||'选择工作区';$('workspace-current-name').textContent=current.name||'当前工作区';$('workspace-current-path').textContent=current.path||'';
 $('workspace-list').innerHTML=(result.workspaces||[]).length?result.workspaces.map((workspace,i)=>`<button type="button" class="workspace-choice" data-workspace-index="${i}" ${workspace.path===current.path?'disabled':''}><span><strong>${esc(workspace.name||workspace.path)}</strong><small>${esc(workspace.path)}</small></span><em>${workspace.path===current.path?'当前':'打开 ↗'}</em></button>`).join(''):'<p class="help">还没有其他工作区。</p>';
 $('workspace-list').querySelectorAll('[data-workspace-index]').forEach(button=>button.onclick=()=>switchWorkspace(result.workspaces[Number(button.dataset.workspaceIndex)].path,false));
}
async function showWorkspacePicker(){
 $('workspace-switch-status').textContent='';$('workspace-switch-status').classList.remove('error');$('workspace-dialog').showModal();
 if(!workspaceInventory)$('workspace-list').innerHTML='<p class="help">正在读取工作区…</p>';
 try{await refreshWorkspaces()}catch(e){$('workspace-switch-status').textContent='无法读取工作区：'+e.message;$('workspace-switch-status').classList.add('error')}
}
async function switchWorkspace(path,create){
 if(workspaceSwitching)return;
 if(chat.busy||chat.uploading){$('workspace-switch-status').textContent='请等待消息发送或附件上传完成后再切换。';$('workspace-switch-status').classList.add('error');return}
 workspaceSwitching=true;$('workspace-switch-status').classList.remove('error');$('workspace-switch-status').textContent='正在打开工作区…';
 const controls=[...$('workspace-dialog').querySelectorAll('button,input')];const originalDisabled=new Map(controls.map(c=>[c,c.disabled]));controls.forEach(c=>c.disabled=true);
 try{
  rememberDraft();clearTimeout(saveTimer);const deadline=Date.now()+15000;while(saving&&Date.now()<deadline)await new Promise(resolve=>setTimeout(resolve,60));
  if(saving)throw Error('当前简报仍在保存，请稍后重试。');if(dirty){$('workspace-switch-status').textContent='正在保存当前简报…';await save();if(dirty)throw Error('当前简报尚未保存，请先完成保存。')}
  const result=await api('workspaces/open',{path:path.trim(),create});if(!result.url)throw Error('工作区服务尚未准备好，请重试。');
  $('workspace-switch-status').textContent='已打开，正在切换…';location.assign(result.url);
 }catch(e){$('workspace-switch-status').textContent=e.message;$('workspace-switch-status').classList.add('error')}
 finally{workspaceSwitching=false;controls.forEach(c=>c.disabled=originalDisabled.get(c))}
}
$('workspace-switch').onclick=showWorkspacePicker;
$('close-workspace').onclick=()=>$('workspace-dialog').close();
$('workspace-open-form').onsubmit=event=>{event.preventDefault();switchWorkspace($('workspace-path').value,false)};
$('workspace-create-form').onsubmit=event=>{event.preventDefault();switchWorkspace($('workspace-new-name').value,true)};

function renderContext(){
 const events=[...chat.events.values()].reverse(),usageEvent=events.find(e=>e.kind==='thread/tokenUsage/updated'),providerChange=events.find(e=>e.kind==='thread/providerChanged');
 const usage=providerChange&&(!usageEvent||providerChange.seq>usageEvent.seq)?null:(chat.tokenUsage||usageEvent?.data?.tokenUsage),last=usage?.last||{},windowSize=usage?.modelContextWindow;
 const finite=value=>typeof value==='number'&&Number.isFinite(value)&&value>=0;const count=value=>finite(value)?new Intl.NumberFormat('zh-CN').format(value):'未知';
 const hasInput=finite(last.inputTokens),hasWindow=finite(windowSize)&&windowSize>0;
 $('context-summary').textContent=usage?`上下文 · ${hasInput?count(last.inputTokens):'未知'} / ${hasWindow?count(windowSize):'上限未知'}`:'上下文 · 待统计';
 $('context-details').innerHTML=usage?`<strong>最近一次模型请求</strong><dl><div><dt>输入 Token</dt><dd>${count(last.inputTokens)}</dd></div><div><dt>其中缓存</dt><dd>${count(last.cachedInputTokens)}</dd></div><div><dt>输出 Token</dt><dd>${count(last.outputTokens)}</dd></div><div><dt>模型窗口</dt><dd>${hasWindow?count(windowSize):'未知'}</dd></div></dl>${hasInput&&hasWindow?`<meter min="0" max="${windowSize}" value="${Math.min(last.inputTokens,windowSize)}" aria-label="最近输入与上下文窗口的比例"></meter><p>最近输入占窗口 ${(last.inputTokens/windowSize*100).toFixed(1)}%。</p>`:''}<p>输入量来自最近一次请求，累计用量不作为上下文占用。</p>`:'<p>开始执行后，按后端返回的真实数据更新；暂无用量。</p>';
}
$('chat-permission').onchange=()=>{rememberDraft();updateComposer()};
function showSettings(){$('timeout-minutes').value=state.settings.timeout_minutes;moveSearchSettings('settings');page('settings-dialog');$('settings-dialog').scrollIntoView({block:'start'});refreshRuntimeDiscovery();updateLearningPause().catch(()=>{});refreshTavilySettings().catch(()=>{})}
$('settings-open').onclick=showSettings;$('chat-setup-open').onclick=showSettings;$('settings-close').onclick=()=>page('chat');
{
 const modelPanel=document.querySelector('.model-settings');const shortcut=document.createElement('div');shortcut.className='setup-settings-shortcut';shortcut.innerHTML='<div><span>生成模型</span><strong id="setup-model-summary">Luna / high</strong></div><button type="button" class="outline">模型与角色设置</button>';shortcut.querySelector('button').onclick=showSettings;modelPanel.before(shortcut);$('settings-model-block').append(modelPanel);
 const providerRow=document.querySelector('.chat-provider-row');providerRow.querySelector('label').textContent='当前对话 Provider';providerRow.querySelector('span').textContent='用于当前对话下一次执行';$('settings-model-block').append(providerRow);
 const learningControl=document.querySelector('.learning-control'),learningHelp=learningControl.nextElementSibling,autoLearnLabel=$('auto-learn').closest('label');$('settings-learning-block').append(learningControl,learningHelp,autoLearnLabel);
 const link=document.createElement('button');link.type='button';link.className='outline feedback-settings';link.textContent='学习设置';link.onclick=showSettings;$('learn-now').before(link);
}

// One width axis for reading and composing; existing controls remain mounted.
{
 const starters=document.querySelector('.starter-prompts');starters.classList.add('composer-starters');$('chat-form').after(starters);
 const options=document.querySelector('.composer-options'),trailing=document.querySelector('.send-controls');
 trailing.prepend($('chat-model'),$('chat-effort'));
 const help=$('composer-help');$('chat-form').after(help);
 const settingsButton=$('settings-open');settingsButton.innerHTML='<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M9 3h6l1 3 3 1 2 5-2 5-3 1-1 3H9l-1-3-3-1-2-5 2-5 3-1 1-3Z"/><circle cx="12" cy="12" r="3"/></svg><span>设置</span>';
}

function autoSizeChatInput(){const input=$('chat-input');input.style.height='auto';input.style.height=Math.min(210,Math.max(36,input.scrollHeight))+'px'}
{
 const inputHandler=$('chat-input').oninput;$('chat-input').oninput=event=>{inputHandler?.(event);autoSizeChatInput()};
}
async function updateLearningPause(){
 const runtime=await api('runtime'),paused=runtime.automatic_learning_paused===true;const note=$('settings-paused-note');note.hidden=!paused;note.textContent=paused?'此工作区的自动学习已暂停。勾选下方选项后启用。':'';if(paused)$('auto-learn').checked=false;else $('auto-learn').checked=!!state.settings.auto_learn;
}
$('auto-learn').onchange=async event=>{const enabled=event.target.checked;event.target.disabled=true;try{await api('settings',{auto_learn:enabled});await refresh();await updateLearningPause()}catch(e){notice(e.message,true)}finally{event.target.disabled=false}};

function showSource(result){
 const source=result.source||{},provenance=result.provenance||{};
 showSourceMedia(result);
 $('source-title').textContent=source.name||'来源';$('source-body').textContent=source.error||result.text||'';
 $('source-link').textContent=source.url||'';$('source-link').href=source.url||'#';$('source-link').hidden=!source.url;
 const original=$('source-original');original.textContent=provenance.original_kind==='provider_response'?'下载 Tavily 返回内容 ↗':'下载原件 ↗';original.hidden=!result.original_url;if(result.original_url){original.href=result.original_url;original.download=source.name||''}else original.removeAttribute('href');
 const rows=[];if(provenance.fetched_at){const date=new Date(provenance.fetched_at);rows.push(['抓取时间',Number.isNaN(date.getTime())?String(provenance.fetched_at):date.toLocaleString('zh-CN',{hour12:false})])}if(provenance.content_type)rows.push(['内容类型',String(provenance.content_type)]);
 $('source-provenance').innerHTML=rows.map(([label,value])=>`<div><dt>${esc(label)}</dt><dd>${esc(value)}</dd></div>`).join('');$('source-provenance').hidden=!rows.length;
 $('source-dialog').showModal();
}

let sourceMediaId=null;
function resetSourceMedia(){sourceMediaId=null;$('source-media').hidden=true;$('source-images').replaceChildren();$('source-pages-render').disabled=false}
function showSourceMedia(result){
 resetSourceMedia();const media=result.attachment;if(!media||result.source?.status==='failed')return;
 sourceMediaId=result.source.id;
 const isPDF=media.media_type==='application/pdf',isImage=media.media_type?.startsWith('image/');
 if(!isPDF&&!isImage)return;
 $('source-media').hidden=false;$('source-page-controls').hidden=!isPDF;$('source-pages').value='1';
 $('source-media-note').textContent=isPDF?`PDF 原件可用${media.pages?'，共 '+media.pages+' 页':''}。正文提取不覆盖所有图表，可按页查看。`:'原图已保存；发送给支持视觉的模型时会作为图片输入，不冒充 OCR 文本。';
 if(isImage&&result.image_url){const img=document.createElement('img');img.src=result.image_url;img.alt=result.source.name||'来源图片';img.className='source-preview-image';$('source-images').append(img)}
}
$('source-pages-render').onclick=()=>action(async()=>{
 const id=sourceMediaId;if(!id)return;const value=$('source-pages').value.trim();if(!/^\d+(?:\s*[,，]\s*\d+)*$/.test(value))throw Error('请输入页码，例如 1 或 1,3');
 const pages=[...new Set(value.split(/[,，]/).map(Number))];if(pages.some(p=>p<1)||pages.length>4)throw Error('每次请选择 1–4 页，页码从 1 开始');
 $('source-pages-render').disabled=true;
 try{const result=await api('source-pages',{source_id:id,pages});if(sourceMediaId!==id)return;$('source-images').replaceChildren();
  for(const page of result.pages){const figure=document.createElement('figure');const caption=document.createElement('figcaption');caption.textContent='第 '+page.page+' 页';const img=document.createElement('img');img.className='source-preview-image';img.alt=caption.textContent;img.src=page.url;figure.append(caption,img);$('source-images').append(figure)}
 }finally{if(sourceMediaId===id)$('source-pages-render').disabled=false}
});
$('source-dialog').addEventListener('close',resetSourceMedia);

function nativeSearchName(){const sp=$('search-provider').value;return sp==='tavily'?'Tavily':(runtimeName(state.settings.agent_backend||'codex')+' 原生')}
function renderSearchProvider(){$('tavily-settings').hidden=$('search-provider').value!=='tavily';$('setup-search-summary').textContent='搜索工具：'+nativeSearchName();renderBudgetProviderScope()}
async function refreshTavilySettings(){
 try{const result=await api('tavily');const where={environment:'环境变量',file:'本机配置'}[result.source]||'本机配置';$('tavily-key-status').textContent=result.configured?`已配置 · ${where} · 所有工作区可用`:'尚未配置 Tavily 密钥';$('tavily-key-remove').hidden=result.source!=='file';return result}
 catch{$('tavily-key-status').textContent='暂时无法读取本机配置，请稍后重试。';$('tavily-key-remove').hidden=true}
}
$('search-provider').onchange=async event=>{
 const input=event.target,previous=state.settings.search_provider||'codex';input.disabled=true;renderSearchProvider();$('search-settings-status').textContent='保存中…';
 try{await api('settings',{search_provider:input.value});state.settings.search_provider=input.value;$('search-settings-status').textContent='已保存，用于下一次联网任务。';await refreshTavilySettings()}catch{input.value=previous;renderSearchProvider();$('search-settings-status').textContent='设置未保存，请稍后重试。'}finally{input.disabled=false}
};
async function saveTavilyKey(event){
 event.preventDefault();const input=$('tavily-key'),key=input.value.trim();if(!key){$('search-settings-status').textContent='请先粘贴 Tavily API Key。';return}
 $('tavily-key-save').disabled=true;input.disabled=true;$('search-settings-status').textContent='正在保存本机配置…';
 try{await api('tavily',{api_key:key});$('search-settings-status').textContent='密钥已保存，未发起验证或搜索。';await refreshTavilySettings()}catch{$('search-settings-status').textContent='密钥未能保存，请检查本地服务后重试。'}finally{input.value='';input.disabled=false;$('tavily-key-save').disabled=false}
};
$('tavily-key-remove').onclick=async()=>{
 const button=$('tavily-key-remove');button.disabled=true;$('tavily-key').value='';
 try{await api('tavily',{remove:true});$('search-settings-status').textContent='本机保存的密钥已移除。';await refreshTavilySettings()}catch{$('search-settings-status').textContent='未能移除本机配置，请稍后重试。'}finally{button.disabled=false}
};
$('settings-dialog').addEventListener('close',()=>{$('tavily-key').value='';if(!$('setup').hidden)moveSearchSettings('setup')});
let candidatesPolling=false;
async function refreshCandidates(){
 if(candidatesPolling)return;candidatesPolling=true;
 try{
  const result=await api('learning-candidates'),candidates=result.candidates||[],signature=JSON.stringify(candidates);if(refreshCandidates.signature===signature)return;refreshCandidates.signature=signature;
  const opened=new Set([...$('learning-candidates').querySelectorAll('details[open]')].map(d=>d.dataset.candidateKey));
  const labels={pending_validation:'待验证',validation_running:'正在验证',accepted:'已接受',rejected:'未采用',paused:'验证已暂停',failed:'验证未完成',no_action:'未提出候选'};
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
$('chat-model-provider').oninput=()=>{rememberDraft();updateComposer()};
for(const id of ['chat-model','chat-model-provider','model-select','model-provider'])$(id).addEventListener('keydown',event=>{if(event.key==='Enter'){event.preventDefault();event.stopPropagation();$(id).blur()}});

const LENGTH_PRESETS={compact:[800,1000],balanced:[1500,2000],detailed:[2000,2500]};
function initializeLengthInputs(requirements){
 const preset=LENGTH_PRESETS[$('length-preset').value]||LENGTH_PRESETS.balanced;
 $('target-words').value=requirements.target_words??preset[0];$('max-words').value=requirements.max_words??preset[1];validateLengthInputs();
}
function validateLengthInputs(){
 const target=Number($('target-words').value),maximum=Number($('max-words').value);
 $('max-words').setCustomValidity(Number.isInteger(target)&&target>0&&Number.isInteger(maximum)&&maximum>0&&maximum<target?'字数上限不能小于目标字数。':'');
}
$('length-preset').onchange=()=>{const [target,maximum]=LENGTH_PRESETS[$('length-preset').value];$('target-words').value=target;$('max-words').value=maximum;validateLengthInputs()};
for(const id of ['target-words','max-words'])$(id).oninput=validateLengthInputs;
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
$('session-view').onchange=async event=>{closeSessionMenu();chat.view=event.target.value;chat.sessions=[];renderSessions();await pollChat(true).catch(e=>notice(e.message,true))};
$('archive-completed').onclick=async()=>{
 if(chat.busy)return;closeSessionMenu();const button=$('archive-completed');button.disabled=true;
 try{rememberDraft();const result=await api('harness/archive-completed',{});await pollChat(true);notice(result.count?`已归档 ${result.count} 条已结束对话。`:'没有可归档的已结束对话。')}catch(e){notice(e.message,true)}finally{button.disabled=chat.busy||!chat.sessions.some(s=>!sessionBusy(s))}
};
document.addEventListener('click',event=>{if(!event.target.closest('#session-actions-menu')&&!event.target.closest('[data-manage-session]'))closeSessionMenu()});
document.addEventListener('keydown',event=>{if(event.key==='Escape')closeSessionMenu()});

const RESEARCH_BUDGET_PRESETS={weekly:{search_requests:12,candidate_urls:60,source_pages:18},monthly:{search_requests:30,candidate_urls:200,source_pages:45}};
const BUDGET_FIELDS={search_requests:'budget-search-requests',candidate_urls:'budget-candidate-urls',source_pages:'budget-source-pages'};
function readResearchBudget(){return Object.fromEntries(Object.entries(BUDGET_FIELDS).map(([key,id])=>[key,Number($(id).value)]))}
function reflectBudgetPreset(){const current=readResearchBudget();$('budget-preset').value=Object.keys(RESEARCH_BUDGET_PRESETS).find(name=>Object.keys(BUDGET_FIELDS).every(key=>current[key]===RESEARCH_BUDGET_PRESETS[name][key]))||'custom'}
function initializeResearchBudget(requirements){const budget=requirements.research_budget||RESEARCH_BUDGET_PRESETS.weekly;for(const [key,id] of Object.entries(BUDGET_FIELDS))$(id).value=budget[key]??RESEARCH_BUDGET_PRESETS.weekly[key];reflectBudgetPreset();renderBudgetProviderScope()}
function renderBudgetProviderScope(){$('budget-provider-scope').textContent=$('search-provider').value==='tavily'?'Tavily 搜索请求与候选 URL 可计量；经 BriefLoop 工具获取的全文来源受预算控制。':nativeSearchName()+'搜索的请求数和候选 URL 数不可精确计量；经 BriefLoop 工具获取的全文来源仍受预算控制。'}
$('budget-preset').onchange=()=>{const budget=RESEARCH_BUDGET_PRESETS[$('budget-preset').value];if(budget)for(const [key,id] of Object.entries(BUDGET_FIELDS))$(id).value=budget[key]};
for(const id of Object.values(BUDGET_FIELDS))$(id).oninput=reflectBudgetPreset;

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
  const scope=result.scope||{},native=scope.search_provider!=='tavily',limits=result.limits,used=result.used||{},remaining=result.remaining||{},format=value=>typeof value==='number'?new Intl.NumberFormat('zh-CN').format(value):'未知';
  $('budget-view-title').textContent='研究预算'+(result.exhausted?' · 已达到上限':'');
  const labels={search_requests:'搜索请求',candidate_urls:'候选 URL',source_pages:'全文获取（URL）'};
  $('budget-view-body').innerHTML=`<p class="budget-run-label">${esc(target.label)}</p><table class="budget-usage-table"><thead><tr><th>项目</th><th>已用</th><th>上限</th><th>剩余</th></tr></thead><tbody>${Object.entries(labels).map(([key,label])=>{const unmetered=native&&key!=='source_pages';return `<tr><th>${label}</th><td>${unmetered?'不可精确计量':format(used[key])}</td><td>${format(limits[key])}</td><td>${unmetered?'—':format(remaining[key])}</td></tr>`}).join('')}</tbody></table><p class="help">${native?'Codex 原生搜索的请求数和候选 URL 数不作为精确计量。':''}全文获取按本轮不同 URL 计数，不代表已核验或已阅读数量；已有上传材料不扣。</p>${result.exhausted?'<p class="budget-exhausted">已达到预算上限，保留已有结果与缺口，不自动加额。</p>':''}`;
 }catch{panel.hidden=false;$('budget-view-title').textContent='研究预算';$('budget-view-body').innerHTML='<p class="help">预算信息暂不可用。</p>'}finally{budgetPolling=false}
}

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
 if(industryProfileActive())for(const id of referenceSelected)selected.delete(id);
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
  // 普通简报没有参考材料这个概念，之前标为参考的材料要回到本期材料里。
  for(const id of referenceSelected){if((state.sources||[]).some(s=>s.id===id&&s.status!=='failed')){selected.add(id);const evidence=$('source-list').querySelector(`[data-check="${CSS.escape(id)}"]`);if(evidence)evidence.checked=true}}
 }
 $('industry-profile-options').hidden=!industryProfileActive();$('length-preset').disabled=industryProfileActive();$('length-preset').closest('label').hidden=industryProfileActive();validateLengthInputs();
};
$('industry-task-outline').onclick=()=>{const field=$('requirements').elements.objective;if(!field.value.includes(INDUSTRY_TASK_OUTLINE))field.value=(field.value.trim()?field.value.trim()+'\n\n':'')+INDUSTRY_TASK_OUTLINE;field.focus()};

function renderReportDataButton(){
 const run=current&&state?.runs.find(item=>item.id===current.run_id);
 $('report-data-control').hidden=!run||parse(run.requirements).report_profile!=='industry_periodic';
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
 box.innerHTML=jobs.slice(0,6).map(j=>{const result=parse(j.result),payload=parse(j.payload);const url=j.kind==='release'?'/api/release-file?id='+encodeURIComponent(payload.release_id):j.kind==='audit_bundle'?'/api/audit-file?job='+encodeURIComponent(j.id):result.download_url;return `<div class="job"><span>${fileKinds[j.kind]} · ${j.status==='complete'?'已制作':statuses[j.status]||esc(j.status)}</span><small>${payload.version_id===current?.id?'当前稿件版本':'历史稿件版本'}</small>${j.status==='complete'&&url?`<a href="${esc(url)}" download>下载${fileKinds[j.kind]}</a>`:`<span>${esc(j.error||'使用提交时固定的版本，可继续编辑')}</span>`}</div>`}).join('');
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
 select.innerHTML='<option value="">通用模板</option>'+(state.templates||[]).filter(t=>t.status==='ready').map(t=>`<option value="${esc(t.id)}">${esc(t.name)} · v${t.revision}</option>`).join('')
  +(blocked?`<option value="${esc(blocked.id)}">${esc(blocked.name)} · 尚未就绪</option>`:'');
 select.value=chosen;
 $('template-status').textContent=[(state.templates||[]).filter(t=>t.status!=='ready').map(t=>t.name+'：'+(t.error||'模板准备中，可在任务列表查看或恢复')).join('；'),
  blocked?'当前选中的模板尚未就绪，请改用通用模板或等它准备完成。':''].filter(Boolean).join(' ');
 templateSections();
 applyTemplateSectionEdits();
}
$('template-select').onchange=()=>{templateSections();applyTemplateSectionEdits()};
$('template-import-button').onclick=()=>$('template-file').click();
$('template-file').onchange=e=>action(async()=>{const file=e.target.files[0];if(!file)return;const bytes=new Uint8Array(await file.arrayBuffer());let raw='';for(let i=0;i<bytes.length;i+=8192)raw+=String.fromCharCode(...bytes.subarray(i,i+8192));await api('template-import',{name:file.name,data:btoa(raw)});e.target.value='';notice('模板已上传，BriefLoop 将准备章节和版式，完成后可在我的模板中选择')});

$('company-mode').onchange=e=>action(()=>api('settings',{company_context_enabled:e.target.value==='ask'?null:e.target.value==='on'}));
$('company-open').onclick=()=>action(async()=>{const data=await api('company-context');resetSourceMedia();$('source-title').textContent='企业背景知识库';$('source-original').hidden=true;$('source-provenance').hidden=true;$('source-link').textContent='';$('source-body').textContent=data.facts.map(f=>f.fact_key+'\n'+f.value+'\n截至 '+f.effective_date+' · '+f.source_id+(f.locator?' · '+f.locator:'')).join('\n\n')||'尚未保存企业背景。企业内部周报开始前，BriefLoop 可以提议维护。';for(const pending of data.pending){const row=document.createElement('div');row.textContent='待确认：'+pending.fact_key+' — '+pending.value;for(const [label,accept] of [['采用新资料',true],['保留原记录',false]]){const button=document.createElement('button');button.textContent=label;button.onclick=()=>action(async()=>{await api('company-resolve',{fact_id:pending.id,accept});row.textContent='已处理'});row.append(button)}$('source-body').append(row)}$('source-dialog').showModal()});
$('auto-revision').onchange=e=>action(()=>api('settings',{auto_revision:e.target.checked}));

function updateFormattingTools(){if(!editor||editor.isDestroyed)return;document.querySelectorAll('[data-table-command]').forEach(b=>b.hidden=!editor.isActive('table'));document.querySelectorAll('[data-image-command]').forEach(b=>b.hidden=!editor.isActive('image'));$('cell-color').closest('label').hidden=!editor.isActive('table');for(const name of ['bold','italic'])$('toolbar').querySelector('[data-command='+name+']')?.setAttribute('aria-pressed',String(editor.isActive(name)))}

$('word-import-button').onclick=()=>$('word-import-file').click();
$('word-import-file').onchange=e=>action(async()=>{const file=e.target.files[0];if(!file)return;const version=await savedVersion();const bytes=new Uint8Array(await file.arrayBuffer());let raw='';for(let i=0;i<bytes.length;i+=8192)raw+=String.fromCharCode(...bytes.subarray(i,i+8192));const result=await api('import-revision',{base_version:version,name:file.name,data:btoa(raw)});e.target.value='';if(result.status==='imported'){await refresh();openBrief(result.version);notice('Word 修订已保存，原稿保留')}else{resetSourceMedia();$('source-title').textContent='Word 修订待对齐';$('source-original').hidden=true;$('source-provenance').hidden=true;$('source-link').textContent='';$('source-body').textContent=result.message+'\n\n'+result.extracted_markdown;$('source-dialog').showModal();notice('原文件已保留，可请 BriefLoop 协助对齐到当前报告',true)}});

$('template-default').onclick=()=>action(()=>api('settings',{default_template_id:$('template-select').value||null}),'已保存工作区默认模板');

let savedProviderConfigurations=[];
$('provider-open').onclick=async()=>{$('provider-result').textContent='';$('custom-supports-images').value='';$('provider-use').hidden=true;settingsView('models');settingsModelTab('api');
 try{const r=await api('opencode/providers');savedProviderConfigurations=r.configurations||[];
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
$('provider-close').onclick=()=>{$('custom-api-key').value='';settingsModelTab('cli')};
$('provider-dialog').addEventListener('close',()=>{$('custom-api-key').value=''});
$('provider-form').onsubmit=async event=>{
 event.preventDefault();const button=$('provider-save');button.disabled=true;$('provider-result').textContent='正在保存到 Opencode…';
 const body={protocol:$('custom-protocol').value,name:$('custom-name').value.trim(),context_limit:$('custom-context-limit').value?Number($('custom-context-limit').value):null,output_limit:$('custom-output-limit').value?Number($('custom-output-limit').value):null,provider:$('custom-provider').value.trim(),base_url:$('custom-base-url').value.trim(),model:$('custom-model').value.trim(),api_key:$('custom-api-key').value,supports_images:$('custom-supports-images').value===''?null:$('custom-supports-images').value==='true'};
 $('custom-api-key').value='';
 try{
  const result=await api('opencode/provider',body);body.api_key='';
  $('provider-use').hidden=false;$('provider-use').dataset.model=result.model;
  $('provider-result').textContent='已保存 '+result.model+'。当前模型选择保持原值。';
 await loadProviderCatalog();
 }catch(e){$('provider-result').textContent=e.message}finally{body.api_key='';button.disabled=false}
};

let providerCatalogRequest=0;
async function loadProviderCatalog(){
 const provider=$('custom-provider').value.trim();if(!provider)return;
 const request=++providerCatalogRequest;$('provider-model-options').innerHTML='';
 $('provider-result').textContent='正在读取模型目录…';
 try{
  const r=await api('opencode/provider-catalog',{provider});
  if(request!==providerCatalogRequest||provider!==$('custom-provider').value.trim())return;
  $('provider-model-options').innerHTML=(r.models||[]).map(id=>`<option value="${esc(id)}"></option>`).join('');
  const labels={reachable:'目录已更新',auth_failed:'认证失败',insufficient_balance:'余额不足',forbidden:'无权访问',catalog_unavailable:'目录接口不可用，可手填模型',rate_limited:'请求限流',upstream_unavailable:'服务商暂不可用',connection_failed:'连接失败，可手填模型',invalid_catalog:'响应不是可识别的模型目录',http_error:'接口返回错误'};
  $('provider-result').textContent=(labels[r.status]||r.status)+' · '+(r.models||[]).length+' 个模型。未调用模型。';
 }catch(e){if(request===providerCatalogRequest)$('provider-result').textContent='目录读取失败：'+e.message+'；可手动输入模型 ID。'}
}
$('provider-catalog').onclick=loadProviderCatalog;
$('provider-test-model').onclick=()=>action(async()=>{
 $('provider-result').textContent='正在提交短工具调用…';
 const r=await api('opencode/provider-test',{provider:$('custom-provider').value.trim(),model:$('custom-model').value.trim()});
 await selectChat(r.session_id);
},'已提交模型工具测试；进展见对话，可随时停止');
$('provider-use').onclick=()=>action(async()=>{
 const model=$('provider-use').dataset.model;
 $('agent-backend').value='opencode';$('model-select').value=model;
 await saveModel();await refresh();renderBackend();await refreshModelSuggestions(true);
 if(!chatActive()){$('chat-model').value=model;rememberDraft();updateComposer()}
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
 if(!Number.isInteger(minutes)||minutes<1||minutes>240)throw Error('执行时限请输入 1–240 的整数');
 await api('settings',{timeout_minutes:minutes});
},'执行时限已更新，当前报告也会采用新上限');
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
 $('review-list').innerHTML=(data.conflicts||[]).filter(c=>c.status!=='resolved').map(c=>`<article class="evidence-card"><span class="tag error">来源分歧 · ${esc(states[c.status]||c.status)}</span><p>${esc(c.data.description)}</p><p>已提醒不等于已解决，需由独立Reviewer核对双方依据。</p></article>`).join('')+(data.reviews||[]).map(r=>reviewResultHTML(r,states,data.requirements||parse(state.runs.find(run=>run.id===current?.run_id)?.requirements))).join('')+(data.reviews.length?'':'<p>当前版本尚未审阅，不能视为已通过。</p>')+data.findings.map(f=>`<article class="evidence-card"><span class="tag">${esc(states[f.status]||f.status)} · ${f.data.severity==='major'?'重要问题':'一般问题'}</span><h3>${esc(f.data.description)}</h3><blockquote>${esc(f.data.report_quote)}</blockquote><p>依据：${esc(f.data.evidence)}</p><p>${esc(f.data.suggested_action)}</p><p class="help">目标版本：${esc(f.version_id)}</p>${['open','addressed_pending_review'].includes(f.status)?`<form data-finding-response="${f.id}"><select name="action"><option value="corrected">已修改当前稿</option><option value="removed">已移除相关主张</option><option value="disagree">提出有依据的异议</option></select><textarea name="reason" required placeholder="说明修改位置或异议依据"></textarea><button type="submit">提交处理说明，等待复核</button></form>`:''}</article>`).join('');
 $('review-list').querySelectorAll('[data-finding-response]').forEach(form=>form.onsubmit=e=>{e.preventDefault();action(async()=>{const target=await savedVersion();await api('review-response',{finding_id:form.dataset.findingResponse,version_id:target,action:form.elements.action.value,reason:form.elements.reason.value});$('review-dialog').close()},'处理说明已保存；只有独立复核才能关闭问题')});
 $('review-dialog').showModal();
});

$('review-revise').onclick=()=>action(async()=>{const version=await savedVersion();if(version)await api('revise-findings',{version_id:version})},'已安排一次针对性修订及独立复核');

function premiseCards(premises){return premises.map(p=>`<details><summary>间接依据／前提：${esc(p.claim?.data.statement||p.claim_id)}${p.status==='unreviewed'?'':' · 依据需复核'}</summary>${(p.evidence||[]).map(e=>`<p>${esc(e.source_name)} · ${esc(evidenceLocation(e.data.locator))}</p><blockquote>${esc(e.data.excerpt)}</blockquote><button type="button" data-evidence-source="${esc(e.source_id)}">查看原始来源</button>`).join('')}${premiseCards(p.premises||[])}</details>`).join('')}

// Formal delivery is an explicit action over a saved version; draft export stays available.
let releaseView={version:null,data:null,loading:false},auditTarget=null;
const releaseStatus={pending:'等待制作',released:'正式件已保存',failed:'制作未完成',cancelled:'已停止'};
const changeTypeLabel={initial:'首次交付',correction:'更正',update:'后续信息更新'};
const displayDate=value=>value?new Date(value).toLocaleString():'时间未记录';
function releaseEligibilityHTML(eligibility){
 if(!eligibility)return '<p>交付条件暂不可用，尚未判定通过。</p>';
 const blockers=eligibility.blockers||[],notices=eligibility.notices||[];
 return `<h3>${eligibility.eligible?'当前版本满足正式交付条件':'当前版本还有需处理事项'}</h3>${blockers.length?`<ul class="release-blockers">${blockers.map(item=>`<li>${esc(item.message||item)}</li>`).join('')}</ul>`:''}${notices.length?`<details open><summary>保留的提示 · ${notices.length} 项</summary><ul>${notices.map(item=>`<li>${esc(item.message||item)}</li>`).join('')}</ul></details>`:''}${eligibility.eligible?'':'<p class="help">工作稿仍可编辑和下载。请在“审阅与需处理”中处理问题后复核。</p>'}`;
}
function releaseCardHTML(release){
 const job=state.jobs.find(j=>j.id===release.job_id),status=release.status==='released'?releaseStatus.released:statuses[job?.status]||releaseStatus[release.status]||release.status;
 const change=changeTypeLabel[release.change_type]||(release.previous_id?'关联旧正式件':'首次交付');
 return `<article class="release-card"><div class="section-title"><strong>${esc(change)} · ${esc(displayDate(release.created))}</strong><span class="tag">${esc(status)}</span></div><p class="help">${release.version_id===current?.id?'当前正在查看的稿件版本':'历史稿件版本'}${release.previous_id?' · 关联旧正式件 '+esc(release.previous_id.slice(-8)):''}</p>${release.change_reason?`<p>${esc(release.change_reason)}</p>`:''}${job?.error?`<p class="error">${esc(job.error)}</p>`:''}${release.status==='released'?`<div class="release-actions"><a href="/api/release-file?id=${encodeURIComponent(release.id)}" download>下载正式 Word</a><button type="button" data-audit-release="${esc(release.id)}">导出审计包…</button></div>`:'<p class="help">任务进度见报告下方的文件制作记录。</p>'}</article>`;
}
async function refreshReleaseState(){
 if(releaseView.loading||!$('release-dialog').open)return;
 const version=current?.id;if(!version)return;
 releaseView.loading=true;
 try{
  const data=await api('release-state?version='+encodeURIComponent(version));
  if(!$('release-dialog').open||current?.id!==version)return;
  releaseView.version=version;releaseView.data=data;
  $('release-eligibility').innerHTML=(dirty?'<p class="help">有未保存修改，下列条件针对上次保存的版本。</p>':'')+releaseEligibilityHTML(data.eligibility);
  $('release-submit').disabled=dirty||saving||!data.eligibility?.eligible||releaseView.submitting;
  const released=(data.releases||[]).filter(r=>r.status==='released');
  const previous=$('release-previous'),choices=released.map(r=>r.id).join(',');
  if(previous.dataset.choices!==choices){const chosen=previous.value;previous.innerHTML='<option value="">首次交付</option>'+released.map(r=>`<option value="${esc(r.id)}">${esc(displayDate(r.created))} · ${esc(changeTypeLabel[r.change_type]||'正式件')} · ${esc(r.id.slice(-8))}</option>`).join('');previous.dataset.choices=choices;if(released.some(r=>r.id===chosen))previous.value=chosen;updateReleaseChangeFields()}
  const html=(data.releases||[]).map(releaseCardHTML).join('')||'<p class="help">此报告尚无正式交付记录。</p>';
  if($('release-list').innerHTML!==html){$('release-list').innerHTML=html;$('release-list').querySelectorAll('[data-audit-release]').forEach(button=>button.onclick=()=>openAuditBundle(button.dataset.auditRelease))}
 }finally{releaseView.loading=false}
}
function updateReleaseChangeFields(){const linked=!!$('release-previous').value;$('release-change-fields').hidden=!linked;$('release-change-reason').required=linked}
$('release-previous').onchange=updateReleaseChangeFields;
$('release-open').onclick=()=>action(async()=>{await savedVersion();$('release-eligibility').textContent='正在核对当前版本的交付条件…';$('release-submit').disabled=true;$('release-dialog').showModal();await refreshReleaseState()});
$('release-close').onclick=()=>$('release-dialog').close();
async function submitFormalRelease(){
 const previous=$('release-previous').value,changeType=$('release-change-type').value,reason=$('release-change-reason').value.trim();
 if(previous&&!reason)throw Error('请说明本次更正或更新的依据和影响');
 const version=await savedVersion();
 const payload={version_id:version};if(previous)Object.assign(payload,{previous_id:previous,change_type:changeType,change_reason:reason});
 const result=await api('release',payload);
 return result;
}
$('release-form').onsubmit=event=>{event.preventDefault();if(releaseView.submitting)return;releaseView.submitting=true;$('release-submit').disabled=true;action(async()=>{try{await submitFormalRelease();$('release-dialog').close();notice('正式 Word 已排队，使用本次提交时固定的版本')}finally{releaseView.submitting=false;await refreshReleaseState()}})};
function openAuditBundle(releaseId){
 const release=releaseView.data?.releases.find(r=>r.id===releaseId);if(!release||release.status!=='released'){notice('请先等待正式件制作完成',true);return}
 auditTarget=release;
 $('audit-target').textContent='正式件：'+displayDate(release.created)+' · '+(changeTypeLabel[release.change_type]||'首次交付')+' · '+release.id.slice(-8);
 $('audit-source-list').innerHTML=(release.sources||release.data?.snapshot?.sources||[]).map(source=>`<label class="audit-source-row"><span>${esc(source.name||source.id)}</span><select data-audit-source="${esc(source.id)}" aria-label="${esc(source.name||source.id)}的打包范围"><option value="metadata">仅定位，不含原件和摘录</option><option value="excerpt">定位与证据摘录</option><option value="original">原件及证据摘录</option></select></label>`).join('')||'<p class="help">此正式件没有登记来源文件。</p>';
 $('audit-submit').disabled=false;$('audit-dialog').showModal();
}
$('audit-close').onclick=()=>$('audit-dialog').close();
$('audit-all-original').onclick=()=>{$('audit-source-list').querySelectorAll('[data-audit-source]').forEach(select=>select.value='original')};
$('audit-all-metadata').onclick=()=>{$('audit-source-list').querySelectorAll('[data-audit-source]').forEach(select=>select.value='metadata')};
async function submitAuditBundle(){
 if(!auditTarget)throw Error('请先选择一个已保存的正式件');
 const releaseId=auditTarget.id,permissions=Object.fromEntries([...$('audit-source-list').querySelectorAll('[data-audit-source]')].map(select=>[select.dataset.auditSource,select.value]));
 return api('audit-bundle',{release_id:releaseId,source_permissions:permissions});
}
$('audit-form').onsubmit=event=>{event.preventDefault();if($('audit-submit').disabled)return;$('audit-submit').disabled=true;action(async()=>{try{await submitAuditBundle();$('audit-dialog').close();$('release-dialog').close();notice('审计包已排队，完成后可在文件制作记录下载')}finally{$('audit-submit').disabled=false}})};
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

function reviewResultHTML(review,states,requirements={}){
 const result=review.result||{},items=requirements.requirement_items||[],labels={covered:'已回答',manual:'用户安排人工填写',partial:'部分完成',missing:'未完成'};
 const unchecked=[...(result.unchecked_items||[]),...(result.unchecked||[]).map(description=>({description,importance:'unknown'}))];
 return `<article class="review-version"><strong>${esc(states[review.status]||review.status)}</strong><p>${esc(result.summary||'当前没有完整审阅结果')}</p><p class="help">${result.coverage_scan_complete?'已检查正文是否遗漏重要主张绑定':'重要主张覆盖尚未完成检查'}；正式交付另按当前版本的条件判断。</p>${unchecked.length?`<details class="review-checks" open><summary>尚未核验 · ${unchecked.length} 项</summary><ul>${unchecked.map(item=>`<li><span class="tag">${item.importance==='core'?'核心事项':item.importance==='supporting'?'非核心事项':'重要性未确定'}</span> ${esc(item.description)}</li>`).join('')}</ul></details>`:''}${result.requirement_checks?.length?`<details class="review-checks" open><summary>本轮要求落实情况</summary><ul>${result.requirement_checks.map(item=>`<li><strong>${esc(labels[item.status]||item.status)}</strong> · ${esc(items.find(req=>req.requirement_id===item.requirement_id)?.text||item.requirement_id)}<br>${esc(item.reason)}</li>`).join('')}</ul></details>`:'<p class="help">尚无逐项要求核查结果。</p>'}</article>`;
}

$('source-refresh-form').onsubmit=event=>{event.preventDefault();action(async()=>{
 const source=$('source-refresh-source').value,cutoff=$('source-refresh-cutoff').value;
 if(!source||!cutoff)throw Error('请选择来源与本轮信息截止时间');
 const version=await savedVersion();await api('source-refresh',{version_id:version,source_id:source,information_cutoff:new Date(cutoff).toISOString()});
 $('source-updates-dialog').close();notice('来源复查已排队，可在任务记录查看结果');
})};

function sourceRefreshOutcome(outcome){return {not_authorized:'本轮未允许联网，未执行在线复查',local_source_requires_upload:'本地来源更新需上传独立的新文件',budget_exhausted:'本轮预算已用尽，未获取新快照',fetch_failed:'新快照读取未成功，保留原来源',unchanged_snapshot:'实际取得的快照未变化',changed_needs_review:'取得的快照有变化，待判断影响并独立复核'}[outcome]||''}

function settingsView(name){
 for(const view of ['models','execution','learning'])$('settings-view-'+view).hidden=view!==name;
 document.querySelectorAll('[data-settings-view]').forEach(b=>{b.classList.toggle('active',b.dataset.settingsView===name);b.setAttribute('aria-current',b.dataset.settingsView===name?'page':'false')});
}
function settingsModelTab(name){
 $('settings-cli').hidden=name!=='cli';$('settings-api').hidden=name!=='api';
 for(const tab of ['cli','api'])$('settings-tab-'+tab).setAttribute('aria-selected',String(tab===name));
}
{
 document.querySelector('main').append($('settings-dialog'));
 $('settings-api').append($('provider-dialog'));
 document.querySelectorAll('[data-settings-view]').forEach(b=>b.onclick=()=>settingsView(b.dataset.settingsView));
 $('settings-tab-cli').onclick=()=>settingsModelTab('cli');
 $('settings-tab-api').onclick=()=>$('provider-open').click();
 $('agent-backend').closest('label').classList.add('runtime-select-legacy');
 document.querySelector('.model-settings legend').textContent='当前模型与角色';
}
