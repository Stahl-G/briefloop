'use strict';
const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const main = fs.readFileSync(require.resolve('../main.cjs'), 'utf8');
const stop = main.slice(main.indexOf('async function stopCurrent()'), main.indexOf('async function openWorkspace('));
const install = main.slice(main.indexOf('function beforeAppQuit('), main.indexOf('if (!app.requestSingleInstanceLock())'));
function gate({busy=false,confirm=1,saveError=false,stopError=false,installError=false,mode='dmg',status='downloaded'}={}) {
  const calls=[];
  const service={child:{},directory:'/synthetic',info:{url:'http://127.0.0.1:12345'},
    status:async()=>{calls.push('status');return {busy}},
    stop:async value=>{calls.push(['stop',value.cancelBusy]);if(stopError)throw Error('stop failed');service.child=null},
    start:async(directory,options)=>{calls.push(['restart',options.port]);service.child={}}};
  const context=vm.createContext({URL,nativeInstall:null,nativeQuitPending:false,nativeQuitTransactions:[],quitting:false,closePending:false,switching:false,menuSave:null,expectedExit:false,service,
    prepareClose:async()=>{calls.push('save');if(saveError)throw Error('save failed')},
    dialog:{showMessageBox:async()=>{calls.push('busy-dialog');return {response:confirm}}},
    updates:{status:()=>({state:status,installMode:mode,error:status==='error'?{code:'open_failed'}:null}),installReady:async()=>{
      calls.push(['install',context.quitting]);if(installError)throw Error('open failed');return {mode,...(mode==='native'?{requested:true}:{})}}},
    window:{destroy:()=>calls.push('destroy')},app:{quit:()=>calls.push('quit')},resumeEditing:()=>calls.push('resume')});
  vm.runInContext(stop+install,context);
  return {calls,context,run:()=>vm.runInContext('installAppUpdate()',context)};
}
test('install gate retains the editor on unsaved changes, declined busy cancellation, or stop failure',async()=>{
  const save=gate({saveError:true});await assert.rejects(save.run(),/save failed/);assert.deepEqual(save.calls,['save','resume']);
  const busy=gate({busy:true,confirm:0});assert.equal((await busy.run()).cancelled,true);assert.deepEqual(busy.calls,['save','status','busy-dialog','resume']);
  const stop=gate({stopError:true});await assert.rejects(stop.run(),/stop failed/);
  assert.deepEqual(stop.calls,['save','status',['stop',false],'resume']);assert.equal(stop.context.quitting,false);
});
test('explicit busy cancellation precedes native install and bypasses only the already-completed quit gate',async()=>{
  const f=gate({busy:true,mode:'native'});await f.run();
  assert.deepEqual(f.calls,['save','status','busy-dialog',['stop',true],['install',true]]);
  assert.equal(f.context.quitting,true);assert.equal(f.context.closePending,false);
});
test('DMG installation exits only after opening; failed open restores its owned service for retry',async()=>{
  const success=gate();await success.run();assert.deepEqual(success.calls,['save','status',['stop',false],['install',true],'destroy','quit']);
  const failure=gate({installError:true});await assert.rejects(failure.run(),/open failed/);
  assert.deepEqual(failure.calls,['save','status',['stop',false],['install',true],['restart',12345],'resume']);
  assert.equal(failure.context.quitting,false);
  const retry=gate({status:'error'});await retry.run();assert.ok(retry.calls.includes('quit'));
});
test('update IPC checks window identity, ignores extra renderer arguments, and packaged apps ignore test feeds',async()=>{
  const handlers=new Map(),calls=[];
  const context=vm.createContext({ipcMain:{handle:(name,fn)=>handlers.set(name,fn)},trusted:event=>{if(event!=='trusted')throw Error('untrusted')},
    updates:{status:(...args)=>calls.push(['status',...args]),check:(...args)=>calls.push(['check',...args]),download:(...args)=>calls.push(['download',...args])},
    installAppUpdate:(...args)=>calls.push(['install',...args])});
  const lines=main.split('\n').filter(line=>line.includes("ipcMain.handle('updates:"));vm.runInContext(lines.join('\n'),context);
  assert.equal(handlers.size,4);
  for(const handler of handlers.values()){
    assert.throws(()=>handler('attacker','https://attacker.invalid','command'),/untrusted/);
    await handler('trusted','https://attacker.invalid','command');
  }
  assert.deepEqual(calls,[['status'],['check'],['download'],['install']]);
  const init=main.slice(main.indexOf('    updates = createUpdater('),main.indexOf('    window = new BrowserWindow('));
  for(const packaged of [true,false]){
    let config;
    vm.runInNewContext(init,{app:{isPackaged:packaged},shell:{},process:{env:{BRIEFLOOP_UPDATE_TEST_FEED:'http://127.0.0.1:12345/release'}},createUpdater:value=>{config=value},updateChanged:()=>{},window:null});
    assert.equal(config.testFeed,packaged?null:'http://127.0.0.1:12345/release');
  }
});

