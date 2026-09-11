import fs from 'node:fs';
import vm from 'node:vm';
import assert from 'node:assert/strict';
const source=fs.readFileSync('frontend/app.js','utf8');
const code=source.slice(source.indexOf('const CHAT_COMMANDS=['),source.indexOf('function applyRequirements'));
const elements=new Map();const el=id=>{if(!elements.has(id))elements.set(id,{});return elements.get(id)};
const input=el('chat-input'),panel=el('chat-commands');
panel.querySelectorAll=()=>[];input.focus=()=>{};input.setSelectionRange=()=>{};
const c=vm.createContext({$:el,esc:v=>String(v),rememberDraft:()=>{},updateComposer:()=>{},notice:()=>{},String});
vm.runInContext(code,c);

input.value='/';vm.runInContext('renderCommands()',c);
assert.equal(panel.hidden,false,'typing / opens the command menu');
for(const name of ['discuss','new','help'])assert.ok(panel.innerHTML.includes('/'+name),`menu lists /${name}`);

input.value='/di';vm.runInContext('renderCommands()',c);
assert.ok(panel.innerHTML.includes('/discuss')&&!panel.innerHTML.includes('/new'),'typing filters the menu');

input.value='/zzz';vm.runInContext('renderCommands()',c);
assert.equal(panel.hidden,true,'an unknown command closes the menu');

input.value='/';vm.runInContext('renderCommands()',c);vm.runInContext('commandIndex=1',c);
vm.runInContext('acceptCommand()',c);
assert.equal(input.value,'/new ','choosing a command fills it in');
assert.equal(panel.hidden,true,'choosing a command closes the menu');

input.value='普通消息';vm.runInContext('renderCommands()',c);
assert.equal(panel.hidden,true,'normal messages do not open the menu');
console.log('PASS: slash command menu lists, filters and inserts chat commands');
