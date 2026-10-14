// The report page's full-chat drawer must stay in sync with normal page navigation.
import fs from 'node:fs';
import vm from 'node:vm';
import assert from 'node:assert/strict';
import {section} from './source_section.mjs';
import {createReportChatContext} from '../frontend/report-chat-context.js';
const source=fs.readFileSync('frontend/app.js','utf8');

const elements=new Map();
const el=id=>{if(!elements.has(id))elements.set(id,{hidden:true,value:''});return elements.get(id)};
const classes=new Set();
const body={classList:{contains:c=>classes.has(c),add:c=>classes.add(c),remove:c=>classes.delete(c)}};
const navButton={dataset:{page:'chat'},classList:{toggle(){}}};
const documentMock={body,querySelectorAll:()=>[navButton]};

const drawerCode=section(source,'function setReportChatOpen','function renderReportStatus','frontend/app.js');
const pageCode=section(source,'function page(name){','async function action(fn','frontend/app.js');
const c=vm.createContext({
 $:el,chat:{sessions:[],id:null},selectChat:()=>Promise.resolve(),setTimeout:()=>0,
 reportChatContext:{activate(){}},
 state:{settings:{model:'chosen-model',model_selection_required:false}},
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
// Only the visible report drawer binds a target; save before capturing its version.
let selected={id:'draft-1',hash:'old',run_id:'report-1'},nextReport=null,saves=0;
const context=createReportChatContext({getCurrent:()=>selected,
 isOpen:()=>classes.has('report-chat-open'),getNextReport:()=>nextReport,
 clearNextReport:()=>{nextReport=null},
 savedVersion:async()=>{saves++;selected={id:'draft-2',hash:'saved',run_id:'report-1'};return selected.id}});
assert.equal(await context.prepare(),null);
assert.equal(saves,0,'ordinary chat does not save or bind the previous report');
vm.runInContext('openReportChat()',c);
assert.deepEqual(await context.prepare(),{version_id:'draft-2',hash:'saved'});
nextReport={version_id:'previous'};
assert.equal(await context.prepare(),null,'next-period selection does not revise the visible report');
context.activate();
assert.equal(nextReport,null,'opening a current-report conversation clears the previous next-period selection');
vm.runInContext('closeReportChat()',c);
assert.equal(await context.prepare(),null,'closing the drawer leaves no report target in ordinary chat');
const switched=createReportChatContext({getCurrent:()=>selected,isOpen:()=>true,getNextReport:()=>null,
 savedVersion:async()=>{selected={id:'other',hash:'other',run_id:'report-2'};return selected.id}});
await assert.rejects(switched.prepare());
console.log('PASS: report chat drawer open/close stays in sync with sidebar navigation');
