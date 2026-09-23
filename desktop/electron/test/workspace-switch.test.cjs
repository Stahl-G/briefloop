'use strict';
const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path').posix;
const main = fs.readFileSync(require.resolve('../main.cjs'), 'utf8');
const welcome = fs.readFileSync(require.resolve('../welcome.js'), 'utf8');
const operations = main.slice(main.indexOf('async function stopCurrent()'), main.indexOf('async function chooseWorkspace('));
const oldURL = 'http://127.0.0.1:12345';
const targetURL = 'http://127.0.0.1:23456';
const welcomeURL = 'file:///welcome.html';

function fixture(options = {}) {
  const calls = [], instances = [], errors = [];
  const oldDocument = options.welcome ? {url: welcomeURL} : {unsaved: 'Synthetic editor text'};
  let document = oldDocument;
  class Service {
    constructor(runtime, exited) { this.runtime = runtime; this.exited = exited; this.child = null; this.info = null; instances.push(this); }
    async preflight(directory, value) {
      calls.push(['preflight', directory, value.create]);
      if (options.preflightError) throw Error('preflight failed');
      return options.realpath || directory;
    }
    async status() { calls.push('status'); return {busy: !!options.busy}; }
    async stop(value) {
      const old = this === previous;
      calls.push([old ? 'stop-old' : 'stop-target', value.cancelBusy]);
      if (!old && options.targetStopError) throw Error('target stop failed');
      this.child = null; this.info = null;
    }
    async start(directory, value = {}) {
      const old = this === previous;
      calls.push([old ? 'restore-old' : 'start-target', directory, value.port]);
      if (old && options.restoreError) throw Error('restore failed');
      this.directory = directory;
      if (!old && options.startError) {
        if (options.liveOnStartError) this.child = {target: true};
        throw Error('target start failed');
      }
      this.child = {owner: old ? 'old' : 'target'};
      this.info = {path: directory, url: old ? oldURL : targetURL};
      return this.info;
    }
  }
  const previous = options.welcome ? null : new Service({}, null);
  if (previous) {
    previous.directory = '/old'; previous.child = {owner: 'old'};
    previous.info = {path: '/old', url: oldURL};
  }
  const context = vm.createContext({URL, path, WorkspaceService: Service,
    service: previous, switching: false, quitting: false, closePending: false,
    expectedExit: false, menuSave: null, workspaceOrigin: previous ? oldURL : null, welcomeURL,
    environment: {runtime: async () => { calls.push('runtime'); return {python: 'synthetic'}; }},
    prepareClose: async () => { calls.push('save'); },
    resumeEditing: () => calls.push('resume'),
    rememberWorkspace: async directory => calls.push(['remember', directory]),
    reportError: async error => { errors.push(error.message); },
    dialog: {showMessageBox: async (_window, value) => {
      calls.push(['dialog', value.type]);
      if (options.dialog) return options.dialog(value);
      return {response: options.confirm ?? 1};
    }},
    window: {isDestroyed: () => false, setTitle: title => calls.push(['title', title]),
      webContents: {getURL: () => document.url || oldURL},
      loadURL: async url => {
        calls.push(['load', url]);
        if (url === targetURL && options.loadError) throw Error('target page failed');
        if (url === oldURL && options.restorePageError) throw Error('old page failed');
        document = {url};
      }},
  });
  vm.runInContext(operations, context);
  return {calls, context, previous, instances, errors, oldDocument,
    document: () => document,
    run: () => context.openWorkspace({path: '/target', create: false}),
    visibleRun: async () => { try { return await context.openWorkspace({path: '/target', create: false}); }
      catch (error) { await context.reportError(error); return null; } },
  };
}
const named = (f, name) => f.calls.filter(value => Array.isArray(value) && value[0] === name);

test('an invalid folder keeps the welcome renderer alive to show its opening error', async () => {
  const f = fixture({welcome: true, preflightError: true});
  const status = {textContent: ''};
  const renderer = vm.createContext({status, environment: {state: 'ready'}, opening: false,
    renderEnvironment() {}, open: f.run});
  vm.runInContext(welcome.slice(welcome.indexOf('function welcomeErrorMessage('), welcome.indexOf("document.getElementById('prepare').onclick")), renderer);
  await vm.runInContext('action(open)', renderer);
  assert.equal(f.document(), f.oldDocument, 'the IPC rejection must reach the same welcome document');
  assert.match(status.textContent, /preflight failed/);
  assert.equal(named(f, 'load').length, 0); assert.equal(named(f, 'start-target').length, 0);
  assert.equal(renderer.opening, false); assert.equal(f.context.switching, false);
  assert.equal(f.context.service, null);
});

test('welcome removes the IPC envelope while preserving the actionable error and diagnostic type', async () => {
  const status = {textContent: ''};
  const renderer = vm.createContext({status, environment: {state: 'ready'}, opening: false, renderEnvironment() {}});
  vm.runInContext(welcome.slice(welcome.indexOf('function welcomeErrorMessage('), welcome.indexOf("document.getElementById('prepare').onclick")), renderer);
  const explanation = '这个文件夹还不是 BriefLoop 工作区，请使用“新建工作区”。';
  for (const [message, expected] of [
    [`Error invoking remote method 'workspace:choose': Error: ${explanation}`, explanation],
    ["Error invoking remote method 'workspace:open': TypeError: invalid workspace result", 'TypeError: invalid workspace result'],
    ['EACCES: cannot read workspace', 'EACCES: cannot read workspace'],
  ]) {
    const error = Error(message), stack = error.stack;
    renderer.open = async () => { throw error; };
    await vm.runInContext('action(open)', renderer);
    assert.equal(status.textContent, expected);
    assert.equal(error.message, message); assert.equal(error.stack, stack);
    assert.equal(renderer.opening, false);
  }
});

