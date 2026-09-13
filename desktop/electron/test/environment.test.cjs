'use strict';
const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs/promises');
const os = require('node:os');
const path = require('node:path');
const {createHash} = require('node:crypto');
const {createEnvironment, runOwnedProcess, pythonCandidates} = require('../environment.cjs');

async function fixture(t) {
  const root=await fs.mkdtemp(path.join(os.tmpdir(),'briefloop-environment-test-'));
  t.after(()=>fs.rm(root,{recursive:true,force:true}));
  const payloadPath=path.join(root,'payload'),host=path.join(root,'host-python');
  await fs.mkdir(payloadPath);await fs.writeFile(host,'Synthetic host marker',{mode:0o700});
  let version='0.19.0',failInstall=false,waitInstall=false,enteredInstall;
  const versions=new Map(),calls=[],changes=[];
  async function payload(next) {
    version=next;const wheel=`briefloop-${version}-py3-none-any.whl`,bytes=Buffer.from('Synthetic wheel fixture '+version);
    await fs.writeFile(path.join(payloadPath,wheel),bytes);
    await fs.writeFile(path.join(payloadPath,'manifest.json'),JSON.stringify({version,wheel,sha256:createHash('sha256').update(bytes).digest('hex')}));
  }
  await payload(version);
  const runProcess=async(executable,args,options)=>{
    calls.push({executable,args,options});
    if(args.includes('-c')&&args.some(value=>value.includes('sys.version_info')))return {stdout:JSON.stringify({version:[3,12,5],executable:host})};
    if(args[2]==='venv'){
      const target=args.at(-1);versions.set(target,version);await fs.mkdir(path.join(target,'bin'));
      await fs.writeFile(path.join(target,'bin','python3'),'Synthetic venv marker',{mode:0o700});return {stdout:''};
    }
    if(args.includes('install')){
      enteredInstall?.();
      if(waitInstall)await new Promise((resolve,reject)=>options.signal.addEventListener('abort',()=>reject(Error('cancelled fake install')),{once:true}));
      if(failInstall)throw Error('https://private.invalid/?token=do-not-expose');
      return {stdout:''};
    }
    if(args.includes('-c')){await fs.access(executable);return {stdout:JSON.stringify({version:versions.get(args.at(-1))})};}
    assert.equal(args.at(-1),'check');return {stdout:'No broken requirements found.'};
  };
  const config={app:{getPath:()=>root},payloadPath,platform:'darwin',arch:'arm64',candidates:[host],runProcess,changed:value=>changes.push(value)};
  return {root,host,payloadPath,payload,config,calls,changes,environment:createEnvironment(config),
    failInstall:()=>{failInstall=true},recover:()=>{failInstall=false},
    waitInstall:()=>{waitInstall=true;return new Promise(resolve=>{enteredInstall=resolve})}};
}

test('inspection never installs, detects missing Python, and refuses a tampered bundled wheel',async t=>{
  const f=await fixture(t),missing=createEnvironment({...f.config,candidates:[]});
  assert.equal((await missing.inspect()).state,'missing-python');assert.equal(f.calls.length,0);
  assert.throws(()=>missing.runtime(),/尚未验证/);
  assert.equal((await f.environment.inspect()).state,'needs-setup');
  assert.ok(f.calls.every(call=>call.args.includes('-c')));
  const count=f.calls.length;
  await fs.appendFile(path.join(f.payloadPath,'briefloop-0.19.0-py3-none-any.whl'),'tampered');
  assert.equal((await f.environment.prepare()).error.code,'payload_hash');assert.equal(f.calls.length,count);
});

test('prepare verifies declared modules and pip check before atomically selecting a stable-path venv; failed upgrade keeps the previous environment',async t=>{
  const f=await fixture(t);assert.equal((await f.environment.prepare()).state,'ready');
  const runtime=f.environment.runtime();assert.equal(runtime.node,process.execPath);assert.equal(runtime.nodeIsElectron,true);
  const activeFile=path.join(f.root,'environments','active.json'),before=await fs.readFile(activeFile,'utf8');
  const record=JSON.parse(before),target=path.join(f.root,'environments',record.environmentId);
  assert.equal(runtime.python,path.join(target,'bin','python3'));
  const install=f.calls.find(call=>call.args.includes('install'));
  assert.deepEqual(install.args.slice(0,10),['-I','-m','pip','--isolated','--disable-pip-version-check','--no-input','install','--only-binary=:all:','--index-url','https://pypi.org/simple']);
  assert.equal(f.calls.find(call=>call.args[2]==='venv').args.at(-1),target);
  const verify=f.calls.find(call=>call.args.some(value=>value.includes('importlib.import_module'))).args.join(' ');
  for(const name of ['briefloop','wikiskill','mcp','docx','lxml','PIL','pypdf','pypdfium2','openpyxl'])assert.ok(verify.includes(`"${name}"`));
  assert.ok(f.calls.some(call=>call.args.at(-1)==='check'));
  assert.ok(f.changes.some(value=>value.phase==='install-dependencies'));
  await f.payload('0.20.0');f.failInstall();assert.equal((await f.environment.prepare()).state,'error');
  assert.doesNotMatch(JSON.stringify(f.environment.status()),/private.invalid|do-not-expose/);
  assert.equal(await fs.readFile(activeFile,'utf8'),before);
  assert.deepEqual((await fs.readdir(path.dirname(activeFile))).sort(),['active.json',record.environmentId].sort());
  assert.throws(()=>f.environment.runtime(),/尚未验证/);
  f.recover();assert.equal((await f.environment.prepare()).state,'ready');
  assert.notEqual(f.environment.runtime().python,runtime.python);assert.equal((await fs.stat(target)).isDirectory(),true);
  const fresh=createEnvironment(f.config);assert.equal((await fresh.inspect()).state,'ready');
  await fs.unlink(fresh.runtime().python);assert.equal((await fresh.inspect()).state,'needs-setup');
  await fs.unlink(f.host);assert.equal((await fresh.inspect()).state,'missing-python');assert.throws(()=>fresh.runtime(),/尚未验证/);
});

