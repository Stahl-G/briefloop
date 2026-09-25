import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';
import {section} from './source_section.mjs';

test('Markdown replacement requires confirmation and cancellation preserves the editor',async()=>{
 const source=fs.readFileSync(new URL('../frontend/app.js',import.meta.url),'utf8');
 const code=section(source,'let pendingMarkdownImport=',"$('markdown-source').oninput=",'frontend/app.js');
 let version='original',changes=0;
 const nodes=new Map();
 const $=id=>{if(!nodes.has(id))nodes.set(id,{value:'selected.md',showModal(){this.open=true},close(){this.open=false},addEventListener(name,fn){this[name]=fn}});return nodes.get(id)};
 const context=vm.createContext({$,action:async fn=>fn(),savedVersion:async()=>version,
  toEditor:x=>x,notice:()=>{},editor:{commands:{setContent(text,options){assert.equal(text,'New content');assert.equal(options.emitUpdate,true);changes++;version='replacement'}}}});
 vm.runInContext(code,context);
 const choose=()=>$('markdown-import').onchange({target:{files:[{text:async()=>'New content'}]}});
 await choose();assert.equal($('markdown-import-dialog').open,true);assert.equal(changes,0);
 $('markdown-import-cancel').onclick();assert.equal(changes,0);assert.equal($('markdown-import').value,'');
 await choose();version='changed elsewhere';
 await assert.rejects($('markdown-import-confirm').onclick(),/报告已有修改/);assert.equal(changes,0);
 $('markdown-import-cancel').onclick();await choose();await $('markdown-import-confirm').onclick();
 assert.equal(changes,1);assert.equal($('markdown-import-dialog').open,false);
});