test('target preflight fails before saving or stopping the current editor', async () => {
  const f = fixture({preflightError: true});
  await assert.rejects(f.run(), /preflight failed/);
  assert.deepEqual(f.calls, ['runtime', ['preflight', '/target', false], 'resume']);
  assert.equal(f.context.service, f.previous); assert.ok(f.previous.child);
  assert.equal(f.document(), f.oldDocument); assert.equal(f.context.switching, false);
});

test('failed target startup restores the previous port without replacing its editor document', async () => {
  const f = fixture({startError: true});
  await assert.rejects(f.run(), /target start failed.*已恢复原工作区/);
  assert.deepEqual(named(f, 'restore-old'), [['restore-old', '/old', 12345]]);
  assert.equal(named(f, 'load').length, 0); assert.equal(named(f, 'remember').length, 0);
  assert.equal(f.document(), f.oldDocument); assert.equal(f.context.service, f.previous);
  assert.equal(f.context.workspaceOrigin, oldURL); assert.ok(f.previous.child);
  assert.equal(f.context.switching, false);
});

test('failed target navigation stops its service before restoring the old service and page', async () => {
  const f = fixture({loadError: true});
  await assert.rejects(f.run(), /target page failed.*已恢复原工作区/);
  const sequence = f.calls.filter(value => Array.isArray(value)).map(value => value[0]);
  assert.ok(sequence.indexOf('stop-target') < sequence.indexOf('restore-old'));
  assert.deepEqual(named(f, 'stop-target'), [['stop-target', true]]);
  assert.deepEqual(named(f, 'load'), [['load', targetURL], ['load', oldURL]]);
  assert.equal(f.instances[1].child, null); assert.equal(f.context.service, f.previous);
  assert.equal(f.context.workspaceOrigin, oldURL); assert.equal(f.document().url, oldURL);
  assert.equal(named(f, 'remember').length, 0);
});

test('declining cancellation of busy work does not start the target', async () => {
  const f = fixture({busy: true, confirm: 0});
  assert.equal((await f.run()).cancelled, true);
  assert.equal(named(f, 'start-target').length, 0); assert.equal(named(f, 'stop-old').length, 0);
  assert.equal(f.context.service, f.previous); assert.ok(f.previous.child);
  assert.equal(f.document(), f.oldDocument); assert.equal(f.calls.at(-1), 'resume');
});

test('failed recovery surfaces an error instead of reporting a successful workspace switch', async () => {
  for (const options of [{startError: true, restoreError: true}, {loadError: true, restorePageError: true}]) {
    const f = fixture(options);
    assert.equal(await f.visibleRun(), null);
    assert.equal(f.errors.length, 1); assert.match(f.errors[0], /原工作区尚未重新连接/);
    assert.doesNotMatch(f.errors[0], /已恢复原工作区/);
    assert.equal(named(f, 'remember').length, 0); assert.equal(f.context.switching, false);
  }
});

test('a target that cannot stop remains owned and prevents a second managed service', async () => {
  for (const options of [{loadError: true, targetStopError: true}, {startError: true, liveOnStartError: true}]) {
    const f = fixture(options);
    await assert.rejects(f.run());
    assert.equal(f.context.service, f.instances[1]); assert.ok(f.context.service.child);
    assert.equal(f.previous.child, null); assert.equal(named(f, 'restore-old').length, 0);
    assert.equal(named(f, 'remember').length, 0);
    assert.equal(f.instances.filter(service => service.child).length, 1);
  }
});

test('preflight aliases to the current real directory preserve the existing service without a save gate', async () => {
  const f = fixture({realpath: '/old'});
  assert.equal((await f.run()).url, oldURL);
  assert.equal(f.context.service, f.previous); assert.equal(f.document(), f.oldDocument);
  assert.ok(!f.calls.includes('save')); assert.equal(named(f, 'start-target').length, 0);
});

test('reconnection reuses the old port and preserves the unsaved page', async () => {
  const f = fixture({confirm: 0});
  const owned = f.context.workspaceService({python: 'synthetic'});
  owned.directory = '/old'; f.context.service = owned;
  await owned.exited({lastInfo: {url: oldURL}});
  assert.deepEqual(named(f, 'start-target'), [['start-target', '/old', 12345]]);
  assert.equal(named(f, 'load').length, 0); assert.equal(f.document(), f.oldDocument);
  assert.equal(f.context.switching, false); assert.equal(f.calls.at(-1), 'resume');
});

test('an old exit dialog cannot restart a service after ownership changes or while switching', async () => {
  let answer;
  const f = fixture({dialog: () => new Promise(resolve => { answer = resolve; })});
  const owned = f.context.workspaceService({python: 'synthetic'});
  owned.directory = '/old'; f.context.service = owned;
  const exited = owned.exited({lastInfo: {url: oldURL}});
  f.context.service = f.previous;
  answer({response: 0}); await exited;
  assert.equal(named(f, 'start-target').length, 0);
  f.context.service = owned; f.context.switching = true;
  await owned.exited({lastInfo: {url: oldURL}});
  assert.equal(named(f, 'dialog').length, 1);
});