test('cancel waits for its setup operation and removes only its incomplete venv',async t=>{
  const f=await fixture(t);await f.environment.prepare();
  const activeFile=path.join(f.root,'environments','active.json'),before=await fs.readFile(activeFile,'utf8');
  await f.payload('0.20.0');const installing=f.waitInstall();const pending=f.environment.prepare();await installing;
  assert.equal((await f.environment.cancel()).error.code,'cancelled');await pending;
  assert.equal(await fs.readFile(activeFile,'utf8'),before);
  assert.deepEqual((await fs.readdir(path.dirname(activeFile))).sort(),['active.json',JSON.parse(before).environmentId].sort());
});

test('owned subprocess cancellation reaps the child and ignores inherited Python/pip configuration',async()=>{
  const controller=new AbortController();let child;
  const running=runOwnedProcess(process.execPath,['-e','setInterval(()=>{},1000)'],{signal:controller.signal,onChild:value=>{child=value}});
  controller.abort();await assert.rejects(running,error=>error.code==='cancelled');
  assert.ok(child.signalCode !== null || child.exitCode !== null);
  assert.throws(()=>process.kill(child.pid,0),error=>error.code==='ESRCH');
  const result=await runOwnedProcess(process.execPath,['-e','console.log(JSON.stringify(process.env))'],{
    env:{...process.env,PYTHONPATH:'/private-source',PIP_EXTRA_INDEX_URL:'https://private.invalid',PIP_CONFIG_FILE:'/private-pip.conf',ELECTRON_RUN_AS_NODE:'1'}});
  const env=JSON.parse(result.stdout);assert.equal(env.PYTHONPATH,undefined);assert.equal(env.PIP_EXTRA_INDEX_URL,undefined);
  assert.equal(env.ELECTRON_RUN_AS_NODE,undefined);assert.equal(env.PYTHON_MANAGER_AUTOMATIC_INSTALL,'false');assert.equal(env.PYLAUNCHER_ALLOW_INSTALL,undefined);assert.equal(env.PIP_CONFIG_FILE,process.platform==='win32'?'nul':'/dev/null');
});

test('host candidates omit relative PATH entries and include standard macOS and Windows launcher locations',()=>{
  const mac=pythonCandidates('darwin',{PATH:'.:relative:/custom/bin'});
  assert.ok(mac.some(item=>item.executable==='/custom/bin/python3'));
  assert.ok(mac.some(item=>item.executable==='/Library/Frameworks/Python.framework/Versions/3.12/bin/python3'));
  assert.ok(mac.every(item=>path.isAbsolute(item.executable)));
  const win=pythonCandidates('win32',{PATH:'.;C:\\Tools',SystemRoot:'C:\\Windows',LOCALAPPDATA:'C:\\Users\\Test\\AppData\\Local'});
  assert.deepEqual(win.find(item=>item.executable==='C:\\Windows\\py.exe').args,['-0p']);
  assert.ok(win.every(item=>path.win32.isAbsolute(item.executable)));
});


test('cancel waits for an owned descendant that ignores TERM after its leader and stdio exit', {skip:process.platform==='win32',timeout:12000}, async t=>{
  const root=await fs.mkdtemp(path.join(os.tmpdir(),'briefloop-process-group-test-'));
  t.after(()=>fs.rm(root,{recursive:true,force:true}));
  const marker=path.join(root,'ready');
  const descendantCode=`process.on('SIGTERM',()=>{});require('node:fs').writeFileSync(${JSON.stringify(marker)},String(process.pid));setInterval(()=>{},1000);`;
  const parentCode=`const fs=require('node:fs');const c=require('node:child_process').spawn(process.execPath,['-e',${JSON.stringify(descendantCode)}],{stdio:'ignore'});const timer=setInterval(()=>{if(fs.existsSync(${JSON.stringify(marker)})){console.log(c.pid);clearInterval(timer)}},10);setInterval(()=>{},1000);`;
  const controller=new AbortController();let leader,ready;
  const started=new Promise(resolve=>{ready=resolve});
  const running=runOwnedProcess(process.execPath,['-e',parentCode],{signal:controller.signal,onChild:child=>{leader=child;child.stdout.once('data',data=>ready(Number(String(data).trim())))}});
  const descendant=await started;
  t.after(()=>{try{process.kill(descendant,'SIGKILL')}catch{}});
  controller.abort();await assert.rejects(running,error=>error.code==='cancelled');
  for(const pid of [leader.pid,descendant])assert.throws(()=>process.kill(pid,0),error=>error.code==='ESRCH');
});
