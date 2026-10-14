import test from 'node:test';
import assert from 'node:assert/strict';
import {createLearningOnboarding} from '../frontend/learning-onboarding.js';

test('first-use choice authorizes the shown budget, preserves off, and refuses a changed plan',async()=>{
 const elements=new Map();
 const $=id=>{if(!elements.has(id))elements.set(id,{});return elements.get(id)};
 const plan={rounds:1,fingerprint:'plan-one',scope_fingerprint:'scope-one'};
 let state={settings:{model:'chosen',auto_learn:true,k:1},learning_authorization:{state:'needs_confirmation',plan}};
 const calls=[];
 const api=async(route,body)=>{
  calls.push([route,body]);
  if(body.auto_learn){
   if(body.confirm_plan!==state.learning_authorization.plan.fingerprint)throw Error('budget changed');
   state.settings={...state.settings,...body,auto_learn_authorized_rounds:1,auto_learn_authorized_plan:plan.scope_fingerprint};
   state.learning_authorization.state='authorized';
  }else{
   state.settings.auto_learn=false;
   state.learning_authorization.state='off';
  }
  return state.settings;
 };
 const ui=createLearningOnboarding({api,$,getState:()=>state,refresh:async()=>{},describePlan:value=>JSON.stringify(value)});
 ui.mount({insertAdjacentHTML(){}});
 assert.equal($('welcome-learning-choice').checked,true);
 assert.equal(state.learning_authorization.state,'needs_confirmation');
 assert.equal(calls.length,0);
 $('welcome-learning-choice').onchange({target:{checked:true}});
 ui.sync();
 assert.equal($('welcome-learning-choice').checked,true);
 await ui.confirmStart();
 assert.equal(state.learning_authorization.state,'authorized');
 assert.deepEqual(calls[0],['settings',{auto_learn:true,confirm_learning_rounds:1,confirm_plan:'plan-one'}]);
 await api('settings',{auto_learn:false});
 ui.sync();
 assert.equal($('welcome-learning-choice').checked,false);
 await ui.confirmStart();
 assert.equal(calls.at(-1)[1].auto_learn,false);
 $('welcome-learning-choice').onchange({target:{checked:false}});
 await ui.confirmStart();
 assert.equal(state.settings.auto_learn,false);
 assert.equal(state.learning_authorization.state,'off');
 const reopened=createLearningOnboarding({api,$,getState:()=>state,refresh:async()=>{},describePlan:JSON.stringify});
 reopened.mount({insertAdjacentHTML(){}});
 assert.equal($('welcome-learning-choice').checked,false);
 $('welcome-learning-choice').onchange({target:{checked:true}});
 reopened.sync();
 assert.equal($('welcome-learning-choice').checked,true);
 state.learning_authorization={state:'needs_confirmation',plan:{...plan,fingerprint:'plan-two'}};
 await assert.rejects(reopened.confirmStart(),/budget changed/);
 assert.equal(state.learning_authorization.state,'needs_confirmation');
 assert.equal(state.settings.auto_learn,false);
});
