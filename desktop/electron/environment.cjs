'use strict';
const fs = require('node:fs/promises');
const {constants, createReadStream} = require('node:fs');
const path = require('node:path');
const {spawn} = require('node:child_process');
const {createHash, randomUUID} = require('node:crypto');

class EnvironmentError extends Error {
  constructor(code, message) { super(message); this.code = code; }
}
const stopped = () => new EnvironmentError('cancelled', '环境准备已取消，可以重试。');
function checkAbort(signal) { if (signal?.aborted) throw stopped(); }
function cleanEnvironment(input, platform) {
  const env = {...input};
  for (const name of Object.keys(env)) if (/^(PYTHON|PIP_)/i.test(name) || /^(VIRTUAL_ENV|ELECTRON_RUN_AS_NODE|PYLAUNCHER_ALLOW_INSTALL|PYLAUNCHER_ALWAYS_INSTALL)$/i.test(name)) delete env[name];
  return {...env, PYTHONNOUSERSITE: '1', PYTHONUNBUFFERED: '1', PYTHON_MANAGER_AUTOMATIC_INSTALL: 'false', PIP_CONFIG_FILE: platform === 'win32' ? 'nul' : '/dev/null'};
}

// Every child is owned by this operation. POSIX children get their own process
// group; Windows taskkill targets only the recorded child PID and its descendants.
function runOwnedProcess(executable, args, {signal, timeoutMs = 30000, env = process.env,
                        platform = process.platform, onChild = () => {}} = {}) {
  checkAbort(signal);
  return new Promise((resolve, reject) => {
    let child, timer, failure = null, closed = false, stdout = '', stderr = '', cleaning = false, cleanupDone = Promise.resolve();
    const groupExists = pid => {
      try { process.kill(-pid, 0); return true; }
      catch (error) { if (error.code === 'ESRCH') return false; if (error.code === 'EPERM') return true; throw error; }
    };
    const stopGroup = async pid => {
      const send = signal => { try { process.kill(-pid, signal); } catch (error) { if (error.code !== 'ESRCH') throw error; } };
      const waitUntil = async deadline => {
        while (groupExists(pid) && Date.now() < deadline) await new Promise(resolve => setTimeout(resolve, 50));
        return !groupExists(pid);
      };
      send('SIGTERM');
      if (await waitUntil(Date.now() + 3000)) return;
      send('SIGKILL');
      if (!await waitUntil(Date.now() + 5000)) throw Error('Owned process group still exists');
    };
    const terminate = () => {
      if (!child?.pid || cleaning) return;
      cleaning = true;
      if (platform === 'win32') {
        const systemRoot = env.SystemRoot || env.SYSTEMROOT || 'C:\\Windows';
        const killer = spawn(path.win32.join(systemRoot, 'System32', 'taskkill.exe'), ['/PID', String(child.pid), '/T', '/F'],
          {windowsHide: true, shell: false, stdio: 'ignore'});
        cleanupDone = new Promise(done => {
          killer.once('error', () => { if (!closed) child.kill('SIGKILL'); });
          killer.once('close', code => {
            if (code !== 0) {
              if (!closed) child.kill('SIGKILL');
              failure = new EnvironmentError('cleanup_failed', '无法确认环境准备子进程已全部退出，请保留 App 并重试取消。');
            }
            done();
          });
        });
      } else {
        // Leader close does not imply an empty group: pip/venv descendants may
        // redirect stdio or ignore SIGTERM. Await group cleanup independently.
        cleanupDone = stopGroup(child.pid).catch(() => {
          failure = new EnvironmentError('cleanup_failed', '无法确认环境准备子进程已全部退出，请保留 App 并重试取消。');
          // Later retries may confirm disappearance, but must not signal an old
          // numeric PID again after it could have been reused by another process.
          failure.confirmCleanup = () => !groupExists(child.pid);
        });
      }
    };
    const interrupt = error => { if (!failure) { failure = error; terminate(); } };
    const abort = () => interrupt(stopped());
    try {
      child = spawn(executable, args, {env: cleanEnvironment(env, platform), shell: false, windowsHide: true,
        detached: platform !== 'win32', stdio: ['ignore', 'pipe', 'pipe']});
    } catch { reject(new EnvironmentError('spawn_failed', '无法启动 Python 检查或环境准备进程。')); return; }
    onChild(child);
    const collect = (name, chunk) => {
      if (name === 'stdout') stdout = (stdout + chunk).slice(-65536);
      else stderr = (stderr + chunk).slice(-65536);
    };
    child.stdout.on('data', chunk => collect('stdout', chunk));
    child.stderr.on('data', chunk => collect('stderr', chunk));
    child.once('error', () => { failure ||= new EnvironmentError('spawn_failed', '无法启动 Python 检查或环境准备进程。'); });
    child.once('close', async code => {
      if (failure || code !== 0) terminate();
      closed = true; clearTimeout(timer); signal?.removeEventListener('abort', abort);
      await cleanupDone;
      if (failure) reject(failure);
      else if (code !== 0) reject(new EnvironmentError('process_failed', 'Python 检查或依赖安装进程未成功完成。'));
      else resolve({stdout, stderr});
    });
    timer = setTimeout(() => interrupt(new EnvironmentError('timeout', '环境操作超时，请检查网络和本机 Python 后重试。')), timeoutMs);
    signal?.addEventListener('abort', abort, {once: true});
    if (signal?.aborted) abort();
  });
}

