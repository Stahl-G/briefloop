'use strict';

const test = require('node:test');
const assert = require('node:assert/strict');
const childProcess = require('node:child_process');
const {discoverWindowsPython} = require('../windows-python.cjs');

function probe(executable, changes = {}) {
  return JSON.stringify({executable, version: '3.13.15', version_info: [3, 13, 15], platform: 'win32',
    machine: 'AMD64', bits: 64, venv: false, has_venv: true, has_ensurepip: true, ...changes});
}

test('discovers launcher/PATH/registry paths, ignores aliases and selects highest compatible actual installation', async t => {
  const calls = [];
  t.mock.method(childProcess, 'execFile', (file, args, options, callback) => {
    calls.push({file, args, options});
    let output;
    if (file === 'py.exe') output = ' -V:3.11 * C:\\Python 311\\python.exe\r\n -V:3.13 C:\\中文 Python\\python.exe';
    else if (/where.exe$/i.test(file)) output = 'C:\\Users\\u\\AppData\\Local\\Microsoft\\WindowsApps\\python.exe\r\nC:\\Dev\\venv\\python.exe\r\nC:\\Python 311\\python.exe';
    else if (/reg.exe$/i.test(file)) output = 'HKEY_CURRENT_USER\\Software\\Python\\PythonCore\\3.14\\InstallPath\r\n    (Default)    REG_SZ    C:\\Python314\\\r\n    ExecutablePath    REG_SZ    C:\\Python314\\python.exe';
    else if (file.includes('311')) output = probe(file, {version: '3.11.9', version_info: [3, 11, 9]});
    else if (file.includes('314')) output = probe(file, {version: '3.14.2', version_info: [3, 14, 2]});
    else output = probe(file);
    callback(null, output);
  });
  const result = await discoverWindowsPython({env: {SystemRoot: 'C:\\Windows', PATH: 'C:\\Windows', PythonHome: 'bad', PYTHONPATH: 'bad', VIRTUAL_ENV: 'old'}, excludeRoots: ['C:\\Dev']});
  assert.equal(result.python, 'C:\\Python314\\python.exe');
  assert.equal(result.status, 'ready');
  assert.equal(result.candidates.length, 5);
  assert.equal(result.candidates.find(c => c.path.includes('WindowsApps')).reason, 'windows_store_alias');
  assert.equal(result.candidates.find(c => c.path.includes('Dev')).reason, 'excluded_root');
  assert.ok(!calls.some(c => /WindowsApps|\\Dev\\/.test(c.file)));
  for (const call of calls) {
    assert.equal(call.options.shell, false); assert.equal(call.options.windowsHide, true);
    assert.ok(call.options.timeout <= 5000);
    assert.equal(call.options.env.PYTHONPATH, undefined); assert.equal(call.options.env.PythonHome, undefined);
    if (!/^(py.exe)$|(?:where|reg)\.exe$/i.test(call.file)) assert.deepEqual(call.args.slice(0, 5), ['-I', '-X', 'utf8', '-B', '-c']);
  }
});

test('rejects old versions, venvs, ARM/x86, missing bootstrap modules and excluded actual executables', async t => {
  const variants = [
    {version: '3.10.9', version_info: [3, 10, 9]}, {venv: true}, {machine: 'ARM64'}, {bits: 32},
    {has_ensurepip: false}, {has_venv: false}, {executable: 'C:\\Dev\\python.exe'},
  ];
  t.mock.method(childProcess, 'execFile', (file, args, options, callback) => {
    if (file === 'py.exe') return callback(null, variants.map((_, i) => ` -V:3.${i} C:\\P${i}\\python.exe`).join('\n'));
    if (/(where|reg)\.exe$/i.test(file)) return callback(null, '');
    const index = Number(file.match(/P(\d+)/)[1]);callback(null, probe(file, variants[index]));
  });
  const result = await discoverWindowsPython({excludeRoots: ['C:\\Dev']});
  assert.equal(result.status, 'missing');assert.equal(result.python, null);assert.equal(result.version, null);
  assert.deepEqual(result.candidates.map(c => c.reason), ['python_too_old', 'virtual_environment', 'requires_windows_x64', 'requires_windows_x64', 'missing_venv_or_ensurepip', 'missing_venv_or_ensurepip', 'excluded_root']);
  assert.equal(result.guidance.url, 'https://www.python.org/downloads/windows/');
});

test('failed or malformed interpreter probes expose stable reasons, never stderr', async t => {
  t.mock.method(childProcess, 'execFile', (file, args, options, callback) => {
    if (file === 'py.exe') return callback(null, ' -V:3.13 C:\\Timeout\\python.exe\n -V:3.14 C:\\Malformed\\python.exe');
    if (/(where|reg)\.exe$/i.test(file)) return callback(null, '');
    if (file.includes('Timeout')) return callback(Object.assign(new Error('secret stderr'), {killed: true}), '', 'secret stderr');
    callback(null, 'not JSON');
  });
  const result = await discoverWindowsPython();
  assert.deepEqual(result.candidates.map(c => c.reason), ['probe_timeout', 'invalid_probe']);
  assert.ok(!JSON.stringify(result).includes('secret'));
});
