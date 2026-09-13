'use strict';
const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs/promises');
const os = require('node:os');
const path = require('node:path');
const http = require('node:http');
const crypto = require('node:crypto');
const {EventEmitter} = require('node:events');
const {createUpdater} = require('../updater.cjs');

async function fixture(t, options = {}) {
  const directory = await fs.mkdtemp(path.join(os.tmpdir(), 'briefloop-update-test-'));
  const bytes = Buffer.from('Synthetic DMG payload; not an executable or installer.');
  let failures = options.failures || 0, assetRequests = 0;
  const server = http.createServer((req, res) => {
    if (req.url === '/release') {res.setHeader('Content-Type', 'application/json'); res.end(JSON.stringify(release)); return;}
    assetRequests++;
    if (failures-- > 0) {res.writeHead(503); res.end('synthetic unavailable'); return;}
    if (options.redirect) {res.writeHead(302, {Location: options.redirect}); res.end(); return;}
    res.writeHead(200, {'Content-Length': bytes.length}); res.end(bytes);
  });
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  const origin = `http://127.0.0.1:${server.address().port}`;
  const release = {tag_name: 'v0.20.0', draft: false, prerelease: false, html_url: `${origin}/notes`,
    body: 'Synthetic release notes', assets: [{name: 'BriefLoop-0.20.0-arm64.dmg', size: bytes.length,
      browser_download_url: `${origin}/asset`, digest: 'sha256:' + crypto.createHash('sha256').update(bytes).digest('hex')}]};
  const changes = [], opened = [];
  const config = {app: {getVersion: () => '0.19.0', getPath: () => directory},
    shell: {openPath: async file => {opened.push(file); return '';}}, platform: 'darwin', arch: 'arm64',
    changed: value => changes.push(value), testFeed: `${origin}/release`};
  const updater = createUpdater(config);
  t.after(async () => {await new Promise(resolve => server.close(resolve)); await fs.rm(directory, {recursive: true, force: true});});
  return {updater, release, bytes, changes, opened, directory, config, assetRequests: () => assetRequests};
}

test('local-test download failure retries, verifies bytes, and opens only after installReady', async t => {
  const f = await fixture(t, {failures: 1});
  assert.equal(f.updater.status().currentAppVersion, '0.19.0');
  const checked = await f.updater.check();
  assert.equal(checked.source, 'local-test'); assert.equal(checked.state, 'available');
  assert.equal(checked.installMode, 'dmg'); assert.equal(checked.releaseVersion, '0.20.0');
  assert.equal(f.assetRequests(), 0); assert.equal(f.opened.length, 0);
  const failed = await f.updater.download();
  assert.equal(failed.state, 'error'); assert.equal(failed.retryable, true); assert.equal(failed.error.code, 'http_503');
  assert.deepEqual(await fs.readdir(path.join(f.directory, 'updates')), []);
  assert.equal((await f.updater.download()).state, 'downloaded');
  assert.equal(f.updater.status().progress.percent, 100); assert.equal(f.opened.length, 0);
  assert.deepEqual(await f.updater.installReady(), {mode: 'dmg', opened: true, manualInstall: true});
  assert.deepEqual(await fs.readFile(f.opened[0]), f.bytes);
  assert.ok(f.changes.some(value => value.state === 'downloading' && value.progress.transferred > 0));
  assert.equal(JSON.stringify(f.updater.status()).includes(f.directory), false);
});

test('digest mismatch never installs; fresh check and download can recover', async t => {
  const f = await fixture(t); const digest = f.release.assets[0].digest;
  f.release.assets[0].digest = 'sha256:' + '0'.repeat(64);
  await f.updater.check();
  assert.equal((await f.updater.download()).error.code, 'hash_mismatch');
  await assert.rejects(f.updater.installReady(), /尚未下载/);
  assert.equal(f.opened.length, 0);
  f.release.assets[0].digest = digest;
  await f.updater.check(); assert.equal((await f.updater.download()).state, 'downloaded');
});

test('stable checks do not downgrade and reject prerelease and missing matching DMG', async t => {
  const f = await fixture(t);
  f.release.tag_name = 'v0.18.0'; assert.equal((await f.updater.check()).state, 'current');
  assert.equal((await f.updater.download()).error.code, 'not_available');
  f.release.tag_name = 'v0.21.0-beta.1'; assert.equal((await f.updater.check()).error.code, 'invalid_release');
  f.release.tag_name = 'v0.21.0'; f.release.prerelease = true;
  assert.equal((await f.updater.check()).error.code, 'unstable_release');
  f.release.prerelease = false; f.release.assets = [];
  assert.equal((await f.updater.check()).error.code, 'asset_unavailable');
  assert.equal(f.assetRequests(), 0);
});

