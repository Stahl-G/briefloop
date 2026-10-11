import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';
import {homeUI} from '../frontend/home.js';
import {section} from './source_section.mjs';

const app=fs.readFileSync(new URL('../frontend/app.js',import.meta.url),'utf8');

test('the home task button opens the pending run and respects unsaved edits on repeat clicks',()=>{
 const button={dataset:{railOpenJob:'job'}},nodes=new Map();
 const $=id=>{if(!nodes.has(id))nodes.set(id,{hidden:false,dataset:{},classList:{toggle(){}},
  querySelectorAll:selector=>selector==='[data-rail-open-job]'?[button]:[]});return nodes.get(id)};
 const pages=[],state={jobs:[{id:'job',kind:'generate',status:'running',payload:'{"run_id":"run"}'}],briefs:[],runs:[{id:'run',requirements:'{"title":"正在生成的报告"}'}]};
 const context=vm.createContext({$,state,current:{id:'old'},pendingRun:null,dirty:false,saving:false,editor:null,
  openBrief:()=>false,parse:value=>JSON.parse(value||'{}'),notice(){},page:name=>pages.push(name),refreshProgress(){}});
 vm.runInContext(section(app,'function showPendingReport(','function tryOpenPending(','frontend/app.js'),context);
 vm.runInContext(section(app,'function taskFor(','const taskSnapshots=','frontend/app.js'),context);
 const home=homeUI({$,getState:()=>state,parse:context.parse,taskLabel:()=> '生成报告',taskFor:context.taskFor,openTask:context.openTask});
 home.renderHomeTasks();button.onclick();button.onclick();
 assert.equal(context.pendingRun,'run');assert.equal(context.current,null);assert.deepEqual(pages,['report','report']);
 assert.equal($('report-title').textContent,'正在生成的报告');assert.equal($('export-menu-toggle').hidden,true);
 context.current={id:'unsaved'};context.dirty=true;button.onclick();
 assert.equal(context.current.id,'unsaved');assert.equal(pages.length,2,'task navigation cannot discard unsaved edits');
});

