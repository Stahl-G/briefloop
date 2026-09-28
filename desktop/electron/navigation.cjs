'use strict';

// A report download must never replace the workspace, even if a backend
// response is inline text (or an error) instead of an attachment.
const downloads = new Set(['/api/download', '/api/export-file', '/api/release-file', '/api/audit-file', '/api/source-original']);
function isWorkspaceDownload(url, workspace) {
  if (!workspace) return false;
  try {
    const target = new URL(url);
    return target.origin === new URL(workspace).origin && downloads.has(target.pathname);
  } catch { return false; }
}
function installNavigation({contents, getWorkspaceURL, welcomeURL, shell, reportError}) {
  function download(url) {
    if (!isWorkspaceDownload(url, getWorkspaceURL())) return false;
    contents.downloadURL(url);
    return true;
  }
  contents.setWindowOpenHandler(({url}) => {
    if (!download(url) && /^https?:\/\//.test(url)) shell.openExternal(url).catch(reportError);
    return {action: 'deny'};
  });
  contents.on('will-navigate', (event, url) => {
    const target = new URL(url), workspace = getWorkspaceURL();
    if (isWorkspaceDownload(url, workspace)) {
      event.preventDefault();
      download(url);
      return;
    }
    if (url === welcomeURL || (workspace && target.origin === new URL(workspace).origin)) return;
    event.preventDefault();
    if (/^https?:\/\//.test(url) && target.hostname !== '127.0.0.1') shell.openExternal(url).catch(reportError);
    else reportError(Error('请使用桌面菜单打开工作区。'));
  });
}

module.exports = {installNavigation, isWorkspaceDownload};
