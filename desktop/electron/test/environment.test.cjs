'use strict';
const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs/promises');
const os = require('node:os');
const path = require('node:path');
const {createHash} = require('node:crypto');
const {spawn} = require('node:child_process');
const vm = require('node:vm');
const {createEnvironment, runOwnedProcess, pythonCandidates} = require('../environment.cjs');

async function fixture(t, platform='darwin') {
  const root=await fs.mkdtemp(path.join(os.tmpdir(),'briefloop-environment-test-'));
  t.after(()=>fs.rm(root,{recursive:true,force:true}));
  const payloadPath=path.join(root,'payload'),host=path.join(root,'host-python');
  await fs.mkdir(payloadPath);await fs.writeFile(host,'Synthetic host marker',{mode:0o700});
  let version='0.19.0',failInstall=false,waitInstall=false,enteredInstall,lockedPackagesMatch=true;
  const versions=new Map(),calls=[],changes=[];
  async function payload(next, dependencyVersion='1.0') {
    version=next;const wheel=`briefloop-${version}-py3-none-any.whl`,bytes=Buffer.from('Synthetic wheel fixture '+version);
    const lock=Buffer.from('synthetic-dependency=='+dependencyVersion+' --hash=sha256:'+'0'.repeat(64)+'\n');
    await fs.writeFile(path.join(payloadPath,wheel),bytes);await fs.writeFile(path.join(payloadPath,'requirements.txt'),lock);
    await fs.writeFile(path.join(payloadPath,'manifest.json'),JSON.stringify({version,wheel,sha256:createHash('sha256').update(bytes).digest('hex'),
      requirements:'requirements.txt',requirements_sha256:createHash('sha256').update(lock).digest('hex')}));
  }
  await payload(version);
  const runProcess=async(executable,args,options)=>{
    calls.push({executable,args,options});
    if(args.includes('-c')&&args.some(value=>value.includes('list(sys.version_info')))return {stdout:JSON.stringify({version:[3,12,5],executable:host})};
    if(args.includes('-c')&&args.some(value=>value.includes('checked = 0')))return {stdout:JSON.stringify({matches:lockedPackagesMatch})};
    if(args.includes('-c')&&args.some(value=>value.includes('def copy_dependencies')))return {stdout:JSON.stringify({reused:['synthetic-dependency'],skipped:[]})};
    if(executable==='/bin/cp'){
      const [source,target]=args.slice(-2);await fs.cp(source,target,{recursive:true});versions.set(target,versions.get(source));return {stdout:''};
    }
    if(args[2]==='venv'){
      const target=args.at(-1);versions.set(target,version);await fs.mkdir(path.join(target,platform==='win32'?'Scripts':'bin'));
      await fs.writeFile(path.join(target,platform==='win32'?'Scripts':'bin',platform==='win32'?'python.exe':'python3'),'Synthetic venv marker',{mode:0o700});return {stdout:''};
    }
    if(args.includes('install')){
      enteredInstall?.();
      if(waitInstall)await new Promise((resolve,reject)=>options.signal.addEventListener('abort',()=>reject(Error('cancelled fake install')),{once:true}));
      if(failInstall)throw Error('https://private.invalid/?token=do-not-expose');
      // Installing the wheel is what gives the environment its BriefLoop version.
      if(args.at(-1).endsWith('.whl'))versions.set(path.dirname(path.dirname(executable)),version);
      return {stdout:''};
    }
    if(args.includes('-c')){await fs.access(executable);return {stdout:JSON.stringify({version:versions.get(args.at(-1))})};}
    assert.equal(args.at(-1),'check');return {stdout:'No broken requirements found.'};
  };
  const config={app:{getPath:()=>root},payloadPath,platform,arch:platform==='win32'?'x64':'arm64',candidates:[host],runProcess,changed:value=>changes.push(value)};
  return {root,host,payloadPath,payload,config,calls,changes,environment:createEnvironment(config),
    invalidateDependencies:()=>{lockedPackagesMatch=false},
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
  const runtime=f.environment.runtime();assert.equal(runtime.node,process.execPath);assert.equal(runtime.nodeIsElectron,true);assert.equal(runtime.basePython,f.host);
  const activeFile=path.join(f.root,'environments','active.json'),before=await fs.readFile(activeFile,'utf8');
  const record=JSON.parse(before),target=path.join(f.root,'environments',record.environmentId);
  assert.equal(runtime.python,path.join(target,'bin','python3'));
  const [locked,wheel]=f.calls.filter(call=>call.args.includes('install'));
  assert.deepEqual(locked.args.slice(0,13),['-I','-m','pip','--isolated','--disable-pip-version-check','--no-input','install','--require-hashes','--only-binary=:all:','--index-url','https://pypi.org/simple','-r',path.join(f.payloadPath,'requirements.txt')]);
  assert.deepEqual(wheel.args.slice(6),['install','--no-deps','--no-index','--force-reinstall',path.join(f.payloadPath,'briefloop-0.19.0-py3-none-any.whl')]);
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

test('warm startup uses one lightweight process; explicit inspection still checks imports and dependencies',async t=>{
  const f=await fixture(t);await f.environment.prepare();f.calls.length=0;
  const warm=createEnvironment(f.config);assert.equal((await warm.startup()).state,'ready');
  assert.equal(f.calls.length,1);assert.match(f.calls[0].args[2],/find_spec/);
  assert.equal(warm.runtime().python,f.environment.runtime().python);
  f.calls.length=0;assert.equal((await warm.inspect()).state,'ready');
  assert.ok(f.calls.some(c=>c.args.some(a=>a.includes('importlib.import_module'))));
  assert.ok(f.calls.some(c=>c.args.at(-1)==='check'));
  await fs.unlink(warm.runtime().python);
  assert.equal((await createEnvironment(f.config).startup()).state,'needs-setup');
  f.calls.length=0;await f.payload('0.20.0');
  const updated=createEnvironment(f.config),seen=[];f.changes.length=0;
  const result=await updated.startup();
  assert.equal(result.state,'ready');assert.equal(result.reason,'update');assert.equal(result.previousVersion,'0.19.0');assert.equal(result.version,'0.20.0');
  assert.ok(f.calls.some(c=>c.args.includes('install')));assert.ok(!f.calls.some(c=>c.args.some(a=>a.includes('find_spec'))));
  assert.ok(f.changes.some(value=>value.state==='installing'&&value.reason==='update'));
});

test('first install and repair wait for the user; updates preserve environments potentially used by CLI tasks',async t=>{
  const f=await fixture(t),first=await createEnvironment(f.config).startup();
  assert.equal(first.state,'needs-setup');assert.equal(first.reason,'first-install');assert.equal(first.previousVersion,null);
  assert.ok(!f.calls.some(c=>c.args.includes('install')));
  await f.environment.prepare();
  const directory=path.join(f.root,'environments'),stale=path.join(directory,'00000000-0000-4000-8000-000000000000');
  await fs.mkdir(stale);await fs.writeFile(path.join(directory,'keep-me.txt'),'not an environment');
  const one=JSON.parse(await fs.readFile(path.join(directory,'active.json'),'utf8')).environmentId;
  f.calls.length=0;await f.payload('0.20.0');assert.equal((await createEnvironment(f.config).startup()).state,'ready');
  const two=JSON.parse(await fs.readFile(path.join(directory,'active.json'),'utf8')).environmentId;
  // A macOS update on the same base Python starts from an APFS clone, not a new venv.
  const clone=f.calls.find(c=>c.executable==='/bin/cp');
  assert.deepEqual(clone.args,['-c','-R',path.join(directory,one),path.join(directory,two)]);
  assert.ok(!f.calls.some(c=>c.args[2]==='venv'));
  assert.equal(JSON.parse(await fs.readFile(path.join(directory,'active.json'),'utf8')).cloned,true);
  assert.deepEqual((await fs.readdir(directory)).sort(),['active.json','keep-me.txt',path.basename(stale),one,two].sort());
  await fs.unlink(path.join(directory,two,'bin','python3'));f.calls.length=0;
  const repair=await createEnvironment(f.config).startup();
  assert.equal(repair.state,'needs-setup');assert.equal(repair.reason,'repair');
  assert.ok(!f.calls.some(c=>c.args.includes('install')));
});

test('startup keeps payload verification and falls back on missing packages without losing cleanup failures',async t=>{
  const f=await fixture(t);await f.environment.prepare();
  const broken=createEnvironment({...f.config,runProcess:async(executable,args,options)=>{
    if(args.some(a=>a.includes('find_spec')||a.includes('importlib.import_module')))throw Object.assign(Error('missing package'),{code:'process_failed'});
    return f.config.runProcess(executable,args,options);
  }});
  assert.equal((await broken.startup()).state,'needs-setup');assert.throws(()=>broken.runtime(),/尚未验证/);
  const stuck=createEnvironment({...f.config,runProcess:async()=>{throw Object.assign(Error('cleanup uncertain'),{code:'cleanup_failed'})}});
  assert.equal((await stuck.startup()).error.code,'cleanup_failed');assert.throws(()=>stuck.runtime(),/尚未验证/);
  await fs.appendFile(path.join(f.payloadPath,'briefloop-0.19.0-py3-none-any.whl'),'tampered');
  f.calls.length=0;assert.equal((await createEnvironment(f.config).startup()).error.code,'payload_hash');
  assert.equal(f.calls.length,0);
});

test('owned subprocess cancellation reaps the child and ignores inherited Python/pip configuration',async()=>{
  const controller=new AbortController();let child;
  const running=runOwnedProcess(process.execPath,['-e','setInterval(()=>{},1000)'],{signal:controller.signal,onChild:value=>{child=value}});
  controller.abort();await assert.rejects(running,error=>error.code==='cancelled');
  assert.ok(child.signalCode !== null || child.exitCode !== null);
  assert.throws(()=>process.kill(child.pid,0),error=>error.code==='ESRCH');
  const result=await runOwnedProcess(process.execPath,['-e','console.log(JSON.stringify(process.env))'],{
    env:{...process.env,PYTHONPATH:'/private-source',PIP_EXTRA_INDEX_URL:'https://private.invalid',PIP_CONFIG_FILE:'/private-pip.conf',ELECTRON_RUN_AS_NODE:'1',__PYVENV_LAUNCHER__:'/untrusted-venv'}});
  const env=JSON.parse(result.stdout);assert.equal(env.PYTHONPATH,undefined);assert.equal(env.PIP_EXTRA_INDEX_URL,undefined);
  assert.equal(env.ELECTRON_RUN_AS_NODE,undefined);assert.equal(env.__PYVENV_LAUNCHER__,undefined);assert.equal(env.PYTHON_MANAGER_AUTOMATIC_INSTALL,'false');assert.equal(env.PYLAUNCHER_ALLOW_INSTALL,undefined);assert.equal(env.PIP_CONFIG_FILE,process.platform==='win32'?'nul':'/dev/null');
});

test('host candidates omit relative PATH entries and include standard macOS and Windows launcher locations',()=>{
  const mac=pythonCandidates('darwin',{PATH:'.:relative:/custom/bin'});
  assert.ok(mac.some(item=>item.executable==='/custom/bin/python3'));
  assert.ok(mac.some(item=>item.executable==='/Library/Frameworks/Python.framework/Versions/3.12/bin/python3'));
  assert.ok(mac.every(item=>path.posix.isAbsolute(item.executable)));
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

test('incomplete cleanup during host probe or existing-environment validation blocks fallback and new setup even on cancellation',async t=>{
  for(const stage of ['probe','validate']){
    const f=await fixture(t);
    if(stage==='validate')await f.environment.prepare();
    const second=path.join(f.root,'second-python');await fs.writeFile(second,'Synthetic second host',{mode:0o700});
    const calls=[];let entered,cleaned=false;
    const reached=new Promise(resolve=>{entered=resolve});
    const error=Object.assign(Error('private cleanup diagnostic'),{code:'cleanup_failed',confirmCleanup:()=>cleaned});
    const environment=createEnvironment({...f.config,candidates:[f.host,second],runProcess:async(executable,args,options)=>{
      calls.push({executable,args});
      const probe=args.some(value=>value.includes('sys.version_info'));
      if(stage==='probe'?probe:args.some(value=>value.includes('importlib.import_module'))){
        entered();await new Promise((resolve,reject)=>options.signal.addEventListener('abort',()=>reject(error),{once:true}));
      }
      return f.config.runProcess(executable,args,options);
    }});
    const preparing=environment.prepare();await reached;
    await assert.rejects(environment.cancel(),/尚未确认退出/);
    assert.equal((await preparing).error.code,'cleanup_failed');
    assert.equal(environment.status().state,'error');assert.throws(()=>environment.runtime(),/尚未验证/);
    assert.ok(!calls.some(call=>call.executable===second||call.args[2]==='venv'));
    const count=calls.length;
    assert.equal((await environment.prepare()).error.code,'cleanup_failed');
    assert.equal((await environment.inspect()).error.code,'cleanup_failed');assert.equal(calls.length,count);
    assert.doesNotMatch(JSON.stringify(environment.status()),/private cleanup diagnostic/);
    await assert.rejects(environment.cancel(),/尚未确认退出/);
    cleaned=true;assert.equal((await environment.cancel()).error.code,'cancelled');
  }
});

test('Windows failed Python candidate safely falls through to a real installation and preserves Unicode argv', {skip:process.platform!=='win32',timeout:20000}, async t=>{
  const f=await fixture(t),calls=[];
  let python;
  for(const candidate of pythonCandidates('win32')){
    if(path.basename(candidate.executable).toLowerCase()!=='python.exe')continue;
    try{await fs.access(candidate.executable);python=candidate.executable;break}catch{}
  }
  assert.ok(python,'Native test requires an installed Python 3.11+');
  const environment=createEnvironment({...f.config,platform:'win32',arch:'x64',candidates:[process.execPath,python],runProcess:async(executable,args,options)=>{
    try{const result=await runOwnedProcess(executable,args,options);calls.push({executable,success:true});return result}
    catch(error){calls.push({executable,code:error.code});throw error}
  }});
  assert.equal((await environment.inspect()).state,'needs-setup');
  assert.equal(calls[0].code,'process_failed');assert.equal(calls.at(-1).success,true);
  const args=['中文 空格','"quoted"',String.raw`C:\中文 空格\trailing`+'\\'];
  const result=await runOwnedProcess(process.execPath,['-e','console.log(JSON.stringify(process.argv.slice(1)))',...args]);
  assert.deepEqual(JSON.parse(result.stdout),args);
  const unavailable=createEnvironment({...f.config,platform:'win32',candidates:[python],runProcess:runOwnedProcess,
    env:{...process.env,SystemRoot:f.root,SYSTEMROOT:f.root}});
  assert.equal((await unavailable.inspect()).error.code,'supervisor_unavailable');
});

test('Windows Job cleanup owns detached grandchildren through cancellation, leader failure, and supervisor loss', {skip:process.platform!=='win32',timeout:25000}, async t=>{
  const f=await fixture(t);
  const alive=pid=>{try{process.kill(pid,0);return true}catch(error){if(error.code==='ESRCH')return false;throw error}};
  const unrelated=spawn(process.execPath,['-e','setTimeout(()=>{},20000)'],{stdio:'ignore',windowsHide:true});
  t.after(async()=>{if(unrelated.exitCode===null&&unrelated.signalCode===null){const closed=new Promise(resolve=>unrelated.once('close',resolve));unrelated.kill();await closed}});
  for(const mode of ['cancel','nonzero','stdin-eof','supervisor-kill']){
    const marker=path.join(f.root,mode+' 中文 ready.json');
    const grandchild=`require('node:fs').writeFileSync(${JSON.stringify(marker)},JSON.stringify([Number(process.argv[1]),Number(process.argv[2]),process.pid]));setTimeout(()=>{},20000);`;
    const middle=`require('node:child_process').spawn(process.execPath,['-e',${JSON.stringify(grandchild)},process.argv[1],String(process.pid)],{stdio:'ignore',detached:true,windowsHide:true}).unref();setTimeout(()=>{},20000);`;
    const leader=`require('node:child_process').spawn(process.execPath,['-e',${JSON.stringify(middle)},String(process.pid)],{stdio:'ignore',detached:true,windowsHide:true}).unref();${mode==='nonzero'?`const timer=setInterval(()=>{if(require('node:fs').existsSync(${JSON.stringify(marker)})){clearInterval(timer);process.exit(7)}},10);`:''}setTimeout(()=>{},20000);`;
    const controller=new AbortController();let supervisor;
    const running=runOwnedProcess(process.execPath,['-e',leader],{signal:controller.signal,onChild:child=>{supervisor=child}});
    const settled=running.then(()=>null,error=>error);
    let pids;const deadline=Date.now()+10000;
    while(Date.now()<deadline){try{pids=JSON.parse(await fs.readFile(marker,'utf8'));break}catch{await new Promise(resolve=>setTimeout(resolve,25))}}
    assert.equal(pids?.length,3,'Owned tree must actually start');
    if(mode==='cancel'){assert.ok(pids.every(alive));controller.abort()}
    if(mode==='stdin-eof')supervisor.stdin.end();
    if(mode==='supervisor-kill')supervisor.kill();
    const error=await settled;
    assert.equal(error?.code,mode==='cancel'?'cancelled':mode==='supervisor-kill'?'cleanup_failed':'process_failed');
    const exitDeadline=Date.now()+3000;
    while(pids.some(alive)&&Date.now()<exitDeadline)await new Promise(resolve=>setTimeout(resolve,20));
    assert.ok(pids.every(pid=>!alive(pid)));assert.equal(alive(unrelated.pid),true);
    if(mode==='supervisor-kill'){
      const environment=createEnvironment({...f.config,runProcess:async()=>{throw error}});
      assert.equal((await environment.inspect()).retryable,false);
      await assert.rejects(environment.cancel(),/重启 Windows/);
    }
    t.diagnostic(JSON.stringify({mode,ownedProcessCount:pids.length,allExited:true,unrelatedAlive:true}));
  }
});

test('welcome hides unavailable retries and preserves the cleanup guard after cancel IPC rejects',async()=>{
  const elements=new Map(),buttons=[{}];let changed;
  const getElementById=id=>{if(!elements.has(id))elements.set(id,{});return elements.get(id)};
  const state={state:'error',phase:'install-dependencies',retryable:false,error:{code:'cleanup_failed',message:'请重启 Windows 后重新打开 App。'}};
  const api={environment:{status:async()=>state,onChanged:callback=>{changed=callback},cancel:async()=>{throw Error(state.error.message)}},recentWorkspace:async()=>null};
  const context=vm.createContext({document:{getElementById,querySelectorAll:()=>buttons},window:{briefloopDesktop:api}});
  vm.runInContext(await fs.readFile(path.join(__dirname,'..','welcome.js'),'utf8'),context);
  await new Promise(resolve=>setImmediate(resolve));
  assert.equal(getElementById('prepare').hidden,true);assert.equal(getElementById('inspect').hidden,true);
  assert.equal(buttons[0].disabled,true);assert.match(getElementById('environment-status').textContent,/重启 Windows/);
  await getElementById('cancel-setup').onclick();
  assert.equal(getElementById('prepare').hidden,true);assert.equal(getElementById('inspect').hidden,true);
  changed({state:'error',retryable:true,error:{code:'process_failed',message:'普通安装失败，可以重试。'}});
  assert.equal(getElementById('prepare').hidden,false);assert.equal(getElementById('inspect').hidden,false);
});


test('unchanged locked dependencies are reused without running pip install requirements',async t=>{
  const f=await fixture(t);await f.environment.prepare();f.calls.length=0;
  await f.payload('0.20.0');
  const updated=createEnvironment(f.config);assert.equal((await updated.startup()).state,'ready');
  const installs=f.calls.filter(c=>c.args.includes('install'));
  assert.equal(installs.length,1);assert.ok(installs[0].args.at(-1).endsWith('.whl'));
  assert.ok(f.changes.some(x=>x.phase==='reuse-dependencies'));
  assert.ok(f.calls.some(c=>c.args.at(-1)==='check'));
});

test('changed lock and mismatched installed dependencies still use locked installer',async t=>{
  for(const changed of [true,false]){
    const f=await fixture(t);await f.environment.prepare();f.calls.length=0;
    await f.payload('0.20.0',changed?'2.0':'1.0');if(!changed)f.invalidateDependencies();
    assert.equal((await createEnvironment(f.config).startup()).state,'ready');
    assert.ok(f.calls.some(c=>c.args.includes('--require-hashes')));
    assert.ok(f.changes.some(x=>x.phase==='update-dependencies'));
  }
});

test('a changed lock cannot take the warm-start shortcut even for identical wheel',async t=>{
  const f=await fixture(t);await f.environment.prepare();f.calls.length=0;
  await f.payload('0.19.0','2.0');
  assert.equal((await createEnvironment(f.config).startup()).state,'ready');
  assert.ok(f.calls.some(c=>c.args.includes('--require-hashes')));
});


test('Windows creates fresh interpreter launchers and reuses verified dependencies before the app wheel',async t=>{
  const f=await fixture(t,'win32');await f.environment.prepare();f.calls.length=0;
  await f.payload('0.20.0');
  assert.equal((await createEnvironment(f.config).startup()).state,'ready');
  assert.equal(f.calls.filter(c=>c.args[2]==='venv').length,1);
  assert.equal(f.calls.filter(c=>c.executable==='/bin/cp').length,0);
  const installs=f.calls.filter(c=>c.args.includes('install'));
  assert.equal(installs.length,1);assert.ok(installs[0].args.at(-1).endsWith('.whl'));
  assert.ok(f.changes.some(v=>v.reusedDependencyCount===1));
});

test('cleanup ignores unrelated Python but preserves referenced environments and uncertain inventories',async t=>{
 for(const platform of ['darwin','win32']){
  const f=await fixture(t,platform);await f.environment.prepare();
  const directory=path.join(f.root,'environments'),activeFile=path.join(directory,'active.json');
  const previous=JSON.parse(await fs.readFile(activeFile,'utf8')).environmentId;
  const busy='10000000-0000-4000-8000-000000000000',idle='20000000-0000-4000-8000-000000000000';
  for(const id of [busy,idle])await fs.mkdir(path.join(directory,id));
  const commands=[platform==='win32'?'C:\\Other\\python.exe unrelated.py':'/usr/bin/python3 /tmp/unrelated.py',path.join(directory,busy,platform==='win32'?'Scripts/python.exe':'bin/python3')+' task.py'];
  let inventory='known';
  const config={...f.config,runProcess:async(executable,args,options)=>{
   if(executable==='/bin/ps'||args.includes('-Command')||args.some(a=>a.includes('Environment file usage unavailable'))){
    if(inventory==='failed')throw Error('Process inventory unavailable');
    const rows=inventory==='bare'?['python3 task.py']:commands;
    return {stdout:JSON.stringify(rows)};
   }
   return f.config.runProcess(executable,args,options);
  }};
  await f.payload('0.20.0');assert.equal((await createEnvironment(config).startup()).state,'ready');
  const current=JSON.parse(await fs.readFile(activeFile,'utf8')).environmentId;
  assert.deepEqual((await fs.readdir(directory)).sort(),['active.json',previous,current,busy].sort());
  await fs.mkdir(path.join(directory,idle));
  for(const [mode,version] of [['failed','0.21.0'],['bare','0.22.0']]){
   inventory=mode;await f.payload(version);assert.equal((await createEnvironment(config).startup()).state,'ready');
   assert.equal((await fs.stat(path.join(directory,idle))).isDirectory(),true);
  }
 }
});

test('macOS actual process/file scan keeps an occupied old environment and removes only idle fixtures', {skip:process.platform!=='darwin'}, async t=>{
 const f=await fixture(t);await f.environment.prepare();
 const directory=path.join(f.root,'environments');
 const busy='30000000-0000-4000-8000-000000000000',idle='40000000-0000-4000-8000-000000000000';
 for(const id of [busy,idle])await fs.mkdir(path.join(directory,id));
 // An open file matters even when its process command has no environment UUID.
 const held=await fs.open(path.join(directory,busy,'in-use'),'w');t.after(()=>held.close());
 const unrelated=spawn('/usr/bin/python3',['-c','import time; print("ready", flush=True); time.sleep(60)'],{stdio:['ignore','pipe','pipe']});
 t.after(async()=>{if(unrelated.exitCode===null){const closed=new Promise(resolve=>unrelated.once('close',resolve));unrelated.kill();await closed}});
 await new Promise((resolve,reject)=>{unrelated.stdout.once('data',resolve);unrelated.once('error',reject)});
 const config={...f.config,runProcess:(executable,args,options)=>args.some(a=>a.includes('Environment file usage unavailable'))
  ?runOwnedProcess('/usr/bin/python3',args,options):f.config.runProcess(executable,args,options)};
 await f.payload('0.20.0');assert.equal((await createEnvironment(config).startup()).state,'ready');
 await assert.rejects(fs.stat(path.join(directory,idle)),{code:'ENOENT'});
 assert.equal((await fs.stat(path.join(directory,busy))).isDirectory(),true);
 assert.equal(unrelated.exitCode,null);
});


test('declared prerelease versions can prepare and validate without using a stable version',async t=>{
  const f=await fixture(t);
  await f.payload('0.19.1.dev7');
  assert.equal((await f.environment.prepare()).state,'ready');
  await f.payload('0.19.1rc1');
  assert.equal((await createEnvironment(f.config).startup()).state,'ready');
  await f.payload('0.19.1-preview');
  assert.equal((await createEnvironment(f.config).inspect()).error.code,'invalid_payload');
});
