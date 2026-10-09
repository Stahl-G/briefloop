const status = document.getElementById('status');
const api = window.briefloopDesktop;
let environment = {state: 'checking'}, opening = false, recentName = '';
const phases = {'verify-payload': '正在校验 App 运行组件…', 'detect-python': '正在检测本机 Python…',
  'reuse-windows-dependencies': '正在复用可用的已有依赖…',
  'clone-environment': '正在复用已有运行环境…', 'check-existing-dependencies': '正在检查已有依赖…',
  'reuse-dependencies': '依赖未变化，直接复用，无需下载…', 'update-dependencies': '正在补充或更新变化的依赖…',
  'install-backend': '正在安装新版 BriefLoop 组件，无需联网…',
  'create-venv': '正在创建 App 专属环境…', 'install-dependencies': '正在下载并安装依赖，请保持网络连接…',
  'verify-imports': '正在验证运行组件…', 'verify-dependencies': '正在核对依赖完整性…',
  'activate-environment': '正在启用运行环境…'};
// The checklist groups the environment phases into the steps a person can follow.
const checklist = ['verify-payload', 'detect-python', 'create-venv', 'install-dependencies', 'verify-imports'];
const checklistStep = {'reuse-windows-dependencies': 'install-dependencies', 'clone-environment': 'create-venv', 'check-existing-dependencies': 'install-dependencies',
  'reuse-dependencies': 'install-dependencies', 'update-dependencies': 'install-dependencies', 'install-backend': 'install-dependencies', 'check-runtime': 'verify-payload', 'verify-dependencies': 'verify-imports', 'activate-environment': 'verify-imports'};
