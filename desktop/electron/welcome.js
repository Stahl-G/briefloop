const status = document.getElementById('status');
async function action(callback) {
  document.querySelectorAll('button').forEach(button => button.disabled = true);
  status.textContent = '正在打开工作区…';
  try { const result = await callback(); if (result?.cancelled) status.textContent = ''; }
  catch (error) { status.textContent = error.message; }
  finally { document.querySelectorAll('button').forEach(button => button.disabled = false); }
}
document.getElementById('create').onclick = () => action(() => window.briefloopDesktop.chooseWorkspace(true));
document.getElementById('open').onclick = () => action(() => window.briefloopDesktop.chooseWorkspace(false));
window.briefloopDesktop.recentWorkspace().then(directory => {
  if (!directory) return;
  const button = document.getElementById('recent');
  button.textContent = '继续上次工作区 · ' + directory;
  button.style.display = 'block';
  button.onclick = () => action(() => window.briefloopDesktop.openWorkspace({path: directory, create: false}));
});
