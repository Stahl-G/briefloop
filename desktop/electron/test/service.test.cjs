const test = require('node:test');
const assert = require('node:assert/strict');
const {validateMarker, runtimeLaunch} = require('../service.cjs');

test('Windows launch uses the owned venv and Electron Node while preserving native CLI PATH', () => {
  const launch = runtimeLaunch({python: 'C:\\中文 应用\\venv\\Scripts\\python.exe', node: 'C:\\应用\\BriefLoop.exe', nodeIsElectron: true}, {Path: 'C:\\User CLI;C:\\Windows;relative', PYTHONPATH: 'old-checkout', PythonHome: 'old-python', NODE_PATH: 'old-node', ELECTRON_RUN_AS_NODE: '1', APPDATA: 'C:\\UserData'}, 'win32');
  assert.equal(launch.python, 'C:\\中文 应用\\venv\\Scripts\\python.exe');
  assert.equal(launch.node, 'C:\\应用\\BriefLoop.exe');
  assert.equal(launch.env.PATH, 'C:\\中文 应用\\venv\\Scripts;C:\\User CLI;C:\\Windows');
  assert.equal(launch.env.BRIEFLOOP_NODE_IS_ELECTRON, '1');
  assert.equal(launch.env.APPDATA, 'C:\\UserData');
  for (const name of ['Path', 'PYTHONPATH', 'PythonHome', 'NODE_PATH', 'ELECTRON_RUN_AS_NODE']) assert.equal(launch.env[name], undefined);
  assert.deepEqual(launch.args, ['-I', '-X', 'utf8', '-u']);
});

test('macOS launch uses the prepared environment and colon PATH', () => {
  const launch = runtimeLaunch({python: '/App Data/venv/bin/python3', node: '/Applications/BriefLoop.app/Contents/MacOS/BriefLoop', nodeIsElectron: true}, {PATH: '/usr/local/bin:/usr/bin', NODE_PATH: '/old'}, 'darwin');
  assert.equal(launch.python, '/App Data/venv/bin/python3');
  assert.equal(launch.env.PATH, '/App Data/venv/bin:/usr/local/bin:/usr/bin');
  assert.equal(launch.env.NODE_PATH, undefined);
});

test('startup accepts only the exact owned child and loopback launch marker', () => {
  const marker = {pid: 123, launch_id: 'own-launch', workspace_id: 'workspace', url: 'http://127.0.0.1:49152'};
  assert.equal(validateMarker(marker, {pid: 123}, 'own-launch'), marker.url);
  for (const changed of [{pid: 456}, {launch_id: 'stale-launch'}, {workspace_id: ''},
    ...['http://localhost:49152', 'https://127.0.0.1:49152', 'http://127.0.0.1:49152/other', 'http://user@127.0.0.1:49152'].map(url => ({url}))]) {
    assert.throws(() => validateMarker({...marker, ...changed}, {pid: 123}, 'own-launch'));
  }
});
