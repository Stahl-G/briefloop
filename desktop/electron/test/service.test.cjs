const test = require('node:test');
const assert = require('node:assert/strict');
const {validateMarker, runtimeLaunch} = require('../service.cjs');

test('Windows launch finds the native bundle and preserves host CLI PATH without inherited Python/Node overrides', () => {
  const launch = runtimeLaunch('C:\\中文 应用\\runtime', {Path: 'C:\\User CLI;C:\\Windows', PYTHONPATH: 'old-checkout', PythonHome: 'old-python', NODE_PATH: 'old-node', ELECTRON_RUN_AS_NODE: '1', APPDATA: 'C:\\UserData'}, 'win32');
  assert.equal(launch.python, 'C:\\中文 应用\\runtime\\python\\python.exe');
  assert.equal(launch.node, 'C:\\中文 应用\\runtime\\node\\node.exe');
  assert.equal(launch.env.PATH, 'C:\\中文 应用\\runtime\\node;C:\\User CLI;C:\\Windows');
  assert.equal(launch.env.APPDATA, 'C:\\UserData');
  for (const name of ['Path', 'PYTHONPATH', 'PythonHome', 'NODE_PATH', 'ELECTRON_RUN_AS_NODE']) assert.equal(launch.env[name], undefined);
  assert.deepEqual(launch.args, ['-I', '-X', 'utf8', '-u']);
});

test('macOS launch retains its native bundle layout and colon PATH', () => {
  const launch = runtimeLaunch('/Applications/BriefLoop.app/runtime', {PATH: '/usr/local/bin:/usr/bin', NODE_PATH: '/old'}, 'darwin');
  assert.equal(launch.python, '/Applications/BriefLoop.app/runtime/python/bin/python3');
  assert.equal(launch.env.PATH, '/Applications/BriefLoop.app/runtime/node/bin:/usr/local/bin:/usr/bin');
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
