'use strict';
const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const main = fs.readFileSync(require.resolve('../main.cjs'), 'utf8');
const stop = main.slice(main.indexOf('async function stopCurrent()'), main.indexOf('async function openWorkspace('));
const install = main.slice(main.indexOf('async function installAppUpdate()'), main.indexOf('if (!app.requestSingleInstanceLock())'));
function gate({busy=false,confirm=1,saveError=false,stopError=false,installError=false,mode='dmg',status='downloaded'}={}) {
  const calls=[];
  const service={child:{},directory:'/synthetic',info:{url:'http://127.0.0.1:12345'},
    status:async()=>{calls.push('status');return {busy}},
    stop:async value=>{calls.push(['stop',value.cancelBusy]);if(stopError)throw Error('stop failed');service.child=null},
    start:async(directory,options)=>{calls.push(['restart',options.port]);service.child={}}};
  const context=vm.createContext({URL,quitting:false,closePending:false,switching:false,menuSave:null,expectedExit:false,service,
    prepareClose:async()=>{calls.push('save');if(saveError)throw Error('save failed')},
    dialog:{showMessageBox:async()=>{calls.push('busy-dialog');return {response:confirm}}},
    updates:{status:()=>({state:status,error:status==='error'?{code:'open_failed'}:null}),installReady:async()=>{
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
    vm.runInNewContext(init,{app:{isPackaged:packaged},shell:{},process:{env:{BRIEFLOOP_UPDATE_TEST_FEED:'http://127.0.0.1:12345/release'}},createUpdater:value=>{config=value},window:null});
    assert.equal(config.testFeed,packaged?null:'http://127.0.0.1:12345/release');
  }
});
