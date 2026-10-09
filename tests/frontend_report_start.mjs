import test from 'node:test';
import assert from 'node:assert/strict';
import {reportStartUI} from '../frontend/report-start.js';

function fixture(draft='',busy=false){
 const calls=[],nodes=Object.fromEntries(['new-report','home-start-report','home-import-previous','home-report-form','previous-report-file'].map(id=>[id,{}]));
 nodes['chat-input']={value:draft,focus(){calls.push('focus')},setSelectionRange(){}};
 nodes['previous-report-file'].click=()=>calls.push('file-picker');
 const ui=reportStartUI({$:id=>nodes[id],openChatHome:async()=>calls.push('home'),rememberDraft:()=>calls.push('save-draft'),updateComposer:()=>calls.push('composer'),page:value=>calls.push(value),notice:value=>calls.push(value),isBusy:()=>busy});
 ui.bind();return {ui,nodes,calls};
}
test('report shortcut prepares an unsent conversation, preserving an existing draft',async()=>{
 for(const draft of ['', '已有需求，尚未发送']){
  const f=fixture(draft);await f.nodes['new-report'].onclick();
  assert.equal(f.nodes['chat-input'].value,draft||'我想做一份报告，');
  assert.deepEqual(f.calls,['home','save-draft','composer','focus']);
 }
});
test('old report and manual form are direct alternatives; active upload prevents navigation',async()=>{
 const f=fixture();f.nodes['home-import-previous'].onclick();f.nodes['home-report-form'].onclick();
 assert.deepEqual(f.calls,['file-picker','setup']);
 const busy=fixture('保留',true);await busy.ui.start();busy.nodes['home-import-previous'].onclick();
 assert.equal(busy.nodes['chat-input'].value,'保留');
 assert.equal(busy.calls.some(x=>x==='home'||x==='file-picker'),false);
});
