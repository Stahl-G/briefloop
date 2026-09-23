import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';
import {beginPanel,updatePanel} from '../frontend/report-panels.js';
import {createAssessmentPanel} from '../frontend/assessment-panel.js';

test('refresh keeps rendered findings and expanded state until data changes',()=>{
 let writes=0,html='';
 const box={dataset:{},expanded:false,get innerHTML(){return html},set innerHTML(value){html=value;writes++;this.expanded=false}};
 beginPanel(box,'v1');updatePanel(box,'<details>Finding</details>');box.expanded=true;
 const before=writes;
 beginPanel(box,'v1','Loading');updatePanel(box,'<details>Finding</details>');
 assert.equal(writes,before);assert.equal(box.expanded,true);
 updatePanel(box,'New result');assert.equal(box.innerHTML,'New result');
 beginPanel(box,'v2','Loading');assert.equal(box.innerHTML,'Loading');
});

test('fact-check polling keeps content in flight and rejects a stale version response',async()=>{
 let html='',writes=0;
 const box={dataset:{},isConnected:true,querySelectorAll:()=>[],get innerHTML(){return html},set innerHTML(v){html=v;writes++}};
 const pending=[];
 let current={id:'v1'};
 const panel=createAssessmentPanel({
  api:()=>new Promise(resolve=>pending.push(resolve)),
  action:async fn=>fn(),notice(){},
  $:()=>box,esc:s=>String(s),parse:s=>JSON.parse(s||'{}'),
  getState:()=>({assessments:[],jobs:[],sources:[]}),
  getCurrent:()=>current,getEditor:()=>null,isDirty:()=>false,
  bindSources(){},applyHighlightState(){},readerHighlights:()=>[],toEditor:x=>x,
  beginPanel,updatePanel,factCheckHTML:data=>data.html,reviewPending:()=>false,
 });
 const refresh=()=>panel.renderFactChecks();
 let task=refresh();pending.shift()({html:'Long findings'});await task;
 const before=writes;
 task=refresh();assert.equal(html,'Long findings');assert.equal(writes,before);
 pending.shift()({html:'Long findings'});await task;assert.equal(writes,before);
 const old=refresh();current={id:'v2'};const latest=refresh();
 pending.pop()({html:'Version two'});await latest;
 pending.shift()({html:'Stale version one'});await old;
 assert.equal(html,'Version two');
});

test('saved-version numeric occurrence hint stays bounded and does not imply fact approval',async()=>{
 let html='';
 const box={dataset:{},isConnected:true,get innerHTML(){return html},set innerHTML(v){html=v}};
 const assessment={querySelector:()=>box};
 const checks={broken_refs:[],numbers:{total:1,checked:1,matched:1,status:'checked_bindings',
  unmatched:[],skipped:[],occurrence_review:{candidate_count:8,checked_occurrences:1,
   review_candidate_count:7,samples:[{kind:'table_cell',table:1,row:2,column:2,text:'25%',context:'<bad> 25%'}],
   sample_limit:12,truncated:false,scope:'仅是候选，不是事实正确率。'}},
  export:{escaped_bold:false,figure_error:null,figure_markers:[]},layout:null};
 const panel=createAssessmentPanel({api:async()=>checks,action:async fn=>fn(),notice(){},
  $:()=>assessment,esc:s=>String(s).replaceAll('&','&amp;').replaceAll('<','&lt;').replaceAll('>','&gt;'),
  parse:s=>JSON.parse(s||'{}'),getState:()=>({assessments:[],jobs:[],sources:[]}),
  getCurrent:()=>({id:'v1'}),getEditor:()=>null,isDirty:()=>false,
  bindSources(){},applyHighlightState(){},readerHighlights:()=>[],toEditor:x=>x,
  beginPanel,updatePanel,reviewPending:()=>false});
 await panel.renderDeliveryChecks();
 assert.match(html,/带明确单位的数值出现 8 处；直接对应已核对绑定 1 处；待看 7 处/);
 assert.match(html,/表 1 · 第 2 行 2 列/);
 assert.match(html,/&lt;bad&gt; 25%/);
 assert.doesNotMatch(html,/<bad>/);
 assert.match(html,/不代表正文数字已全部核验/);
});
