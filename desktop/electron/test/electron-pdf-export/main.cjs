'use strict';
// Real-Electron acceptance for the desktop PDF export channel (#733/#746).
// The unit test in ../pdf-export.test.cjs uses doubles; this one runs the same
// extracted handler inside a real Electron main process, so the window options,
// the request filter and printToPDF are exercised by the actual runtime.
//
//   desktop/electron $ npm run verify:pdf         (or)
//   <electron binary> desktop/electron/test/electron-pdf-export
const {app, BrowserWindow, dialog} = require('electron');
const fs = require('node:fs/promises');
const fsSync = require('node:fs');
const http = require('node:http');
const os = require('node:os');
const path = require('node:path');

const source = fsSync.readFileSync(path.join(__dirname, '..', '..', 'main.cjs'), 'utf8');
const code = source.slice(source.indexOf('function trusted(event)'), source.indexOf('function environmentOperation('));
const checks = [];
let failures = 0;

function check(name, condition, detail) {
  checks.push({name, ok: !!condition, ...(detail === undefined ? {} : {detail})});
  if (!condition) failures += 1;
}

// Destroying the hidden render window must not end this checker.
app.on('window-all-closed', () => {});

async function main() {
  await app.whenReady();
  const workspace = await new Promise(resolve => {
    const server = http.createServer((request, response) => {
      hits.push(request.url);
      response.writeHead(200, {'Content-Type': 'image/png'});
      response.end(Buffer.from('89504e470d0a1a0a', 'hex'));
    });
    const hits = [];
    server.hits = hits;
    server.listen(0, '127.0.0.1', () => resolve(server));
  });
  const origin = `http://127.0.0.1:${workspace.address().port}`;
  const mainFrame = {url: origin + '/'};
  const mainWindow = {webContents: {mainFrame}};
  let saveTo = null, savePermission = Promise.resolve();
  const build = new Function('BrowserWindow', 'dialog', 'fs', 'path', 'app', 'window', 'welcomeURL', 'workspaceOrigin', 'URL',
    code + '\nreturn {exportReportPdf, exportFileName};');
  const shell = build(BrowserWindow, {
    showSaveDialog: async (owner, options) => {
      await savePermission;
      return saveTo ? {canceled: false, filePath: saveTo} : {canceled: true};
    },
  }, fs, path, app, mainWindow, 'file:///welcome.html', origin, URL);
  const event = {sender: mainWindow.webContents, senderFrame: mainFrame};
  const output = await fs.mkdtemp(path.join(os.tmpdir(), 'pdf-export-acceptance-'));
  const renderFolders = async () => (await fs.readdir(app.getPath('temp'))).filter(name => name.startsWith('briefloop-pdf-'));
  const stale = await renderFolders();
  const pixel = 'data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8DwHwAFBQIAX8jx0gAAAABJRU5ErkJggg==';
  const report = '<!doctype html><html lang="zh-CN"><meta charset="utf-8"><title>季度报告</title>'
    + '<style>body{font-family:-apple-system,sans-serif;margin:40px}table{border-collapse:collapse;width:100%}'
    + 'td,th{border:1px solid #DEDFD8;padding:8px}@page{size:A4;margin:20mm}</style><body>'
    + '<h1>季度报告</h1><p>本期交付三项，图表与引用如下。</p>'
    + '<table><tr><th>项目</th><th>数量</th></tr><tr><td>交付</td><td>3</td></tr></table>'
    + `<img src="${pixel}" width="80" height="80" alt="inline">`
    + `<img src="${origin}/blocked-image.png" alt="external">`
    + `<script>fetch('${origin}/blocked-script')</` + 'script>'
    + '<div style="page-break-before:always"></div><h2>来源</h2><ol><li id="reference-1">合成材料</li></ol>'
    + '</body></html>';

  // 1. A real export saves a real PDF where the user picked.
  saveTo = path.join(output, '季度报告.pdf');
  const before = BrowserWindow.getAllWindows().length;
  const saved = await shell.exportReportPdf(event, {html: report, title: ' 季度/报告:终稿. '});
  check('saved status', saved.status === 'saved', saved);
  const bytes = await fs.readFile(saveTo);
  check('real PDF bytes', bytes.subarray(0, 5).toString() === '%PDF-', bytes.subarray(0, 8).toString());
  check('PDF has content', bytes.length > 3000, bytes.length);
  check('two pages rendered', (bytes.toString('latin1').match(/\/Type\s*\/Page[^s]/g) || []).length === 2,
    (bytes.toString('latin1').match(/\/Type\s*\/Page[^s]/g) || []).length);
  check('file name sanitised for the dialog', shell.exportFileName(' 季度/报告:终稿. ') === '季度_报告_终稿');

  // 2. The offline renderer really is offline, and its scripts never run.
  check('no external request reached the workspace', workspace.hits.length === 0, workspace.hits);

  // 3. The hidden window is gone and the temporary file is cleaned up.
  check('no window left behind', BrowserWindow.getAllWindows().length === before, BrowserWindow.getAllWindows().length);
  const temporary = (await renderFolders()).filter(name => !stale.includes(name));
  check('temporary render folder removed', temporary.length === 0, {left: temporary, stale});

  // 4. Cancelling the save dialog writes nothing.
  saveTo = null;
  const cancelled = await shell.exportReportPdf(event, {html: report, title: 'x'});
  check('cancel status', cancelled.status === 'cancelled', cancelled);
  check('cancel wrote no file', (await fs.readdir(output)).length === 1, await fs.readdir(output));

  // 5. Two exports at once are refused rather than sharing the render session.
  let release;
  savePermission = new Promise(resolve => { release = resolve; });
  saveTo = path.join(output, 'second.pdf');
  const first = shell.exportReportPdf(event, {html: report, title: 'first'});
  await new Promise(resolve => setTimeout(resolve, 300));
  let concurrent = 'not refused';
  try { await shell.exportReportPdf(event, {html: report, title: 'second'}); }
  catch (error) { concurrent = error.message; }
  check('concurrent export refused', concurrent.includes('正在导出另一份 PDF'), concurrent);
  release();
  check('first export still completed', (await first).status === 'saved');

  // 6. The same window options really disable scripting in this Electron build.
  const probe = new BrowserWindow({show: false, webPreferences: {javascript: false, sandbox: true, contextIsolation: true, nodeIntegration: false}});
  await probe.loadURL('data:text/html,<title>probe</title>');
  let scripting = 'ran';
  try { await probe.webContents.executeJavaScript('document.title = "SCRIPT RAN"'); }
  catch (error) { scripting = 'refused'; }
  check('javascript:false blocks scripting', scripting === 'refused' || (await probe.webContents.getTitle()) === 'probe',
    {scripting, title: await probe.webContents.getTitle()});
  probe.destroy();

  // BRIEFLOOP_PDF_KEEP=1 leaves the produced file for a human to open.
  if (process.env.BRIEFLOOP_PDF_KEEP) console.log('kept: ' + output);
  else await fs.rm(output, {recursive: true, force: true});
  workspace.close();
  console.log(JSON.stringify({electron: process.versions.electron, chrome: process.versions.chrome, failures, checks}, null, 2));
  app.exit(failures ? 1 : 0);
}

main().catch(error => {
  console.error(error && error.stack || String(error));
  app.exit(2);
});
