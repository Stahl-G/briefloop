'use strict';
const {test} = require('node:test');
const assert = require('node:assert/strict');
const {EventEmitter} = require('node:events');
const fs = require('node:fs');
const vm = require('node:vm');
const {installNavigation, isWorkspaceDownload} = require('../navigation.cjs');

test('report links download without replacing the workspace or opening a browser', () => {
  const contents = new EventEmitter(), downloaded = [], external = [], errors = [];
  contents.downloadURL = url => downloaded.push(url);
  contents.setWindowOpenHandler = callback => { contents.open = callback; };
  let workspace = 'http://127.0.0.1:23456';
  installNavigation({contents, getWorkspaceURL: () => workspace, welcomeURL: 'file:///welcome.html',
    shell: {openExternal: async url => external.push(url)}, reportError: error => errors.push(error)});
  function navigate(url) {
    let prevented = false;
    contents.emit('will-navigate', {preventDefault() { prevented = true; }}, url);
    return prevented;
  }
  const url = workspace + '/api/download?version=synthetic';
  assert.equal(navigate(url), true);
  assert.deepEqual(contents.open({url}), {action: 'deny'});
  assert.deepEqual(downloaded, [url, url]);
  assert.deepEqual(external, []);
  assert.equal(navigate(workspace + '/'), false);
  assert.equal(navigate('https://example.org/'), true);
  assert.deepEqual(external, ['https://example.org/']);
  workspace = 'http://127.0.0.1:34567';
  assert.equal(navigate(url), true);
  assert.equal(downloaded.length, 2);
  assert.equal(errors.length, 1);
});

test('inline download recovery skips the absent editor only, retaining the task stop gate', async () => {
  const main = fs.readFileSync(require.resolve('../main.cjs'), 'utf8');
  const workspace = 'http://127.0.0.1:23456';
  let url = workspace + '/api/download?version=synthetic', response = 0, pendingSave;
  const calls = [];
  const context = vm.createContext({isWorkspaceDownload, welcomeURL: 'file:///welcome.html',
    window: {webContents: {getURL: () => url, send: (...args) => calls.push(args)}, loadURL: async target => { calls.push(['load', target]); url = target; }},
    service: {info: {url: workspace}, child: {}, status: async () => ({busy: true}), stop: async options => calls.push(['stop', options.cancelBusy])},
    dialog: {showMessageBox: async () => ({response})}, prepared: new Map(), randomUUID: () => 'save-request',
    setTimeout: () => 1, clearTimeout: () => {}, closePending: false, switching: false, menuSave: null, expectedExit: false});
  vm.runInContext(main.slice(main.indexOf('function prepareClose()'), main.indexOf('function workspaceService(')), context);
  assert.equal((await context.prepareClose()).status, 'not-editor');
  assert.equal(await context.stopCurrent(), false);
  assert.deepEqual(calls, []);
  response = 1;
  assert.equal(await context.stopCurrent(), true);
  assert.deepEqual(calls, [['stop', true]]);
  await context.returnToWorkspace();
  assert.equal(url, workspace);
  pendingSave = context.prepareClose();
  assert.equal(context.prepared.size, 1);
  context.prepared.get('save-request')({status: 'failed', error: 'synthetic save failure'});
  await assert.rejects(pendingSave, /synthetic save failure/);
  const count = calls.length;
  await context.returnToWorkspace();
  assert.equal(calls.length, count, 'must not reload a live editor');
  assert.equal(isWorkspaceDownload('http://evil.invalid/api/download', workspace), false);
});
