'use strict';
const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path').posix;
const main = fs.readFileSync(require.resolve('../main.cjs'), 'utf8');
const preload = fs.readFileSync(require.resolve('../preload.cjs'), 'utf8');
const code = main.slice(main.indexOf('function trusted(event)'), main.indexOf('function environmentOperation('));
const workspaceURL = 'http://127.0.0.1:23456';

function fixture(options = {}) {
  const calls = [], windows = [];
  let release;
  const mainFrame = {url: workspaceURL + '/'};
  const window = {webContents: {mainFrame}};
  class BrowserWindow {
    constructor(value) {
      this.options = value; this.destroyed = false; this.loaded = null; windows.push(this);
      const self = this;
      this.webContents = {
        session: {webRequest: {onBeforeRequest: filter => { self.filter = filter; }}},
        setWindowOpenHandler: handler => { self.openHandler = handler; },
        on: (name, handler) => { if (name === 'will-navigate') self.navigate = handler; },
        printToPDF: async value => {
          calls.push(['print', value]);
          if (options.hold) await new Promise(resolve => { release = resolve; });
          if (options.printError) throw Error('print failed');
          return Buffer.from('%PDF-synthetic');
        },
      };
    }
    async loadFile(file) { this.loaded = file; calls.push(['load', file]); }
    isDestroyed() { return this.destroyed; }
    destroy() { this.destroyed = true; calls.push('destroy'); }
  }
  const context = vm.createContext({URL, path, Buffer, BrowserWindow, window,
    console: {warn: (...values) => calls.push(['warn', ...values])},
    welcomeURL: 'file:///welcome.html', workspaceOrigin: workspaceURL,
    app: {getPath: name => name === 'temp' ? '/tmp' : '/Users/synthetic/Downloads'},
    fs: {
      mkdtemp: async prefix => { calls.push(['mkdtemp', prefix]); return prefix + 'abc'; },
      writeFile: async (file, data, value) => { calls.push(['write', file, String(data), value]); },
      rm: async (file, value) => { calls.push(['rm', file, value]); },
    },
    dialog: {showSaveDialog: async (owner, value) => {
      calls.push(['save-dialog', owner === window, value]);
      return options.cancel ? {canceled: true} : {canceled: false, filePath: '/Users/synthetic/Documents/季度 报告.pdf'};
    }},
  });
  vm.runInContext(code, context);
  const event = (overrides = {}) => ({sender: window.webContents, senderFrame: mainFrame, ...overrides});
  return {context, calls, windows, event, mainFrame, release: () => release?.()};
}

test('desktop PDF export renders offline without scripts and saves where the user picks', async () => {
  const {context, calls, windows, event} = fixture();
  const html = '<!doctype html><title>x</title><p>正文</p>';
  const result = await context.exportReportPdf(event(), {html, title: ' 季度/报告:终稿. '});
  assert.deepEqual({...result}, {status: 'saved', name: '季度 报告.pdf'});
  assert.equal(windows.length, 1);
  const [printer] = windows;
  assert.equal(printer.options.show, false);
  assert.deepEqual({...printer.options.webPreferences}, {partition: 'briefloop-pdf-export', javascript: false, sandbox: true,
    contextIsolation: true, nodeIntegration: false, webSecurity: true, spellcheck: false});
  assert.ok(!printer.options.webPreferences.partition.startsWith('persist:'));
  assert.ok(!('preload' in printer.options.webPreferences));
  const written = calls.find(call => call[0] === 'write' && call[1].endsWith('report.html'));
  assert.equal(written[2], html);
  assert.equal(written[3].mode, 0o600);
  assert.equal(printer.loaded, written[1]);
  assert.deepEqual({...calls.find(call => call[0] === 'print')[1]}, {printBackground: true, preferCSSPageSize: true});
  const dialog = calls.find(call => call[0] === 'save-dialog');
  assert.equal(dialog[1], true);
  assert.equal(dialog[2].defaultPath, '/Users/synthetic/Downloads/季度_报告_终稿.pdf');
  assert.ok(calls.some(call => call[0] === 'write' && call[1] === '/Users/synthetic/Documents/季度 报告.pdf' && call[2] === '%PDF-synthetic'));
  assert.equal(printer.destroyed, true);
  assert.ok(calls.some(call => call[0] === 'rm' && call[1] === '/tmp/briefloop-pdf-abc' && call[2].recursive));
  assert.equal(printer.openHandler().action, 'deny');
  let prevented = false; printer.navigate({preventDefault: () => { prevented = true; }});
  assert.equal(prevented, true);
});

