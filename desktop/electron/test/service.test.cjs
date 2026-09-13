const test = require('node:test');
const assert = require('node:assert/strict');
const {validateMarker, runtimeLaunch} = require('../service.cjs');
const fs = require('node:fs/promises');
const path = require('node:path');
const os = require('node:os');
const vm = require('node:vm');
const {spawn} = require('node:child_process');
const runtime = {python: process.execPath, basePython: process.execPath, node: process.execPath};

test('Windows launch uses the owned venv and Electron Node while preserving native CLI PATH', () => {
  const launch = runtimeLaunch({python: 'C:\\中文 应用\\venv\\Scripts\\python.exe', basePython: 'C:\\Python\\python.exe', node: 'C:\\应用\\BriefLoop.exe', nodeIsElectron: true}, {Path: 'C:\\User CLI;C:\\Windows;relative', PYTHONPATH: 'old-checkout', PythonHome: 'old-python', NODE_PATH: 'old-node', ELECTRON_RUN_AS_NODE: '1', __PYVENV_LAUNCHER__: 'C:\\old-venv\\python.exe', APPDATA: 'C:\\UserData'}, 'win32');
  assert.equal(launch.python, 'C:\\中文 应用\\venv\\Scripts\\python.exe');
  assert.equal(launch.executable, 'C:\\Python\\python.exe');
  assert.equal(launch.env.__PYVENV_LAUNCHER__, launch.python);
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

test('preflight rejects invalid existing workspaces and cleans up its write probe', async t => {
  const {WorkspaceService} = require('../service.cjs');
  const root = await fs.mkdtemp(path.join(os.tmpdir(), 'briefloop-preflight-'));
  t.after(() => fs.rm(root, {recursive: true, force: true}));
  const service = new WorkspaceService(runtime);
  const file = path.join(root, 'file'); await fs.writeFile(file, 'synthetic');
  await assert.rejects(service.preflight(file));
  await assert.rejects(service.preflight(root), /新建工作区/);
  assert.equal(await service.preflight(path.join(root, 'new'), {create: true}), await fs.realpath(path.join(root, 'new')));
  assert.deepEqual(await fs.readdir(path.join(root, 'new')), []);
  assert.equal(service.child, null);
});

test('failed startup closes the owner pipe and waits for EOF cleanup, without killing the service PID', async t => {
  const root = await fs.mkdtemp(path.join(os.tmpdir(), 'briefloop-owner-pipe-'));
  t.after(() => fs.rm(root, {recursive: true, force: true}));
  // A real subprocess implements only the startup/EOF protocol. Full Python
  // cancellation and descendant cleanup require the separate native acceptance.
  const protocol = `
    const fs = require('node:fs'), http = require('node:http');
    const server = http.createServer((req,res) => {
      res.setHeader('Content-Type','application/json');
      if (req.url === '/api/runtime') res.end(JSON.stringify({server_pid:process.pid}));
      else { res.statusCode = 500; res.end(JSON.stringify({error:'synthetic session failure'})); }
    });
    server.listen(0,'127.0.0.1',() => fs.writeFileSync('server.json',JSON.stringify({
      pid:process.pid, launch_id:process.env.BRIEFLOOP_LAUNCH_ID, workspace_id:'synthetic',
      url:'http://127.0.0.1:'+server.address().port
    })));
    process.stdin.resume(); process.stdin.on('end',() => {
      fs.writeFileSync('owner-eof.json',JSON.stringify({flag:process.env.BRIEFLOOP_DESKTOP_OWNER_PIPE}));
      server.close(() => process.exit(0)); server.closeAllConnections();
    });`;
  let child, launchArgs, killCalls = 0;
  const source = await fs.readFile(require.resolve('../service.cjs'), 'utf8');
  const module = {exports: {}};
  vm.runInNewContext(source, {module, process, URL, fetch, AbortSignal, setTimeout, clearTimeout,
    require: name => name === 'node:child_process' ? {spawn: (_executable, args, options) => {
      launchArgs = args;
      child = spawn(process.execPath, ['-e', protocol], options);
      child.kill = () => { killCalls++; throw Error('must request EOF cleanup'); };
      return child;
    }} : require(name)});
  const service = new module.exports.WorkspaceService(runtime);
  await assert.rejects(service.start(root, {create: true}), /synthetic session failure/);
  assert.ok(launchArgs.includes('--paused'));
  assert.equal((JSON.parse(await fs.readFile(path.join(root, 'owner-eof.json'), 'utf8'))).flag, '1');
  assert.equal(child.exitCode, 0); assert.equal(service.child, null); assert.equal(killCalls, 0);
});

test('Windows owner hard termination lets the owned service receive EOF and finish cleanup',
  {skip: process.platform !== 'win32', timeout: 30000}, async t => {
  const root = await fs.mkdtemp(path.join(os.tmpdir(), 'briefloop-owner-killed-'));
  let owner, childPid;
  const alive = pid => {
    try { process.kill(pid, 0); return true; }
    catch (error) { if (error.code === 'ESRCH') return false; throw error; }
  };
  const waitFor = async (check, label) => {
    const deadline = Date.now() + 10000;
    while (Date.now() < deadline) {
      if (await check()) return;
      await new Promise(resolve => setTimeout(resolve, 25));
    }
    throw Error('Timed out waiting for ' + label);
  };
  t.after(async () => {
    if (owner && owner.exitCode === null && owner.signalCode === null) owner.kill('SIGKILL');
    // The failure path must not leave an orphan when the regression is restored.
    if (childPid && alive(childPid)) process.kill(childPid, 'SIGKILL');
    if (childPid) await waitFor(() => !alive(childPid), 'fixture service cleanup');
    if (owner) await waitFor(() => owner.exitCode !== null || owner.signalCode !== null, 'fixture owner cleanup');
    await fs.rm(root, {recursive: true, force: true, maxRetries: 20, retryDelay: 100});
  });
  const protocol = `
    const fs = require('node:fs'), http = require('node:http');
    fs.writeFileSync('child-pid.json', JSON.stringify({pid:process.pid}));
    const server = http.createServer((req,res) => {
      res.setHeader('Content-Type','application/json');
      res.end(JSON.stringify(req.url === '/api/runtime' ? {server_pid:process.pid} : {token:'synthetic'}));
    });
    server.listen(0,'127.0.0.1',() => fs.writeFileSync('server.json',JSON.stringify({
      pid:process.pid, launch_id:process.env.BRIEFLOOP_LAUNCH_ID, workspace_id:'synthetic',
      url:'http://127.0.0.1:'+server.address().port
    })));
    process.stdin.resume();
    process.stdin.once('end',() => {
      fs.writeFileSync('eof-started.json',JSON.stringify({pid:process.pid,flag:process.env.BRIEFLOOP_DESKTOP_OWNER_PIPE}));
      server.close(() => setTimeout(() => {
        fs.writeFileSync('eof-finished.json',JSON.stringify({pid:process.pid,cleaned:true}));
        process.exit(0);
      },150));
      server.closeAllConnections();
    });`;
  // Run the real WorkspaceService in a separate owner process. Substitute only
  // the Python executable/protocol; preserve actual spawn options and stdin.
  const ownerSource = `
    const fs=require('node:fs'), vm=require('node:vm'), {spawn}=require('node:child_process');
    const serviceModule={exports:{}};
    vm.runInNewContext(fs.readFileSync(${JSON.stringify(require.resolve('../service.cjs'))},'utf8'),{
      module:serviceModule,process,URL,fetch,AbortSignal,setTimeout,clearTimeout,
      require:name=>name==='node:child_process'?{spawn:(_executable,_args,options)=>
        spawn(process.execPath,['-e',${JSON.stringify(protocol)}],options)}:require(name)
    });
    const runtime={python:process.execPath,basePython:process.execPath,node:process.execPath};
    const service=new serviceModule.exports.WorkspaceService(runtime);
    service.start(${JSON.stringify(root)},{create:true}).then(() => {
      fs.writeFileSync('owner-ready.json',JSON.stringify({owner:process.pid,child:service.child.pid}));
    }).catch(error=>{console.error(error);process.exitCode=1});`;
  const ownerPath = path.join(root, 'owner.cjs');
  await fs.writeFile(ownerPath, ownerSource);
  owner = spawn(process.execPath, [ownerPath], {cwd: root, stdio: ['ignore', 'ignore', 'pipe'], windowsHide: true});
  let diagnostic = '';
  owner.stderr.on('data', chunk => { diagnostic += chunk; });
  const exists = async name => fs.access(path.join(root, name)).then(() => true, () => false);
  await waitFor(async () => {
    if (await exists('child-pid.json')) childPid = JSON.parse(await fs.readFile(path.join(root, 'child-pid.json'), 'utf8')).pid;
    if (owner.exitCode !== null || owner.signalCode !== null) throw Error('Owner exited before readiness: ' + diagnostic);
    return exists('owner-ready.json');
  }, 'owned service readiness');
  const ready = JSON.parse(await fs.readFile(path.join(root, 'owner-ready.json'), 'utf8'));
  assert.equal(ready.owner, owner.pid); assert.equal(ready.child, childPid);
  assert.ok(alive(childPid)); assert.equal(await exists('eof-started.json'), false);
  // Kill only the owner PID: no tree kill, service.stop(), or stdin.end().
  assert.equal(owner.kill('SIGKILL'), true);
  await waitFor(() => owner.exitCode !== null || owner.signalCode !== null, 'owner termination');
  await waitFor(() => exists('eof-finished.json'), 'service EOF cleanup after owner termination');
  assert.deepEqual(JSON.parse(await fs.readFile(path.join(root, 'eof-started.json'), 'utf8')), {pid: childPid, flag: '1'});
  assert.deepEqual(JSON.parse(await fs.readFile(path.join(root, 'eof-finished.json'), 'utf8')), {pid: childPid, cleaned: true});
  await waitFor(() => !alive(childPid), 'service exit after cleanup');
});