// Actual NsisUpdater/BaseUpdater and cache/event path; app, service, transport
// and spawn are isolated. Synthetic bytes are never executed as an installer.
async function actualNativeGate(t, failures = []) {
  const f = gate({mode:'native'}), path = require('node:path'), promises = require('node:fs/promises');
  const {EventEmitter} = require('node:events'), Module = require('node:module');
  const {createUpdater} = require('../updater.cjs');
  const directory = await promises.mkdtemp(path.join(require('node:os').tmpdir(), 'BriefLoop 中文 空格-'));
  const configPath = path.join(directory, 'synthetic-app-update.yml');
  await promises.writeFile(configPath, 'updaterCacheDirName: synthetic-cache\n');
  const bytes = Buffer.from('Synthetic NSIS fixture; never executed.');
  const entry = {url:`BriefLoop-Setup-0.20.0-${process.arch}.exe`, sha512:require('node:crypto').createHash('sha512').update(bytes).digest('base64')};
  const info = {version:'0.20.0', files:[entry]};
  const app = new EventEmitter(), electronAutoUpdater = new EventEmitter(), states = [], instances = [], queued = [];
  const originalLoad = Module._load;
  Module._load = function(request, parent, isMain) {
    return request === 'electron' ? {autoUpdater:electronAutoUpdater,shell:{openPath(){assert.fail('No OS launch allowed');}}}
      : originalLoad.call(this, request, parent, isMain);
  };
  t.after(async () => {Module._load = originalLoad; await promises.rm(directory, {recursive:true, force:true});});
  const {NsisUpdater} = require('electron-updater');
  app.quit = () => {
    const event = {prevented:false,preventDefault(){this.prevented=true;}};
    app.emit('before-quit',event); f.calls.push(['native-quit',event.prevented]);
  };
  const adapter = {version:'0.19.0',name:'Synthetic',isPackaged:true,appUpdateConfigPath:configPath,
    baseCachePath:directory,userDataPath:directory,whenReady:async()=>{},quit:()=>app.quit(),onQuit(){},relaunch(){assert.fail('No relaunch allowed');}};
  f.context.app = app; f.context.electronAutoUpdater = electronAutoUpdater;
  f.context.window = {isDestroyed:()=>false,webContents:{send:(_event,value)=>states.push(value)}};
  f.context.reportError = async error => {throw error;};
  f.context.requestQuit = () => f.calls.push('user-quit-gate');
  vm.runInContext(main.split('\n').filter(line=>line.includes("electronAutoUpdater.on('before-quit-for-update'")||line.includes("app.on('before-quit', beforeAppQuit)")).join('\n'),f.context);
  const nativeUpdaterFactory = () => {
    const failure = failures[instances.length], native = new NsisUpdater(null,adapter);
    native.logger = {info(){},warn(){},error(){},debug(){}};
    native.checkForUpdates = async () => ({updateInfo:info});
    native.downloadUpdate = () => native.executeDownload({fileExtension:'exe',
      fileInfo:{url:new URL(`https://synthetic.invalid/${entry.url}`),info:entry},
      downloadUpdateOptions:{updateInfoAndProvider:{info},requestHeaders:{}},
      task:async destination => promises.writeFile(destination,bytes)});
    native.spawnLog = (command,args) => {
      f.calls.push(['spawn',command,args]);
      const error = Object.assign(Error('private synthetic spawn failure'),{code:'SYNTHETIC_SPAWN_FAILURE'});
      if (failure === 'sync') throw error;
      return failure === 'async' ? Promise.reject(error) : Promise.resolve(true);
    };
    instances.push(native); return native;
  };
  f.context.updates = createUpdater({app:{getVersion:()=>adapter.version},shell:{},platform:'win32',nativeUpdaterFactory,
    changed:value=>f.context.updateChanged(value)});
  const install = async () => {
    const originalImmediate = global.setImmediate;
    // Preserve BaseUpdater's callback; control only delivery order to overlap
    // an old failed request and a new one without timing-based assertions.
    global.setImmediate = callback => {queued.push(callback); return {};};
    try {return await f.run();} finally {global.setImmediate = originalImmediate;}
  };
  await f.context.updates.check(); await f.context.updates.download();
  return {...f,install,queued,instances,states,file:path.join(directory,'synthetic-cache','pending',entry.url)};
}