test('the hidden renderer loads only its own file and inline data', async () => {
  const {context, windows, event} = fixture();
  await context.exportReportPdf(event(), {html: '<p>x</p>', title: 'x'});
  const filter = windows[0].filter, decide = details => { let decision; filter(details, value => { decision = value.cancel; }); return decision; };
  assert.equal(decide({url: 'file:///tmp/briefloop-pdf-abc/report.html', resourceType: 'mainFrame'}), false);
  assert.equal(decide({url: 'data:image/png;base64,AAAA', resourceType: 'image'}), false);
  assert.equal(decide({url: 'file:///etc/passwd', resourceType: 'image'}), true);
  assert.equal(decide({url: 'https://example.test/pixel.png', resourceType: 'image'}), true);
  assert.equal(decide({url: 'http://127.0.0.1:23456/api/state', resourceType: 'xhr'}), true);
});

test('cancelling the save dialog writes no PDF and still cleans up', async () => {
  const {context, calls, windows, event} = fixture({cancel: true});
  assert.deepEqual({...await context.exportReportPdf(event(), {html: '<p>x</p>', title: 'x'})}, {status: 'cancelled'});
  assert.ok(!calls.some(call => call[0] === 'write' && call[1].endsWith('.pdf')));
  assert.equal(windows[0].destroyed, true);
  assert.ok(calls.some(call => call[0] === 'rm'));
});

test('a failed render cleans up and does not block the next export', async () => {
  const {context, calls, windows, event} = fixture({printError: true});
  await assert.rejects(context.exportReportPdf(event(), {html: '<p>x</p>', title: 'x'}), {message: 'print failed'});
  assert.equal(windows[0].destroyed, true);
  assert.ok(calls.some(call => call[0] === 'rm'));
  await assert.rejects(context.exportReportPdf(event(), {html: '<p>x</p>', title: 'x'}), {message: 'print failed'});
  assert.equal(windows.length, 2);
});

test('locked temporary files preserve the export outcome and allow the next export', async () => {
  for (const options of [{}, {cancel: true}, {printError: true}]) {
    const {context, calls, windows, event} = fixture(options);
    const remove = context.fs.rm;
    context.fs.rm = async () => { throw Object.assign(Error('temporary report is locked'), {code: 'EBUSY'}); };
    const exportPdf = () => context.exportReportPdf(event(), {html: '<p>x</p>', title: 'x'});
    if (options.printError) await assert.rejects(exportPdf(), {message: 'print failed'});
    else assert.equal((await exportPdf()).status, options.cancel ? 'cancelled' : 'saved');
    assert.equal(windows[0].destroyed, true);
    assert.ok(calls.some(call => call[0] === 'warn' && call.includes('EBUSY')));

    context.fs.rm = remove;
    if (options.printError) await assert.rejects(exportPdf(), {message: 'print failed'});
    else assert.equal((await exportPdf()).status, options.cancel ? 'cancelled' : 'saved');
    assert.equal(windows.length, 2);
  }
});

test('only the workspace page may export, one valid document at a time', async () => {
  const {context, windows, event, mainFrame, release} = fixture({hold: true});
  await assert.rejects(context.exportReportPdf(event({sender: {}}), {html: '<p>x</p>', title: 'x'}), {message: '窗口身份不匹配。'});
  await assert.rejects(context.exportReportPdf(event({senderFrame: {url: workspaceURL}}), {html: '<p>x</p>', title: 'x'}), {message: '窗口身份不匹配。'});
  mainFrame.url = 'file:///welcome.html';
  await assert.rejects(context.exportReportPdf(event(), {html: '<p>x</p>', title: 'x'}), {message: '请在工作区页面导出 PDF。'});
  mainFrame.url = workspaceURL + '/';
  for (const request of [null, {html: '', title: 'x'}, {html: 1, title: 'x'}, {html: '<p>x</p>'}, {html: 'x'.repeat(256 * 1024 * 1024 + 1), title: 'x'}]) {
    await assert.rejects(context.exportReportPdf(event(), request), {message: '导出内容无效。'});
  }
  assert.equal(windows.length, 0);
  const first = context.exportReportPdf(event(), {html: '<p>x</p>', title: 'x'});
  while (!windows.length || !release) await new Promise(resolve => setImmediate(resolve));
  await assert.rejects(context.exportReportPdf(event(), {html: '<p>y</p>', title: 'y'}), {message: '正在导出另一份 PDF，请稍候。'});
  release();
  assert.equal((await first).status, 'saved');
});

test('preload exposes the PDF export as a fixed operation and main registers it', () => {
  assert.match(preload, /exportPdf: request => ipcRenderer\.invoke\('report:export-pdf', \{html: String\(request\?\.html \?\? ''\), title: String\(request\?\.title \?\? ''\)\}\)/);
  assert.match(main, /ipcMain\.handle\('report:export-pdf', exportReportPdf\);/);
});
