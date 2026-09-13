// Assessment activity belongs to the exact saved version, never its whole run.
export function reviewPending(version, jobs = []) {
 if (!version) return false;
 return jobs.some(job => {
  if (!['review', 'assess'].includes(job.kind) || !['queued', 'running'].includes(job.status)) return false;
  let payload = job.payload;
  if (typeof payload === 'string') { try { payload = JSON.parse(payload); } catch { return false; } }
  return payload?.version_id === version.id;
 });
}

// Pass the report's scoped jobs, not the workspace history. A retry supersedes
// its predecessor even after it finishes; the history objects remain untouched.
export function withoutSupersededRetries(jobs = []) {
 const byId = new Map(jobs.map(job => [job.id, job]));
 const superseded = new Set();
 for (const job of jobs) {
  let payload = job.payload;
  if (typeof payload === 'string') { try { payload = JSON.parse(payload); } catch { continue; } }
  const previous = byId.get(payload?.retry_of_job_id);
  if (previous && previous.id !== job.id && previous.kind === job.kind) superseded.add(previous.id);
 }
 return jobs.filter(job => !superseded.has(job.id));
}

// Independent fact-check candidates are observation-mode: the four candidate
// states are the checker's per-claim calls (更正/口径/材料不足 specifics live in
// the reason), execution status only says why the run stopped, and neither may
// read as "事实有误" or "核验通过". Reviewer conclusions render separately.
export const FACT_CANDIDATE_LABELS = { supported_for_scope: '口径内支持', contradicted: '与来源不一致', insufficient_evidence: '材料不足', unknown: '无法判断' };
export const FACT_EXECUTION_LABELS = { completed: '核查执行完成', cancelled: '已取消', failed: '执行失败', budget_exhausted: '预算耗尽' };

const escHtml = s => String(s ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));

export function locatorText(locator) {
 if (!locator || typeof locator !== 'object') return '';
 if (locator.kind === 'text') return locator.start_line === locator.end_line ? `第 ${locator.start_line} 行` : `第 ${locator.start_line}–${locator.end_line} 行`;
 if (locator.page) return `第 ${locator.page} 页`;
 if (locator.kind === 'url') return '链接定位';
 return locator.kind || '';
}

function candidateCard(candidate) {
 const label = FACT_CANDIDATE_LABELS[candidate.status] || candidate.status || '未知';
 const title = candidate.statement || (candidate.anchors?.[0]?.quote ?? '') || candidate.claim_id;
 const sources = (candidate.spans || []).map(span =>
  `<button data-source="${escHtml(span.source_id)}">原文 · ${escHtml(span.source_name)}${locatorText(span.locator) ? '（' + escHtml(locatorText(span.locator)) + '）' : ''}</button>`).join('');
 const reviewer = candidate.reviewer
  ? `Reviewer 结论：${escHtml(FACT_CANDIDATE_LABELS[candidate.reviewer.status] || candidate.reviewer.status)} — ${escHtml(candidate.reviewer.reason)}`
  : '尚无独立审阅结论（观察模式候选，不构成硬门）';
 return `<details class="issue-card fact-candidate"><summary><span class="issue-label">候选 · ${escHtml(label)}</span><strong>${escHtml(title)}</strong></summary>` +
  `<p class="issue-basis">${escHtml(candidate.reason)}</p>` +
  (sources ? `<p class="fact-sources">${sources}</p>` : '') +
  `<p class="help">${escHtml(reviewer)}</p></details>`;
}

// Default addition mirrors the product's starting reserve share; an explicit
// limits object (a recorded pending grant) renders its own numbers.
export function factGrantText(limits) {
 const l = limits || { search_requests: 6, candidate_urls: 30, source_pages: 12 };
 return `${l.search_requests} 次搜索 · ${l.candidate_urls} 候选 · ${l.source_pages} 正文页`;
}

export function factCheckHTML(data) {
 const records = data?.records || [];
 const stage = data?.stage || null;
 const stageLabel = { active: '核查阶段进行中', budget_exhausted: '核查阶段预算耗尽', completed: '核查阶段已完成', cancelled: '核查阶段已取消', failed: '核查阶段执行失败' };
 // The user-facing grant entry (plan D3): visible while the stage can still
 // spend (active) or was closed by exhaustion (追加后重开继续)；预登记 shows when
 // the switch is on but the stage has not been admitted yet.
 const grantable = stage ? ['active', 'budget_exhausted'].includes(stage.status) : !!data?.enabled;
 const grantNote = data?.pending_grant
  ? `<p class="help">已登记追加核查预算（${escHtml(factGrantText(data.pending_grant.limits))}），将在核查阶段接纳时并入计量。</p>` : '';
 const stageSection = (stage || data?.enabled || grantNote)
  ? `<section class="issue-card fact-check"><div class="issue-head"><strong>独立事实核查</strong><span class="help">${escHtml(stage ? stageLabel[stage.status] || stage.status : '已开启，交付检查前自动执行')}</span></div>` +
     (stage ? `<p class="help">预算来源：${escHtml(stage.budget_source?.kind === 'user_grant' ? '用户明确追加' : '任务预留份额')}${(stage.grants || []).length ? `，另追加 ${(stage.grants || []).length} 次` : ''}；追加额度只在阶段进行期间并入计量。</p>` : '') +
     grantNote +
     (stage?.status === 'budget_exhausted' ? '<p class="help">预算耗尽只是执行收束原因，不代表主张真假；追加后重开阶段继续核查。</p>' : '') +
     (grantable ? `<p><button data-fact-grant="${escHtml(data.version_id)}">追加核查预算（${escHtml(factGrantText())}）</button></p>` : '') +
    '</section>'
  : '';
 return stageSection + records.map(record => {
  const execution = record.execution || {};
  const executionLabel = FACT_EXECUTION_LABELS[execution.status] || execution.status || '';
  const model = record.snapshot?.model || '未记录模型';
  const provider = record.snapshot?.search_provider || '未记录搜索源';
  const unselected = (record.unselected || []).map(item =>
   `<li>${escHtml(item.statement || item.claim_id)} — ${escHtml(item.reason)}</li>`).join('');
  const unchecked = (record.unchecked || []).map(item =>
   `<li>${escHtml(item.statement || item.claim_id)} — 未及核查即收束，按未核查处理</li>`).join('');
  const skipped = [unselected ? `<p class="help">未查主张（不进搜索框或影响小）：</p><ul>${unselected}</ul>` : '',
                   unchecked ? `<p class="help">选中但未出候选：</p><ul>${unchecked}</ul>` : ''].join('');
  return `<section class="issue-card fact-check"><div class="issue-head"><strong>独立事实核查</strong><span class="help">截至 ${escHtml(record.as_of || '未记录')} · ${escHtml(model)} · ${escHtml(provider)}</span></div>` +
   `<p class="help">执行状态：${escHtml(executionLabel)}${execution.summary ? ' — ' + escHtml(execution.summary) : ''}（执行状态只说明本次为何收束，不代表主张真假）</p>` +
   (record.covers_version === false ? '<p class="help">该记录针对修订前的旧稿；本稿不算已核查，需重新核查。</p>' : '') +
   (record.candidates || []).map(candidateCard).join('') + skipped +
   '<p class="help">候选须由独立 Reviewer 接纳后才影响处理；核查详情不写入报告正文。</p></section>';
 }).join('');
}
