'use strict';

const fs = require('node:fs/promises');
const path = require('node:path');
const crypto = require('node:crypto');
const semver = require('semver');
const delta = require('./differential-download.cjs');

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
                        installMode = platform === 'darwin' ? 'zip' : 'native', nativeUpdaterFactory,
                        testFeed = null, fetch: fetchImpl = globalThis.fetch} = {}) {
  if (!app || !shell || !['native', 'dmg', 'zip'].includes(installMode)) throw new Error('Invalid updater configuration');
  const local = testFeed ? loopback(testFeed) : null;
  if (local && installMode === 'native') throw new Error('Local test feed only supports the manual Mac update path');
  const currentAppVersion = app.getVersion();
  let data = {currentAppVersion, state: 'idle', releaseVersion: null, notes: '', url: null,
              progress: null, installMode, error: null, retryable: false, reinstall: false, source: local ? 'local-test' : 'github'};
  let pending = null, asset = null, ready = null, native = null, available = false;
  let metadataRateLimit = null, operation = 'check';
  const status = () => structuredClone(data);
  const publish = patch => {data = {...data, ...patch}; changed(status()); return status();};
  const fail = (error, kind = operation) => {
    if (installMode === 'native' && platform === 'win32' && kind === 'install') {
      retireNative();
      if (!(error instanceof UpdateError)) error = new UpdateError('native_install_failed', '更新未能启动，请重新下载后重试。');
    }
    if (installMode === 'native' && platform === 'win32' && error?.code === 'ERR_UPDATER_CHANNEL_FILE_NOT_FOUND') {
      error = new UpdateError('windows_update_unavailable', '官方发布尚未提供 Windows 更新文件，请稍后重试。');
    }
    return publish({state: 'error', error: {
      operation: kind, code: error instanceof UpdateError ? error.code : 'update_failed',
      message: error instanceof UpdateError ? error.message : '更新请求失败，请检查网络后重试。'},
      retryable: error instanceof UpdateError ? error.retryable : true});
  };
  const once = (action, kind) => {
    if (pending) return pending;
    operation = kind;
    pending = Promise.resolve().then(action).catch(fail).finally(() => {pending = null;});
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
  async function response(value, kind, headers = {}, deadlineSignal = null) {
    let url = trusted(value, kind);
    if (kind === 'metadata' && metadataRateLimit?.until > Date.now()) throw metadataRateLimit.error;
    const signal = deadlineSignal || AbortSignal.timeout(kind === 'metadata' ? 30000 : 10 * 60 * 1000);
    for (let n = 0; n < 6; n++) {
      const res = await fetchImpl(url, {redirect: 'manual', signal,
        headers: {'Accept': kind === 'metadata' ? 'application/vnd.github+json' : 'application/octet-stream',
                  'User-Agent': 'BriefLoop-Desktop-Updater', ...headers}});
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
    // A failed NsisUpdater retains its install guard. Recovery needs a new
    // instance, not the module's cached autoUpdater or a private flag reset.
    const updater = nativeUpdaterFactory ? nativeUpdaterFactory() : platform === 'win32'
      ? new (require('electron-updater').NsisUpdater)() : require('electron-updater').autoUpdater;
    const session = native = {updater, checkedVersion: null, downloaded: null};
    updater.autoDownload = false;
    updater.disableDifferentialDownload = false;
    updater.autoInstallOnAppQuit = false;
    updater.allowPrerelease = false;
    updater.allowDowngrade = false;
    updater.setFeedURL({provider: 'github', owner: 'Stahl-G', repo: 'briefloop', private: false});
    session.progress = value => {if (native === session) publish({state: 'downloading', progress: {
      percent: Math.max(0, Math.min(100, Number(value.percent) || 0)),
      transferred: Number(value.transferred) || 0, total: Number(value.total) || 0}});};
    session.downloadedListener = value => {
      if (native !== session) return;
      try {session.downloaded = structuredClone(value);} catch {session.downloaded = null;}
    };
    updater.on('download-progress', session.progress);
    updater.on('update-downloaded', session.downloadedListener);
    // Keep this guarded listener after retirement: a late EventEmitter error
    // must neither mutate the new session nor become an unhandled exception.
    updater.on('error', error => {if (native === session) fail(error, session.installing ? 'install' : operation);});
    return session;
  }
  function retireNative() {
    if (native) {
      native.updater.removeListener('download-progress', native.progress);
      native.updater.removeListener('update-downloaded', native.downloadedListener);
    }
    native = null;
    ready = null;
  }
  async function checkNative(session) {
    const result = await session.updater.checkForUpdates();
    if (native !== session || !result?.updateInfo) throw new UpdateError('feed_unavailable', '原生更新源尚未就绪，请稍后重试。');
    session.checkedVersion = releaseVersion(result.updateInfo.version);
    return result.updateInfo;
  }
  function nativeReady(session, files) {
    const info = session.downloaded, version = data.releaseVersion;
    const invalid = () => new UpdateError('invalid_native_download', '更新文件与版本的校验信息不完整，请重新检查并下载。');
    // This build uses one full NSIS EXE. The public event identifies only the
    // installer, so reject web-installer packages rather than guess their paths.
    if (!info || info.version !== version || !Array.isArray(files) || files.length !== 1
        || (info.packages && Object.keys(info.packages).length)) throw invalid();
    const expectedName = `BriefLoop-Setup-${version}-${arch}.exe`;
    const installers = (Array.isArray(info.files) ? info.files : []).filter(file => {
      try {return /\.exe$/i.test(new URL(file.url, 'https://github.com/').pathname);} catch {return false;}
    });
    if (installers.length !== 1) throw invalid();
    const entry = installers[0];
    let name;
    try {name = path.posix.basename(decodeURIComponent(new URL(entry.url, 'https://github.com/').pathname));} catch {throw invalid();}
    if (name !== expectedName || typeof info.downloadedFile !== 'string' || !path.isAbsolute(info.downloadedFile)
        || typeof files[0] !== 'string' || path.normalize(files[0]) !== path.normalize(info.downloadedFile)
        || path.basename(info.downloadedFile) !== expectedName
        || typeof entry.sha512 !== 'string' || Buffer.from(entry.sha512, 'base64').length !== 64
        || Buffer.from(entry.sha512, 'base64').toString('base64') !== entry.sha512) throw invalid();
    return Object.freeze({native: true, session, version, file: path.normalize(info.downloadedFile), sha512: entry.sha512});
  }
  async function verifyNativeReady(value) {
    if (native !== value.session || value.version !== data.releaseVersion) throw new UpdateError('native_changed', '更新版本发生变化，请重新检查并下载。');
    try {
      for (const file of [value.file, path.dirname(value.file)]) {
        const stat = await fs.lstat(file);
        if (stat.isSymbolicLink() || (file === value.file ? !stat.isFile() || stat.size > MAX_ASSET : !stat.isDirectory())) {
          throw new Error('invalid update file');
        }
      }
      const hash = crypto.createHash('sha512');
      for await (const chunk of require('node:fs').createReadStream(value.file)) hash.update(chunk);
      if (hash.digest('base64') !== value.sha512) throw new UpdateError('hash_mismatch', '已下载更新文件发生变化，请重新下载。');
    } catch (error) {
      if (error instanceof UpdateError) throw error;
      throw new UpdateError('update_file_unavailable', '已下载更新文件不可用，请重新下载。');
    }
  }
  async function checkImpl() {
    if (data.state === 'downloaded') return status();
    publish({state: 'checking', error: null, retryable: false, progress: null, reinstall: false}); asset = null; available = false;
    if (installMode === 'native') {
      const info = await checkNative(setupNative());
      const version = releaseVersion(info.version);
      available = semver.gt(version, currentAppVersion);
      return publish({state: available ? 'available' : 'current',
        releaseVersion: version, notes: typeof info.releaseNotes === 'string' ? info.releaseNotes.slice(0, 20000) : '',
        url: `${RELEASES}tag/v${version}`});
    }
    if (arch !== 'arm64') throw new UpdateError('unsupported_arch', '当前手动更新仅支持 macOS Apple Silicon。', false);
    const release = await metadata();
    if (release.draft || release.prerelease) throw new UpdateError('unstable_release', '仅接受已公开的稳定版本。', false);
    const version = releaseVersion(release.tag_name), url = releaseURL(release.html_url);
    publish({releaseVersion: version, notes: typeof release.body === 'string' ? release.body.slice(0, 20000) : '', url});
    const reinstall = !!local && version === semver.valid(currentAppVersion);
    if (!semver.gt(version, currentAppVersion) && !reinstall) return publish({state: 'current'});
    const candidates = (Array.isArray(release.assets) ? release.assets : []).filter(item =>
      typeof item.name === 'string' && /(?:^|[-_.])arm64(?:[-_.]|$)/i.test(item.name) && (installMode === 'zip' ? /-mac\.zip$/i : /\.dmg$/i).test(item.name));
    if (candidates.length !== 1) throw new UpdateError('asset_unavailable', '该版本尚无唯一可用的 Apple Silicon 更新包，请稍后重试或从发行页下载 DMG。');
    const selected = candidates[0];
    // A same-repository asset can still belong to an older release. Bind the
    // expected installer name and exact release tag before trusting its digest.
    const expectedName = `BriefLoop-${version}-arm64${installMode === 'zip' ? '-mac.zip' : '.dmg'}`;
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
    if (installMode === 'zip' && !digest) throw new UpdateError('missing_digest', '发布尚未提供 ZIP 更新包校验信息，请稍后重试。');
    asset = {name: expectedName, url: trusted(selected.browser_download_url, 'asset'), size: selected.size, digest};
    const maps = (release.assets || []).filter(item => item.name === expectedName + '.blockmap');
    if (maps.length === 1 && Number.isSafeInteger(maps[0].size) && maps[0].size > 0 && maps[0].size <= 16 * 1024 ** 2) {
      // Optional optimisation: malformed/missing maps must not prevent full updates.
      try {
        const mapURL = new URL(trusted(maps[0].browser_download_url, 'asset'));
        if (mapURL.href === selectedURL.href + '.blockmap') asset.blockmap = mapURL.href;
      } catch {}
    }
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
      const session = setupNative();
      if (!session.checkedVersion) await checkNative(session);
      if (session.checkedVersion !== data.releaseVersion) throw new UpdateError('release_changed', '可用更新版本已变化，请重新检查后下载。');
      session.downloaded = null;
      const files = await session.updater.downloadUpdate();
      if (!Array.isArray(files) || !files.length) throw new UpdateError('download_failed', '原生更新尚未下载完成。');
      ready = platform === 'win32' ? nativeReady(session, files) : {native: true, session};
      return publish({state: 'downloaded', progress: {...data.progress, percent: 100}});
    }
    if (!asset) throw new UpdateError('not_available', '请重新检查可用更新。');
    const directory = path.join(app.getPath('userData'), 'updates');
    await fs.mkdir(directory, {recursive: true, mode: 0o700});
    if ((await fs.lstat(directory)).isSymbolicLink()) throw new UpdateError('unsafe_path', '更新下载目录不可用。', false);
    const staging = await fs.mkdtemp(path.join(directory, 'download-'));
    const temporary = path.join(staging, 'update.part');
    let handle, map = null, fallback = 'no_baseline';
    try {
      if (asset.blockmap && asset.digest) {
        try {map = await delta.readMap(await response(asset.blockmap, 'asset'), asset.size);} catch {fallback = 'blockmap_unavailable';}
      } else fallback = 'blockmap_unavailable';
      const previousCache = map && await delta.readCache(directory);
      // A DMG cannot serve as the byte baseline for a ZIP (or vice versa).
      const cache = previousCache && path.extname(previousCache.file) === path.extname(asset.name) ? previousCache : null;
      if (cache) {
        try {
          const deadline = AbortSignal.timeout(10 * 60 * 1000);
          let lastProgress = 0;
          const progress = await delta.reconstruct({cache, map, size: asset.size, destination: temporary,
            request: (start, end) => response(asset.url, 'asset', {'Range': `bytes=${start}-${end}`, 'Accept-Encoding': 'identity'}, deadline),
            progress: value => {
              if (Date.now() - lastProgress > 100 || value.percent === 100) {publish({progress: value}); lastProgress = Date.now();}
            }});
          if (await delta.hashFile(temporary, asset.digest.algorithm) !== asset.digest.value) throw new Error('delta_hash');
          const file = path.join(staging, asset.name);
          const sha256 = await delta.hashFile(temporary);
          await fs.rename(temporary, file);
          ready = {file, sha256};
          await delta.saveCache(directory, file, asset.size, sha256, map, previousCache).catch(() => {});
          return publish({state: 'downloaded', progress});
        } catch (error) {
          await fs.rm(temporary, {force: true});
          fallback = ['little_reuse', 'range_unavailable', 'range_size', 'delta_hash'].includes(error.message) ? error.message : 'differential_unavailable';
        }
      }
      publish({progress: {percent: 0, transferred: 0, total: asset.size, mode: 'full', fallback}});
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
          publish({progress: {percent: transferred / asset.size * 100, transferred, total: asset.size, mode: 'full', fallback}});
          lastProgress = Date.now();
        }
      }
      if (transferred !== asset.size) throw new UpdateError('size_mismatch', '下载文件不完整，请重试。');
      if (advertised && advertised.digest('hex') !== asset.digest.value) throw new UpdateError('hash_mismatch', '下载校验失败，请重试。');
      await handle.sync(); await handle.close(); handle = null;
      const file = path.join(staging, asset.name);
      await fs.rename(temporary, file);
      ready = {file, sha256: checksum.digest('hex')};
      if (map) await delta.saveCache(directory, file, asset.size, ready.sha256, map, previousCache).catch(() => {});
      return publish({state: 'downloaded', progress: {percent: 100, transferred, total: asset.size, mode: 'full', fallback}});
    } catch (error) {
      await handle?.close(); await fs.rm(staging, {recursive: true, force: true}); throw error;
    }
  }
  async function installReady() {
    // This method is main-process-only. The caller must finish its save/busy/
    // owned-service-stop gate BEFORE invoking it; there is no automatic install.
    if (pending || !ready || (data.state !== 'downloaded' && !(data.state === 'error' && data.error?.code === 'open_failed'))) throw new UpdateError('not_downloaded', '更新尚未下载完成。', false);
    operation = 'install';
    try {
      if (ready.native) {
        const value = ready;
        if (platform === 'win32') await verifyNativeReady(value);
        if (native !== value.session || ready !== value) throw new UpdateError('native_changed', '更新状态发生变化，请重新下载。');
        value.session.installing = true;
        value.session.updater.quitAndInstall(false, true);
        // BaseUpdater reports synchronous installation failures through its error event.
        if (data.state === 'error') throw new UpdateError(data.error.code, data.error.message, data.retryable);
        return {mode: 'native', requested: true};
      }
      for (const directory of [path.dirname(ready.file), path.dirname(path.dirname(ready.file))]) {
        const stat = await fs.lstat(directory);
        if (!stat.isDirectory() || stat.isSymbolicLink()) throw new UpdateError('unsafe_path', '更新下载目录发生变化，请重新下载。', false);
      }
      const stat = await fs.lstat(ready.file);
      if (!stat.isFile() || stat.isSymbolicLink()) throw new UpdateError('unsafe_path', '已下载文件不可用，请重新下载。');
      const hash = crypto.createHash('sha256');
      for await (const chunk of require('node:fs').createReadStream(ready.file)) hash.update(chunk);
      if (hash.digest('hex') !== ready.sha256) throw new UpdateError('hash_mismatch', '已下载文件发生变化，请重新下载。');
      let target = ready.file;
      if (installMode === 'zip') {
        try {target = await require('./mac-update-handoff.cjs').prepare(ready.file, data.releaseVersion);}
        catch {throw new UpdateError('open_failed', '更新包解压或应用校验失败，请重试；当前应用未被替换。');}
      }
      const error = await shell.openPath(target);
      if (error) throw new UpdateError('open_failed', '无法打开已下载更新包，请重试。');
      return {mode: installMode, opened: true, manualInstall: true};
    } catch (error) {fail(error, 'install'); throw new UpdateError(data.error.code, data.error.message, data.retryable);}
  }
  return {status, check: () => once(checkImpl, 'check'), download: () => once(downloadImpl, 'download'), installReady};
}

module.exports = {createUpdater};
