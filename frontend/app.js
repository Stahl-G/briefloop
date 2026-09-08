import {Editor} from '@tiptap/core';
import StarterKit from '@tiptap/starter-kit';
import {TableKit} from '@tiptap/extension-table';
import {Markdown} from '@tiptap/markdown';
const $=id=>document.getElementById(id),esc=s=>String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c])),parse=s=>JSON.parse(s||'{}');
let token='',state,current,pendingRun=null,editor,dirty=false,saving=false,saveTimer,learnTimer,markdownMode=false,selected=new Set();
function notice(s,error=false){$('notice').textContent=s;$('notice').classList.toggle('error',error);$('notice').hidden=false;clearTimeout(notice.timer);notice.timer=setTimeout(()=>$('notice').hidden=true,error?12000:4500)}
async function api(path,data,retried=false){const r=await fetch('/api/'+path,data===undefined?{}:{method:'POST',headers:{'Content-Type':'application/json','X-BriefLoop-Token':token},body:JSON.stringify(data)});const b=await r.json();if(r.status===403&&data!==undefined&&!retried){token=(await api('session')).token;return api(path,data,true)}if(!r.ok)throw Error(b.error||'操作失败');return b}
function page(name){for(const id of ['report','setup','learning'])$(id).hidden=id!==name;document.querySelectorAll('nav [data-page]').forEach(b=>b.classList.toggle('active',b.dataset.page===name))}
document.querySelectorAll('[data-page]').forEach(b=>b.onclick=()=>page(b.dataset.page));
async function action(fn,message){try{await fn();if(message)notice(message);await refresh()}catch(e){notice(e.message,true)}}
async function refresh(first=false){try{const next=await api('state');$('connection').textContent='本地已连接';const signature=JSON.stringify(next);state=next;if(first||signature!==refresh.signature){refresh.signature=signature;render(first)}await refreshProgress()}catch(e){$('connection').textContent='连接中断';if(first)notice(e.message,true)}}
const toEditor=md=>md.replace(/\\?\[@(src\\?_[a-zA-Z0-9]+)\\?\]/g,(_,raw)=>{const id=raw.replaceAll('\\','');return `[${(state.sources.findIndex(s=>s.id===id)+1)||'?'}](#source-${id})`});
const fromEditor=md=>md.replace(/\[([^\]]+)\]\(#source-(src_[a-zA-Z0-9]+)\)/g,(_,label,id)=>`[@${id}]`);
const statuses={queued:'等待运行',running:'正在运行',complete:'已完成',failed:'未完成',interrupted:'已中断',cancelled:'已停止'};
function render(first){
 if(first){state.sources.forEach(s=>selected.add(s.id));$('rounds').value=state.settings.k;$('auto-learn').checked=state.settings.auto_learn;$('model-select').value=state.settings.model||'gpt-5.6-luna';$('effort-select').value=state.settings.reasoning_effort||'high';updateModelLabel();if(state.requirements)for(const [k,v] of Object.entries(state.requirements)){const e=$('requirements').elements[k];if(e)e.type==='checkbox'?e.checked=v:e.value=v}}
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

 const incoming=pendingRun&&state.briefs.find(b=>b.run_id===pendingRun);if(incoming&&!dirty){openBrief(incoming);pendingRun=null}if(!current&&state.briefs.length)openBrief(state.briefs[0]);if(current){$('version-select').value=current.id;assessment();citations()}
 $('empty').hidden=!!current||state.jobs.length>0;$('document-area').hidden=!current;
 $('jobs').innerHTML=state.jobs.map(j=>`<div class="job"><span class="tag ${j.status==='failed'?'error':''}">${statuses[j.status]}</span><div class="job-main">${{generate:'生成简报',assess:'重新评分',learn:'WikiSkill 学习'}[j.kind]}<small>${parse(j.payload).runtime?esc(modelLabel(parse(j.payload).runtime)):'旧任务：沿用当时本机配置'} · ${j.progress?`第 ${j.progress.round}/${j.progress.k} 轮 · ${{maintainer:'整理经验',proposer:'提出候选',validation:'验证候选'}[j.progress.phase]||j.progress.phase} · `:''}${esc(j.error||new Date(j.created).toLocaleString())}</small></div>${j.kind==='learn'?`<button data-details="${j.id}">查看比较</button>`:''}${['queued','running'].includes(j.status)?`<button data-stop="${j.id}">停止</button>`:''}${['failed','interrupted','cancelled'].includes(j.status)?`<button data-resume="${j.id}">恢复</button>`:''}</div>`).join('');
 document.querySelectorAll('[data-stop]').forEach(b=>b.onclick=()=>action(()=>api('stop',{job_id:b.dataset.stop})));document.querySelectorAll('[data-resume]').forEach(b=>b.onclick=()=>action(()=>api('resume',{job_id:b.dataset.resume})));
 document.querySelectorAll('[data-details]').forEach(b=>b.onclick=()=>action(async()=>{const d=await api('learning-details?job='+b.dataset.details);$('source-title').textContent='技能比较与依据';$('source-link').textContent='';$('source-body').textContent=d.rounds.length?d.rounds.map((r,i)=>`第 ${i+1} 轮\n${r.result?.reason||'比较尚未完成'}\n${(r.result?.pairs||[]).map(p=>({better:'候选更好',tie:'差不多，保留原技能',worse:'原稿更好'}[p.verdict])+': '+p.reason).join('\n')}\n\n`+r.cases.map(c=>`任务：${c.requirements.title}\n\n旧版\n${gradeSummary(c.baseline.assessment)}\n${c.baseline.reader_markdown||c.baseline.markdown}\n\n候选\n${gradeSummary(c.candidate.assessment)}\n${c.candidate.reader_markdown||c.candidate.markdown}`).join('\n\n')).join('\n\n'):d.job.error||'比较尚未开始；先整理 Wiki 和提出候选。';$('source-dialog').showModal()}));
 $('skills').innerHTML=`<div class="skill">${state.active_skill?'当前启用 '+esc(state.active_skill):'当前使用基础任务提示词'}${state.active_skill?'<button data-rollback="">回到基础版本</button>':''}</div>`+state.skills.map(s=>`<div class="skill"><strong>${esc(s.id)}</strong><p>${esc(s.reason)}</p>${s.id===state.active_skill?'<span class="tag">正在使用</span>':`<button data-rollback="${s.id}" class="outline">使用这个版本</button>`}</div>`).join('');document.querySelectorAll('[data-rollback]').forEach(b=>b.onclick=()=>action(()=>api('rollback',{skill_id:b.dataset.rollback||null}),'下一轮将使用所选技能'));
 if(state.wiki!==render.wiki){render.wiki=state.wiki;if(state.wiki)api('render',{markdown:state.wiki}).then(r=>$('wiki').innerHTML=r.html);else $('wiki').innerHTML='<h2>还没有学习经验</h2><p class="muted">生成简报后直接改稿，或留下评论。Maintainer 会在这里整理观察、方法与适用条件。</p>'}bindSources();
}
function openBrief(b){if(dirty){notice('请先保存当前修改，再切换版本',true);return}current=b;$('report-title').textContent=parse(b.detail).title||'简报';$('download').href='/api/download?version='+b.id;$('download-docx').href='/api/download?format=docx&version='+b.id;if(editor)editor.destroy();editor=new Editor({element:$('editor'),editable:state.briefs.find(x=>x.run_id===b.run_id)?.id===b.id,extensions:[StarterKit.configure({link:{openOnClick:false}}),TableKit,Markdown],content:toEditor(b.markdown),contentType:'markdown',onUpdate:changed});$('markdown-source').value=b.markdown;const historical=state.briefs.find(x=>x.run_id===b.run_id)?.id!==b.id;$('markdown-source').readOnly=historical;$('toolbar').querySelectorAll('button').forEach(x=>x.disabled=historical);$('save-state').textContent=historical?'历史记录（只读）':b.author==='user'?'当前编辑稿已自动保存':'原稿已保存';$('version-select').value=b.id;assessment();citations()}
function changed(){dirty=true;$('save-state').textContent='有未保存修改';clearTimeout(saveTimer);saveTimer=setTimeout(save,1400)}
$('version-select').onchange=e=>openBrief(state.briefs.find(b=>b.id===e.target.value));
async function save(){if(!dirty||saving||!current)return;saving=true;$('save-state').textContent='保存中…';const text=markdownMode?$('markdown-source').value:fromEditor(editor.getMarkdown());try{current=await api('save',{base_version:current.id,markdown:text,editor_document:markdownMode?null:editor.getJSON()});dirty=(markdownMode?$('markdown-source').value:fromEditor(editor.getMarkdown()))!==text;$('save-state').textContent=dirty?'有新的修改':'已保存';$('download').href='/api/download?version='+current.id;$('download-docx').href='/api/download?format=docx&version='+current.id;await refresh();if(dirty)saveTimer=setTimeout(save,1400);else scheduleLearning()}catch(e){$('save-state').textContent='未保存，请保留编辑';notice(e.message,true)}finally{saving=false}}
function scheduleLearning(){ /* Worker consumes the durable feedback after inactivity. */ }
function assessment(){if(!current)return;const r=state.assessments.find(a=>a.version_id===current.id);if(!r){$('assessment').innerHTML='<p class="muted">尚未评分</p><p class="help">你可以先阅读和修改。评分针对这个版本独立运行。</p>';return}const d=parse(r.data);$('assessment').innerHTML=`<div class="judgment">${esc(d.overall)}</div><p>${esc(d.summary)}</p><div class="grades">${[['evidence','证据与准确性'],['coverage','覆盖与取舍'],['analysis','分析有效性'],['expression','表达与可用性']].map(([k,l])=>`<div class="grade"><span>${l}</span><strong>${d[k]??'—'}</strong><small>${d[k]?' / 5':''}</small></div>`).join('')}</div><p class="help">等级是本轮要求完成程度，评分可有不同意见。</p>${(d.findings||[]).map(f=>`<details class="finding"><summary>${f.severity==='major'?'●':'○'} ${esc(f.description)}</summary>${f.report_quote?`<blockquote>${esc(f.report_quote)}</blockquote>`:''}<p>${esc(f.requirement)}</p><p>${esc(f.evidence)}</p>${f.source_id?`<button data-source="${esc(f.source_id)}">查看来源 · ${esc(f.locator)}</button>`:''}<p>${esc(f.suggestion)}</p></details>`).join('')}`;bindSources()}
function citations(){const refs=(parse(current.detail).citations||[]).filter(r=>toEditor(current.markdown).includes('#source-'+r.source_id));$('citations').innerHTML=refs.length?'引用来源 '+refs.map(r=>`<button data-source="${esc(r.source_id)}">${esc(state.sources.find(s=>s.id===r.source_id)?.name||r.source_id)} · ${esc(r.locator)}</button>`).join(''):'尚无引用记录';bindSources()}
function bindSources(){document.querySelectorAll('[data-source]').forEach(b=>b.onclick=()=>action(async()=>{const r=await api('source?id='+b.dataset.source);$('source-title').textContent=r.source.name;$('source-body').textContent=r.source.error||r.text;$('source-link').textContent=r.source.url||'';$('source-link').href=r.source.url||'#';$('source-dialog').showModal()}))}
$('close-source').onclick=()=>$('source-dialog').close();
$('requirements').onsubmit=e=>{e.preventDefault();action(async()=>{const f=new FormData(e.target),req=Object.fromEntries(f.entries());req.allow_web=f.has('allow_web');req.raw_input=req.objective;delete req.runtime_model;delete req.runtime_effort;await saveModel();await save();if(dirty)throw Error('请先保存当前修改');const job=await api('generate',{requirements:req,source_ids:[...selected]});pendingRun=parse(job.payload).run_id;page('report')},'任务已排队，后台会生成简报')};
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
(async()=>{try{token=(await api('session')).token;await refresh(true);setInterval(()=>refresh(),3000)}catch(e){notice(e.message,true)}})();

$('editor').addEventListener('click',e=>{const a=e.target.closest('a[href^="#source-"]');if(a){e.preventDefault();const id=a.getAttribute('href').slice(8);action(async()=>{const r=await api('source?id='+id);$('source-title').textContent=r.source.name;$('source-body').textContent=r.source.error||r.text;$('source-link').textContent=r.source.url||'';$('source-link').href=r.source.url||'#';$('source-dialog').showModal()})}});

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

function modelLabel(cfg){if(!cfg?.model)return '旧任务未固定模型';return ({'gpt-5.6-luna':'Luna','gpt-5.6-terra':'Terra','gpt-5.6-sol':'Sol','gpt-6-astra':'Astra'}[cfg.model]||cfg.model)+' / '+(cfg.reasoning_effort||'未记录')}
function updateModelLabel(){const cfg={model:$('model-select').value,reasoning_effort:$('effort-select').value};$('execution-choice').textContent='即将使用：'+modelLabel(cfg);$('generate-button').textContent='使用 '+modelLabel(cfg)+' 生成简报 →'}
async function saveModel(){await api('settings',{model:$('model-select').value,reasoning_effort:$('effort-select').value});updateModelLabel()}
$('model-select').onchange=()=>action(saveModel,'模型已保存；不会自动开始调用');$('effort-select').onchange=()=>action(saveModel,'推理档位已保存；不会自动开始调用');

$('version-history').onclick=()=>action(async()=>{
 await save();if(dirty)throw Error('请先保存当前修改');if(!current)return;
 const versions=state.briefs.filter(b=>b.run_id===current.run_id);
 $('history-list').innerHTML=versions.map((b,i)=>`<button class="history-row" data-history-version="${b.id}"><strong>${b.author==='agent'?'生成原稿':i===0?'当前编辑稿':'自动保存快照'}</strong><span>${new Date(b.created).toLocaleString('zh-CN',{hour12:false,year:'numeric',month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit',second:'2-digit'})}</span></button>`).join('');
 $('history-list').querySelectorAll('[data-history-version]').forEach(button=>button.onclick=()=>{const b=state.briefs.find(x=>x.id===button.dataset.historyVersion);openBrief(b);render(false);$('history-dialog').close()});
 $('history-dialog').showModal();
});
$('close-history').onclick=()=>$('history-dialog').close();
