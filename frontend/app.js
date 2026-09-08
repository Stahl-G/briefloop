import {Editor} from '@tiptap/core';
import StarterKit from '@tiptap/starter-kit';
import {TableKit} from '@tiptap/extension-table';
import {Markdown} from '@tiptap/markdown';
const $=id=>document.getElementById(id),esc=s=>String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c])),parse=s=>JSON.parse(s||'{}');
let token='',state,current,pendingRun=null,editor,dirty=false,saving=false,saveTimer,learnTimer,markdownMode=false,selected=new Set();
function notice(s,error=false){$('notice').textContent=s;$('notice').classList.toggle('error',error);$('notice').hidden=false;clearTimeout(notice.timer);notice.timer=setTimeout(()=>$('notice').hidden=true,error?12000:4500)}
async function api(path,data,retried=false){const r=await fetch('/api/'+path,data===undefined?{}:{method:'POST',headers:{'Content-Type':'application/json','X-BriefLoop-Token':token},body:JSON.stringify(data)});const b=await r.json();if(r.status===403&&data!==undefined&&!retried){token=(await api('session')).token;return api(path,data,true)}if(!r.ok)throw Error(b.error||'操作失败');return b}
function page(name){for(const id of ['chat','report','setup','learning'])$(id).hidden=id!==name;document.querySelectorAll('nav [data-page]').forEach(b=>b.classList.toggle('active',b.dataset.page===name));if(name==='learning')refreshCandidates();if(name==='setup')moveSearchSettings('setup');else if($('tavily-key'))$('tavily-key').value=''}
document.querySelectorAll('[data-page]').forEach(b=>b.onclick=()=>page(b.dataset.page));
async function action(fn,message){try{await fn();if(message)notice(message);await refresh()}catch(e){notice(e.message,true)}}
async function refresh(first=false){try{const next=await api('state');$('connection').textContent='本地已连接';const signature=JSON.stringify(next);state=next;if(first||signature!==refresh.signature){refresh.signature=signature;render(first)}await refreshProgress();if(first||!$('learning').hidden)await refreshCandidates()}catch(e){$('connection').textContent='连接中断';if(first)notice(e.message,true)}}
const toEditor=md=>md.replace(/\\?\[@(src\\?_[a-zA-Z0-9]+)\\?\]/g,(_,raw)=>{const id=raw.replaceAll('\\','');return `[${(state.sources.findIndex(s=>s.id===id)+1)||'?'}](#source-${id})`});
const fromEditor=md=>md.replace(/\[([^\]]+)\]\(#source-(src_[a-zA-Z0-9]+)\)/g,(_,label,id)=>`[@${id}]`);
const statuses={queued:'等待运行',running:'正在运行',complete:'已完成',failed:'未完成',interrupted:'已中断',cancelled:'已停止'};
function render(first){
 if(first){state.sources.forEach(s=>selected.add(s.id));$('rounds').value=state.settings.k;$('auto-learn').checked=state.settings.auto_learn;$('model-select').value=state.settings.model||'gpt-5.6-luna';assignEffort('effort-select',effortValue(state.settings,'reasoning_effort'));$('model-provider').value=state.settings.model_provider||'';updateModelLabel();renderRoleModels();$('search-provider').value=state.settings.search_provider||'codex';renderSearchProvider();if(state.requirements)for(const [k,v] of Object.entries(state.requirements)){const e=$('requirements').elements[k];if(e)e.type==='checkbox'?e.checked=v:e.value=v}initializeLengthInputs(state.requirements||{})}
 $('source-count').textContent=state.sources.length+' 份';$('source-list').innerHTML=state.sources.map(s=>`<div class="source-item"><input type="checkbox" data-check="${s.id}" ${selected.has(s.id)?'checked':''} aria-label="选择 ${esc(s.name)}"><button data-source="${s.id}">${esc(s.name)}</button><span class="tag ${s.status==='failed'?'error':''}">${s.status==='failed'?'读取失败':'可读取'}</span>${s.status==='failed'?`<button data-retry-source="${s.id}">重试</button>`:''}</div>`).join('');
 document.querySelectorAll('[data-retry-source]').forEach(b=>b.onclick=()=>action(async()=>{const s=await api('retry-source',{source_id:b.dataset.retrySource});selected.delete(b.dataset.retrySource);selected.add(s.id);notice(s.status==='ready'?'来源已重新读取':s.error,s.status!=='ready')}));
 document.querySelectorAll('[data-check]').forEach(b=>b.onchange=()=>b.checked?selected.add(b.dataset.check):selected.delete(b.dataset.check));
 const visible=[];
 for(const runId of [...new Set(state.briefs.map(b=>b.run_id))]){
  const versions=state.briefs.filter(b=>b.run_id===runId),latest=versions[0],original=[...versions].reverse().find(b=>b.author==='agent');
  if(latest)visible.push({brief:latest,label:latest.author==='user'?'当前编辑稿':'生成原稿'});
  if(original&&original.id!==latest?.id)visible.push({brief:original,label:'生成原稿'});
 }
 if(current&&!visible.some(v=>v.brief.id===current.id))visible.push({brief:current,label:'正在查看历史快照'});
 $('version-select').innerHTML=visible.map(({brief:b,label})=>`<option value="${b.id}">${esc(parse(b.detail).title||'简报')} · ${label}</option>`).join('');
 if($('version-history'))$('version-history').textContent='编辑历史'+(current?'（'+state.briefs.filter(b=>b.run_id===current.run_id&&b.author==='user').length+'）':'');

 const incoming=pendingRun&&state.briefs.find(b=>b.run_id===pendingRun);if(incoming&&!dirty){openBrief(incoming);pendingRun=null}if(!current&&state.briefs.length)openBrief(state.briefs[0]);if(current){$('version-select').value=current.id;assessment();citations();renderBriefLength()}
 $('empty').hidden=!!current||state.jobs.length>0;$('document-area').hidden=!current;
 $('jobs').innerHTML=state.jobs.map(j=>`<div class="job"><span class="tag ${j.status==='failed'?'error':''}">${statuses[j.status]}</span><div class="job-main">${{generate:'生成简报',assess:'重新评分',learn:'WikiSkill 学习'}[j.kind]}<small>${parse(j.payload).runtime?esc(modelLabel(parse(j.payload).runtime)):'旧任务：沿用当时本机配置'} · ${j.progress?`第 ${j.progress.round}/${j.progress.k} 轮 · ${{maintainer:'整理经验',proposer:'提出候选',validation:'验证候选'}[j.progress.phase]||j.progress.phase} · `:''}${esc(j.error||new Date(j.created).toLocaleString())}</small></div>${j.kind==='learn'?`<button data-details="${j.id}">查看比较</button>`:''}${['queued','running'].includes(j.status)?`<button data-stop="${j.id}">停止</button>`:''}${['failed','interrupted','cancelled'].includes(j.status)?`<button data-resume="${j.id}">恢复</button>`:''}</div>`).join('');
 document.querySelectorAll('[data-stop]').forEach(b=>b.onclick=()=>action(()=>api('stop',{job_id:b.dataset.stop})));document.querySelectorAll('[data-resume]').forEach(b=>b.onclick=()=>action(()=>api('resume',{job_id:b.dataset.resume})));
 document.querySelectorAll('[data-details]').forEach(b=>b.onclick=()=>action(async()=>{const d=await api('learning-details?job='+b.dataset.details);$('source-title').textContent='技能比较与依据';$('source-original').hidden=true;$('source-provenance').hidden=true;$('source-link').textContent='';$('source-body').textContent=d.rounds.length?d.rounds.map((r,i)=>`第 ${i+1} 轮\n${r.result?.reason||'比较尚未完成'}\n${(r.result?.pairs||[]).map(p=>({better:'候选更好',tie:'差不多，保留原技能',worse:'原稿更好'}[p.verdict])+': '+p.reason).join('\n')}\n\n`+r.cases.map(c=>`任务：${c.requirements.title}\n\n旧版\n${gradeSummary(c.baseline.assessment)}\n${c.baseline.reader_markdown||c.baseline.markdown}\n\n候选\n${gradeSummary(c.candidate.assessment)}\n${c.candidate.reader_markdown||c.candidate.markdown}`).join('\n\n')).join('\n\n'):d.job.error||'比较尚未开始；先整理 Wiki 和提出候选。';$('source-dialog').showModal()}));
 $('skills').innerHTML=`<div class="skill">${state.active_skill?'当前启用 '+esc(state.active_skill):'当前使用基础任务提示词'}${state.active_skill?'<button data-rollback="">回到基础版本</button>':''}</div>`+state.skills.map(s=>`<div class="skill"><strong>${esc(s.id)}</strong><p>${esc(s.reason)}</p>${s.id===state.active_skill?'<span class="tag">正在使用</span>':`<button data-rollback="${s.id}" class="outline">使用这个版本</button>`}</div>`).join('');document.querySelectorAll('[data-rollback]').forEach(b=>b.onclick=()=>action(()=>api('rollback',{skill_id:b.dataset.rollback||null}),'下一轮将使用所选技能'));
 if(state.wiki!==render.wiki){render.wiki=state.wiki;if(state.wiki)api('render',{markdown:state.wiki}).then(r=>$('wiki').innerHTML=r.html);else $('wiki').innerHTML='<h2>还没有学习经验</h2><p class="muted">生成简报后直接改稿，或留下评论。Maintainer 会在这里整理观察、方法与适用条件。</p>'}bindSources();
}
function openBrief(b){if(dirty){notice('请先保存当前修改，再切换版本',true);return}current=b;$('report-title').textContent=parse(b.detail).title||'简报';$('download').href='/api/download?version='+b.id;$('download-docx').href='/api/download?format=docx&version='+b.id;if(editor)editor.destroy();editor=new Editor({element:$('editor'),editable:state.briefs.find(x=>x.run_id===b.run_id)?.id===b.id,extensions:[StarterKit.configure({link:{openOnClick:false}}),TableKit,Markdown],content:toEditor(b.markdown),contentType:'markdown',onUpdate:changed});$('markdown-source').value=b.markdown;const historical=state.briefs.find(x=>x.run_id===b.run_id)?.id!==b.id;$('markdown-source').readOnly=historical;$('toolbar').querySelectorAll('button').forEach(x=>x.disabled=historical);$('save-state').textContent=historical?'历史记录（只读）':b.author==='user'?'当前编辑稿已自动保存':'原稿已保存';$('version-select').value=b.id;assessment();citations();renderBriefLength()}
function changed(){dirty=true;renderBriefLength();$('save-state').textContent='有未保存修改';clearTimeout(saveTimer);saveTimer=setTimeout(save,1400)}
$('version-select').onchange=e=>openBrief(state.briefs.find(b=>b.id===e.target.value));
async function save(){if(!dirty||saving||!current)return;saving=true;$('save-state').textContent='保存中…';const text=markdownMode?$('markdown-source').value:fromEditor(editor.getMarkdown());try{current=await api('save',{base_version:current.id,markdown:text,editor_document:markdownMode?null:editor.getJSON()});dirty=(markdownMode?$('markdown-source').value:fromEditor(editor.getMarkdown()))!==text;$('save-state').textContent=dirty?'有新的修改':'已保存';$('download').href='/api/download?version='+current.id;$('download-docx').href='/api/download?format=docx&version='+current.id;await refresh();if(dirty)saveTimer=setTimeout(save,1400);else scheduleLearning()}catch(e){$('save-state').textContent='未保存，请保留编辑';notice(e.message,true)}finally{saving=false}}
function scheduleLearning(){ /* Worker consumes the durable feedback after inactivity. */ }
function assessment(){if(!current)return;const r=state.assessments.find(a=>a.version_id===current.id);if(!r){$('assessment').innerHTML='<p class="muted">尚未评分</p><p class="help">你可以先阅读和修改。评分针对这个版本独立运行。</p>';return}const d=parse(r.data);$('assessment').innerHTML=`<div class="judgment">${esc(d.overall)}</div><p>${esc(d.summary)}</p><div class="grades">${[['evidence','证据与准确性'],['coverage','覆盖与取舍'],['analysis','分析有效性'],['expression','表达与可用性']].map(([k,l])=>`<div class="grade"><span>${l}</span><strong>${d[k]??'—'}</strong><small>${d[k]?' / 5':''}</small></div>`).join('')}</div><p class="help">等级是本轮要求完成程度，评分可有不同意见。</p>${(d.findings||[]).map(f=>`<details class="finding"><summary>${f.severity==='major'?'●':'○'} ${esc(f.description)}</summary>${f.report_quote?`<blockquote>${esc(f.report_quote)}</blockquote>`:''}<p>${esc(f.requirement)}</p><p>${esc(f.evidence)}</p>${f.source_id?`<button data-source="${esc(f.source_id)}">查看来源 · ${esc(f.locator)}</button>`:''}<p>${esc(f.suggestion)}</p></details>`).join('')}`;bindSources()}
function citations(){const refs=(parse(current.detail).citations||[]).filter(r=>toEditor(current.markdown).includes('#source-'+r.source_id));$('citations').innerHTML=refs.length?'引用来源 '+refs.map(r=>`<button data-source="${esc(r.source_id)}">${esc(state.sources.find(s=>s.id===r.source_id)?.name||r.source_id)} · ${esc(r.locator)}</button>`).join(''):'尚无引用记录';bindSources()}
function bindSources(){document.querySelectorAll('[data-source]').forEach(b=>b.onclick=()=>action(async()=>{const r=await api('source?id='+b.dataset.source);showSource(r)}))}
$('close-source').onclick=()=>$('source-dialog').close();
$('requirements').onsubmit=e=>{e.preventDefault();action(async()=>{const f=new FormData(e.target),req=Object.fromEntries(f.entries());req.allow_web=f.has('allow_web');req.target_words=Number(req.target_words);req.max_words=Number(req.max_words);req.raw_input=req.objective;delete req.runtime_model;delete req.runtime_effort;await saveModel();await save();if(dirty)throw Error('请先保存当前修改');const job=await api('generate',{requirements:req,source_ids:[...selected]});pendingRun=parse(job.payload).run_id;page('report')},'任务已排队，后台会生成简报')};
$('upload').onchange=e=>action(async()=>{for(const f of e.target.files){const buf=new Uint8Array(await f.arrayBuffer());let b='';for(let i=0;i<buf.length;i+=8192)b+=String.fromCharCode(...buf.subarray(i,i+8192));const s=await api('upload',{name:f.name,data:btoa(b)});selected.add(s.id)}e.target.value=''},'来源已保存');
$('add-url').onclick=()=>action(async()=>{const s=await api('source-url',{url:$('source-url').value});selected.add(s.id);$('source-url').value='';notice(s.status==='ready'?'网页已读取':'来源已保存，但读取失败：'+s.error,s.status!=='ready')});
$('rescore').onclick=()=>action(async()=>{await save();if(dirty)throw Error('请先完成保存');await api('assess',{version_id:current.id})},'已提交评分');
$('comment-submit').onclick=()=>action(async()=>{await api('comment',{version_id:current.id,text:$('comment').value});$('comment').value='';scheduleLearning()},'反馈已保存');
$('learn-now').onclick=()=>action(async()=>{await save();if(dirty)throw Error('请先完成保存');clearTimeout(learnTimer);const result=await api('learn',{});notice(result.message||'已提交反馈学习')});
async function setK(v){const k=Math.max(1,Math.min(20,Number(v)||1));$('rounds').value=k;await action(()=>api('settings',{k}),'轮数已保存，下一批生效')}
$('rounds').onchange=e=>setK(e.target.value);$('k-minus').onclick=()=>setK(Number($('rounds').value)-1);$('k-plus').onclick=()=>setK(Number($('rounds').value)+1);$('auto-learn').onchange=e=>action(()=>api('settings',{auto_learn:e.target.checked}),'学习偏好已保存');
$('toolbar').querySelectorAll('button').forEach(b=>b.onclick=()=>{if(!editor)return;const c=editor.chain().focus();({bold:()=>c.toggleBold().run(),italic:()=>c.toggleItalic().run(),heading:()=>c.toggleHeading({level:2}).run(),bullet:()=>c.toggleBulletList().run(),table:()=>c.insertTable({rows:3,cols:3,withHeaderRow:true}).run(),undo:()=>c.undo().run(),redo:()=>c.redo().run()})[b.dataset.command]()});
$('markdown-toggle').onclick=async()=>{await save();if(dirty)return;markdownMode=!markdownMode;$('editor').hidden=markdownMode;$('toolbar').hidden=markdownMode;$('markdown-source').hidden=!markdownMode;if(markdownMode)$('markdown-source').value=current.markdown;else editor.commands.setContent(toEditor(current.markdown),{contentType:'markdown',emitUpdate:false});$('markdown-toggle').textContent=markdownMode?'返回文档编辑':'Markdown'};
$('markdown-source').oninput=changed;window.addEventListener('beforeunload',e=>{if(dirty){e.preventDefault();e.returnValue=''}});
(async()=>{try{token=(await api('session')).token;await refresh(true);await initChat();refreshWorkspaces().catch(()=>{});setInterval(()=>refresh(),3000)}catch(e){notice(e.message,true)}})();

$('editor').addEventListener('click',e=>{const a=e.target.closest('a[href^="#source-"]');if(a){e.preventDefault();const id=a.getAttribute('href').slice(8);action(async()=>{const r=await api('source?id='+id);showSource(r)})}});

function gradeSummary(a){return a?`评分：证据 ${a.evidence??'—'}/5 · 覆盖 ${a.coverage??'—'}/5 · 分析 ${a.analysis??'—'}/5 · 表达 ${a.expression??'—'}/5\n${a.summary}\n`:'尚未评分\n'}

let progressRequest=false;
async function refreshProgress(){
 if(progressRequest||!state)return;
 const job=state.jobs.find(j=>['running','queued'].includes(j.status));
 if(!job){
 const paused=state.jobs.find(j=>['cancelled','interrupted','failed'].includes(j.status));
 $('run-progress').hidden=!paused;
 if(paused){const service=await api('runtime');$('run-progress').innerHTML=`<div class="section-title"><h2>${paused.status==='failed'?'任务未完成':'任务已暂停'}</h2><button id="paused-resume" class="primary">使用 ${esc(modelLabel(state.settings))} 重新开始</button></div><p>当前没有继续执行这个任务。已有来源和产物保留。</p><p class="help">本地服务 PID ${service.server_pid||'—'}（页面与任务管理） · ${service.pid?'模型进程 PID '+service.pid:'本工作区没有模型进程'}</p><p class="help">${esc(paused.error||'')}</p><p class="help">下一次使用：${esc(modelLabel(state.settings))}。模型变更会创建新执行，不恢复旧模型子 agent。</p><button id="paused-settings" class="outline">修改模型与要求</button>`;$('paused-settings').onclick=()=>page('setup');$('paused-resume').onclick=()=>action(()=>api('resume',{job_id:paused.id}),'已按页面显示的模型提交')}
 return
}
 progressRequest=true;
 try{
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
  $('run-progress').innerHTML=`<div class="section-title"><div><p class="eyebrow">${job.kind==='learn'?'技能学习':'简报生成'} · ${running?'后台正在运行':'等待后台执行'}</p><h2>${esc(p.stage||(job.status==='queued'?'任务已排队':'正在启动 Orchestrator'))}</h2></div><button class="outline" id="progress-stop">停止任务</button></div><p><strong>${esc(modelLabel(parse(job.payload).runtime||start.runtime))}</strong> · 模型进程 PID ${live.pid||'—'}${live.server_pid?' · 本地服务 PID '+live.server_pid:''}</p><p class="help">已用 ${mins} 分 ${secs} 秒 · 单次执行上限 ${state.settings.timeout_minutes} 分钟${run?` · ${JSON.parse(run.source_ids).length} 份初始来源 · ${req.allow_web?'允许联网':'仅本地来源'}`:''}</p>${p.message?`<p class="progress-message">${esc(p.message)}</p>`:''}<div class="agent-progress">${agents.map(a=>`<div><strong>${esc(a.role)}</strong><span>${labels[a.status]||esc(a.status)}</span>${a.task?`<p>${esc(a.task)}</p>`:''}</div>`).join('')}</div><p class="help">${p.draft_ready?'正文已可查看，评分独立完成。':'正文保存后会自动显示；等待子 agent 时可能暂时没有新消息。'}${p.last_activity?' 最近活动：'+new Date(p.last_activity).toLocaleTimeString():''}</p>`;
  $('progress-stop').onclick=()=>action(()=>api('stop',{job_id:job.id}));
 }catch(e){$('run-progress').hidden=false;$('run-progress').textContent='进度连接暂时中断，任务没有重新提交。'+e.message}
 finally{progressRequest=false}
}

function friendlyModel(model){return ({'gpt-5.6-luna':'Luna','gpt-5.6-terra':'Terra','gpt-5.6-sol':'Sol','gpt-6-astra':'Astra'}[model]||model)}
function effortValue(runtime,key){return Object.prototype.hasOwnProperty.call(runtime,key)?(runtime[key]||'none'):'high'}
function assignEffort(id,value){const input=$(id);if(![...input.options].some(o=>o.value===value))input.add(new Option(value,value));input.value=value}
function activeChatRuntime(){return chat.messages.find(m=>m.role==='user'&&m.turn_id===chat.session?.turn_id&&m.runtime)?.runtime||chat.session?.runtime||{}}
function modelLabel(cfg){if(!cfg?.model)return '未指定模型';const effort=cfg.reasoning_effort,effortLabel=Object.prototype.hasOwnProperty.call(cfg,'reasoning_effort')?(!effort||effort==='none'?'模型默认':effort):'未记录';return friendlyModel(cfg.model)+' / '+effortLabel+(cfg.model_provider?' · '+cfg.model_provider:'')}
function updateModelLabel(){const cfg={model:$('model-select').value.trim(),reasoning_effort:$('effort-select').value,model_provider:$('model-provider').value.trim()};$('execution-choice').textContent='即将使用：'+modelLabel(cfg);if($('setup-model-summary'))$('setup-model-summary').textContent=modelLabel(cfg);$('generate-button').textContent='使用 '+modelLabel(cfg)+' 生成简报 →';$('model-select').title=cfg.model?friendlyModel(cfg.model)+' · '+cfg.model:'输入模型 ID'}
async function saveModel(){const model=$('model-select').value.trim();if(!model)throw Error('请输入模型 ID');await api('settings',{model,reasoning_effort:$('effort-select').value,model_provider:$('model-provider').value.trim()||null});updateModelLabel()}
$('model-select').onchange=()=>action(saveModel,'模型已保存；下一次启动生效');$('effort-select').onchange=()=>action(saveModel,'推理档位已保存；下一次启动生效');$('model-provider').onchange=()=>action(saveModel,'Provider 已保存；下一次启动生效');

$('version-history').onclick=()=>action(async()=>{
 await save();if(dirty)throw Error('请先保存当前修改');if(!current)return;
 const versions=state.briefs.filter(b=>b.run_id===current.run_id);
 $('history-list').innerHTML=versions.map((b,i)=>`<button class="history-row" data-history-version="${b.id}"><strong>${b.author==='agent'?'生成原稿':i===0?'当前编辑稿':'自动保存快照'}</strong><span>${new Date(b.created).toLocaleString('zh-CN',{hour12:false,year:'numeric',month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit',second:'2-digit'})}</span></button>`).join('');
 $('history-list').querySelectorAll('[data-history-version]').forEach(button=>button.onclick=()=>{const b=state.briefs.find(x=>x.id===button.dataset.historyVersion);openBrief(b);render(false);$('history-dialog').close()});
 $('history-dialog').showModal();
});
$('close-history').onclick=()=>$('history-dialog').close();

// Interactive agent conversations. Artifact editors keep their existing state.
const chat = {sessions:[],id:null,session:null,messages:[],requests:[],events:new Map(),after:0,busy:false,uploading:0,polling:false,drafts:new Map(),attachments:new Set(),request:null};
const chatStates={idle:'准备就绪',starting:'正在启动',running:'正在处理',complete:'已完成',completed:'已完成',failed:'运行失败',interrupted:'已中断',cancelled:'已停止',queued:'已排队',sending:'发送中',delivered:'已发送',streaming:'正在回复'};
const chatActive=()=>['running','starting'].includes(chat.session?.status);
function rememberDraft(){chat.drafts.set(chat.id||'new',{text:$('chat-input').value,sources:[...chat.attachments],model:$('chat-model').value,model_provider:$('chat-model-provider').value.trim()||null,effort:$('chat-effort').value,allow_web:$('chat-allow-web').checked,permission:$('chat-permission').value});try{sessionStorage.setItem('briefloop-chat-drafts',JSON.stringify([...chat.drafts].slice(-30)))}catch{}}
function restoreDraft(){const d=chat.drafts.get(chat.id||'new');$('chat-input').value=d?.text||'';chat.attachments=new Set(d?.sources||[]);$('chat-allow-web').checked=d?.allow_web||false;const runtime=d||chat.session?.runtime||{model:'gpt-5.6-luna',effort:'high'};$('chat-model').value=runtime.model||'gpt-5.6-luna';assignEffort('chat-effort',effortValue(runtime,'effort'));$('chat-model-provider').value=runtime.model_provider||'';$('chat-permission').value=runtime.permission||'workspace-write';renderAttachments();updateComposer();autoSizeChatInput()}
function runtimeChoice(){const model=$('chat-model').value.trim();if(!model)throw Error('请输入模型 ID');return {model,model_provider:$('chat-model-provider').value.trim()||null,effort:$('chat-effort').value,permission:$('chat-permission').value}}
function messageTime(value){const date=new Date(value);return Number.isNaN(date.getTime())?'':date.toLocaleTimeString('zh-CN',{hour:'2-digit',minute:'2-digit',hour12:false})}
function chatError(text=''){$('chat-error').textContent=text;$('chat-error').hidden=!text}
function updateComposer(){const active=chatActive(),steering=active&&$('chat-mode').value==='steer';if(steering){const runtime=activeChatRuntime();$('chat-permission').value=runtime.permission||'workspace-write';$('chat-model').value=runtime.model||'';assignEffort('chat-effort',effortValue(runtime,'effort'));$('chat-model-provider').value=runtime.model_provider||''}for(const id of ['chat-model','chat-effort','chat-model-provider'])$(id).disabled=steering||chat.busy;$('chat-permission').disabled=steering||chat.busy;$('new-session').disabled=chat.busy||chat.uploading>0;$('chat-input').readOnly=chat.busy;document.querySelectorAll('[data-chat-session]').forEach(b=>b.disabled=chat.busy||chat.uploading>0);$('chat-send').disabled=chat.busy||chat.uploading>0||!$('chat-input').value.trim()||!$('chat-model').value.trim();const sendLabel=chat.busy?'发送中…':active?($('chat-mode').value==='steer'?'立即补充':'排队发送'):'发送消息';$('chat-send').textContent=chat.busy?'…':'↑';$('chat-send').setAttribute('aria-label',sendLabel);$('chat-send').title=sendLabel;$('chat-stop').hidden=!active;$('chat-stop').disabled=chat.busy;$('chat-mode').disabled=!active||chat.busy;$('chat-attach').disabled=chat.busy||chat.uploading>0;$('attach-existing').disabled=chat.busy;$('chat-attach').querySelector('span').textContent=chat.uploading?'上传中…':'文件';const model=$('chat-model').value.trim(),label=friendlyModel(model)||'输入模型 ID';$('chat-model').title=model?friendlyModel(model)+' · '+model:'输入模型 ID';const effort=$('chat-effort').value==='none'?'模型默认':$('chat-effort').value;$('composer-help').textContent=`Enter 发送 · Shift + Enter 换行 · ${label} / ${effort}${$('chat-model-provider').value.trim()?' · '+$('chat-model-provider').value.trim():''}${active?' · 立即补充会调整当前工作，排队消息在本轮结束后处理':''}`}
function renderSessions(){
 const signature=JSON.stringify([chat.id,chat.sessions]);if(renderSessions.signature===signature)return;renderSessions.signature=signature;
 $('session-list').innerHTML=chat.sessions.length?chat.sessions.map(s=>`<button class="session-item ${s.id===chat.id?'active':''}" data-chat-session="${esc(s.id)}" ${s.id===chat.id?'aria-current="page"':''}><span class="session-name">${esc(s.title||'新对话')}</span><span class="session-meta"><i class="session-dot ${['running','starting'].includes(s.status)?'running':''}"></i>${esc(chatStates[s.status]||s.status)}<time>${messageTime(s.updated)}</time></span></button>`).join(''):'<p class="sidebar-empty">对话会保存在这里。<br>随时回来继续。</p>';
 $('session-list').querySelectorAll('[data-chat-session]').forEach(b=>b.onclick=()=>selectChat(b.dataset.chatSession).catch(e=>chatError(e.message)));
}
function renderAttachments(){
 const find=id=>state?.sources.find(s=>s.id===id);
 $('chat-attachments').innerHTML=[...chat.attachments].map(id=>`<span class="attachment-chip"><span>▤ ${esc(find(id)?.name||id)}</span><button type="button" data-remove-attachment="${esc(id)}" aria-label="移除附件 ${esc(find(id)?.name||id)}">×</button></span>`).join('');
 $('chat-attachments').querySelectorAll('[data-remove-attachment]').forEach(b=>b.onclick=()=>{if(chat.busy)return;chat.attachments.delete(b.dataset.removeAttachment);renderAttachments();rememberDraft()});
 const sources=state?.sources||[];
 $('existing-sources').innerHTML=sources.length?sources.map(s=>`<label><input type="checkbox" data-chat-source="${esc(s.id)}" ${chat.attachments.has(s.id)?'checked':''} ${s.status==='failed'?'disabled':''}><span>${esc(s.name)}</span><small>${s.status==='failed'?'读取失败':''}</small></label>`).join(''):'<p class="help">还没有已保存来源。可以上传文件，也可以开启联网检索。</p>';
 $('existing-sources').querySelectorAll('[data-chat-source]').forEach(b=>b.onchange=()=>{b.checked?chat.attachments.add(b.dataset.chatSource):chat.attachments.delete(b.dataset.chatSource);renderAttachments();rememberDraft()});
}
function publicActivity(event){
 const data=event.data||{},item=data.item;
 if(item){
  const types={commandExecution:'运行命令',fileChange:'更新文件',mcpToolCall:'调用工具',webSearch:'搜索网页',collabAgentToolCall:'子 Agent',imageView:'查看图片',dynamicToolCall:'调用工具'};
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
  const messageSignature=JSON.stringify(message);if(node.dataset.signature===messageSignature)continue;node.dataset.signature=messageSignature;node.className=`chat-message ${message.role==='user'?'from-user':'from-assistant'} ${['failed','interrupted','cancelled'].includes(message.status)?'message-error':''}`;
  const files=(message.source_ids||[]).map(id=>state?.sources.find(s=>s.id===id)?.name||id);
  const label=message.role==='user'?'你':'BriefLoop';
  node.innerHTML=`<div class="message-heading"><strong>${label}</strong><span>${messageTime(message.created)}</span><span class="message-state">${esc(chatStates[message.status]||message.status)}${message.mode==='steer'&&message.role==='user'?' · 中途补充':''}</span></div><div class="message-body">${esc(message.text||(['streaming','sending'].includes(message.status)?'…':''))}</div>${files.length?`<div class="message-files">${files.map(name=>`<span>▤ ${esc(name)}</span>`).join('')}</div>`:''}`;
  if(message.role==='assistant'&&message.status==='completed'&&message.text){api('render',{markdown:message.text}).then(result=>{if(node.isConnected&&node.dataset.signature===messageSignature){node.querySelector('.message-body').innerHTML=result.html;node.querySelector('.message-body').classList.add('rendered-markdown');if(nearEnd)scroll.scrollTop=scroll.scrollHeight}}).catch(()=>{})}
 }
 for(const node of nodes.values())node.remove();if(nearEnd)scroll.scrollTop=scroll.scrollHeight;
 }
 $('chat-empty').hidden=chat.messages.length>0;
}
function renderChat(){
 $('chat').classList.toggle('is-empty',chat.messages.length===0&&!chatActive());
 $('chat-title').textContent=chat.session?.title||'新对话';const runtime=chat.session?.runtime;const pending=chat.messages.filter(m=>m.role==='user'&&m.status==='queued').length;
 $('chat-status').textContent=`${chatStates[chat.session?.status]||'准备就绪'}${runtime?' · '+modelLabel({model:runtime.model,reasoning_effort:runtime.effort,model_provider:runtime.model_provider}):''}${pending?' · '+pending+' 条消息排队中':''}`;
 renderMessages();renderActivities();renderRequests();renderContext();renderSessions();updateComposer();
}
async function selectChat(id){
 if(chat.busy||chat.uploading)return;if(id===chat.id){page('chat');return}rememberDraft();chat.id=id;chat.session=chat.sessions.find(s=>s.id===id)||null;chat.tokenUsage=null;chat.messages=[];chat.requests=[];chat.events=new Map();chat.after=0;chat.request=null;renderMessages.signature='';localStorage.setItem('briefloop-chat-session',id);chatError();restoreDraft();renderChat();page('chat');await pollChat(true);
}
async function newChat(){
 if(chat.busy||chat.uploading)return;rememberDraft();chat.id=null;chat.session=null;chat.tokenUsage=null;chat.messages=[];chat.requests=[];chat.events=new Map();chat.after=0;chat.request=null;renderMessages.signature='';chat.drafts.delete('new');localStorage.removeItem('briefloop-chat-session');restoreDraft();chatError();renderChat();page('chat');$('chat-input').focus();
}
async function pollChat(force=false){
 if(chat.polling&&!force)return;chat.polling=true;const sid=chat.id,after=chat.after;
 try{
  const [list,snapshot]=await Promise.all([api('harness/sessions'),sid?api(`harness/session?id=${encodeURIComponent(sid)}&after=${after}`):Promise.resolve(null)]);
  chat.sessions=list.sessions||[];if(sid===chat.id&&snapshot){chat.session=snapshot.session;chat.tokenUsage=snapshot.token_usage||null;chat.messages=snapshot.messages||[];chat.requests=snapshot.requests||[];for(const event of snapshot.events||[]){chat.events.set(event.seq,event);chat.after=Math.max(chat.after,event.seq)}}renderChat();
 }catch(e){if(force)throw e;else if(!$('chat').hidden){$('chat-status').textContent='会话连接中断，正在重连';chatError(e.message)}}finally{chat.polling=false}
}
async function sendChat(event){
 event.preventDefault();if(chat.busy||chat.uploading)return;const text=$('chat-input').value.trim();if(!text)return;chat.busy=true;chatError();updateComposer();
 try{
  const runtime=runtimeChoice();if(!chat.id){const result=await api('harness/session',{title:text.slice(0,48),runtime});chat.session=result.session||result;chat.id=chat.session.id;if(!chat.id)throw Error('未能创建会话');localStorage.setItem('briefloop-chat-session',chat.id)}
  const payload={session_id:chat.id,text,mode:chatActive()?$('chat-mode').value:'queue',source_ids:[...chat.attachments],runtime,allow_web:$('chat-allow-web').checked};const signature=JSON.stringify(payload);
  if(!chat.request||chat.request.signature!==signature)chat.request={signature,message_id:crypto.randomUUID()};
  await api('harness/message',{...payload,message_id:chat.request.message_id});chat.request=null;$('chat-input').value='';chat.attachments.clear();rememberDraft();renderAttachments();await pollChat(true);
 }catch(e){chatError(e.message+'。消息仍保留在输入框中，可修改或再次发送。')}finally{chat.busy=false;updateComposer();$('chat-input').focus()}
}
$('chat-form').onsubmit=sendChat;
$('new-session').onclick=newChat;
$('chat-input').oninput=()=>{rememberDraft();updateComposer()};
$('chat-input').onkeydown=e=>{if(e.key==='Enter'&&!e.shiftKey&&!e.isComposing){e.preventDefault();if(!$('chat-send').disabled)$('chat-form').requestSubmit()}};
$('chat-mode').onchange=updateComposer;
for(const id of ['chat-model','chat-effort','chat-model-provider'])$(id).onchange=()=>{rememberDraft();updateComposer()};
$('chat-stop').onclick=async()=>{if(!chat.id||chat.busy)return;chat.busy=true;updateComposer();try{await api('harness/cancel',{session_id:chat.id});await pollChat(true)}catch(e){chatError(e.message)}finally{chat.busy=false;updateComposer()}};
$('chat-attach').onclick=()=>$('chat-upload').click();
$('attach-existing').onclick=()=>{const show=$('existing-sources').hidden;$('existing-sources').hidden=!show;$('attach-existing').setAttribute('aria-expanded',String(show));renderAttachments()};
$('chat-upload').onchange=async event=>{
 const input=event.target,files=[...input.files];input.value='';if(!files.length)return;chat.uploading++;chatError();updateComposer();
 try{for(const file of files){const bytes=new Uint8Array(await file.arrayBuffer());let binary='';for(let i=0;i<bytes.length;i+=8192)binary+=String.fromCharCode(...bytes.subarray(i,i+8192));const source=await api('upload',{name:file.name,data:btoa(binary)});if(source.status==='failed'){chatError(`${file.name} 读取失败：${source.error||'请检查文件后重试'}`);continue}chat.attachments.add(source.id);selected.add(source.id)}await refresh();renderAttachments();rememberDraft()}catch(e){chatError('上传未完成：'+e.message)}finally{chat.uploading--;updateComposer()}
};
document.querySelectorAll('[data-prompt]').forEach(b=>b.onclick=()=>{$('chat-input').value=b.dataset.prompt;rememberDraft();updateComposer();$('chat-input').focus()});
async function initChat(){
 try{const saved=JSON.parse(sessionStorage.getItem('briefloop-chat-drafts')||'[]');if(Array.isArray(saved))chat.drafts=new Map(saved)}catch{}
 page('chat');chat.id=localStorage.getItem('briefloop-chat-session')||null;restoreDraft();
 try{await pollChat(true)}catch(e){chat.id=null;localStorage.removeItem('briefloop-chat-session');chatError(e.message);await pollChat().catch(()=>{})}
 if(chat.session)restoreDraft();setInterval(()=>pollChat(),1300);
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
 $('role-model-options').innerHTML=roles.map(([role,name,label])=>{const configured=state.settings.role_models||{},value=(role==='evaluator'?(configured.evaluator||configured.scorer||configured.assessor):configured[role])||{},inherits=!value.model,effort=effortValue(inherits?state.settings:value,'reasoning_effort');return `<div class="role-model-row"><span><b>${name}</b><small>${label}</small></span><div class="role-runtime-fields"><div class="role-model-pair"><input id="role-${role}-model" aria-label="${name} 模型 ID" data-role-model="${role}" list="model-suggestions" autocomplete="off" spellcheck="false" value="${esc(value.model||'')}" placeholder="留空继承主链模型" title="${esc(value.model?friendlyModel(value.model):'继承主链模型')}"><select id="role-${role}-effort" aria-label="${name} 推理档位" data-role-effort="${role}" ${inherits?'disabled':''}>${[...new Set(['none','low','medium','high','xhigh','max',effort])].map(e=>`<option value="${e}" ${e===effort?'selected':''}>${e==='none'?'模型默认':e}</option>`).join('')}</select></div><label class="role-provider-field"><span>Provider</span><input id="role-${role}-provider" aria-label="${name} Codex provider" value="${esc(value.model_provider||'')}" placeholder="${inherits?'继承主链全部配置':'留空沿用本机 Codex 配置'}" autocomplete="off" spellcheck="false" ${inherits?'disabled':''}></label></div></div>`}).join('');
 $('role-model-options').querySelectorAll('input,select').forEach(input=>input.onchange=saveRoleModels);
}
async function saveRoleModels(){
 const role_models={};for(const input of $('role-model-options').querySelectorAll('[data-role-model]')){const role=input.dataset.roleModel,model=input.value.trim(),effort=$(`role-${role}-effort`),provider=$(`role-${role}-provider`);effort.disabled=!model;provider.disabled=!model;if(model)role_models[role]={model,reasoning_effort:effort.value,model_provider:provider.value.trim()||null}}
 const controls=[...$('role-model-options').querySelectorAll('input,select')];controls.forEach(input=>input.disabled=true);$('role-model-status').textContent='保存中…';
 try{await api('settings',{role_models});state.settings.role_models=role_models;$('role-model-status').textContent='已保存，下一次启动时生效。'}catch(e){$('role-model-status').textContent='未保存：'+e.message;renderRoleModels()}finally{for(const input of $('role-model-options').querySelectorAll('[data-role-model]')){input.disabled=false;const inherits=!input.value.trim();$(`role-${input.dataset.roleModel}-effort`).disabled=inherits;$(`role-${input.dataset.roleModel}-provider`).disabled=inherits}}
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
function showSettings(){moveSearchSettings('settings');$('settings-dialog').showModal();updateLearningPause().catch(()=>{});refreshTavilySettings().catch(()=>{})}
$('settings-open').onclick=showSettings;$('settings-close').onclick=()=>$('settings-dialog').close();
{
 const modelPanel=document.querySelector('.model-settings');const shortcut=document.createElement('div');shortcut.className='setup-settings-shortcut';shortcut.innerHTML='<div><span>生成模型</span><strong id="setup-model-summary">Luna / high</strong></div><button type="button" class="outline">模型与角色设置</button>';shortcut.querySelector('button').onclick=showSettings;modelPanel.before(shortcut);$('settings-model-block').append(modelPanel);
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
 $('source-title').textContent=source.name||'来源';$('source-body').textContent=source.error||result.text||'';
 $('source-link').textContent=source.url||'';$('source-link').href=source.url||'#';$('source-link').hidden=!source.url;
 const original=$('source-original');original.textContent=provenance.original_kind==='provider_response'?'下载 Tavily 返回内容 ↗':'下载原件 ↗';original.hidden=!result.original_url;if(result.original_url){original.href=result.original_url;original.download=source.name||''}else original.removeAttribute('href');
 const rows=[];if(provenance.fetched_at){const date=new Date(provenance.fetched_at);rows.push(['抓取时间',Number.isNaN(date.getTime())?String(provenance.fetched_at):date.toLocaleString('zh-CN',{hour12:false})])}if(provenance.content_type)rows.push(['内容类型',String(provenance.content_type)]);
 $('source-provenance').innerHTML=rows.map(([label,value])=>`<div><dt>${esc(label)}</dt><dd>${esc(value)}</dd></div>`).join('');$('source-provenance').hidden=!rows.length;
 $('source-dialog').showModal();
}

function renderSearchProvider(){$('tavily-settings').hidden=$('search-provider').value!=='tavily';$('setup-search-summary').textContent='搜索工具：'+($('search-provider').value==='tavily'?'Tavily':'Codex 原生')}
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
 const element=$('brief-length');if(!current){element.hidden=true;return}element.hidden=false;
 const stats=state.briefs.find(brief=>brief.id===current.id)?.length_stats||current.length_stats;
 element.classList.remove('over-limit');if(!stats||!Number.isFinite(stats.count)){element.textContent='字数信息暂不可用';return}
 const count=value=>new Intl.NumberFormat('zh-CN').format(value),parts=[`${dirty?'上次保存':'正文'} ${count(stats.count)} 字`];
 if(stats.target_words==null&&stats.max_words==null)parts.push('字数目标与上限未设置');else {parts.push(stats.target_words==null?'目标未设置':`目标 ${count(stats.target_words)}`);parts.push(stats.max_words==null?'上限未设置':`上限 ${count(stats.max_words)}`)}
 if(stats.over_limit===true&&stats.max_words!=null){parts.push(`超出 ${count(stats.count-stats.max_words)} 字`);element.classList.add('over-limit')}
 if(dirty)parts.push('保存后更新');element.textContent=parts.join(' · ');
}
