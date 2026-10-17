'use strict';
const fs = require('node:fs/promises');
const path = require('node:path');
const os = require('node:os');
const {randomUUID} = require('node:crypto');
const semver = require('semver');
const {createEnvironment, runOwnedProcess} = require('./environment.cjs');
const {runtimeLaunch, WorkspaceService} = require('./service.cjs');
const {parseEnvelope, compatible, wheelURL, wheelMatches, FEED, RELEASES} = require('./backend-update-manifest.cjs');
const trust = require('./backend-update-trust.json');

// No live workspace is opened by this probe. Compare actual fresh database DDL,
// save an ordinary record and round-trip the export with the candidate packages.
const PROBE = `import hashlib,json,sqlite3,sys
from pathlib import Path
from io import BytesIO
from briefloop.store import Store
from briefloop.exports import docx_bytes
from docx import Document
from openpyxl import Workbook,load_workbook
s=Store(Path(sys.argv[1]))
with s.tx() as c:
 schema=c.execute("SELECT type,name,sql FROM sqlite_master WHERE sql IS NOT NULL ORDER BY type,name").fetchall()
 c.execute("CREATE TABLE update_probe(value TEXT)")
 c.execute("INSERT INTO update_probe VALUES('saved')")
 assert c.execute("SELECT value FROM update_probe").fetchone()[0]=='saved'
doc=Document(BytesIO(docx_bytes('Backend update probe **83**')))
assert '83' in '\\n'.join(p.text for p in doc.paragraphs)
w=Workbook();w.active['A1']=83;b=BytesIO();w.save(b)
assert load_workbook(BytesIO(b.getvalue())).active['A1'].value==83
print(json.dumps({'schema':hashlib.sha256(json.dumps([list(r) for r in schema]).encode()).hexdigest()}))`;
async function smoke(runtime, directory) {
  const launch = runtimeLaunch(runtime);
  const result = await runOwnedProcess(runtime.python, [...launch.args, '-c', PROBE, directory], {env: launch.env, timeoutMs: 60000});
  const schema = JSON.parse(result.stdout.trim()).schema;
  const service = new WorkspaceService(runtime);
  try {
    await service.start(directory);
    const state = await service.status();
    if (state.busy) throw Error('临时工作区启动检查失败。');
  } finally {
    if (service.child) {
      try { await service.stop({cancelBusy: false}); }
      catch (error) {
        error.code = 'cleanup_failed';
        error.cleanupOwner = () => service.stop({cancelBusy: false});
        throw error;
      }
    }
  }
  return schema;
}
async function writeJSON(file, value) {
  const temporary = file + '.' + randomUUID() + '.tmp';
  const handle = await fs.open(temporary, 'wx', 0o600);
  try { await handle.writeFile(JSON.stringify(value)); await handle.sync(); } finally { await handle.close(); }
  try { await fs.rename(temporary, file); } finally { await fs.rm(temporary, {force: true}); }
}
async function fetchOfficialBytes(fetchImpl, url, maximum, {optional = false, local = null, signal: parentSignal = null} = {}) {
  const initial = url;
  const deadline = AbortSignal.timeout(10 * 60 * 1000);
  const signal = parentSignal ? AbortSignal.any([parentSignal, deadline]) : deadline;
  for (let hop = 0; hop < 6; hop++) {
    const u = new URL(url);
    const official = u.origin === 'https://github.com' && u.pathname.startsWith('/Stahl-G/briefloop/releases/');
    const cdn = hop > 0 && u.origin === 'https://release-assets.githubusercontent.com';
    if (u.username || u.password || u.hash || (local ? u.origin !== local.origin : u.protocol !== 'https:' || (!official && !cdn))) {
      throw Error('更新来源无效。');
    }
    const response = await fetchImpl(url, {redirect: 'manual', signal, headers: {'User-Agent': 'BriefLoop-Backend-Updater'}});
    if ([301, 302, 303, 307, 308].includes(response.status)) {
      url = new URL(response.headers.get('location'), url).href;
      await response.body?.cancel();
      continue;
    }
    if (optional && response.status === 404 && (initial === FEED || local)) { await response.body?.cancel(); return null; }
    if (!response.ok || !response.body) throw Error('更新下载失败。');
    const chunks = [];
    let size = 0;
    for await (const chunk of response.body) {
      size += chunk.length;
      if (size > maximum) { throw Error('更新包超过声明大小。'); }
      chunks.push(Buffer.from(chunk));
    }
    return Buffer.concat(chunks);
  }
  throw Error('更新重定向过多。');
}
async function loadState(root, file) {
  await fs.mkdir(root, {recursive: true, mode: 0o700});
  if ((await fs.lstat(root)).isSymbolicLink()) throw Error('更新目录无效。');
  try {
    const state = JSON.parse(await fs.readFile(file, 'utf8'));
    if (!Number.isSafeInteger(state.high) || state.high < 0
        || ![false, 'prepared', 'started'].includes(state.trial)
        || [state.active, state.previous].some(value => value !== null && !/^\d+\.\d+\.\d+$/.test(value))) {
      throw Error('更新状态无效。');
    }
    return state;
  } catch (error) {
    if (error.code !== 'ENOENT') throw error;
    return {high: 0, active: null, previous: null, trial: false};
  }
}
async function readStoredRelease({id, root, payloadPath, keys, app, createEnv, reuseFrom, environmentChanged}) {
  if (!/^\d+\.\d+\.\d+$/.test(id || '')) throw Error('更新版本无效。');
  const directory = path.join(root, id);
  if ((await fs.lstat(directory)).isSymbolicLink()) throw Error('更新目录无效。');
  const envelope = JSON.parse(await fs.readFile(path.join(directory, 'envelope.json'), 'utf8'));
  const release = parseEnvelope(envelope, keys, {installed: true});
  const bundled = JSON.parse(await fs.readFile(path.join(payloadPath, 'manifest.json'), 'utf8'));
  if (semver.lte(release.version, bundled.version) || release.version !== id || !compatible(release, {shellVersion: app.getVersion(), bundledLock: bundled.requirements_sha256})) {
    throw Error('该研究组件需要完整 App 更新。');
  }
  const stored = JSON.parse(await fs.readFile(path.join(directory, 'manifest.json'), 'utf8'));
  if (stored.version !== release.version || stored.wheel !== release.wheel || stored.sha256 !== release.wheel_sha256
      || stored.requirements !== 'requirements.txt' || stored.requirements_sha256 !== release.requirements_sha256) {
    throw Error('本地更新清单与签名发布不一致。');
  }
  const wheel = await fs.readFile(path.join(directory, release.wheel));
  if (!wheelMatches(wheel, release)) throw Error('更新包校验失败。');
  const environment = createEnv({app: {getPath: name => name === 'userData' ? directory : app.getPath(name)},
    payloadPath: directory, reuseFrom, changed: environmentChanged});
  return {release, environment};
}
function createBackendUpdates({app, payloadPath, changed = () => {}, fetch: fetchImpl = globalThis.fetch,
  keys = trust.keys, createEnv = createEnvironment, probe = smoke, testFeed = null, environmentChanged = () => {}} = {}) {
  const local = testFeed ? new URL(testFeed) : null;
  if (local && (app.isPackaged !== false || local.protocol !== 'http:' || local.hostname !== '127.0.0.1'
      || local.username || local.password || local.search || local.hash)) throw Error('测试更新源仅限开发版回环地址。');
  const root = path.join(app.getPath('userData'), 'backend-updates');
  const stateFile = path.join(root, 'state.json');
  let state = {high: 0, active: null, previous: null, trial: false};
  let pending, controller, candidate, candidateEnv, selectedEnv, selectedRelease, cleanupError;
  let data = {state: 'idle', installMode: 'backend', currentAppVersion: app.getVersion(), releaseVersion: null, source: local ? 'local-test' : 'github'};
  const status = () => structuredClone(data);
  const publish = value => { data = {...data, ...value}; changed(status()); return status(); };
  function operation(action) {
    if (cleanupError) return Promise.resolve(status());
    if (pending) return pending;
    controller = new AbortController();
    pending = Promise.resolve().then(action).catch(error => {
      if (error.code === 'cleanup_failed') cleanupError = error;
      return publish({state: 'error', retryable: !cleanupError,
        error: {message: cleanupError ? '更新进程尚未确认退出，请保留 App。' : '研究组件更新未完成，当前安装保留。请重试或使用完整 App 更新。'}});
    }).finally(() => {pending = null; controller = null;});
    return pending;
  }
  const readRelease = (id, reuseFrom) => readStoredRelease({id, root, payloadPath, keys, app, createEnv, reuseFrom, environmentChanged});
  async function select() {
    state = await loadState(root, stateFile);
    if (state.trial === 'started') {
      state.active = state.previous;
      state.previous = null;
      state.trial = false;
      await writeJSON(stateFile, state);
    }
    if (!state.active) return null;
    if (state.trial === 'prepared') {
      state.trial = 'started';
      await writeJSON(stateFile, state);
    }
    try {
      const item = await readRelease(state.active);
      const result = await item.environment.startup();
      if (result.state !== 'ready') throw Error('研究组件未就绪。');
      selectedEnv = item.environment;
      selectedRelease = item.release;
      return selectedEnv;
    } catch {
      state.active = state.previous;
      state.previous = null;
      state.trial = false;
      await writeJSON(stateFile, state);
      return state.active ? select() : null;
    }
  }
  return {
    status,
    trial: () => state.trial === 'started',
    selected: async () => {
      try { return await select(); }
      catch { publish({state: 'error', error: {message: '本地更新状态无效，已使用随包组件。'}}); return null; }
    },
    cancel: async () => {
      controller?.abort();
      await candidateEnv?.cancel?.();
      if (pending) await pending;
      if (cleanupError?.cleanupOwner) await cleanupError.cleanupOwner();
      else if (cleanupError && !cleanupError.confirmCleanup?.()) throw Error('更新进程尚未确认退出，请保留 App。');
      cleanupError = null;
    },
    check: bundledEnvironment => operation(async () => {
      candidate = null;
      candidateEnv = null;
      state = await loadState(root, stateFile);
      const body = await fetchOfficialBytes(fetchImpl, local?.href || FEED, 128 * 1024, {optional: true, local, signal: controller.signal});
      if (!body) return publish({state: 'current', error: null});
      const envelope = JSON.parse(body.toString('utf8'));
      const release = parseEnvelope(envelope, keys);
      const bundled = JSON.parse(await fs.readFile(path.join(payloadPath, 'manifest.json'), 'utf8'));
      const current = selectedRelease?.version || bundled.version;
      if (release.sequence <= state.high || semver.lte(release.version, current)) return publish({state: 'current', error: null});
      if (!compatible(release, {shellVersion: app.getVersion(), bundledLock: bundled.requirements_sha256})) {
        return publish({state: 'current', requiresAppUpdate: true, error: null});
      }
      candidate = {release, envelope, bundledEnvironment};
      return publish({state: 'available', releaseVersion: release.version, error: null,
        url: `${RELEASES}/tag/v${release.version}`, notes: '更新研究组件；保存并重启后生效。'});
    }),
    download: () => operation(async () => {
      if (!candidate) throw Error('请先检查更新。');
      const {release, envelope, bundledEnvironment} = candidate;
      publish({state: 'downloading', error: null});
      const body = await fetchOfficialBytes(fetchImpl, local ? `${local.origin}/${release.wheel}` : wheelURL(release), release.wheel_bytes, {local, signal: controller.signal});
      if (!wheelMatches(body, release)) throw Error('更新包校验失败。');
      const directory = path.join(root, release.version);
      await fs.mkdir(directory, {mode: 0o700}).catch(error => { if (error.code !== 'EEXIST') throw error; });
      if ((await fs.lstat(directory)).isSymbolicLink()) throw Error('更新目录无效。');
      const existing = await fs.readFile(path.join(directory, release.wheel)).catch(error => { if (error.code !== 'ENOENT') throw error; });
      if (existing && !wheelMatches(existing, release)) throw Error('本地已有不同内容的更新包。');
      if (!existing) await fs.writeFile(path.join(directory, release.wheel), body, {flag: 'wx', mode: 0o600});
      const lockPath = path.join(directory, 'requirements.txt');
      const lockStat = await fs.lstat(lockPath).catch(error => { if (error.code !== 'ENOENT') throw error; });
      if (lockStat?.isSymbolicLink()) throw Error('更新依赖路径无效。');
      if (!lockStat) await fs.copyFile(path.join(payloadPath, 'requirements.txt'), lockPath, require('node:fs').constants.COPYFILE_EXCL);
      await writeJSON(path.join(directory, 'manifest.json'), {version: release.version, wheel: release.wheel,
        sha256: release.wheel_sha256, requirements: 'requirements.txt', requirements_sha256: release.requirements_sha256});
      await writeJSON(path.join(directory, 'envelope.json'), envelope);
      const item = await readRelease(release.version, bundledEnvironment.runtime());
      candidateEnv = item.environment;
      controller.signal.throwIfAborted();
      const ready = await candidateEnv.prepare();
      controller.signal.throwIfAborted();
      if (ready.state !== 'ready') throw Error('研究组件准备失败。');
      const temporary = await fs.mkdtemp(path.join(os.tmpdir(), 'briefloop-backend-probe-'));
      let keepProbe = false;
      try {
        const before = await probe(bundledEnvironment.runtime(), path.join(temporary, 'before'));
        const after = await probe(candidateEnv.runtime(), path.join(temporary, 'after'));
        if (before !== after) throw Error('研究组件数据库格式发生变化，请完整升级。');
      } catch (error) { keepProbe = error.code === 'cleanup_failed'; throw error; }
      finally { if (!keepProbe) await fs.rm(temporary, {recursive: true, force: true}); }
      controller.signal.throwIfAborted();
      return publish({state: 'downloaded', error: null});
    }),
    activate: async () => {
      if (data.state !== 'downloaded' || !candidateEnv || !candidate) throw Error('请先完成研究组件准备。');
      if (candidate.release.sequence <= state.high) throw Error('该更新已尝试，请重新检查更新。');
      parseEnvelope(candidate.envelope, keys);
      await readRelease(candidate.release.version);
      state = {...state, high: Math.max(state.high, candidate.release.sequence), previous: state.active,
        active: candidate.release.version, trial: 'prepared'};
      await writeJSON(stateFile, state);
      selectedEnv = candidateEnv;
      selectedRelease = candidate.release;
      return selectedEnv;
    },
    confirm: async () => {
      if (!state.trial) return;
      state.trial = false;
      await writeJSON(stateFile, state);
    },
    rollback: async () => {
      state.active = state.previous;
      state.previous = null;
      state.trial = false;
      await writeJSON(stateFile, state);
      selectedEnv = null;
      selectedRelease = null;
      return select();
    },
  };
}
module.exports = {createBackendUpdates, smoke};
