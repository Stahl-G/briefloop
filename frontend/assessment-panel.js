// Delivery checks, issue list, fact-check panel, and score card for one open brief.
import {beginPanel as beginPanelDefault,updatePanel as updatePanelDefault} from './report-panels.js';
import {reviewPending as reviewPendingDefault,factCheckHTML as factCheckHTMLDefault} from './review-status.js';
import {createFactCheckGrants} from './fact-check-grants.js';
import {officeIssueLine} from './office-tools.js';
import {reviewModeLabel} from './review-controls.js';

const RELATION_LABELS={compatible:'可合并',different_scope:'口径不同',temporal_sequence:'时间演进',correction:'明确更正',supersession:'替代',republication:'转载',attributed_difference:'归属分歧',contradiction:'实质矛盾',unknown:'无法判断'};

function findingTitle(f){return [(f.requirement?'要求：'+f.requirement:''),f.description||'',(f.suggestion?'建议：'+f.suggestion:'')].filter(Boolean).join('\n')}

export function createAssessmentPanel(deps){
 const {api,action,notice,$,esc,parse,getState,getCurrent,getEditor,isDirty,bindSources,applyHighlightState,readerHighlights}=deps;
 const beginPanel=deps.beginPanel||beginPanelDefault;
 const updatePanel=deps.updatePanel||updatePanelDefault;
 const reviewPending=deps.reviewPending||reviewPendingDefault;
 const factCheckHTML=deps.factCheckHTML||factCheckHTMLDefault;
 const factGrants=deps.factGrants||createFactCheckGrants({api});
 let showSuggestionMarks=false;

 async function renderDeliveryChecks(){
  const ticket=(renderDeliveryChecks.ticket||0)+1;renderDeliveryChecks.ticket=ticket;
  const current=getCurrent();
  if(!current||!$('assessment'))return;const vid=current.id;
  let box=$('assessment').querySelector('.delivery-checks');
  if(!box){box=document.createElement('div');box.className='delivery-checks';$('assessment').prepend(box)}
  beginPanel(box,vid,isDirty()?'有未保存修改；下列检查仅针对已保存版本。':'正在检查已保存版本…');
  let c;try{c=await api('version-checks?version='+encodeURIComponent(vid))}catch(e){if(box.isConnected&&getCurrent()?.id===vid&&ticket===renderDeliveryChecks.ticket)updatePanel(box,'检查暂不可用，请稍后重试；未判定通过。');return}
  if(!getCurrent()||getCurrent().id!==vid||ticket!==renderDeliveryChecks.ticket||!box.isConnected)return;
  const parts=[];
  if(isDirty())parts.push('<span class="tag">有未保存修改；仅检查已保存版本</span>');
  if(c.temporal){const t=c.temporal;parts.push(`<span class="tag ${t.out_of_range_count?'error':''}">时效：${t.status==='not_checked'?(t.reason==='legacy_window'?'旧任务未冻结范围':'未提供事件日期记录，待核实'):`范围外当期事件 ${t.out_of_range_count||0}；日期待核实 ${t.missing_date_count||0}；${t.items?.length||0} 条日期记录，仍需核对原文`}</span>`)}
  parts.push(c.broken_refs.length?`<span class="tag error">断链引用 ${c.broken_refs.length} 处：${c.broken_refs.map(esc).join('、')}</span>`:'<span class="tag">正文引用可定位；支持关系仍需评价</span>');
  const n=c.numbers;
  parts.push(`<span class="tag">${n.status==='not_checked'?'未做数值核对':n.status==='partial'?'部分绑定已检查':'已检查提交的绑定'}：提交 ${n.total} 项，已检查 ${n.checked} 项，匹配 ${n.matched} 项</span>`);
  if(n.unmatched.length)parts.push(`<span class="tag error">绑定数值不一致 ${n.unmatched.length} 项：${n.unmatched.map(r=>esc(r.label||r.expected)).join('、')}</span>`);
  if(n.skipped.length)parts.push(`<span class="tag">未检查 ${n.skipped.length} 项：${n.skipped.map(r=>esc((r.label||'未命名')+'：'+r.reason)).join('；')}</span>`);
  const occurrences=n.occurrence_review;
  if(occurrences?.candidate_count){
   parts.push(`<span class="tag">带明确单位的数值出现 ${occurrences.candidate_count} 处；直接对应已核对绑定 ${occurrences.checked_occurrences} 处；待看 ${occurrences.review_candidate_count} 处</span>`);
   if(occurrences.review_candidate_count){
    const where=s=>s.kind==='table_cell'?`表 ${s.table} · 第 ${s.row} 行 ${s.column} 列`:`正文第 ${s.paragraph} 段`;
    parts.push(`<details class="help"><summary>查看待看数值位置</summary><ul>${occurrences.samples.map(s=>`<li>${esc(where(s))}：${esc(s.text)} · ${esc(s.context)}</li>`).join('')}</ul>${occurrences.truncated?`<p>仅显示前 ${occurrences.sample_limit} 处。</p>`:''}<p>${esc(occurrences.scope)}</p></details>`);
   }
  }
  if(c.export.escaped_bold)parts.push('<span class="tag error">存在转义加粗，请检查排版</span>');
  if(c.export.figure_error)parts.push('<span class="tag error">图表资源不可用：'+esc(c.export.figure_error)+'</span>');
  else if(c.export.figure_markers.length)parts.push('<span class="tag">含图表：独立交付请下载 Word 或含图片的 Markdown 包</span>');
  const l=c.layout;
  if(l){
   const findings=[];
   if(l.heading_jumps.length)findings.push('标题层级中断 '+l.heading_jumps.length+' 处（如 '+l.heading_jumps.slice(0,2).map(j=>'第'+j.after+'级后接第'+j.level+'级').join('、')+'）');
   if(l.empty_headings)findings.push('空标题 '+l.empty_headings+' 个');
   if(l.tables_without_header.length)findings.push('缺表头行的表格 '+l.tables_without_header.length+' 张');
   parts.push(findings.length?`<span class="tag error">版式：${esc(findings.join('；'))}</span>`:'<span class="tag">版式：标题层级、表头与空标题检查通过</span>');
  }
  // Optional OfficeCLI tool output, composed by the server outside brief_checks.
  // Observation only: it never gates delivery and is absent when never run.
  if(c.office){
   const o=c.office,validate=o.validate||{},issues=o.issues||{},items=Array.isArray(issues.items)?issues.items:[];
   const found=Number(issues.count)||items.length,filtered=Number(issues.noise_filtered)||0,filteredNote=filtered?`（已过滤纯标点类噪音 ${filtered} 项）`:'';
   if(validate.status==='error'||issues.status==='error')parts.push(`<span class="tag error">OfficeCLI：质检未完成${(validate.reason||issues.reason)?'：'+esc(validate.reason||issues.reason):''}；不影响导出</span>`);
   else if(validate.status!=='ok')parts.push(`<span class="tag error">OfficeCLI：校验未通过${validate.summary?'：'+esc(validate.summary):''}</span>`);
   else if(issues.status==='ok'&&!found)parts.push(`<span class="tag">OfficeCLI：结构校验与质检通过${filteredNote}（工具输出，不阻断交付）</span>`);
   else parts.push(`<span class="tag">OfficeCLI：观察 ${found} 项${filteredNote}</span><details class="help"><summary>查看观察记录</summary><ul>${items.slice(0,10).map(item=>`<li>${esc(officeIssueLine(item))}</li>`).join('')}</ul>${items.length>10?'<p>仅显示前 10 条。</p>':''}<p>工具输出，观察性质检，不阻断交付。</p></details>`);
  }
  if(c.assessment_overall)parts.push(`<span class="tag">模型评分：${esc(c.assessment_overall)}</span>`);
  updatePanel(box,'<strong>已保存版本检查</strong> '+parts.join(' ')+'<p class="help">数值检查仅覆盖已提交并成功定位的绑定，不代表正文数字已全部核验；事实含义、研究覆盖与交付质量仍需评价。</p>');
 }

 async function renderReportIssues(){
  const ticket=(renderReportIssues.ticket||0)+1;renderReportIssues.ticket=ticket;
  const current=getCurrent();
  const box=$('report-issues');if(!box||!current)return;
  const vid=current.id;beginPanel(box,vid,'<p class="help">正在读取问题…</p>');
  let data;try{data=await api('review-status?version='+encodeURIComponent(vid))}catch(e){if(box.isConnected&&getCurrent()?.id===vid&&ticket===renderReportIssues.ticket)updatePanel(box,'<p class="help">问题清单暂不可用，请稍后重试。</p>');return}
  if(!getCurrent()||getCurrent().id!==vid||ticket!==renderReportIssues.ticket)return;
  const kinds={contradiction:'实质矛盾',insufficient_evidence:'证据不足',missing_requirement:'要求缺口',missing_binding:'未绑定正文',execution_gap:'执行缺口',expression:'表达问题'};
  const cards=[];
  for(const conflict of (data.conflicts||[])){
   if(conflict.status==='resolved')continue;const d=conflict.data||{};
   cards.push({tone:d.importance==='core'?'danger':'',label:d.kind==='different_scope'?'口径分歧':d.kind==='forecast_difference'?'预测分歧':d.kind==='correction'?'明确更正':'来源分歧',
     title:d.description||'来源分歧',basis:(d.participants||[]).length?('涉及 '+d.participants.length+' 条来源陈述'):'',scope:d.scope||'',
     action:'按更正/口径/预测分类处理，必要时限制结论',state:'待处理'});
  }
  for(const finding of (data.findings||[])){
   if(['resolved','dismissed_with_evidence'].includes(finding.status))continue;const d=finding.data||{};
   cards.push({tone:d.severity==='major'?'danger':'',label:kinds[d.kind]||'核查发现',title:d.description||'',
     basis:(d.evidence||d.report_quote||'').slice(0,180),scope:(d.requirement_ids||[]).join('、'),action:d.suggested_action||'',
     state:finding.status==='open'?'未处理':'已回应，待复核'});
  }
  const reconciliation=data.reconciliation;
  if(reconciliation&&!reconciliation.error){
   for(const question of (reconciliation.open_questions||[])){
    cards.push({tone:'',label:'待查问题',title:question.question||'',basis:question.missing_evidence||'',
      scope:(question.related_requirement_ids||[]).join('、'),action:question.suggested_next_action||'',state:'未决'});
   }
  }
  const notes=[];
  const latestReview=data.reviews?.[0];
  if(latestReview)notes.push('最近审阅：'+reviewModeLabel(latestReview)+' · '+({queued:'已排队',running:'执行中',complete:'已返回审阅结果',incomplete:'未完成',cancelled:'已取消'}[latestReview.status]||latestReview.status));
  if(reconciliation&&!reconciliation.error&&(reconciliation.relations||[]).length){
   const byLabel={};for(const relation of reconciliation.relations)byLabel[relation.relation]=(byLabel[relation.relation]||0)+1;
   notes.push('写作前对照：'+Object.entries(byLabel).map(([label,count])=>(RELATION_LABELS[label]||label)+' '+count).join('、')+'（只表示关系，不代表已判定真假）');
  }
  if(reconciliation&&reconciliation.stale)notes.push('来源或时间注释已变化，对照记录需更新');
  if(reconciliation&&reconciliation.error)notes.push('对照记录不可用：'+reconciliation.error);
  updatePanel(box,(cards.length||notes.length)
   ? `${cards.length?`<div class="issue-head"><strong>需要处理 ${cards.length} 项</strong></div>`:''}${cards.map(card=>`<details class="issue-card ${card.tone}"><summary><span class="issue-label">${esc(card.label)}</span><strong>${esc(card.title)}</strong></summary>${card.basis?`<p class="issue-basis">${esc(card.basis)}</p>`:''}${card.scope?`<p class="help">关联：${esc(card.scope)}</p>`:''}${card.action?`<p>${esc(card.action)}</p>`:''}<p class="help">状态：${esc(card.state)}</p></details>`).join('')}${notes.length?`<p class="help">${notes.map(esc).join(' · ')}</p>`:''}`
   : '<p class="help">暂未发现需要处理的问题；独立审阅完成后会更新。</p>');
 }

 let factCheckView=null;
 function paintFactChecks(){
  if(!factCheckView)return;
  const {box,vid,data}=factCheckView;
  if(getCurrent()?.id!==vid||!box.isConnected)return;
  let pending;try{pending=factGrants.pending(vid)}catch{} // submit reports storage errors before sending
  updatePanel(box,factCheckHTML({...data,grant_request:pending,grant_request_busy:factGrants.busy(vid)}));if(box.innerHTML)bindSources();
  box.querySelectorAll('[data-fact-grant]').forEach(b=>{
   const version=b.dataset.factGrant;
   // Identical HTML may be retained, including a button disabled by its click.
   b.disabled=factGrants.busy(version);
   b.onclick=()=>action(async()=>{
    b.disabled=true;
    try{
     const result=await factGrants.submit(version);
     if(getCurrent()?.id===version)notice(result.replayed?'已确认上次追加的核查预算，没有重复追加':'已追加核查预算，将在核查阶段计量中生效');
    }finally{
     // Refresh the current DOM from local flight/receipt state before any GET.
     // A poll may have replaced the clicked button while the POST was pending.
     paintFactChecks();await renderFactChecks();
    }
   });
  });
 }
 async function renderFactChecks(){
  // Observation-mode fact-check panel: per-claim candidates with original-text
  // links, execution status on its own line; never part of the report body.
  const current=getCurrent();
  const box=$('fact-checks');if(!box||!current)return;
  const ticket=(renderFactChecks.ticket||0)+1;renderFactChecks.ticket=ticket;
  const vid=current.id;beginPanel(box,vid);
  let data;try{data=await api('fact-checks?version='+encodeURIComponent(vid))}catch(e){return}
  if(!getCurrent()||getCurrent().id!==vid||ticket!==renderFactChecks.ticket||!box.isConnected)return;
  factCheckView={box,vid,data};paintFactChecks();
 }

 function assessment(){
  const current=getCurrent();
  if(!current)return;
  queueMicrotask(()=>{renderDeliveryChecks();renderReportIssues();renderFactChecks()});
  const state=getState();
  const editor=getEditor();
  const r=state.assessments.find(a=>a.version_id===current.id);
  const signature=JSON.stringify([current.id,r?.data,reviewPending(current,state.jobs),showSuggestionMarks]);
  if(assessment.signature===signature&&assessment.editor===editor)return;
  assessment.signature=signature;assessment.editor=editor;
  if(!r){
   applyHighlightState({quotes:[],findings:new Map(),kinds:new Map()});
   const pending=reviewPending(current,state.jobs);
   $('assessment').innerHTML=pending?'<p class="muted">评分将自动进行</p><p class="help">本轮生成完成后，系统会用独立 Evaluator 自动评分，不需要手动点「重新评分」。</p>':'<p class="muted">尚未评分</p><p class="help">你可以先阅读和修改。评分针对这个版本独立运行。</p>';
   return;
  }
  const d=parse(r.data),findings=d.findings||[];
  const quotes=readerHighlights(findings,{expression:d.expression,showSuggestions:showSuggestionMarks}).map(item=>({quote:item.quote,kind:item.kind,title:findingTitle(item.finding),findingIndex:findings.indexOf(item.finding)}));
  const highlightFindings=new Map(findings.map((f,i)=>[String(i),f]));
  const highlightKinds=new Map(quotes.map(q=>[String(q.findingIndex),q.kind]));
  applyHighlightState({quotes,findings:highlightFindings,kinds:highlightKinds});
  const toggle=`<label class="finding-toggle help"><input type="checkbox" id="show-suggestions" ${showSuggestionMarks?'checked':''}> 显示建议标记（黄）；必须修正句始终标红</label>`;
  $('assessment').innerHTML=`<div class="judgment">${esc(d.overall)}</div>${d.basis==='assessment_without_review'?'<p class="help review-basis">普通评分，不是独立审阅：当前配置未运行所选模式的 Reviewer。正式交付仍需独立审阅完成。</p>':''}<p>${esc(d.summary)}</p><div class="grades">${[['evidence','证据与准确性'],['coverage','覆盖与取舍'],['analysis','分析有效性'],['expression','表达与可用性']].map(([k,l])=>`<div class="grade"><span>${l}</span><strong>${d[k]??'—'}</strong><small>${d[k]?' / 5':''}</small></div>`).join('')}</div><p class="help">等级是本轮要求完成程度，评分可有不同意见。</p>${(typeof d.expression==='number'&&d.expression<=2&&d.overall==='达到要求')?'<p class="help">表达分偏低但总体仍判为「达到要求」，两者不一致；系统会按此安排一次修订，实际以正文和独立审阅为准。</p>':''}${findings.length?toggle:''}${findings.map((f,i)=>`<details class="finding" data-finding="${i}"><summary>${f.severity==='major'?'●':'○'} ${esc(f.description)}</summary>${f.report_quote?`<blockquote>${esc(f.report_quote)}</blockquote>`:''}<p>${esc(f.requirement)}</p><p>${esc(f.evidence)}</p>${f.source_id?`<button data-source="${esc(f.source_id)}">查看来源 · ${esc(f.locator)}</button>`:''}<p>${esc(f.suggestion)}</p></details>`).join('')}`;
  bindSources();
  const toggleEl=$('show-suggestions');
  if(toggleEl)toggleEl.onchange=()=>{showSuggestionMarks=toggleEl.checked;assessment()};
 }

 function citations(){
  const current=getCurrent();if(!current)return;
  const state=getState();
  const detail=parse(current.detail),derived=new Set((detail.content_citations||[]).map(r=>r.source_id));
  const refs=(detail.citations||[]).filter(r=>derived.has(r.source_id)||deps.toEditor(current.markdown).includes('#source-'+r.source_id));
  $('citations').innerHTML=refs.length?'引用来源 '+refs.map(r=>`<button data-source="${esc(r.source_id)}">${esc(state.sources.find(s=>s.id=== r.source_id)?.name||r.source_id)} · ${esc(r.locator)}</button>`).join(''):'尚无引用记录';
  bindSources();
 }

 function bumpDeliveryTicket(){
  renderDeliveryChecks.ticket=(renderDeliveryChecks.ticket||0)+1;
 }

 return {assessment,citations,renderDeliveryChecks,renderReportIssues,renderFactChecks,bumpDeliveryTicket,get showSuggestionMarks(){return showSuggestionMarks}};
}