test('actual NSIS asynchronous failure retries with a fresh instance and isolates old events and queued quit', async t => {
  const f = await actualNativeGate(t,['async']);
  await assert.rejects(f.install(),/重新下载/);
  assert.equal(f.context.quitting,false); assert.ok(f.context.service.child);
  assert.equal(f.queued.length,1); assert.equal(f.context.nativeQuitTransactions.length,1);
  assert.equal((await f.context.updates.download()).state,'downloaded');
  assert.equal(f.instances.length,2);
  const before = f.context.updates.status();
  f.instances[0].emit('error',Error('late private diagnostic'));
  f.instances[0].emit('download-progress',{percent:1});
  f.instances[0].emit('update-downloaded',{version:'99.0.0',downloadedFile:'untrusted'});
  assert.deepEqual(f.context.updates.status(),before);
  assert.equal((await f.install()).requested,true);
  assert.equal(f.calls.filter(value=>value[0]==='spawn').length,2);
  assert.equal(f.context.nativeQuitTransactions.length,2);
  f.queued.shift()();
  assert.deepEqual(f.calls.at(-1),['native-quit',true]);
  assert.equal(f.context.quitting,true); assert.equal(f.context.service.child,null);
  f.queued.shift()();
  assert.deepEqual(f.calls.at(-1),['native-quit',false]);
  assert.equal(f.context.nativeQuitTransactions.length,0);
  assert.doesNotMatch(JSON.stringify(f.states),/private|untrusted/);
  const spawn = f.calls.find(value=>value[0]==='spawn');
  assert.equal(spawn[1],f.file); assert.match(spawn[1],/中文 空格/);
});

test('actual NSIS synchronous failure and changed ready bytes restore service without queuing a quit', async t => {
  const f = await actualNativeGate(t,['sync']);
  await assert.rejects(f.install(),/重新下载/);
  assert.equal(f.queued.length,0); assert.equal(f.context.nativeQuitTransactions.length,0);
  assert.ok(f.context.service.child); assert.equal(f.context.quitting,false);
  assert.equal((await f.context.updates.download()).state,'downloaded');
  await require('node:fs/promises').writeFile(f.file,'Changed synthetic installer bytes; never executed.');
  await assert.rejects(f.install(),/发生变化/);
  assert.equal(f.context.updates.status().error.code,'hash_mismatch');
  assert.equal(f.calls.filter(value=>value[0]==='spawn').length,1);
  assert.equal(f.queued.length,0); assert.equal(f.context.nativeQuitTransactions.length,0);
  assert.ok(f.context.service.child); assert.equal(f.context.quitting,false);
  // The new helper revalidates the disk cache and replaces changed bytes using
  // the same expected metadata, not a new hash of the changed local file.
  assert.equal((await f.context.updates.download()).state,'downloaded');
  assert.equal(f.instances.length,3);
  assert.equal((await f.install()).requested,true);
  assert.equal(f.calls.filter(value=>value[0]==='spawn').length,2);
  assert.equal(f.context.nativeQuitTransactions.length,1);
  f.queued.shift()(); assert.deepEqual(f.calls.at(-1),['native-quit',false]);
});