function pythonCandidates(platform = process.platform, env = process.env) {
  const p = platform === 'win32' ? path.win32 : path;
  const dirs = (env.PATH || env.Path || '').split(platform === 'win32' ? ';' : ':').filter(item => p.isAbsolute(item));
  const versions = ['3.15', '3.14', '3.13', '3.12', '3.11'];
  const candidates = [];
  if (platform === 'win32') {
    for (const base of [env.LOCALAPPDATA && p.join(env.LOCALAPPDATA, 'Programs', 'Python'), env.ProgramFiles]) {
      if (!base || !p.isAbsolute(base)) continue;
      dirs.push(p.join(base, 'Launcher'));
      for (const version of versions) dirs.push(p.join(base, 'Python' + version.replace('.', '')));
    }
    if (env.SystemRoot || env.SYSTEMROOT) dirs.push(env.SystemRoot || env.SYSTEMROOT);
    for (const dir of dirs) for (const name of ['python.exe', 'python3.exe', 'py.exe']) {
      // Python Store aliases may open a browser instead of running Python.
      if (/WindowsApps/i.test(dir) && name !== 'py.exe') continue;
      candidates.push({executable: p.join(dir, name), args: name === 'py.exe' ? ['-0p'] : []});
    }
  } else {
    dirs.push('/usr/local/bin', '/usr/bin');
    if (platform === 'darwin') {
      dirs.push('/opt/homebrew/bin', '/Library/Frameworks/Python.framework/Versions/Current/bin');
      for (const version of versions) dirs.push(`/opt/homebrew/opt/python@${version}/bin`, `/usr/local/opt/python@${version}/bin`, `/Library/Frameworks/Python.framework/Versions/${version}/bin`);
    }
    for (const dir of dirs) for (const name of ['python3', ...versions.map(version => 'python' + version)]) candidates.push({executable: p.join(dir, name), args: []});
  }
  return [...new Map(candidates.map(item => [item.executable, item])).values()];
}
const PROBE = 'import json,os,sys; print(json.dumps({"version":list(sys.version_info[:3]),"executable":os.path.realpath(getattr(sys,"_base_executable",sys.executable))}))';
const VERIFY = 'import importlib,importlib.metadata,json,pathlib,sys; assert sys.prefix != sys.base_prefix; assert pathlib.Path(sys.prefix).resolve() == pathlib.Path(sys.argv[1]).resolve(); [importlib.import_module(name) for name in ["briefloop","wikiskill","mcp","docx","lxml","PIL","pypdf","pypdfium2","openpyxl"]]; print(json.dumps({"version":importlib.metadata.version("briefloop")}))';
const UUID = /^[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}$/;

