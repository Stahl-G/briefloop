import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';
import {beginPanel,updatePanel} from '../frontend/report-panels.js';

test('refresh keeps rendered findings and expanded state until data changes',()=>{
 let writes=0,html='';
 const box={dataset:{},expanded:false,get innerHTML(){return html},set innerHTML(value){html=value;writes++;this.expanded=false}};
 beginPanel(box,'v1');updatePanel(box,'<details>Finding</details>');box.expanded=true;
 const before=writes;
 beginPanel(box,'v1','Loading');updatePanel(box,'<details>Finding</details>');
 assert.equal(writes,before);assert.equal(box.expanded,true);
 updatePanel(box,'New result');assert.equal(box.innerHTML,'New result');
 beginPanel(box,'v2','Loading');assert.equal(box.innerHTML,'Loading');
});

test('fact-check polling keeps content in flight and rejects a stale version response',async()=>{
 const source=fs.readFileSync(new URL('../frontend/app.js',import.meta.url),'utf8');
 const code=source.slice(source.indexOf('async function renderFactChecks()'),source.indexOf('\nfunction assessment()'));
 let html='',writes=0;
 const box={dataset:{},isConnected:true,querySelectorAll:()=>[],get innerHTML(){return html},set innerHTML(v){html=v;writes++}};
 const pending=[];
 const ctx=vm.createContext({$:()=>box,current:{id:'v1'},beginPanel,updatePanel,api:()=>new Promise(resolve=>pending.push(resolve)),factCheckHTML:data=>data.html,bindSources:()=>{}});
 vm.runInContext(code,ctx);
 const refresh=()=>vm.runInContext('renderFactChecks()',ctx);
 let task=refresh();pending.shift()({html:'Long findings'});await task;
 const before=writes;
 task=refresh();assert.equal(html,'Long findings');assert.equal(writes,before);
 pending.shift()({html:'Long findings'});await task;assert.equal(writes,before);
 const old=refresh();ctx.current={id:'v2'};const latest=refresh();
 pending.pop()({html:'Version two'});await latest;
 pending.shift()({html:'Stale version one'});await old;
 assert.equal(html,'Version two');
});