test('native requested path permits its marked quit without repeating save or claiming installation completed', async () => {
  const {EventEmitter}=require('node:events');
  const f=gate({mode:'native'}),app=new EventEmitter(),electronAutoUpdater=new EventEmitter();
  f.context.app=app;f.context.electronAutoUpdater=electronAutoUpdater;
  f.context.requestQuit=()=>f.calls.push('user-quit-gate');
  vm.runInContext(main.split('\n').filter(line=>line.includes("electronAutoUpdater.on('before-quit-for-update'")||line.includes("app.on('before-quit', beforeAppQuit)")).join('\n'),f.context);
  await f.run();electronAutoUpdater.emit('before-quit-for-update');
  let prevented=false;app.emit('before-quit',{preventDefault(){prevented=true}});
  assert.equal(prevented,false);assert.equal(f.calls.filter(value=>value==='save').length,1);
  assert.ok(!f.calls.includes('user-quit-gate'));assert.equal(f.context.nativeInstall.failed,false);
});

test('an error racing the native request return keeps the gate pending until owned service recovery finishes', async () => {
  const f=gate({mode:'native'});
  let releaseRecovery,returned=false;
  f.context.service.start=async()=>{
    f.calls.push('restart-pending');
    await new Promise(resolve=>{releaseRecovery=resolve});
    f.context.service.child={};
  };
  f.context.window={isDestroyed:()=>false,webContents:{send(){}}};
  f.context.updates={status:()=>({state:'downloaded',installMode:'native'}),installReady:async()=>{
    queueMicrotask(()=>f.context.updateChanged({installMode:'native',state:'error'}));
    return {mode:'native',requested:true};
  }};
  const result=f.run().then(()=>{returned=true},error=>{returned=true;assert.match(error.message,/未完成/)});
  // Advance through the deterministic microtasks; no timeout asserts installation success.
  for(let step=0;step<20&&!releaseRecovery;step++)await Promise.resolve();
  assert.equal(typeof releaseRecovery,'function');
  assert.equal(returned,false);assert.equal(f.context.closePending,true);assert.equal(f.context.quitting,false);
  releaseRecovery();await result;
  assert.equal(f.context.closePending,false);assert.ok(f.context.service.child);
  assert.equal(f.calls.filter(value=>value==='restart-pending').length,1);
});

test('quit during Python detection waits for cancellation and rejects new environment operations', async () => {
  const calls=[],handlers=new Map();
  let finishCancellation;
  const context=vm.createContext({quitting:false,closePending:false,switching:false,nativeInstall:null,menuSave:null,service:null,
    environment:{status:()=>({state:'checking',phase:'detect-python'}),
      cancel:()=>{calls.push('cancel');return new Promise(resolve=>{finishCancellation=()=>{calls.push('cancelled');resolve()}})},
      inspect:()=>calls.push('inspect'),prepare:()=>calls.push('prepare')},
    ipcMain:{handle:(name,callback)=>handlers.set(name,callback)},trusted:()=>{},
    stopCurrent:async()=>{calls.push('save-and-stop');return true},
    window:{destroy:()=>calls.push('destroy')},app:{quit:()=>calls.push('quit')},
    resumeEditing:()=>calls.push('resume'),reportError:error=>{throw error},
    dialog:{showMessageBox:()=>{throw Error('Python detection should not require installation confirmation')}}});
  const quit=main.slice(main.indexOf('async function requestQuit()'),main.indexOf('function beforeAppQuit('));
  const operation=main.slice(main.indexOf('function environmentOperation('),main.indexOf('function resumeEditing('));
  const ipc=main.split('\n').filter(line=>/ipcMain.handle\('environment:(inspect|prepare)'/.test(line)).join('\n');
  vm.runInContext(operation+quit+ipc,context);
  const quitting=vm.runInContext('requestQuit()',context);
  assert.equal(context.closePending,true);assert.deepEqual(calls,['cancel']);
  assert.equal(handlers.size,2);
  for(const handler of handlers.values())assert.throws(()=>handler({}),/等待当前操作完成/);
  assert.deepEqual(calls,['cancel']);
  finishCancellation();await quitting;
  assert.deepEqual(calls,['cancel','cancelled','save-and-stop','destroy','quit']);
  assert.equal(context.quitting,true);
});

test('ZIP handoff quits only after saved work and successful Finder opening; failure restores service',async()=>{
  const success=gate({mode:'zip'});await success.run();
  assert.deepEqual(success.calls,['save','status',['stop',false],['install',true],'destroy','quit']);
  const failure=gate({mode:'zip',installError:true});await assert.rejects(failure.run(),/open failed/);
  assert.deepEqual(failure.calls,['save','status',['stop',false],['install',true],['restart',12345],'resume']);
});
