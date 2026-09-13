import fs from 'node:fs';
import vm from 'node:vm';
import assert from 'node:assert/strict';
const source=fs.readFileSync(new URL('../frontend/app.js',import.meta.url),'utf8');
const code=source.slice(source.indexOf('// App updates: fixed desktop capabilities'),source.indexOf('// End App updates.'));
const html=fs.readFileSync(new URL('../src/briefloop/static/index.html',import.meta.url),'utf8');
const version=JSON.parse(fs.readFileSync(new URL('../desktop/electron/package.json',import.meta.url))).version;
assert.ok(html.includes(`data-web-version="${version}"`));
assert.match(html,/data-settings-view="updates"/);
function fixture(desktop){
 const elements=new Map(),notices=[];
 const el=id=>{if(!elements.has(id))elements.set(id,{hidden:false,disabled:false,textContent:'',dataset:{webVersion:version}});return elements.get(id)};
 const ctx=vm.createContext({$:el,window:{briefloopDesktop:desktop},notice:(...args)=>notices.push(args)});
 vm.runInContext(code,ctx);
 return {el,ctx,notices,run:expression=>vm.runInContext(expression,ctx)};
}
const browser=fixture();await browser.run('refreshAppUpdates()');
assert.equal(browser.el('app-update-controls').hidden,true);
assert.equal(browser.el('app-update-version').textContent,`网页客户端 v${version}`);
assert.match(browser.el('app-update-guidance').textContent,/浏览器不能安装/);
let changed,calls=[];
let dto={currentAppVersion:'0.17.0',source:'local-test',state:'available',releaseVersion:'0.20.0',installMode:'dmg',notes:'<img src=x onerror=alert(1)>',progress:null,error:null};
const desktop=fixture({updateStatus:async()=>dto,onUpdateStatus:fn=>{changed=fn},
 checkForUpdates:async()=>{calls.push('check');return dto},downloadUpdate:async()=>{calls.push('download');return dto},
 installUpdate:async()=>{calls.push('install');return {cancelled:true}}});
await desktop.run('refreshAppUpdates()');
assert.equal(desktop.el('app-update-version').textContent,'当前 App v0.17.0');
assert.match(desktop.el('app-update-source').textContent,/本地测试/);
assert.equal(desktop.el('app-update-notes').textContent,dto.notes);
assert.equal(desktop.el('app-update-download').hidden,false);
changed({...dto,state:'downloading',progress:{percent:42,transferred:42,total:100}});
assert.equal(desktop.el('app-update-check').disabled,true);
assert.equal(desktop.el('app-update-progress').value,42);
dto={...dto,state:'downloaded'};changed(dto);
assert.equal(desktop.el('app-update-install').hidden,false);
await desktop.el('app-update-install').onclick();
assert.deepEqual(calls,['install']);assert.match(desktop.notices[0][0],/已保留/);
dto={...dto,state:'error',retryable:true,error:{code:'open_failed',message:'无法打开 DMG'}};changed(dto);
await desktop.el('app-update-retry').onclick();assert.deepEqual(calls,['install','install']);
assert.equal(desktop.el('app-update-error').textContent,'无法打开 DMG');
console.log('PASS: browser fallback, actual App version, local-test label, text-only notes, progress, install cancellation and retry');
