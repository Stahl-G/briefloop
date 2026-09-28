import test from 'node:test';
import assert from 'node:assert/strict';
import {createLengthControls} from '../frontend/length-controls.js';

function view(){
 const nodes={},notices=[];
 const $=id=>nodes[id]??=({value:'',textContent:'',hidden:false,title:'',listeners:{},
  addEventListener(name,fn){(this.listeners[name]??=[]).push(fn)},
  emit(name){for(const fn of this.listeners[name]||[])fn()},
  classList:{values:new Set(),add(name){this.values.add(name)},remove(name){this.values.delete(name)}}});
 $('max-words').value='2000';$('length-mode').value='soft';$('report-language').value='zh';
 const controls=createLengthControls({$,language:()=>$('report-language').value,notice:value=>notices.push(value)});controls.init();
 return {$,controls,notices};
}

test('old max value restores as advisory; only selecting strict records the form choice',()=>{
 const {$,controls}=view();controls.restore({target_words:1500,max_words:2000});
 assert.deepEqual(controls.read(),{length_mode:'soft',length_requirement:null});
 assert.equal($('max-words-label').textContent,'建议字数上沿');
 $('length-mode').value='strict';$('length-mode').emit('change');
 const saved=controls.read();assert.equal(saved.length_requirement.kind,'user_selection');
 assert.match(saved.length_requirement.text,/2000/);
 const reopened=view();reopened.controls.restore({...saved,max_words:2000});
 assert.deepEqual(reopened.controls.read(),saved);
 assert.match(reopened.$('length-requirement-source').textContent,/2000/);
 reopened.$('requirements').emit('reset');assert.equal(reopened.controls.read().length_mode,'soft');
});

test('quoted strict requirement survives form restore but model block cannot forge a selection',()=>{
 const {$,controls,notices}=view();
 const req={length_mode:'strict',max_words:2000,length_requirement:{kind:'user_quote',text:'不得超过2000字'}};
 controls.restore(req,{fromDiscussion:true});assert.deepEqual(controls.read().length_requirement,req.length_requirement);
 $('max-words').value='2500';assert.throws(()=>controls.read(),/重新确认/);
 $('max-words').emit('input');assert.equal(controls.read().length_requirement.kind,'user_selection');
 assert.match(controls.read().length_requirement.text,/2500/);
 controls.restore({...req,length_requirement:{kind:'user_selection',text:'模型声称点过选项'}},{fromDiscussion:true});
 assert.equal(controls.read().length_mode,'soft');assert.equal(notices.length,1);
});

test('over target feedback is neutral; explicit strict difference shows its source',()=>{
 const {$,controls}=view(),element=$('brief-length');
 const stats={count:2300,target_words:1500,max_words:2000,over_limit:true};
 controls.renderBrief(element,stats);
 assert.match(element.textContent,/超出建议 300/);assert.match(element.textContent,/篇幅建议/);
 assert.equal(element.classList.values.has('over-limit'),false);
 controls.renderBrief(element,{...stats,length_mode:'strict',length_requirement:{kind:'user_quote',text:'不得超过2000字'}},{language:'en'});
 assert.match(element.textContent,/严格上限/);assert.match(element.textContent,/要求来源：不得超过2000字/);
 assert.equal(element.classList.values.has('over-limit'),true);assert.match(element.title,/可保存、编辑和下载/);
});

test('strict source must be confirmed again when language changes with the same number',()=>{
 const {$,controls}=view();
 $('length-mode').value='strict';$('length-mode').emit('change');
 assert.match(controls.read().length_requirement.text,/2000 字/);
 $('report-language').value='en';$('report-language').emit('change');
 assert.equal($('max-words').value,'2000');
 assert.match($('length-policy-help').textContent,/计数单位已变化/);
 assert.throws(()=>controls.read(),/计数单位已变化/);
 $('length-mode').value='soft';$('length-mode').emit('change');
 $('length-mode').value='strict';$('length-mode').emit('change');
 assert.match(controls.read().length_requirement.text,/2000 词/);
});
