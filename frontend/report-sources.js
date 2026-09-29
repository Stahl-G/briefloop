// The side panel lists the open report's own sources, so they can be read and
// opened without leaving the report. The workspace source page keeps search and batch actions.
const FILTERS = [['', '全部'], ['ready', '可用'], ['visual', '需视觉'], ['failed', '获取失败']];
const PREVIEW = 8;

export function createReportSources({$, esc, getState, getCurrent, runSourceIds, sourceState, sourceStatusChip, sourceTitle, sourceHost, openSource}) {
 let filter = '', expanded = false, signature = '';
 function sourcesOf(runId) {
  const state = getState(), run = (state?.runs || []).find(item => item.id === runId);
  const byId = new Map((state?.sources || []).map(source => [source.id, source]));
  // Readable sources first; the ones that could not be read stay listed after them.
  const rank = source => ({ready: 0, visual: 1}[sourceState(source)] ?? 2);
  return runSourceIds(run).map(id => byId.get(id)).filter(Boolean).sort((a, b) => rank(a) - rank(b));
 }
 function row(source) {
  const host = sourceHost(source), kind = source.url ? '' : (source.name || '').split('.').pop().toUpperCase();
  const meta = [host || kind].filter(Boolean).map(esc).join(' · ');
  return `<li><button type="button" class="report-source-row" data-rail-source="${esc(source.id)}" title="${esc(source.name || source.url || '')}"><span class="report-source-text"><span class="name">${esc(sourceTitle(source))}</span>${meta ? `<span class="meta">${meta}</span>` : ''}</span>${sourceStatusChip(sourceState(source))}</button></li>`;
 }
 function render() {
  const box = $('report-sources'), tab = document.querySelector('#report-panel [data-report-tab="sources"]');
  const current = getCurrent();
  const all = current ? sourcesOf(current.run_id) : [];
  if (tab) tab.textContent = all.length ? `来源与数据 ${all.length}` : '来源与数据';
  if (!box) return;
  const next = JSON.stringify([current?.run_id, filter, expanded, all.map(source => [source.id, source.status, source.needs_visual, source.name])]);
  if (next === signature) return;
  signature = next;
  if (!all.length) { box.innerHTML = ''; return; }
  const counts = Object.fromEntries(FILTERS.map(([key]) => [key, key ? all.filter(source => sourceState(source) === key).length : all.length]));
  const shown = all.filter(source => !filter || sourceState(source) === filter);
  const visible = expanded ? shown : shown.slice(0, PREVIEW);
  box.innerHTML = `<div class="report-sources-head"><h3>${all.length} 个来源</h3></div>`
   + `<div class="seg-tabs report-sources-filter" role="tablist">${FILTERS.filter(([key]) => !key || counts[key]).map(([key, label]) => `<button type="button" role="tab" data-rail-source-filter="${key}" class="${key === filter ? 'active' : ''}" aria-selected="${key === filter}">${label}${key ? ' ' + counts[key] : ''}</button>`).join('')}</div>`
   + `<ul class="report-source-list">${visible.map(row).join('')}</ul>`
   + (shown.length > visible.length ? `<button type="button" class="ghost report-sources-more">显示全部 ${shown.length} 个</button>` : '');
  box.querySelectorAll('[data-rail-source-filter]').forEach(button => button.onclick = () => { filter = button.dataset.railSourceFilter; expanded = false; render(); });
  box.querySelectorAll('[data-rail-source]').forEach(button => button.onclick = () => openSource(button.dataset.railSource));
  const more = box.querySelector('.report-sources-more');
  if (more) more.onclick = () => { expanded = true; render(); };
 }
 return {render};
}
