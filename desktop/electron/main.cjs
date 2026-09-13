'use strict';
const {app, BrowserWindow, Menu, dialog, ipcMain, shell} = require('electron');
const fs = require('node:fs/promises');
const path = require('node:path');
const {pathToFileURL} = require('node:url');
const {randomUUID} = require('node:crypto');
const {WorkspaceService} = require('./service.cjs');
let window, service, switching = false, quitting = false, closePending = false, expectedExit = false;
const prepared = new Map();
let menuSave = null, workspaceOrigin = null;
const welcomeURL = pathToFileURL(path.join(__dirname, 'welcome.html')).href;
const runtime = app.isPackaged ? path.join(process.resourcesPath, 'runtime') : path.join(__dirname, 'runtime', 'macos-arm64');
if (process.env.BRIEFLOOP_DESKTOP_DATA) app.setPath('userData', path.resolve(process.env.BRIEFLOOP_DESKTOP_DATA));
const preferencesPath = () => path.join(app.getPath('userData'), 'desktop.json');
async function recentWorkspace() { try { return JSON.parse(await fs.readFile(preferencesPath(), 'utf8')).workspace || null; } catch { return null; } }
async function rememberWorkspace(directory) { await fs.mkdir(app.getPath('userData'), {recursive: true}); await fs.writeFile(preferencesPath(), JSON.stringify({workspace: directory}), {mode: 0o600}); }
function trusted(event) {
  if (!window || event.sender !== window.webContents || event.senderFrame !== window.webContents.mainFrame) throw Error('窗口身份不匹配。');
  const url = event.senderFrame.url;
  if (url !== welcomeURL && (!workspaceOrigin || new URL(url).origin !== workspaceOrigin)) throw Error('页面来源不匹配。');
}
function resumeEditing() { if (window && !window.isDestroyed()) window.webContents.send('workspace:resume'); }
function prepareClose() {
  if (window.webContents.getURL() === welcomeURL) return Promise.resolve({status: 'saved'});
  const requestId = randomUUID();
  return new Promise((resolve, reject) => {
    const timer = setTimeout(() => { prepared.delete(requestId); reject(Error('页面尚未确认保存，窗口已保留。请等待保存完成后重试。')); }, 30000);
    prepared.set(requestId, result => { clearTimeout(timer); prepared.delete(requestId); result?.status === 'saved' ? resolve(result) : reject(Error(result?.error || '保存失败，窗口已保留。')); });
    window.webContents.send('workspace:prepare-close', requestId);
  });
}
function saveCurrent() {
  if (closePending || switching || menuSave) return;
  menuSave = prepareClose().catch(reportError).finally(() => { menuSave = null; if (!closePending && !switching) resumeEditing(); });
}
async function stopCurrent() {
  await prepareClose();
  if (!service?.child) return true;
  const status = await service.status();
  let cancelBusy = false;
  if (status.busy) {
    const result = await dialog.showMessageBox(window, {type: 'question', title: '工作区仍有任务',
      message: '任务仍在运行或排队', detail: '继续工作会保留窗口。停止并退出会保存已完成内容，并停止当前工作区的任务。',
      buttons: ['继续工作', '停止并退出'], defaultId: 0, cancelId: 0});
    if (result.response !== 1) return false;
    cancelBusy = true;
  }
  expectedExit = true;
  try { await service.stop({cancelBusy}); } finally { expectedExit = false; }
  return true;
}
async function openWorkspace(request) {
  if (switching || closePending) throw Error('正在保存或切换工作区，请稍候。');
  if (!request || typeof request.path !== 'string' || request.path.length > 4096 || typeof request.create !== 'boolean') throw Error('请选择完整的本地工作区路径。');
  if (!path.isAbsolute(request.path) && !service?.directory) throw Error('请选择完整的本地工作区路径。');
  const target = path.isAbsolute(request.path) ? path.resolve(request.path) : path.resolve(path.dirname(service.directory), request.path);
  // Relative names from the existing Web UI resolve beside the current workspace.
  if (service?.info && target === service.directory) return {path: target, url: service.info.url};
  switching = true;
  try {
    if (menuSave) await menuSave;
    if (service?.child && !(await stopCurrent())) return {cancelled: true};
    service = new WorkspaceService(runtime, async ({lastInfo}) => {
      if (expectedExit || switching || quitting || !window || window.isDestroyed()) return;
      const result = await dialog.showMessageBox(window, {type: 'error', message: '工作区服务已退出',
        detail: '窗口中的编辑已保留。重新连接后可以继续保存；失败时请保留窗口和正文。',
        buttons: ['重新连接', '保留窗口'], defaultId: 0, cancelId: 1});
      if (result.response !== 0 || !lastInfo || service.child) return;
      switching = true;
      try {
        await service.start(service.directory, {port: Number(new URL(lastInfo.url).port)});
        // Keep the same document and origin: its unsaved editor stays intact.
        resumeEditing();
      } catch (error) { await reportError(error); }
      finally { switching = false; }
    });
    const opened = await service.start(target, {create: request.create});
    workspaceOrigin = opened.url;
    try { await window.loadURL(opened.url); }
    catch (error) {
      expectedExit = true;
      try { await service.stop(); }
      catch { error.message += ' 后台仍受此窗口管理，请再次打开或退出以处理。'; }
      finally { expectedExit = false; }
      workspaceOrigin = null;
      await window.loadURL(welcomeURL);
      throw error;
    }
    window.setTitle(`BriefLoop · ${path.basename(opened.path)}`);
    // A preferences disk error must not strand a live service behind the old page.
    try { await rememberWorkspace(opened.path); }
    catch { await dialog.showMessageBox(window, {type: 'warning', message: '工作区已打开', detail: '无法记住最近使用的目录；下次可通过“打开工作区”重新选择。报告仍保存在工作区。'}); }
    return opened;
  } catch (error) {
    if (!service?.info) await window.loadURL(welcomeURL);
    throw error;
  } finally { switching = false; resumeEditing(); }
}
async function chooseWorkspace(create) {
  if (closePending || switching) throw Error('正在保存或切换工作区，请稍候。');
  const result = create ? await dialog.showSaveDialog(window, {title: '新建工作区文件夹', buttonLabel: '新建工作区', defaultPath: path.join(app.getPath('documents'), 'BriefLoop 工作区'), properties: ['createDirectory', 'showOverwriteConfirmation']})
    : await dialog.showOpenDialog(window, {title: '打开 BriefLoop 工作区', buttonLabel: '打开工作区', properties: ['openDirectory']});
  if (result.canceled) return {cancelled: true};
  return openWorkspace({path: create ? result.filePath : result.filePaths[0], create});
}
async function reportError(error) { await dialog.showMessageBox(window, {type: 'error', message: '操作未完成', detail: String(error.message || error)}); }
async function requestQuit() {
  if (quitting || closePending || switching) return;
  closePending = true;
  try {
    if (menuSave) await menuSave;
    if (!(await stopCurrent())) return;
    quitting = true;
    window.destroy();
    app.quit();
  } catch (error) { await reportError(error); }
  finally { closePending = false; if (!quitting) resumeEditing(); }
}
if (!app.requestSingleInstanceLock()) app.quit();
else {
  app.on('second-instance', () => { if (window) { if (window.isMinimized()) window.restore(); window.show(); window.focus(); } });
  app.on('before-quit', event => { if (!quitting) { event.preventDefault(); requestQuit(); } });
  app.whenReady().then(async () => {
    window = new BrowserWindow({width: 1320, height: 900, minWidth: 900, minHeight: 640, title: 'BriefLoop', backgroundColor: '#faf9f6',
      webPreferences: {preload: path.join(__dirname, 'preload.cjs'), nodeIntegration: false, contextIsolation: true, sandbox: true, webSecurity: true, spellcheck: false}});
    window.on('close', event => { if (!quitting) { event.preventDefault(); requestQuit(); } });
    window.webContents.session.setPermissionRequestHandler((_contents, _permission, callback) => callback(false));
    window.webContents.session.setPermissionCheckHandler(() => false);
    window.webContents.setWindowOpenHandler(({url}) => { if (/^https?:\/\//.test(url)) shell.openExternal(url).catch(reportError); return {action: 'deny'}; });
    window.webContents.on('will-navigate', (event, url) => {
      if (url === welcomeURL || (service?.info && new URL(url).origin === service.info.url)) return;
      event.preventDefault();
      if (/^https?:\/\//.test(url) && new URL(url).hostname !== '127.0.0.1') shell.openExternal(url).catch(reportError);
      else reportError(Error('请使用桌面菜单打开工作区。'));
    });
    window.webContents.session.on('will-download', (event, item, contents) => {
      if (contents !== window.webContents || !service?.info || new URL(item.getURL()).origin !== service.info.url) { event.preventDefault(); return; }
      item.setSaveDialogOptions({title: '报告另存为', defaultPath: path.join(app.getPath('downloads'), path.basename(item.getFilename())), properties: ['showOverwriteConfirmation', 'createDirectory']});
      item.once('done', (_event, state) => { if (state === 'interrupted') reportError(Error('文件下载中断，请重新另存。')); });
    });
    ipcMain.handle('workspace:recent', event => { trusted(event); return recentWorkspace(); });
    ipcMain.handle('workspace:choose', (event, create) => { trusted(event); if (typeof create !== 'boolean') throw Error('无效选择'); return chooseWorkspace(create); });
    ipcMain.handle('workspace:open', (event, request) => { trusted(event); return openWorkspace(request); });
    ipcMain.on('workspace:prepared', (event, requestId, result) => { try { trusted(event); prepared.get(requestId)?.(result); } catch {} });
    Menu.setApplicationMenu(Menu.buildFromTemplate([
      {label: 'BriefLoop', submenu: [{role: 'about'}, {type: 'separator'}, {label: '退出 BriefLoop', accelerator: 'CmdOrCtrl+Q', click: requestQuit}]},
      {label: '文件', submenu: [{label: '新建工作区…', accelerator: 'CmdOrCtrl+N', click: () => chooseWorkspace(true).catch(reportError)},
        {label: '打开工作区…', accelerator: 'CmdOrCtrl+O', click: () => chooseWorkspace(false).catch(reportError)}, {type: 'separator'},
        {label: '保存当前修改', accelerator: 'CmdOrCtrl+S', click: saveCurrent}, {role: 'close'}]},
      {label: '编辑', submenu: [{role: 'undo'}, {role: 'redo'}, {type: 'separator'}, {role: 'cut'}, {role: 'copy'}, {role: 'paste'}, {role: 'selectAll'}]},
      {label: '视图', submenu: [{role: 'resetZoom'}, {role: 'zoomIn'}, {role: 'zoomOut'}, {role: 'togglefullscreen'}]},
      {label: '窗口', submenu: [{role: 'minimize'}, {role: 'front'}]},
    ]));
    await window.loadURL(welcomeURL);
  }).catch(error => { dialog.showErrorBox('BriefLoop 启动失败', String(error.message || error)); quitting = true; app.quit(); });
}
