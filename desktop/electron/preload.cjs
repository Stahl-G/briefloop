'use strict';
const {contextBridge, ipcRenderer} = require('electron');
// Fixed operations only: never expose ipcRenderer, filesystem, commands or URLs.
contextBridge.exposeInMainWorld('briefloopDesktop', {
  platform: process.platform,
  openWorkspace: request => ipcRenderer.invoke('workspace:open', request),
  chooseWorkspace: create => ipcRenderer.invoke('workspace:choose', !!create),
  recentWorkspace: () => ipcRenderer.invoke('workspace:recent'),
  updateStatus: () => ipcRenderer.invoke('updates:status'),
  checkForUpdates: () => ipcRenderer.invoke('updates:check'),
  downloadUpdate: () => ipcRenderer.invoke('updates:download'),
  installUpdate: () => ipcRenderer.invoke('updates:install'),
  onUpdateStatus: callback => {
    const listener = (_event, value) => callback(value);
    ipcRenderer.on('updates:changed', listener);
    return () => ipcRenderer.removeListener('updates:changed', listener);
  },
  onResume: callback => { ipcRenderer.on('workspace:resume', () => callback()); },
  onPrepareClose: callback => {
    ipcRenderer.on('workspace:prepare-close', async (_event, requestId) => {
      let result;
      try { result = await callback(); } catch (error) { result = {status: 'failed', error: String(error.message || error)}; }
      ipcRenderer.send('workspace:prepared', requestId, result);
    });
  },
});
