import test from 'node:test';
import assert from 'node:assert/strict';
import {previousReportUI,previousReportPrompt,readerBlock} from '../frontend/previous-report.js';

test('importing a previous report opens a /discuss request with the file attached',async()=>{
 const calls=[],opened=[],notices=[];
 const ui=previousReportUI({api:async(path,body)=>{calls.push([path,body]);return {title:'组件价格周报',source_id:'src_1',tracked_changes:true}},
  uploadPayload:async(file,limits,extra)=>({name:file.name,data:'AA==',...extra}),getUploadLimits:()=>({}),
  openChat:async value=>opened.push(value),notice:message=>notices.push(message),$:()=>null});
 await ui.importFile({name:'周报.docx'});
 assert.equal(calls[0][0],'import-previous');
 assert.equal(opened[0].source_id,'src_1');assert.match(opened[0].text,/^\/discuss /);assert.match(opened[0].text,/《组件价格周报》/);
 assert.match(notices[0],/Word 修订已记为改稿/);
 assert.match(previousReportPrompt('x'),/briefloop-reader/);
});

test('a reader block needs a name and only keeps its three text fields',()=>{
 assert.deepEqual(readerBlock('先说明\n```briefloop-reader\n{"name":"管理层","decisions":"出货节奏","preferences":"先给结论","email":"x@y"}\n```'),
  {name:'管理层',decisions:'出货节奏',preferences:'先给结论'});
 assert.equal(readerBlock('```briefloop-reader\n{"decisions":"x"}\n```'),null);
 assert.equal(readerBlock('```briefloop-reader\nnot json\n```'),null);
 assert.equal(readerBlock('no block'),null);
});
