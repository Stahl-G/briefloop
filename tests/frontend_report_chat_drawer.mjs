// The report page's full-chat drawer must stay in sync with normal page navigation.
import fs from 'node:fs';
import vm from 'node:vm';
import assert from 'node:assert/strict';
const source=fs.readFileSync('frontend/app.js','utf8');

const elements=new Map();
const el=id=>{if(!elements.has(id))elements.set(id,{hidden:true,value:''});return elements.get(id)};
const classes=new Set();
const body={classList:{contains:c=>classes.has(c),add:c=>classes.add(c),remove:c=>classes.delete(c)}};
const navButton={dataset:{page:'chat'},classList:{toggle(){}}};
const documentMock={body,querySelectorAll:()=>[navButton]};

const drawerCode=source.slice(source.indexOf('function setReportChatOpen'),source.indexOf('function renderReportStatus'));
const pageCode=source.slice(source.indexOf('function page(name){'),source.indexOf('async function action(fn'));
const c=vm.createContext({
 $:el,chat:{sessions:[],id:null},selectChat:()=>Promise.resolve(),setTimeout:()=>0,
 notice:()=>{},document:documentMock,refreshCandidates:()=>{},moveSearchSettings:()=>{},applyPendingSetupFields:()=>{},
});
vm.runInContext(drawerCode+'\n'+pageCode,c);

// (b) closing the drawer hides the chat element and clears the body flag.
vm.runInContext('openReportChat()',c);
assert.equal(el('chat').hidden,false,'opening the drawer shows #chat');
assert.equal(classes.has('report-chat-open'),true,'opening the drawer flags the body');
vm.runInContext('closeReportChat()',c);
assert.equal(el('chat').hidden,true,'closing the drawer hides #chat');
assert.equal(classes.has('report-chat-open'),false,'closing the drawer clears the body flag');
assert.equal(el('report-chat-close').hidden,true,'closing the drawer hides the close button');
assert.equal(el('report-chat-backdrop').hidden,true,'closing the drawer hides the backdrop');

// (a) the sidebar 对话 nav returns to the full chat page and tears the drawer chrome down.
vm.runInContext('openReportChat()',c);
assert.equal(el('report-chat-close').hidden,false);
assert.equal(el('report-chat-backdrop').hidden,false);
navButton.onclick();
assert.equal(classes.has('report-chat-open'),false,'sidebar nav clears the drawer body flag');
assert.equal(el('report-chat-close').hidden,true,'sidebar nav hides the drawer close button');
assert.equal(el('report-chat-backdrop').hidden,true,'sidebar nav hides the drawer backdrop');
assert.equal(el('chat').hidden,false,'sidebar nav lands on the full chat page');
console.log('PASS: report chat drawer open/close stays in sync with sidebar navigation');
