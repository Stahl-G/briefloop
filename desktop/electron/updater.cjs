'use strict';

const fs = require('node:fs/promises');
const path = require('node:path');
const crypto = require('node:crypto');
const semver = require('semver');

const REPOSITORY = 'Stahl-G/briefloop';
const API = `https://api.github.com/repos/${REPOSITORY}/releases/latest`;
const RELEASES = `https://github.com/${REPOSITORY}/releases/`;
const MAX_ASSET = 4 * 1024 ** 3;

class UpdateError extends Error {
  constructor(code, message, retryable = true) {
    super(message); this.code = code; this.retryable = retryable;
  }
}

function loopback(value) {
  const url = new URL(value);
  if (url.protocol !== 'http:' || !['127.0.0.1', '[::1]', 'localhost'].includes(url.hostname)
      || url.username || url.password || url.hash) throw new Error('Invalid local update test feed');
  return url;
}

function createUpdater({app, shell, changed = () => {}, platform = process.platform, arch = process.arch,
                        installMode = platform === 'darwin' ? 'dmg' : 'native', nativeUpdater,
                        testFeed = null, fetch: fetchImpl = globalThis.fetch} = {}) {
  if (!app || !shell || !['native', 'dmg'].includes(installMode)) throw new Error('Invalid updater configuration');
  const local = testFeed ? loopback(testFeed) : null;
  if (local && installMode !== 'dmg') throw new Error('Local test feed only supports the DMG test path');
  const currentAppVersion = app.getVersion();
  let data = {currentAppVersion, state: 'idle', releaseVersion: null, notes: '', url: null,
              progress: null, installMode, error: null, retryable: false, reinstall: false, source: local ? 'local-test' : 'github'};
  let pending = null, asset = null, ready = null, native = null, available = false;
  let metadataRateLimit = null;
  const status = () => structuredClone(data);
  const publish = patch => {data = {...data, ...patch}; changed(status()); return status();};
  const fail = error => {
    if (installMode === 'native' && platform === 'win32' && error?.code === 'ERR_UPDATER_CHANNEL_FILE_NOT_FOUND') {
      error = new UpdateError('windows_update_unavailable', '官方发布尚未提供 Windows 更新文件，请稍后重试。');
    }
    return publish({state: 'error', error: {
      code: error instanceof UpdateError ? error.code : 'update_failed',
      message: error instanceof UpdateError ? error.message : '更新请求失败，请检查网络后重试。'},
      retryable: error instanceof UpdateError ? error.retryable : true});
  };
  const once = operation => {
    if (pending) return pending;
    pending = Promise.resolve().then(operation).catch(fail).finally(() => {pending = null;});
    return pending;
  };
  function trusted(value, kind, redirected = false) {
    let url;
    try {url = new URL(value);} catch {throw new UpdateError('untrusted_url', '更新地址不受信任。', false);}
    if (url.username || url.password || url.hash) throw new UpdateError('untrusted_url', '更新地址不受信任。', false);
    if (local) {
      if (url.origin !== local.origin) throw new UpdateError('untrusted_url', '测试更新地址必须留在指定回环服务。', false);
    } else {
      if (url.protocol !== 'https:') throw new UpdateError('untrusted_url', '更新地址必须使用 HTTPS。', false);
      const official = kind === 'metadata' ? url.href === API
        : url.origin === 'https://github.com' && url.pathname.startsWith(`/${REPOSITORY}/releases/download/`);
      // GitHub serves release bytes through a signed CDN redirect. This host is
      // admitted only after requesting an official repository asset URL.
      const cdn = redirected && kind === 'asset' && url.hostname === 'release-assets.githubusercontent.com';
      if (!official && !cdn) throw new UpdateError('untrusted_url', '更新地址不属于官方发布来源。', false);
    }
    return url.href;
  }
  async function response(value, kind) {
    let url = trusted(value, kind);
    if (kind === 'metadata' && metadataRateLimit?.until > Date.now()) throw metadataRateLimit.error;
    const signal = AbortSignal.timeout(kind === 'metadata' ? 30000 : 10 * 60 * 1000);
    for (let n = 0; n < 6; n++) {
      const res = await fetchImpl(url, {redirect: 'manual', signal,
        headers: {'Accept': kind === 'metadata' ? 'application/vnd.github+json' : 'application/octet-stream',
                  'User-Agent': 'BriefLoop-Desktop-Updater'}});
      if ([301, 302, 303, 307, 308].includes(res.status)) {
        const location = res.headers.get('location');
        await res.body?.cancel();
        if (!location) break;
        url = trusted(new URL(location, url).href, kind, true); continue;
      }
      if (!res.ok) {
        const rateLimited = !local && kind === 'metadata' && (res.status === 429 ||
          (res.status === 403 && res.headers.get('x-ratelimit-remaining') === '0'));
        if (rateLimited) {
          const now = Date.now();
          const retry = res.headers.get('retry-after');
          const reset = Number(res.headers.get('x-ratelimit-reset')) * 1000;
          const retryAt = retry ? (/^\d+$/.test(retry) ? now + Number(retry) * 1000 : Date.parse(retry)) : NaN;
          const reported = Number.isFinite(retryAt) && retryAt > now ? retryAt : reset;
          const hasReset = Number.isFinite(reported) && reported > now && reported <= now + 24 * 60 * 60 * 1000;
          const until = hasReset ? reported : now + 60000;
          const timing = hasReset ? `请在本机时间 ${new Date(until).toLocaleString('zh-CN', {hour12: false})} 后重试。` : '请稍后重试。';
          const error = new UpdateError('github_rate_limited', `GitHub 更新检查已达到请求频率限制（HTTP ${res.status}）。${timing}也可点击“查看官方发布与安装包”手动下载；已下载的安装包仍可使用。`);
          metadataRateLimit = {until, error};
          await res.body?.cancel();
          throw error;
        }
        await res.body?.cancel();
        throw new UpdateError(`http_${res.status}`, `更新服务返回 HTTP ${res.status}，请稍后重试。`);
      }
      return res;
    }
    throw new UpdateError('redirect_limit', '更新地址重定向异常。');
  }
  async function metadata() {
    const res = await response(local ? local.href : API, 'metadata');
    let bytes = 0, chunks = [];
    for await (const chunk of res.body) {
      bytes += chunk.length;
      if (bytes > 2 * 1024 ** 2) {throw new UpdateError('metadata_limit', '更新说明超出大小限制。', false);}
      chunks.push(Buffer.from(chunk));
    }
    return JSON.parse(Buffer.concat(chunks).toString('utf8'));
  }
  function releaseVersion(value) {
    const version = typeof value === 'string' && semver.valid(value);
    if (!version || semver.prerelease(version)) throw new UpdateError('invalid_release', '发布版本不是有效的稳定版本。', false);
    if (!semver.valid(currentAppVersion)) throw new UpdateError('invalid_app_version', '当前 App 版本无效，无法安全比较更新。', false);
    return version;
  }
  function releaseURL(value) {
    if (local) return local.href;
    if (typeof value !== 'string' || !value.startsWith(RELEASES + 'tag/')) {
      throw new UpdateError('invalid_release', '发布说明地址不属于官方仓库。', false);
    }
    const url = new URL(value);
    if (url.username || url.password || url.hash || url.search) throw new UpdateError('invalid_release', '发布说明地址无效。', false);
    return url.href;
  }
  function setupNative() {
    if (native) return native;
    if (platform !== 'win32' && platform !== 'darwin') throw new UpdateError('unsupported_platform', '此平台尚未配置原生更新。', false);
    native = nativeUpdater || require('electron-updater').autoUpdater;
    native.autoDownload = false;
    native.autoInstallOnAppQuit = false;
    native.allowPrerelease = false;
    native.allowDowngrade = false;
    native.setFeedURL({provider: 'github', owner: 'Stahl-G', repo: 'briefloop', private: false});
    native.on('download-progress', value => publish({state: 'downloading', progress: {
      percent: Math.max(0, Math.min(100, Number(value.percent) || 0)),
      transferred: Number(value.transferred) || 0, total: Number(value.total) || 0}}));
    // Errors must have a listener, but never copy raw provider diagnostics into UI.
    native.on('error', error => fail(error));
    return native;
  }
  async function checkImpl() {
    if (data.state === 'downloaded') return status();
    publish({state: 'checking', error: null, retryable: false, progress: null, reinstall: false}); asset = null; available = false;
    if (installMode === 'native') {
      const result = await setupNative().checkForUpdates();
      if (!result?.updateInfo) throw new UpdateError('feed_unavailable', '原生更新源尚未就绪，请稍后重试。');
      const version = releaseVersion(result.updateInfo.version);
      available = semver.gt(version, currentAppVersion);
      return publish({state: available ? 'available' : 'current',
        releaseVersion: version, notes: typeof result.updateInfo.releaseNotes === 'string' ? result.updateInfo.releaseNotes.slice(0, 20000) : '',
        url: `${RELEASES}tag/v${version}`});
    }
    if (arch !== 'arm64') throw new UpdateError('unsupported_arch', '当前手动更新仅支持 macOS Apple Silicon DMG。', false);
    const release = await metadata();
    if (release.draft || release.prerelease) throw new UpdateError('unstable_release', '仅接受已公开的稳定版本。', false);
    const version = releaseVersion(release.tag_name), url = releaseURL(release.html_url);
    publish({releaseVersion: version, notes: typeof release.body === 'string' ? release.body.slice(0, 20000) : '', url});
    const reinstall = !!local && version === semver.valid(currentAppVersion);
    if (!semver.gt(version, currentAppVersion) && !reinstall) return publish({state: 'current'});
    const candidates = (Array.isArray(release.assets) ? release.assets : []).filter(item =>
      typeof item.name === 'string' && /(?:^|[-_.])arm64(?:[-_.]|$)/i.test(item.name) && /\.dmg$/i.test(item.name));
    if (candidates.length !== 1) throw new UpdateError('asset_unavailable', '该版本尚无唯一可用的 Apple Silicon DMG，请稍后重试。');
    const selected = candidates[0];
    // A same-repository asset can still belong to an older release. Bind the
    // expected installer name and exact release tag before trusting its digest.
    const expectedName = `BriefLoop-${version}-arm64.dmg`;
    if (selected.name !== expectedName) throw new UpdateError('asset_version_mismatch', '更新文件名与发布版本不一致。', false);
    const selectedURL = new URL(trusted(selected.browser_download_url, 'asset'));
    if (decodeURIComponent(selectedURL.pathname) !== `/${REPOSITORY}/releases/download/${release.tag_name}/${expectedName}` || selectedURL.search) {
      throw new UpdateError('asset_version_mismatch', '更新文件不属于所选发布版本。', false);
    }
    if (!Number.isSafeInteger(selected.size) || selected.size <= 0 || selected.size > MAX_ASSET) {
      throw new UpdateError('invalid_asset', '更新文件大小无效或超出限制。', false);
    }
    let digest = null;
    if (selected.digest != null) {
      const match = /^(sha256|sha512):([a-f0-9]+)$/i.exec(selected.digest);
      if (!match || match[2].length !== (match[1].toLowerCase() === 'sha256' ? 64 : 128)) {
        throw new UpdateError('invalid_digest', '更新文件校验信息无效。', false);
      }
      digest = {algorithm: match[1].toLowerCase(), value: match[2].toLowerCase()};
    }
    asset = {url: trusted(selected.browser_download_url, 'asset'), size: selected.size, digest};
    available = true;
    return publish({state: 'available', reinstall});
  }
  async function downloadImpl() {
    if (ready && data.state === 'downloaded') return status();
    if (!available || !data.releaseVersion || !['available', 'error'].includes(data.state)) {
      throw new UpdateError('not_available', '请先检查并选择可用的新版本。', false);
    }
    publish({state: 'downloading', error: null, retryable: false, progress: {percent: 0, transferred: 0, total: asset?.size || 0}});
    ready = null;
    if (installMode === 'native') {
      const files = await setupNative().downloadUpdate();
      if (!Array.isArray(files) || !files.length) throw new UpdateError('download_failed', '原生更新尚未下载完成。');
      ready = {native: true};
      return publish({state: 'downloaded', progress: {...data.progress, percent: 100}});
    }
    if (!asset) throw new UpdateError('not_available', '请重新检查可用更新。');
    const directory = path.join(app.getPath('userData'), 'updates');
    await fs.mkdir(directory, {recursive: true, mode: 0o700});
    if ((await fs.lstat(directory)).isSymbolicLink()) throw new UpdateError('unsafe_path', '更新下载目录不可用。', false);
    const staging = await fs.mkdtemp(path.join(directory, 'download-'));
    const temporary = path.join(staging, 'update.part');
    let handle;
    try {
      const res = await response(asset.url, 'asset');
      handle = await fs.open(temporary, 'wx', 0o600);
      const checksum = crypto.createHash('sha256');
      const advertised = asset.digest && crypto.createHash(asset.digest.algorithm);
      let transferred = 0, lastProgress = 0;
      for await (const chunk of res.body) {
        transferred += chunk.length;
        if (transferred > asset.size) throw new UpdateError('size_mismatch', '下载文件大小与发布记录不一致。');
        checksum.update(chunk); advertised?.update(chunk); await handle.writeFile(chunk);
        if (Date.now() - lastProgress > 100 || transferred === asset.size) {
          publish({progress: {percent: transferred / asset.size * 100, transferred, total: asset.size}});
          lastProgress = Date.now();
        }
      }
      if (transferred !== asset.size) throw new UpdateError('size_mismatch', '下载文件不完整，请重试。');
      if (advertised && advertised.digest('hex') !== asset.digest.value) throw new UpdateError('hash_mismatch', '下载校验失败，请重试。');
      await handle.sync(); await handle.close(); handle = null;
      const file = path.join(staging, `BriefLoop-${data.releaseVersion}-arm64.dmg`);
      await fs.rename(temporary, file);
      ready = {file, sha256: checksum.digest('hex')};
      return publish({state: 'downloaded', progress: {percent: 100, transferred, total: asset.size}});
    } catch (error) {
      await handle?.close(); await fs.rm(staging, {recursive: true, force: true}); throw error;
    }
  }
  async function installReady() {
    // This method is main-process-only. The caller must finish its save/busy/
    // owned-service-stop gate BEFORE invoking it; there is no automatic install.
    if (pending || !ready || (data.state !== 'downloaded' && !(data.state === 'error' && data.error?.code === 'open_failed'))) throw new UpdateError('not_downloaded', '更新尚未下载完成。', false);
    try {
      if (ready.native) {
        setupNative().quitAndInstall(false, true);
        // BaseUpdater reports synchronous installation failures through its error event.
        if (data.state === 'error') throw new UpdateError(data.error.code, data.error.message, data.retryable);
        return {mode: 'native', requested: true};
      }
      const stat = await fs.lstat(ready.file);
      if (!stat.isFile() || stat.isSymbolicLink()) throw new UpdateError('unsafe_path', '已下载文件不可用，请重新下载。');
      const hash = crypto.createHash('sha256');
      for await (const chunk of require('node:fs').createReadStream(ready.file)) hash.update(chunk);
      if (hash.digest('hex') !== ready.sha256) throw new UpdateError('hash_mismatch', '已下载文件发生变化，请重新下载。');
      const error = await shell.openPath(ready.file);
      if (error) throw new UpdateError('open_failed', '无法打开已下载 DMG，请重试。');
      return {mode: 'dmg', opened: true, manualInstall: true};
    } catch (error) {fail(error); throw new UpdateError(data.error.code, data.error.message, data.retryable);}
  }
  return {status, check: () => once(checkImpl), download: () => once(downloadImpl), installReady};
}

module.exports = {createUpdater};
