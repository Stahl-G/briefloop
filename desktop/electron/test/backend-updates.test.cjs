'use strict';
const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs/promises');
const fsSync = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const {generateKeyPairSync, sign, createHash} = require('node:crypto');
const {createBackendUpdates} = require('../backend-updates.cjs');
const hash = bytes => createHash('sha256').update(bytes).digest('hex');
async function fixture(t) {
  const root = await fs.mkdtemp(path.join(os.tmpdir(), 'briefloop-backend-update-test-'));
  t.after(() => fs.rm(root, {recursive: true, force: true}));
  const payload = path.join(root, 'bundled');
  await fs.mkdir(payload);
  const lock = Buffer.from('locked dependency fixture');
  await fs.writeFile(path.join(payload, 'requirements.txt'), lock);
  await fs.writeFile(path.join(payload, 'manifest.json'), JSON.stringify({version: '1.0.0', requirements_sha256: hash(lock)}));
  const key = generateKeyPairSync('ed25519');
  const app = {getPath: () => root, getVersion: () => '1.0.0'};
  let envelope, wheel, badWheel = false, badSignature = false, badProbe = false, pauseDownload = false, newVersion;
  function publish(version, sequence) {
    newVersion = version;
    wheel = Buffer.from('different backend bytes for ' + version);
    const release = {schema: 1, version, sequence, expires: Date.now() + 3600000, source_commit: '1'.repeat(40),
      shell_range: '>=1.0.0 <1.1.0', desktop_protocol: 1, data_epoch: 1,
      requirements_sha256: hash(lock), wheel: `briefloop-${version}-py3-none-any.whl`, wheel_bytes: wheel.length, wheel_sha256: hash(wheel)};
    const bytes = Buffer.from(JSON.stringify(release));
    envelope = {schema: 1, key_id: 'test', payload: bytes.toString('base64'), signature: sign(null, bytes, key.privateKey).toString('base64')};
  }
  publish('1.0.1', 1);
  const notifications = [];
  const config = {app, payloadPath: payload, environmentChanged: value => notifications.push(value), keys: {test: key.publicKey},
    fetch: async (url, options) => {
      if (url.endsWith('/backend-update.json')) {
        return new Response(JSON.stringify(badSignature ? {...envelope, signature: Buffer.alloc(64).toString('base64')} : envelope));
      }
      assert.equal(url, `https://github.com/Stahl-G/briefloop/releases/download/v${newVersion}/briefloop-${newVersion}-py3-none-any.whl`);
      if (pauseDownload) return new Promise((resolve, reject) => options.signal.addEventListener('abort', () => reject(Error('interrupted')), {once: true}));
      return new Response(badWheel ? Buffer.from('tampered bytes') : wheel);
    },
    createEnv: ({payloadPath, changed}) => {
      const {version} = JSON.parse(fsSync.readFileSync(path.join(payloadPath, 'manifest.json')));
      return {prepare: async () => ({state: 'ready'}), startup: async () => { changed?.({state: 'ready'}); return {state: 'ready'}; }, runtime: () => ({version})};
    },
    probe: async runtime => { if (badProbe && runtime.version !== '1.0.0') throw Error('Export failed'); return 'same-database-format'; }};
  return {manager: () => createBackendUpdates(config), bundled: {runtime: () => ({version: '1.0.0'})}, publish,
    notifications,
    state: async () => JSON.parse(await fs.readFile(path.join(root, 'backend-updates/state.json'), 'utf8')),
    pauseDownload: value => {pauseDownload = value;},
    badSignature: value => {badSignature = value;}, badWheel: value => {badWheel = value;}, badProbe: value => {badProbe = value;}};
}

test('forged metadata, changed wheel and a failed export probe cannot activate a backend; a corrected preparation can retry', async t => {
  const f = await fixture(t);
  const m = f.manager();
  f.badSignature(true);
  assert.equal((await m.check(f.bundled)).state, 'error');
  f.badSignature(false);
  assert.equal((await m.check(f.bundled)).state, 'available');
  f.pauseDownload(true);
  const interrupted = m.download();
  await new Promise(resolve => setTimeout(resolve, 5));
  await m.cancel();
  assert.equal((await interrupted).state, 'error');
  await assert.rejects(m.activate());
  f.pauseDownload(false);
  f.badWheel(true);
  assert.equal((await m.download()).state, 'error');
  await assert.rejects(m.activate());
  f.badWheel(false); f.badProbe(true);
  assert.equal((await m.download()).state, 'error');
  await assert.rejects(m.activate());
  f.badProbe(false);
  assert.equal((await m.download()).state, 'downloaded');
  await m.activate();
  assert.equal((await f.state()).active, '1.0.1');
});

test('an unconfirmed restart rolls back without lowering replay protection; a confirmed restart retains the selected backend', async t => {
  const f = await fixture(t);
  let m = f.manager();
  await m.check(f.bundled); await m.download(); await m.activate();
  m = f.manager();
  assert.equal((await m.selected()).runtime().version, '1.0.1');
  assert.equal(f.notifications.at(-1).state, 'ready');
  m = f.manager();
  assert.equal(await m.selected(), null);
  assert.equal((await f.state()).high, 1);
  assert.equal((await m.check(f.bundled)).state, 'current');
  f.publish('1.0.2', 2);
  await m.check(f.bundled); await m.download(); await m.activate();
  m = f.manager();
  assert.equal((await m.selected()).runtime().version, '1.0.2');
  await m.confirm();
  m = f.manager();
  assert.equal((await m.selected()).runtime().version, '1.0.2');
});
