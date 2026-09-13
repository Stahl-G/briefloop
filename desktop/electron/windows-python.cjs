'use strict';

const childProcess = require('node:child_process');
const path = require('node:path').win32;

const GUIDANCE = {
  url: 'https://www.python.org/downloads/windows/',
  message: '请安装 Windows x64 Python 3.11 或更新版本，并保留 venv 与 ensurepip，然后重新检查。BriefLoop 将在自己的目录创建隔离环境，不会向全局 Python 安装依赖。',
};
const PROBE = `import importlib.util, json, platform, struct, sys
print(json.dumps({"executable":sys.executable,"version":platform.python_version(),"version_info":list(sys.version_info[:3]),"platform":sys.platform,"machine":platform.machine(),"bits":struct.calcsize("P")*8,"venv":sys.prefix!=sys.base_prefix or hasattr(sys,"real_prefix"),"has_venv":importlib.util.find_spec("venv") is not None,"has_ensurepip":importlib.util.find_spec("ensurepip") is not None}))`;

function sanitizedEnv(env) {
  const clean = {};
  for (const [name, value] of Object.entries(env)) {
    if (/^(PYTHON.*|PY_PYTHON.*|VIRTUAL_ENV|CONDA_PREFIX|CONDA_DEFAULT_ENV)$/i.test(name)) continue;
    clean[name] = value;
  }
  return clean;
}

function run(file, args, env, timeout) {
  return new Promise(resolve => {
    childProcess.execFile(file, args, {env, timeout, windowsHide: true, shell: false,
      encoding: 'utf8', maxBuffer: 256 * 1024}, (error, stdout) => {
      if (error) return resolve({ok: false, reason: error.killed || error.code === 'ETIMEDOUT' ? 'probe_timeout' : 'execution_failed'});
      resolve({ok: true, output: stdout || ''});
    });
  });
}

function absolute(value) {
  if (typeof value !== 'string') return null;
  const clean = value.trim().replace(/^"(.*)"$/, '$1');
  // Reject controls, UNC/network paths and device namespaces before executing.
  if (!/^[a-z]:[\\/]/i.test(clean) || /[\x00-\x1f]/.test(clean)) return null;
  return path.normalize(clean);
}

function inside(candidate, root) {
  const relative = path.relative(root.toLowerCase(), candidate.toLowerCase());
  return relative === '' || (!relative.startsWith('..\\') && relative !== '..' && !path.isAbsolute(relative));
}

function excludedReason(candidate, roots) {
  if (candidate.split(/[\\/]/).some(part => part.toLowerCase() === 'windowsapps')) return 'windows_store_alias';
  if (roots.some(root => inside(candidate, root))) return 'excluded_root';
  return null;
}

function expand(value, env) {
  return value.replace(/%([^%]+)%/g, (all, key) => {
    const found = Object.keys(env).find(name => name.toLowerCase() === key.toLowerCase());
    return found ? env[found] : all;
  });
}

function launcherPaths(output) {
  return output.split(/\r?\n/).map(line => line.match(/(?:^|\s)([a-z]:[\\/].*?\.exe)\s*$/i)?.[1]).filter(Boolean);
}

function registryPaths(output, env) {
  const candidates = [];
  let installPath = false;
  for (const line of output.split(/\r?\n/)) {
    if (/^HKEY_/i.test(line.trim())) { installPath = /\\InstallPath\s*$/i.test(line.trim()); continue; }
    const match = line.match(/^\s*(.*?)\s+REG_(?:EXPAND_)?SZ\s+(.+?)\s*$/i);
    if (!match || !installPath) continue;
    if (/^ExecutablePath$/i.test(match[1])) candidates.push(expand(match[2], env));
    else if (/^(?:\(Default\)|\(默认\))$/i.test(match[1])) candidates.push(path.join(expand(match[2], env), 'python.exe'));
  }
  return candidates;
}

function evaluate(candidate, probe, roots) {
  const actual = absolute(probe.executable);
  const version = typeof probe.version === 'string' ? probe.version : null;
  const result = {path: candidate, version, compatible: false, reason: 'invalid_probe'};
  if (!actual || !Array.isArray(probe.version_info) || probe.version_info.length !== 3 ||
      !probe.version_info.every(Number.isInteger) || !version) return result;
  const excluded = excludedReason(actual, roots);
  if (excluded) return {...result, reason: excluded};
  if (probe.platform !== 'win32' || probe.bits !== 64 || !/^(amd64|x86_64)$/i.test(probe.machine || '')) return {...result, reason: 'requires_windows_x64'};
  if (probe.version_info[0] < 3 || (probe.version_info[0] === 3 && probe.version_info[1] < 11)) return {...result, reason: 'python_too_old'};
  if (probe.venv !== false) return {...result, reason: 'virtual_environment'};
  if (probe.has_venv !== true || probe.has_ensurepip !== true) return {...result, reason: 'missing_venv_or_ensurepip'};
  return {...result, path: actual, compatible: true, reason: 'compatible', order: probe.version_info};
}

async function discoverWindowsPython({env = process.env, excludeRoots = []} = {}) {
  const clean = sanitizedEnv(env);
  const roots = excludeRoots.map(absolute).filter(Boolean);
  const systemRoot = Object.entries(env).find(([key]) => /^SystemRoot$/i.test(key))?.[1] || 'C:\\Windows';
  const where = path.join(systemRoot, 'System32', 'where.exe');
  const reg = path.join(systemRoot, 'System32', 'reg.exe');
  const queries = [
    run('py.exe', ['-0p'], clean, 3000).then(r => r.ok ? launcherPaths(r.output) : []),
    ...['python', 'python3'].map(name => run(where, [name], clean, 3000).then(r => r.ok ? r.output.split(/\r?\n/) : [])),
    ...['HKCU', 'HKLM'].flatMap(hive => ['32', '64'].map(view =>
      run(reg, ['query', `${hive}\\Software\\Python\\PythonCore`, '/s', `/reg:${view}`], clean, 3000)
        .then(r => r.ok ? registryPaths(r.output, env) : []))),
  ];
  const seen = new Set();
  const paths = (await Promise.all(queries)).flat().map(absolute).filter(value => {
    if (!value || seen.has(value.toLowerCase())) return false;
    seen.add(value.toLowerCase()); return true;
  }).slice(0, 24);
  const candidates = [];
  // Four probes at a time: at most 30 seconds of interpreter waits, plus queries.
  for (let offset = 0; offset < paths.length; offset += 4) {
    candidates.push(...await Promise.all(paths.slice(offset, offset + 4).map(async candidate => {
      const excluded = excludedReason(candidate, roots);
      if (excluded) return {path: candidate, version: null, compatible: false, reason: excluded};
      const probe = await run(candidate, ['-I', '-X', 'utf8', '-B', '-c', PROBE], clean, 5000);
      if (!probe.ok) return {path: candidate, version: null, compatible: false, reason: probe.reason};
      try { return evaluate(candidate, JSON.parse(probe.output), roots); }
      catch { return {path: candidate, version: null, compatible: false, reason: 'invalid_probe'}; }
    })));
  }
  const compatible = candidates.filter(candidate => candidate.compatible).sort((a, b) => {
    for (let i = 0; i < 3; i++) if (a.order[i] !== b.order[i]) return b.order[i] - a.order[i];
    return a.path.localeCompare(b.path);
  });
  const selected = compatible[0];
  return {status: selected ? 'ready' : 'missing', python: selected?.path || null, version: selected?.version || null,
    candidates: candidates.map(({order, ...candidate}) => candidate), guidance: {...GUIDANCE}};
}

module.exports = {discoverWindowsPython};
