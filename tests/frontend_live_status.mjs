// Live status must follow the still-running activity, not just the highest seq.
import fs from 'node:fs';
import vm from 'node:vm';
import assert from 'node:assert/strict';
const source=fs.readFileSync(new URL('../frontend/app.js',import.meta.url),'utf8');
const code=source.slice(source.indexOf('function publicActivity'),source.indexOf('function renderMessages'));
const events=new Map();
const c=vm.createContext({chat:{events},esc:v=>String(v),chatStates:{},messageTime:()=>'',$:()=>({}),document:{}});
vm.runInContext(code,c);
const add=(seq,item,kind='runtime_tool')=>events.set(seq,{seq,kind,created:'2026-01-01T00:00:00Z',data:{item}});

add(1,{id:'tool-a',type:'commandExecution',status:'running',command:'pytest'});
add(2,{id:'tool-b',type:'fileChange',status:'completed'});
assert.equal(vm.runInContext('liveStatusText()',c),'正在运行命令…','a later completed event must not hide the running tool');

add(3,{id:'tool-a',type:'commandExecution',status:'completed',command:'pytest'});
assert.equal(vm.runInContext('liveStatusText()',c),'正在回复…','a completed update for the same item clears its running status');

add(4,{id:'tool-c',type:'webSearch',status:'running',query:'x'});
assert.equal(vm.runInContext('liveStatusText()',c),'正在搜索网页…','a newer running activity wins');
assert.equal(vm.runInContext('activityEntries().length',c),3,'activity entries dedupe by item key');
assert.ok(vm.runInContext('typingHTML()',c).includes('正在搜索网页…'),'typingHTML renders the live activity label');
console.log('PASS: live status follows the running activity and ignores later completed events');
