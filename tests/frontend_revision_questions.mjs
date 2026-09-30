import test from 'node:test';
import assert from 'node:assert/strict';
import {revisionQuestionsUI,questionHTML} from '../frontend/revision-questions.js';

function element(){
 let html='',buttons=[];
 return {hidden:true,textContent:'',get innerHTML(){return html},set innerHTML(value){
  html=value;const edit=(value.match(/data-edit="([^"]+)"/)||[])[1];
  buttons=[...value.matchAll(/data-answer="([^"]+)"/g)].map(m=>({dataset:{answer:m[1]},closest:()=>({dataset:{edit}})}));
 },querySelectorAll:()=>buttons};
}

test('a question names the section and the change, escapes text and posts one answer',async()=>{
 const html=questionHTML({id:'edit_1',op:'replace',heading:'市场',before:'<b>0.31</b>',after:'0.27'});
 assert.match(html,/市场/);assert.match(html,/&lt;b&gt;0.31/);assert.doesNotMatch(html,/<b>0.31/);
 const boxes={'revision-questions':element(),'fact-corrections':element(),'revision-questions-hint':element()};
 const calls=[];
 const ui=revisionQuestionsUI({api:async(path,body)=>calls.push([path,body]),action:fn=>fn(),$:id=>boxes[id]});
 ui.render({questions:[{id:'edit_1',op:'delete',heading:'政策',before:'风险提示',after:''}],fact_corrections:[]});
 assert.equal(boxes['revision-questions'].hidden,false);
 assert.match(boxes['revision-questions-hint'].textContent,/1 处改动/);
 assert.match(boxes['fact-corrections'].innerHTML,/还没有事实纠错/);
 const skip=boxes['revision-questions'].querySelectorAll().find(b=>b.dataset.answer==='skip');
 await skip.onclick();
 assert.deepEqual(calls,[['revision-answer',{edit_id:'edit_1',category:'skip'}]]);
 ui.render({questions:[],fact_corrections:[{heading:'市场',decided_by:'evaluator',before:'0.31',after:'0.27'}]});
 assert.equal(boxes['revision-questions'].hidden,true);
 assert.match(boxes['fact-corrections'].innerHTML,/Evaluator 判断/);
});