test('test feed and redirects cannot escape their explicit loopback origin', async t => {
  const f = await fixture(t, {redirect: 'https://attacker.invalid/file.dmg'});
  assert.throws(() => createUpdater({...f.config, testFeed: 'https://attacker.invalid/release'}), /Invalid local/);
  await f.updater.check();
  const failed = await f.updater.download();
  assert.equal(failed.error.code, 'untrusted_url'); assert.equal(f.opened.length, 0);
  assert.equal(f.assetRequests(), 1);
});

test('official source only accepts repository assets, with app version independent of backend', async t => {
  const f = await fixture(t);
  const updater = createUpdater({...f.config, testFeed: null,
    fetch: async url => {
      assert.equal(url, 'https://api.github.com/repos/Stahl-G/briefloop/releases/latest');
      return new Response(JSON.stringify({...f.release, html_url: 'https://github.com/Stahl-G/briefloop/releases/tag/v0.20.0',
        assets: [{...f.release.assets[0], browser_download_url: 'https://github.com/other/project/releases/download/v0.20.0/app.dmg'}]}));
    }});
  assert.equal((await updater.check()).error.code, 'untrusted_url');
  assert.equal(updater.status().source, 'github');
});

test('native integration delegates download/install and never auto-installs on quit', async () => {
  class Native extends EventEmitter {
    calls = [];
    setFeedURL(value) {this.feed = value;}
    async checkForUpdates() {this.calls.push('check'); return {updateInfo: {version: '0.20.0', releaseNotes: 'Native notes'}};}
    async downloadUpdate() {this.calls.push('download'); this.emit('download-progress', {percent: 50, transferred: 5, total: 10}); return ['installer.exe'];}
    quitAndInstall(...args) {this.calls.push(['install', ...args]);}
  }
  const native = new Native();
  const updater = createUpdater({app: {getVersion: () => '0.19.0'}, shell: {}, platform: 'win32', nativeUpdater: native});
  await updater.check();
  assert.deepEqual(native.feed, {provider: 'github', owner: 'Stahl-G', repo: 'briefloop', private: false});
  assert.equal(native.autoDownload, false); assert.equal(native.autoInstallOnAppQuit, false);
  assert.equal(native.allowPrerelease, false); assert.equal(native.allowDowngrade, false);
  await updater.download(); assert.deepEqual(native.calls, ['check', 'download']);
  assert.deepEqual(await updater.installReady(), {mode: 'native', requested: true});
  assert.deepEqual(native.calls[2], ['install', false, true]);
});

test('temporary DMG open failure retries the verified existing bytes', async t => {
  const f = await fixture(t);
  let attempts = 0;
  const updater = createUpdater({...f.config, shell: {openPath: async () => ++attempts === 1 ? 'synthetic failure' : ''}});
  await updater.check(); await updater.download();
  await assert.rejects(updater.installReady(), /无法打开/);
  assert.equal(updater.status().error.code, 'open_failed');
  assert.equal(updater.status().retryable, true);
  assert.deepEqual(await updater.installReady(), {mode: 'dmg', opened: true, manualInstall: true});
  assert.equal(attempts, 2); assert.equal(f.assetRequests(), 1);
});

test('DMG version and exact official release tag must match metadata', async t => {
  const f = await fixture(t);
  f.release.assets[0].name = 'BriefLoop-0.18.0-arm64.dmg';
  assert.equal((await f.updater.check()).error.code, 'asset_version_mismatch');
  f.release.assets[0].name = 'BriefLoop-0.20.0-arm64.dmg';
  let tag = 'v0.18.0';
  const updater = createUpdater({...f.config, testFeed: null, fetch: async () => new Response(JSON.stringify({
    ...f.release, html_url: 'https://github.com/Stahl-G/briefloop/releases/tag/v0.20.0',
    assets: [{...f.release.assets[0], browser_download_url: `https://github.com/Stahl-G/briefloop/releases/download/${tag}/BriefLoop-0.20.0-arm64.dmg`}]
  }))});
  assert.equal((await updater.check()).error.code, 'asset_version_mismatch');
  tag = 'v0.20.0'; assert.equal((await updater.check()).state, 'available');
});

test('native installation error event is propagated so the main gate can recover', async () => {
  class Native extends EventEmitter {
    setFeedURL() {}
    async checkForUpdates() {return {updateInfo: {version: '0.20.0'}};}
    async downloadUpdate() {return ['installer.exe'];}
    quitAndInstall() {this.emit('error', Error('private diagnostic must not be displayed'));}
  }
  const updater = createUpdater({app: {getVersion: () => '0.19.0'}, shell: {}, platform: 'win32', nativeUpdater: new Native()});
  await updater.check(); await updater.download();
  await assert.rejects(updater.installReady(), /更新请求失败/);
  assert.equal(updater.status().state, 'error');
  assert.doesNotMatch(JSON.stringify(updater.status()), /private diagnostic/);
});
