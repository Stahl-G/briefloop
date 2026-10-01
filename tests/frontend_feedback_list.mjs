import test from 'node:test';
import assert from 'node:assert/strict';
import {createFeedbackList} from '../frontend/feedback-list.js';

test('saved feedback is listed with its learning status and counted beside the save box',()=>{
 const nodes={};let opened=0;
 const $=id=>nodes[id]??={innerHTML:'',hidden:true,querySelector(){return this.innerHTML.includes('data-feedback-open')?(this.button??={addEventListener(name,fn){this.click=fn}}):null}};
 const state={feedback:[
   {id:'f3',kind:'comment',batch_id:null,created:'2026-10-01T02:51:15',data:JSON.stringify({learning_intent:'explicit_requirement',text:'deepseek开源没提到'})},
   {id:'f2',kind:'comment',batch_id:'b1',created:'2026-10-01T02:49:31',data:JSON.stringify({learning_intent:'feedback',text:'摘要太长'})},
   {id:'f1',kind:'revision_edit',batch_id:'b0',created:'2026-09-30T01:00:00',data:JSON.stringify({})}],
  feedback_summary:{total:3,pending:1},
  jobs:[{id:'b1',kind:'learn',status:'running',payload:JSON.stringify({feedback_ids:['f2']})}]};
 const list=createFeedbackList({$,esc:s=>String(s),getState:()=>state,openLearning:()=>opened++});
 assert.deepEqual(list.rows().map(r=>[r.status,r.kind]),[['待整理','必须保留的要求'],['正在整理','反馈'],['整理状态未知','改稿']]);
 list.render();
 assert.match($('feedback-list').innerHTML,/共 3 条，其中 1 条待整理/);
 assert.match($('feedback-list').innerHTML,/deepseek开源没提到/);
 assert.equal($('feedback-saved-hint').hidden,false);
 assert.match($('feedback-saved-hint').innerHTML,/已保存 3 条反馈，1 条待整理/);
 $('feedback-saved-hint').button.click();assert.equal(opened,1);
});

test('persisted statuses survive the job window; global counts include older pending feedback',()=>{
 const nodes={};const $=id=>nodes[id]??={innerHTML:'',hidden:true,querySelector:()=>null};
 const state={feedback_summary:{total:106,pending:6},jobs:[],feedback:[
  ...['failed','interrupted','cancelled','complete',null].map((status,i)=>({id:`f${i}`,batch_id:`j${i}`,kind:'comment',data:{text:'Saved feedback'},learning_status:status})),
  {id:'revision',batch_id:'old-job',kind:'revision',data:{before:'v1',after:'v2'},learning_status:'failed'}]};
 const list=createFeedbackList({$,esc:s=>String(s),getState:()=>state,openLearning:()=>{}});
 assert.deepEqual(list.rows().map(row=>row.status),['整理失败','整理中断','整理已停止','已整理','整理状态未知','整理失败']);
 assert.equal(list.rows().at(-1).kind,'改稿');
 assert.ok(list.rows().at(-1).text.trim());
 assert.equal(list.rows().filter(row=>row.pending).length,0);
 list.render();
 assert.match($('feedback-list').innerHTML,/共 106 条，其中 6 条待整理/);
 assert.match($('feedback-list').innerHTML,/仅显示最近 6 条/);
 assert.match($('feedback-saved-hint').innerHTML,/已保存 106 条反馈，6 条待整理/);
 assert.doesNotMatch($('feedback-saved-hint').innerHTML,/查看全部/);
});

test('nothing saved keeps the hint hidden and says so on the learning page',()=>{
 const nodes={};const $=id=>nodes[id]??={innerHTML:'',hidden:true,querySelector:()=>null};
 createFeedbackList({$,esc:s=>String(s),getState:()=>({}),openLearning:()=>{}}).render();
 assert.equal($('feedback-saved-hint').hidden,true);
 assert.match($('feedback-list').innerHTML,/还没有保存的反馈/);
});
