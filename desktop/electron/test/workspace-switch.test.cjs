'use strict';
const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path').posix;
const main = fs.readFileSync(require.resolve('../main.cjs'), 'utf8');
const operations = main.slice(main.indexOf('async function stopCurrent()'), main.indexOf('async function chooseWorkspace('));
const oldURL = 'http://127.0.0.1:12345';
const targetURL = 'http://127.0.0.1:23456';

function fixture(options = {}) {
  const calls = [], instances = [], errors = [];
  const oldDocument = {unsaved: 'Synthetic editor text'};
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
  const previous = new Service({}, null);
  previous.directory = '/old'; previous.child = {owner: 'old'};
  previous.info = {path: '/old', url: oldURL};
  const context = vm.createContext({URL, path, WorkspaceService: Service,
    service: previous, switching: false, quitting: false, closePending: false,
    expectedExit: false, menuSave: null, workspaceOrigin: oldURL, welcomeURL: 'file:///welcome.html',
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
