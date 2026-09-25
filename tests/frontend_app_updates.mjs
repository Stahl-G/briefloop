import fs from 'node:fs';
import vm from 'node:vm';
import assert from 'node:assert/strict';
import {section} from './source_section.mjs';
const source=fs.readFileSync(new URL('../frontend/app.js',import.meta.url),'utf8');
const code=section(source,'// App updates: fixed desktop capabilities','// End App updates.','frontend/app.js');
const html=fs.readFileSync(new URL('../src/briefloop/static/index.html',import.meta.url),'utf8');
const version=JSON.parse(fs.readFileSync(new URL('../desktop/electron/package.json',import.meta.url))).version;
assert.ok(html.includes(`data-web-version="${version}"`));
assert.match(html,/data-settings-view="updates"/);
function fixture(desktop){
 const elements=new Map(),notices=[];
 const el=id=>{if(!elements.has(id))elements.set(id,{hidden:false,disabled:false,textContent:'',dataset:{webVersion:version}});return elements.get(id)};
 const info={version,installation:'source',build:'abc123',guidance:'当前运行开发源码',update_command:null};
 const ctx=vm.createContext({$:el,window:{briefloopDesktop:desktop},api:async(name)=>name==='software-version'?info:{...info,state:'ahead',releaseVersion:'0.18.0'},notice:(...args)=>notices.push(args)});
 vm.runInContext(code,ctx);
 return {el,ctx,notices,run:expression=>vm.runInContext(expression,ctx)};
}
const browser=fixture();await browser.run('refreshAppUpdates()');
assert.equal(browser.el('app-update-controls').hidden,false);
assert.match(browser.el('app-update-version').textContent,new RegExp(`BriefLoop v${version}`));
assert.match(browser.el('app-update-guidance').textContent,/开发源码/);
await browser.el('app-update-check').onclick();
assert.match(browser.el('app-update-status').textContent,/高于 PyPI/);
assert.equal(browser.el('app-update-download').hidden,true);
let changed,calls=[];
let dto={currentAppVersion:'0.17.0',source:'local-test',state:'available',releaseVersion:'0.20.0',installMode:'dmg',notes:'<img src=x onerror=alert(1)>',progress:null,error:null};
const desktop=fixture({updateStatus:async()=>dto,onUpdateStatus:fn=>{changed=fn},
 checkForUpdates:async()=>{calls.push('check');return dto},downloadUpdate:async()=>{calls.push('download');return dto},
 installUpdate:async()=>{calls.push('install');return {cancelled:true}}});
await desktop.run('refreshAppUpdates()');
assert.equal(desktop.el('app-update-version').textContent,`BriefLoop v${version} · 构建 abc123 · 桌面 App v0.17.0`);
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

dto={...dto,currentAppVersion:'0.19.0',releaseVersion:'0.19.0',state:'available',error:null,reinstall:true};changed(dto);
assert.equal(desktop.el('app-update-status').textContent,'重新安装当前 App v0.19.0');
assert.equal(desktop.el('app-update-download').textContent,'下载当前版本安装包');
assert.doesNotMatch(desktop.el('app-update-status').textContent,/发现/);
assert.match(desktop.el('app-update-guidance').textContent,/重新安装当前版本/);
assert.doesNotMatch(desktop.el('app-update-guidance').textContent,/新版本/);
changed({...dto,state:'downloaded'});
assert.match(desktop.el('app-update-status').textContent,/重新安装当前 App v0.19.0/);
assert.match(desktop.el('app-update-source').textContent,/本地测试/);
console.log('PASS: local same-version reinstall stays explicit across update states');

// Persisted operation takes precedence over the last click in this renderer.
for(const [operation,label] of [['check','更新检查失败（当前安装不受影响）'],['download','更新包下载未完成'],['install','更新安装未完成']]){
 changed({...dto,source:'github',state:'error',reinstall:false,releaseVersion:'0.20.0',retryable:true,error:{operation,code:'http_403',message:'HTTP 403 · 请在本机时间 20:52 后重试'}});
 assert.ok(desktop.el('app-update-status').textContent.startsWith(label));
 assert.match(desktop.el('app-update-error').textContent,/HTTP 403.*20:52/);
 if(operation==='check')assert.equal(desktop.el('app-update-status').textContent,label);
 const before=calls.length;await desktop.el('app-update-retry').onclick();
 assert.equal(calls.length,before+1);assert.equal(calls.at(-1),operation==='install'?'download':operation);
}
console.log('PASS: checking errors do not imply installation failure or repeat an old release target');
