import test from 'node:test';
import assert from 'node:assert/strict';
import {learningMessage,reportFeedbackUI} from '../frontend/report-feedback.js';

test('learning outcomes distinguish tie, adoption, failure and missing evidence',()=>{
 assert.match(learningMessage({status:'running'}),/正在试写/);
 assert.match(learningMessage({status:'complete',result:{history:[{accepted:false,pairs:[{verdict:'tie'}]}],active_skill:'older'}}),/未发现足够改善/);
 assert.match(learningMessage({status:'complete',result:{history:[{accepted:true}]}}),/已采用/);
 assert.match(learningMessage({status:'failed'}),/失败.*保留/);
 assert.match(learningMessage({status:'complete',result:null}),/未记录/);
});
function fixture(){
 const nodes={};const $=id=>nodes[id]??={value:'',hidden:true,textContent:'',innerHTML:'',classList:{toggle(){}},querySelector(){return null},querySelectorAll(){return []},focus(){}};
 let current={id:'v1',run_id:'r1'};const calls=[];
 $('assistant-input').value='以后先给结论';$('agreement-scope').value='series';
 const api=async(path,data)=>{calls.push([path,data]);return data?{id:'a1'}:{items:[{id:'a1',text:'以后先给结论',scope:'series'}],learning:{status:'complete',result:{history:[{accepted:false,pairs:[{verdict:'tie'}]}]}},pending_feedback:0}};
 const ui=reportFeedbackUI({$,api,esc:String,getCurrent:()=>current,savedVersion:async()=>current.id,openLearning(){},refresh:async()=>{},notice(){}});
 return {ui,nodes,calls,setCurrent:x=>{current=x}};
}
test('remember uses local API, clears only the saved draft, and keeps terminal result visible',async()=>{
 const f=fixture();await f.ui.save();
 assert.deepEqual(f.calls[0],['writing-agreements',{version_id:'v1',text:'以后先给结论',scope:'series'}]);
 assert.equal(f.nodes['assistant-input'].value,'');assert.equal(f.nodes['comment-submit'].disabled,false);
 assert.match(f.nodes['agreement-result'].textContent,/已记住/);
 assert.equal(f.nodes['feedback-next-step'].hidden,false);assert.match(f.nodes['feedback-next-step'].innerHTML,/未发现足够改善/);
 assert.ok(!f.calls.some(([p])=>p==='learn'||p.includes('harness')));
});


test('leaving experience keeps the saved-version feedback route and existing learning policy',async()=>{
 const nodes={};const $=id=>nodes[id]??={value:'',classList:{toggle(){}},querySelector(){},querySelectorAll(){return []}};
 let current={id:'edited',run_id:'r'},scheduled=0;const calls=[];
 $('assistant-input').value='这段取舍分析缺少代价比较';
 const ui=reportFeedbackUI({$,api:async(path,data)=>{calls.push([path,data]);return {items:[],pending_feedback:1}},esc:String,
  getCurrent:()=>current,savedVersion:async()=>{current={id:'saved',run_id:'r'};return 'saved'},openLearning(){},refresh:async()=>{},notice(){},scheduleLearning(){scheduled++}});
 await ui.save('feedback');
 assert.deepEqual(calls[0],['comment',{version_id:'saved',text:'这段取舍分析缺少代价比较',learning_intent:'feedback'}]);
 assert.equal(scheduled,1);assert.ok(!calls.some(([path])=>path==='writing-agreements'||path==='learn'));
 assert.match($('agreement-result').textContent,/经验已保存/);
});
