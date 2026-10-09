// Workspace-wide ceiling on concurrent agent sessions (#728), shown beside the report count.
export function sessionBudgetUI({$, api, action, notice, getState}) {
 function mount(after) {
  const label = document.createElement('label');
  label.className = 'session-budget';
  label.textContent = '同时运行的 Agent 会话上限 ';
  const input = document.createElement('input');
  input.id = 'max-agent-sessions'; input.type = 'number'; input.min = '2'; input.max = '64'; input.value = '12';
  const save = document.createElement('button');
  save.type = 'button'; save.className = 'outline'; save.textContent = '保存上限';
  const help = document.createElement('p');
  help.className = 'help';
  help.textContent = '报告、报告内的 Scout、单独发起的审阅和后台任务合计。每份报告开始时占 2 个（自身和 1 个 Scout），有空余时 Scout 才增加到研究设置的数量；报告自己的审阅沿用报告的名额。';
  input.oninput = () => { input.dataset.editing = '1'; };
  input.onchange = () => action(async () => {
   const result = await api('settings', {max_agent_sessions: Number(input.value)});
   getState().settings.max_agent_sessions = result.max_agent_sessions;
   delete input.dataset.editing;
   notice('会话上限已保存；已运行的任务继续，新任务按空余名额开始');
  });
  save.onclick = () => input.onchange();
  label.append(input, save);
  after.after(label, help);
 }
 function sync() {
  const input = $('max-agent-sessions');
  if (input && document.activeElement !== input && !input.dataset.editing) input.value = getState().settings.max_agent_sessions || 12;
 }
 return {mount, sync};
}
