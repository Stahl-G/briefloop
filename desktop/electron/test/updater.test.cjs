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
      browser_download_url: `${origin}/Stahl-G/briefloop/releases/download/v0.20.0/BriefLoop-0.20.0-arm64.dmg`, digest: 'sha256:' + crypto.createHash('sha256').update(bytes).digest('hex')}]};
  const changes = [], opened = [];
  const config = {app: {getVersion: () => '0.19.0', getPath: () => directory},
    shell: {openPath: async file => {opened.push(file); return '';}}, platform: 'darwin', arch: 'arm64', installMode: 'dmg',
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
  assert.equal(failed.state, 'error'); assert.equal(failed.retryable, true); assert.equal(failed.error.code, 'http_503'); assert.equal(failed.error.operation, 'download');
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
  assert.equal(native.disableDifferentialDownload, false);
  assert.equal(require('../electron-builder.windows.cjs').nsis.differentialPackage, true);
  assert.equal(native.allowPrerelease, false); assert.equal(native.allowDowngrade, false);
  await updater.download(); assert.deepEqual(native.calls, ['check', 'download']);
  assert.deepEqual(await updater.installReady(), {mode: 'native', requested: true});
  assert.deepEqual(native.calls[2], ['install', false, true]);
});

test('Windows missing update files have a specific safe message and checking can retry', async () => {
  class Native extends EventEmitter {
    checks = 0;
    setFeedURL() {}
    async checkForUpdates() {
      if (++this.checks < 3) {
        const error = Object.assign(Error('private provider URL https://private.invalid/?token=synthetic-secret'),
          {code: this.checks === 1 ? 'ERR_UPDATER_CHANNEL_FILE_NOT_FOUND' : 'UNKNOWN_PROVIDER_ERROR'});
        this.emit('error', error); throw error;
      }
      return {updateInfo: {version: '0.20.0'}};
    }
  }
  const native = new Native(), changes = [];
  const updater = createUpdater({app: {getVersion: () => '0.19.0'}, shell: {}, platform: 'win32',
    nativeUpdater: native, changed: value => changes.push(value)});
  const missing = await updater.check();
  assert.equal(missing.state, 'error'); assert.equal(missing.retryable, true);
  assert.deepEqual(missing.error, {operation: 'check', code: 'windows_update_unavailable', message: '官方发布尚未提供 Windows 更新文件，请稍后重试。'});
  const unknown = await updater.check();
  assert.equal(unknown.state, 'error'); assert.equal(unknown.retryable, true);
  assert.deepEqual(unknown.error, {operation: 'check', code: 'update_failed', message: '更新请求失败，请检查网络后重试。'});
  const recovered = await updater.check();
  assert.equal(recovered.state, 'available'); assert.equal(recovered.error, null);
  assert.equal(native.checks, 3);
  assert.equal(changes.filter(value => value.state === 'checking').length, 3);
  assert.doesNotMatch(JSON.stringify(changes), /private\.invalid|synthetic-secret|UNKNOWN_PROVIDER_ERROR/);
});