function createEnvironment({app, payloadPath, changed = () => {}, platform = process.platform, arch = process.arch,
                            runProcess = runOwnedProcess, candidates, env = process.env} = {}) {
  if (!app || !path.isAbsolute(payloadPath || '')) throw Error('Invalid environment configuration');
  const directory = path.join(app.getPath('userData'), 'environments');
  const activeFile = path.join(directory, 'active.json');
  let data = {state: 'checking', phase: 'idle', pythonVersion: null, error: null, retryable: false};
  let verified = null, pending = null, controller = null, cleanupFailure = null;
  const status = () => structuredClone(data);
  const publish = patch => { data = {...data, ...patch}; changed(status()); return status(); };
  const phase = (state, value) => publish({state, phase: value, error: null, retryable: false});
  function failure(error) {
    if (error.code === 'cleanup_failed') cleanupFailure = error;
    const code = error.code === 'cleanup_failed' ? 'cleanup_failed' : error instanceof EnvironmentError ? error.code : 'environment_failed';
    let message = code === 'cleanup_failed' ? '无法确认环境准备子进程已全部退出，请保留 App 并重试取消。' : error instanceof EnvironmentError ? error.message : '无法准备运行环境，请检查磁盘权限后重试。';
    if (code === 'process_failed') message = data.phase === 'install-dependencies'
      ? '依赖安装失败，请检查网络或该 Python 版本的预编译包支持后重试。'
      : 'Python 或依赖验证失败，请重新准备运行环境。';
    return publish({state: 'error', error: {code, message}, retryable: true});
  }
  async function run(executable, args, signal, timeoutMs = 30000) {
    checkAbort(signal);
    return runProcess(executable, args, {signal, timeoutMs, env, platform});
  }
  async function payload(signal) {
    phase('checking', 'verify-payload');
    const manifest = JSON.parse(await fs.readFile(path.join(payloadPath, 'manifest.json'), 'utf8'));
    if (!/^\d+\.\d+\.\d+$/.test(manifest.version || '') || typeof manifest.wheel !== 'string'
        || !/^briefloop-[a-zA-Z0-9_.-]+\.whl$/.test(manifest.wheel) || path.basename(manifest.wheel) !== manifest.wheel
        || !/^[a-f0-9]{64}$/i.test(manifest.sha256 || '')) throw new EnvironmentError('invalid_payload', '随包运行组件清单无效，请重新安装 App。');
    const wheel = path.join(payloadPath, manifest.wheel);
    const stat = await fs.lstat(wheel);
    if (!stat.isFile() || stat.isSymbolicLink()) throw new EnvironmentError('invalid_payload', '随包运行组件不可用，请重新安装 App。');
    const hash = createHash('sha256');
    for await (const chunk of createReadStream(wheel)) { checkAbort(signal); hash.update(chunk); }
    if (hash.digest('hex') !== manifest.sha256.toLowerCase()) throw new EnvironmentError('payload_hash', '随包运行组件校验失败，请重新安装 App。');
    return {...manifest, sha256: manifest.sha256.toLowerCase(), wheelPath: wheel};
  }
  async function probe(candidate, signal) {
    const executable = typeof candidate === 'string' ? candidate : candidate.executable;
    if (!path.isAbsolute(executable || '')) return null;
    try {
      await fs.access(executable, platform === 'win32' ? constants.F_OK : constants.X_OK);
      if (candidate.args?.[0] === '-0p') {
        // Both legacy py and the install manager support listing installed paths.
        // Listing cannot auto-install a missing interpreter, unlike `py -3`.
        const listed = await run(executable, ['-0p'], signal, 10000);
        for (const line of listed.stdout.split(/\r?\n/)) {
          const found = line.match(/([a-z]:\\.*\\python(?:w)?\.exe)\s*$/i);
          if (found) { const result = await probe(found[1], signal); if (result) return result; }
        }
        return null;
      }
      const result = JSON.parse((await run(executable, [...(candidate.args || []), '-I', '-c', PROBE], signal, 10000)).stdout.trim());
      if (!Array.isArray(result.version) || result.version.length !== 3 || !result.version.every(value => Number.isInteger(value) && value >= 0) || result.version[0] !== 3 || result.version[1] < 11 || !path.isAbsolute(result.executable || '')) return null;
      await fs.access(result.executable, platform === 'win32' ? constants.F_OK : constants.X_OK);
      return {executable: result.executable, version: result.version.join('.')};
    } catch (error) { if (error.code === 'cleanup_failed') throw error; checkAbort(signal); return null; }
  }
  async function host(active, signal) {
    phase('checking', 'detect-python');
    const choices = [...(active?.hostPython ? [active.hostPython] : []), ...(candidates || pythonCandidates(platform, env))];
    for (const candidate of choices) { const found = await probe(candidate, signal); if (found) return found; }
    return null;
  }
  function environmentPython(id) { return path.join(directory, id, ...(platform === 'win32' ? ['Scripts', 'python.exe'] : ['bin', 'python3'])); }
  async function validate(id, manifest, signal) {
    const envDir = path.join(directory, id);
    if ((await fs.lstat(envDir)).isSymbolicLink()) throw new EnvironmentError('invalid_environment', '运行环境目录无效，请重新准备。');
    const python = environmentPython(id);
    phase(data.state === 'installing' ? 'installing' : 'checking', 'verify-imports');
    const result = JSON.parse((await run(python, ['-I', '-c', VERIFY, envDir], signal, 60000)).stdout.trim());
    if (result.version !== manifest.version) throw new EnvironmentError('version_mismatch', '已安装组件版本与 App 不一致，请重新准备。');
    phase(data.state, 'verify-dependencies');
    await run(python, ['-I', '-m', 'pip', '--isolated', '--disable-pip-version-check', '--no-input', 'check'], signal, 60000);
    return python;
  }
  async function inspectImpl(signal) {
    verified = null;
    const manifest = await payload(signal);
    let active;
    try { if ((await fs.lstat(directory)).isSymbolicLink()) throw new EnvironmentError('unsafe_path', 'App 运行环境目录不可用。'); }
    catch (error) { if (error.code !== 'ENOENT') throw error; }
    try { active = JSON.parse(await fs.readFile(activeFile, 'utf8')); } catch {}
    const python = await host(active, signal);
    if (!python) { publish({state: 'missing-python', phase: 'detect-python', pythonVersion: null,
      error: {code: 'missing_python', message: '未找到 Python 3.11 或更高版本。请从 python.org 安装 Python 后重新检测；不会自动安装系统 Python。'}, retryable: true}); return {manifest, python}; }
    publish({pythonVersion: python.version});
    if (active?.schema === 1 && UUID.test(active.environmentId || '') && active.sha256 === manifest.sha256
        && active.version === manifest.version && active.wheel === manifest.wheel && active.platform === platform && active.arch === arch) {
      try {
        if (!await probe(active.hostPython, signal)) throw Error('Missing base Python');
        const executable = await validate(active.environmentId, manifest, signal);
        verified = {python: executable, node: process.execPath, nodeIsElectron: true};
        publish({state: 'ready', phase: 'ready', error: null, retryable: false}); return {manifest, python};
      } catch (error) { if (error.code === 'cleanup_failed') throw error; checkAbort(signal); }
    }
    publish({state: 'needs-setup', phase: 'needs-setup', error: null, retryable: true});
    return {manifest, python};
  }
  async function prepareImpl(signal) {
    const {manifest, python} = await inspectImpl(signal);
    if (!python || data.state === 'ready') return status();
    let id, created = false, committed = false, safeToRemove = true;
    try {
      await fs.mkdir(directory, {recursive: true, mode: 0o700});
      if ((await fs.lstat(directory)).isSymbolicLink()) throw new EnvironmentError('unsafe_path', 'App 运行环境目录不可用。');
      id = randomUUID();
      const target = path.join(directory, id);
      await fs.mkdir(target, {mode: 0o700}); created = true;
      phase('installing', 'create-venv');
      await run(python.executable, ['-I', '-m', 'venv', target], signal, 120000);
      phase('installing', 'install-dependencies');
      const executable = environmentPython(id);
      await run(executable, ['-I', '-m', 'pip', '--isolated', '--disable-pip-version-check', '--no-input',
        'install', '--only-binary=:all:', '--index-url', 'https://pypi.org/simple', manifest.wheelPath], signal, 15 * 60 * 1000);
      await validate(id, manifest, signal);
      phase('installing', 'activate-environment');
      const record = {schema: 1, environmentId: id, version: manifest.version, wheel: manifest.wheel, sha256: manifest.sha256,
        hostPython: python.executable, pythonVersion: python.version, platform, arch};
      const temporary = path.join(directory, `active-${randomUUID()}.tmp`);
      try {
        const handle = await fs.open(temporary, 'wx', 0o600);
        try { await handle.writeFile(JSON.stringify(record)); await handle.sync(); } finally { await handle.close(); }
        checkAbort(signal);
        await fs.rename(temporary, activeFile);
        committed = true;
      } finally { await fs.rm(temporary, {force: true}); }
      verified = {python: executable, node: process.execPath, nodeIsElectron: true};
      return publish({state: 'ready', phase: 'ready', error: null, retryable: false});
    } catch (error) {
      if (error.code === 'cleanup_failed') { safeToRemove = false; error.partialDirectory = id && created ? path.join(directory, id) : null; }
      throw error;
    } finally { if (id && created && !committed && safeToRemove) await fs.rm(path.join(directory, id), {recursive: true, force: true}); }
  }
  function operation(callback) {
    if (cleanupFailure) return Promise.resolve(status());
    if (pending) return pending;
    controller = new AbortController();
    pending = Promise.resolve().then(() => callback(controller.signal)).catch(error => failure(controller.signal.aborted && error.code !== 'cleanup_failed' ? stopped() : error)).finally(() => {pending = null; controller = null;});
    return pending;
  }
  return {
    status,
    inspect: () => operation(async signal => {await inspectImpl(signal); return status();}),
    prepare: () => operation(prepareImpl),
    runtime: () => {if (!verified || data.state !== 'ready') throw Error('运行环境尚未验证就绪，请先完成环境准备。'); return {...verified};},
    cancel: async () => {
      if (pending) {controller.abort(); await pending;}
      if (cleanupFailure) {
        if (!cleanupFailure.confirmCleanup?.()) throw Error('环境准备进程尚未确认退出，请保留 App；进程退出后可重试取消。');
        if (cleanupFailure.partialDirectory) await fs.rm(cleanupFailure.partialDirectory, {recursive: true, force: true});
        cleanupFailure = null; failure(stopped());
      }
      return status();
    },
  };
}
module.exports = {createEnvironment, runOwnedProcess, pythonCandidates};