function heading(value) {
  const version = value.version ? ` ${value.version}` : '';
  if (value.state === 'installing') return value.reason === 'update'
    ? ['正在更新运行组件', `App 已更新到${version}，正在检查并更新配套组件，未变化的依赖会优先复用，完成后即可打开工作区。`]
    : ['正在准备运行环境', '请保持网络连接，完成后即可打开工作区。'];
  if (value.state === 'missing-python') return ['需要先安装 Python', '安装完成后回到这里重新检测。'];
  if (value.state === 'error') return ['运行环境需要处理', '工作区和报告不受影响。'];
  if (value.state === 'needs-setup') {
    if (value.reason === 'update') return ['更新运行组件', `App 已更新到${version}，需要安装配套的运行组件。`];
    if (value.reason === 'repair') return ['运行环境需要修复', '已准备的环境没有通过检查。重新准备即可，工作区和报告不受影响。'];
    return ['先准备运行环境', '只需要准备一次，以后启动会直接复用。'];
  }
  return ['从你的工作区继续', '整理材料、研究和写作，报告与对话都保存在本机。'];
}
function renderEnvironment(value) {
  environment = value;
  const busy = ['checking', 'installing'].includes(value.state);
  const setup = !['checking', 'ready'].includes(value.state);
  const cleanupBlocked = value.error?.code === 'cleanup_failed' && value.retryable === false;
  const python = value.pythonVersion ? `本机 Python ${value.pythonVersion}` : '本机 Python';
  const detail = value.phase === 'install-dependencies' ? `使用${python}，正在下载并安装 BriefLoop 依赖…` : phases[value.phase];
  const [title, subtitle] = heading(value);
  document.getElementById('title').textContent = title;
  document.getElementById('subtitle').textContent = subtitle;
  document.getElementById('setup').hidden = !setup;
  document.getElementById('workspace-panel').hidden = setup;
  document.getElementById('startup-status').hidden = value.state !== 'checking';
  document.getElementById('setup-title').textContent = value.state === 'missing-python' ? '安装 Python'
    : value.reason === 'update' ? '更新运行组件' : '准备运行环境';
  document.getElementById('environment-status').textContent = value.error?.message
    || (value.reuseFallback ? value.reuseFallback + ' ' : '') + (value.state === 'installing' ? detail : `将使用${python}，联网下载 BriefLoop 依赖到 App 专属环境。`);
  document.getElementById('environment-progress').hidden = value.state !== 'installing';
  document.getElementById('environment-phases').hidden = value.state !== 'installing';
  const current = checklist.indexOf(checklistStep[value.phase] || value.phase);
  document.querySelectorAll('#environment-phases li').forEach((item, index) => {
    const position = checklist.indexOf(item.dataset?.phase ?? checklist[index]);
    item.className = position < current ? 'done' : position === current ? 'current' : '';
  });
  document.getElementById('prepare').hidden = cleanupBlocked || !['needs-setup', 'error'].includes(value.state);
  document.getElementById('prepare').textContent = value.state === 'error' || value.reason === 'repair' ? '重新准备'
    : value.reason === 'update' ? '更新运行组件' : '下载依赖并准备';
  document.getElementById('python-help').hidden = !['missing-python', 'error'].includes(value.state);
  // The setup step already offers its own action; the footer check is for a ready or failed environment.
  document.getElementById('inspect').hidden = busy || cleanupBlocked || value.state === 'needs-setup';
  document.getElementById('inspect').textContent = value.state === 'missing-python' ? '重新检测' : '检查运行环境';
  document.getElementById('cancel-setup').hidden = value.state !== 'installing';
  document.getElementById('workspace-next').textContent = recentName
    ? `准备完成后可以继续「${recentName}」，或新建、打开其他工作区。` : '准备完成后，可以新建或打开工作区。';
  document.querySelectorAll('#workspace-actions button, #recent').forEach(button => {button.disabled = opening || value.state !== 'ready';});
}
function welcomeErrorMessage(error) {
  const message = String(error?.message ?? error);
  // Electron adds this envelope to rejected fixed IPC operations. Keep named
  // errors (TypeError, etc.) and the original error object for diagnostics.
  return message.replace(/^Error invoking remote method '(?:workspace:(?:open|choose|recent)|environment:(?:status|inspect|prepare|cancel|python-help))': (?:Error: )?/, '');
}
async function setupAction(callback) {
  try { renderEnvironment(await callback()); }
  catch (error) { renderEnvironment({...environment, state: 'error', error: {...environment.error, message: welcomeErrorMessage(error)}}); }
}
async function action(callback) {
  if (opening || environment.state !== 'ready') return;
  opening = true; renderEnvironment(environment);
  status.textContent = '正在打开工作区…';
  try { const result = await callback(); if (result?.cancelled) status.textContent = ''; }
  catch (error) { status.textContent = welcomeErrorMessage(error); }
  finally { opening = false; renderEnvironment(environment); }
}
// A short location for the card; the full path stays in the tooltip.
function shortLocation(directory, parts) {
  const parent = parts.slice(0, -1);
  return parent.length > 2 ? `…/${parent.slice(-2).join('/')}` : (directory.startsWith('/') ? '/' : '') + parent.join('/');
}
document.getElementById('prepare').onclick = () => setupAction(() => api.environment.prepare());
document.getElementById('inspect').onclick = () => setupAction(() => api.environment.inspect());
document.getElementById('cancel-setup').onclick = () => setupAction(() => api.environment.cancel());
document.getElementById('python-help').onclick = () => api.environment.pythonHelp().catch(error => {status.textContent = welcomeErrorMessage(error);});
document.getElementById('create').onclick = () => action(() => api.chooseWorkspace(true));
document.getElementById('open').onclick = () => action(() => api.chooseWorkspace(false));
api.environment.onChanged(renderEnvironment);
setupAction(() => api.environment.status());
api.recentWorkspace().then(directory => {
  if (!directory) return;
  const button = document.getElementById('recent');
  const parts = directory.replace(/\\/g, '/').split('/').filter(Boolean);
  recentName = parts.at(-1) || directory;
  document.getElementById('recent-name').textContent = recentName;
  document.getElementById('recent-path').textContent = `上次使用 · ${shortLocation(directory, parts)}`;
  button.title = directory;
  button.setAttribute('aria-label', '继续工作区：' + recentName);
  document.getElementById('create').classList.remove('primary');
  button.hidden = false;
  button.onclick = () => action(() => api.openWorkspace({path: directory, create: false}));
  renderEnvironment(environment);
}).catch(error => {status.textContent = welcomeErrorMessage(error);});
