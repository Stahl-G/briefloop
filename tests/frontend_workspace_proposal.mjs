// The chat workspace-proposal button must route through switchWorkspace and
// never leave unsaved report edits behind.
import fs from 'node:fs';
import vm from 'node:vm';
import assert from 'node:assert/strict';
const source=fs.readFileSync(new URL('../frontend/app.js',import.meta.url),'utf8');
const code=source.slice(source.indexOf('function workspaceProposal'),source.indexOf("$('workspace-switch').onclick=showWorkspacePicker;"));
const status={textContent:'',classList:{add(){},remove(){}}};
const dialog={querySelectorAll:()=>[]};
const node={appended:[],append(child){this.appended.push(child)}};
let navigations=[],opens=0,saves=0,notices=[];
const c=vm.createContext({
 console,Promise,Date,setTimeout,clearTimeout,
 $:id=>id==='workspace-switch-status'?status:dialog,
 document:{createElement:()=>({})},
 confirm:()=>true,notice:message=>notices.push(message),
 chat:{busy:false,uploading:0},
 rememberDraft:()=>{},saveTimer:null,saving:false,dirty:true,
 save:async()=>{saves++},
 api:async route=>{if(route==='workspaces')return{current:{path:'/tmp/ws/current'}};opens++;return{url:'http://127.0.0.1:19002',path:'/tmp/ws/new-topic'}},
 location:{assign:url=>navigations.push(url)},
 node,message:'提议如下：\n```briefloop-workspace\n{"name":"new-topic"}\n```'
});
vm.runInContext(code,c);
vm.runInContext('workspaceProposal(node,message)',c);
const button=node.appended[0];
assert.ok(button&&button.textContent.includes('new-topic'));
await button.onclick();
assert.equal(saves,1,'the dirty editor is saved before switching');
assert.equal(opens,0,'the button never bypasses save to open a workspace');
assert.equal(navigations.length,0,'no navigation while the report is still unsaved');
assert.ok(notices.length,'a hidden status dialog still surfaces the failure');
console.log('PASS: workspace proposal saves first and refuses to navigate while the editor stays dirty');
