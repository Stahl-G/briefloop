const status = document.getElementById('status');
const api = window.briefloopDesktop;
let environment = {state: 'checking'}, opening = false;
const phases = {'verify-payload': '正在校验 App 运行组件…', 'detect-python': '正在检测本机 Python…',
  'create-venv': '正在创建 App 专属环境…', 'install-dependencies': '正在下载并安装依赖，请保持网络连接…',
  'verify-imports': '正在验证运行组件…', 'verify-dependencies': '正在核对依赖完整性…',
  'activate-environment': '正在启用运行环境…'};
function renderEnvironment(value) {
  environment = value;
  const busy = ['checking', 'installing'].includes(value.state);
  const cleanupBlocked = value.error?.code === 'cleanup_failed' && value.retryable === false;
  const detail = value.phase === 'install-dependencies' ? `使用已有 Python ${value.pythonVersion}，正在下载并安装 BriefLoop 依赖…` : phases[value.phase];
  document.getElementById('setup').hidden = ['checking', 'ready'].includes(value.state);
  document.getElementById('startup-status').hidden = value.state !== 'checking';
  document.getElementById('setup-title').textContent = value.state === 'error' ? '运行环境需要处理' : '准备运行环境';
  document.getElementById('environment-status').textContent = value.error?.message || detail
    || (value.state === 'needs-setup' ? `将使用已有 Python ${value.pythonVersion}，仅下载 BriefLoop 依赖。` : '正在检测运行环境…');
  document.getElementById('environment-progress').hidden = !busy;
  document.getElementById('prepare').hidden = cleanupBlocked || !['needs-setup', 'error'].includes(value.state);
  document.getElementById('prepare').textContent = value.state === 'error' ? '重新准备' : '下载依赖并准备';
  document.getElementById('python-help').hidden = !['missing-python', 'error'].includes(value.state);
  document.getElementById('inspect').hidden = busy || cleanupBlocked;
  document.getElementById('cancel-setup').hidden = value.state !== 'installing';
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
  document.getElementById('recent-name').textContent = parts.at(-1) || directory;
  document.getElementById('recent-path').textContent = directory;
  button.title = directory;
  button.setAttribute('aria-label', '继续工作区：' + (parts.at(-1) || directory));
  document.getElementById('create').classList.remove('primary');
  button.hidden = false;
  button.onclick = () => action(() => api.openWorkspace({path: directory, create: false}));
}).catch(error => {status.textContent = welcomeErrorMessage(error);});
