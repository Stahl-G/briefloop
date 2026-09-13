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
  const context=vm.createContext({URL,nativeInstall:null,nativeQuitPending:false,quitting:false,closePending:false,switching:false,menuSave:null,expectedExit:false,service,
    prepareClose:async()=>{calls.push('save');if(saveError)throw Error('save failed')},
    dialog:{showMessageBox:async()=>{calls.push('busy-dialog');return {response:confirm}}},
    updates:{status:()=>({state:status,installMode:mode,error:status==='error'?{code:'open_failed'}:null}),installReady:async()=>{
      calls.push(['install',context.quitting]);if(installError)throw Error('open failed');return {mode}}},
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

test('actual updater late native error restores the owned service and cancels its queued native quit', async () => {
  const {EventEmitter}=require('node:events');
  const {createUpdater}=require('../updater.cjs');
  const f=gate({mode:'native'}),errors=[],states=[];
  const app=new EventEmitter(),electronAutoUpdater=new EventEmitter();
  let resolveQuit;
  const attemptedQuit=new Promise(resolve=>{resolveQuit=resolve});
  app.quit=()=>{
    const event={prevented:false,preventDefault(){this.prevented=true}};
    app.emit('before-quit',event);f.calls.push(['native-quit',event.prevented]);resolveQuit();
  };
  f.context.app=app;f.context.electronAutoUpdater=electronAutoUpdater;
  f.context.window={isDestroyed:()=>false,webContents:{send:(_event,value)=>states.push(value)}};
  f.context.reportError=async error=>errors.push(error.message);
  f.context.requestQuit=()=>f.calls.push('user-quit-gate');
  vm.runInContext(main.split('\n').filter(line=>line.includes("electronAutoUpdater.on('before-quit-for-update'")||line.includes("app.on('before-quit', beforeAppQuit)")).join('\n'),f.context);
  class Native extends EventEmitter {
    setFeedURL(){}
    async checkForUpdates(){return {updateInfo:{version:'0.20.0'}}}
    async downloadUpdate(){return ['installer.exe']}
    quitAndInstall(){
      setImmediate(()=>{
        this.emit('error',Error('private installer diagnostic'));
        this.emit('error',Error('duplicate diagnostic'));
        // BaseUpdater queues app.quit independently of spawnLog().catch(dispatchError).
        setImmediate(()=>{electronAutoUpdater.emit('before-quit-for-update');app.quit()});
      });
    }
  }
  f.context.updates=createUpdater({app:{getVersion:()=> '0.19.0'},shell:{},platform:'win32',nativeUpdater:new Native(),
    changed:value=>f.context.updateChanged(value)});
  await f.context.updates.check();await f.context.updates.download();
  assert.equal((await f.run()).requested,true);
  assert.equal(f.context.quitting,true);assert.equal(f.context.service.child,null);
  await attemptedQuit;
  assert.equal(f.context.quitting,false);assert.equal(f.context.closePending,false);
  assert.ok(f.context.service.child);
  assert.equal(f.calls.filter(value=>Array.isArray(value)&&value[0]==='restart').length,1);
  assert.equal(f.calls.filter(value=>value==='resume').length,1);
  assert.deepEqual(f.calls.at(-1),['native-quit',true]);assert.ok(!f.calls.includes('user-quit-gate'));
  assert.equal(f.context.updates.status().state,'error');assert.deepEqual(errors,[]);
  assert.doesNotMatch(JSON.stringify(states),/private installer|duplicate diagnostic/);
  // Blocking the failed updater's quit must not disable a later ordinary user quit.
  app.quit();assert.equal(f.calls.at(-2),'user-quit-gate');
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
