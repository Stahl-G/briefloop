// Settings > Workspaces must render from the shared list and mark the current
// workspace disabled; a lone current workspace shows the empty state instead.
import fs from 'node:fs';
import vm from 'node:vm';
import assert from 'node:assert/strict';
const source=fs.readFileSync(new URL('../frontend/app.js',import.meta.url),'utf8');
const listCode=source.slice(source.indexOf('let workspaceInventory='),source.indexOf("$('workspace-switch').onclick"));
const settingsCode=source.slice(source.indexOf('function settingsView('),source.indexOf('function settingsModelTab('));
const elements=new Map();
const el=id=>{if(!elements.has(id))elements.set(id,{value:'',disabled:false,hidden:false,title:'',textContent:'',innerHTML:'',classList:{toggle(){},add(){},remove(){}},querySelectorAll:()=>[],showModal(){},close(){}});return elements.get(id)};
let inventory={current:{name:'A',path:'/a'},workspaces:[{name:'A',path:'/a'},{name:'B',path:'/b'}]};
const c=vm.createContext({$:el,esc:String,console,JSON,document:{querySelectorAll:()=>[]},api:async route=>route==='workspaces'?inventory:{},switchWorkspace:()=>{}});
vm.runInContext(listCode,c);vm.runInContext(settingsCode,c);
await vm.runInContext("settingsView('workspaces')",c);
const box=el('settings-workspace-list');
assert.match(box.innerHTML,/data-settings-workspace="0" disabled/);
assert.match(box.innerHTML,/data-settings-workspace="1"(?![^>]*disabled)/);
assert.match(box.innerHTML,/B/);
inventory={current:{name:'A',path:'/a'},workspaces:[{name:'A',path:'/a'}]};
await vm.runInContext("renderSettingsWorkspaces()",c);
assert.match(box.innerHTML,/还没有其他工作区/);
assert.doesNotMatch(box.innerHTML,/class="workspace-choice"/);
console.log('PASS: settings workspaces list shows other workspaces, disables the current one, and falls back to the empty state');