test('temporary DMG open failure retries the verified existing bytes', async t => {
  const f = await fixture(t);
  let attempts = 0;
  const updater = createUpdater({...f.config, shell: {openPath: async () => ++attempts === 1 ? 'synthetic failure' : ''}});
  await updater.check(); await updater.download();
  await assert.rejects(updater.installReady(), /无法打开/);
  assert.equal(updater.status().error.code, 'open_failed');
  assert.equal(updater.status().retryable, true); assert.equal(updater.status().error.operation, 'install');
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


test('only explicit local feeds allow equal-version reinstall; production equality and all downgrades stay current', async t => {
  const f = await fixture(t);
  f.release.tag_name = 'v0.19.0';
  f.release.assets[0].name = 'BriefLoop-0.19.0-arm64.dmg';
  const origin = new URL(f.config.testFeed).origin;
  f.release.assets[0].browser_download_url = `${origin}/Stahl-G/briefloop/releases/download/v0.19.0/BriefLoop-0.19.0-arm64.dmg`;
  const checked = await f.updater.check();
  assert.equal(checked.state, 'available'); assert.equal(checked.reinstall, true);
  assert.equal(checked.currentAppVersion, '0.19.0'); assert.equal(checked.releaseVersion, '0.19.0');
  assert.equal((await f.updater.download()).state, 'downloaded');
  assert.equal(f.updater.status().reinstall, true);
  const official = createUpdater({...f.config, testFeed: null, fetch: async url => {
    assert.equal(url, 'https://api.github.com/repos/Stahl-G/briefloop/releases/latest');
    return new Response(JSON.stringify({...f.release, html_url: 'https://github.com/Stahl-G/briefloop/releases/tag/v0.19.0'}));
  }});
  assert.equal((await official.check()).state, 'current');
  assert.equal(official.status().reinstall, false);
  assert.equal((await official.download()).error.code, 'not_available');
  const lower = createUpdater(f.config);
  f.release.tag_name = 'v0.18.0';
  assert.equal((await lower.check()).state, 'current'); assert.equal(lower.status().reinstall, false);
  assert.equal((await lower.download()).error.code, 'not_available');
  assert.equal(f.assetRequests(), 1);
});

test('equal-version local reinstall keeps filename and exact tag binding', async t => {
  const f = await fixture(t);
  f.release.tag_name = 'v0.19.0';
  assert.equal((await f.updater.check()).error.code, 'asset_version_mismatch');
  f.release.assets[0].name = 'BriefLoop-0.19.0-arm64.dmg';
  const origin = new URL(f.config.testFeed).origin;
  f.release.assets[0].browser_download_url = `${origin}/Stahl-G/briefloop/releases/download/v0.18.0/BriefLoop-0.19.0-arm64.dmg`;
  assert.equal((await f.updater.check()).error.code, 'asset_version_mismatch');
  assert.equal(f.updater.status().reinstall, false); assert.equal(f.assetRequests(), 0);
});


test('GitHub primary rate limit explains reset and avoids repeat metadata requests', async () => {
  let requests = 0;
  const reset = Math.floor(Date.now() / 1000) + 120;
  const updater = createUpdater({app: {getVersion: () => '0.19.0'}, shell: {}, platform: 'darwin',
    fetch: async () => {requests++; return new Response('', {status: 403,
      headers: {'x-ratelimit-remaining': '0', 'x-ratelimit-reset': String(reset)}});}});
  const first = await updater.check();
  assert.equal(first.error.code, 'github_rate_limited'); assert.equal(first.error.operation, 'check');
  assert.match(first.error.message, /本机时间/);
  assert.match(first.error.message, /查看官方发布与安装包/);
  await updater.check();
  assert.equal(requests, 1);
});

test('429 uses retry-after, while other 403 errors are not mislabeled as quota', async () => {
  for (const [status, headers, expected] of [[429, {'retry-after': '120'}, 'github_rate_limited'], [403, {}, 'http_403']]) {
    const updater = createUpdater({app: {getVersion: () => '0.19.0'}, shell: {}, platform: 'darwin',
      fetch: async () => new Response('', {status, headers})});
    assert.equal((await updater.check()).error.code, expected);
  }
});

test('Mac default selects version-bound ZIP alongside DMG, requires its digest, and rejects cross-tag assets', async t => {
  const f=await fixture(t);
  const zip={...f.release.assets[0],name:'BriefLoop-0.20.0-arm64-mac.zip',browser_download_url:f.release.assets[0].browser_download_url.replace('-arm64.dmg','-arm64-mac.zip')};
  f.release.assets.push(zip);
  const updater=createUpdater({...f.config,installMode:undefined});
  assert.equal((await updater.check()).installMode,'zip');
  assert.equal((await updater.download()).state,'downloaded');
  assert.ok((await fs.readdir(path.join(f.directory,'updates', (await fs.readdir(path.join(f.directory,'updates')))[0]))).includes(zip.name));
  delete zip.digest;
  const missing=createUpdater({...f.config,installMode:undefined});
  assert.equal((await missing.check()).error.code,'missing_digest');
  zip.digest=f.release.assets[0].digest;
  zip.browser_download_url=zip.browser_download_url.replace('/v0.20.0/','/v0.19.0/');
  assert.equal((await missing.check()).error.code,'asset_version_mismatch');
});
