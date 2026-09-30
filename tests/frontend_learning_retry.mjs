import test from 'node:test';
import assert from 'node:assert/strict';
import {learningRetry} from '../frontend/learning-retry.js';

test('a linked learning retry sends the explicitly confirmed plan and cancel sends nothing',async()=>{
 const calls=[],seen=[],plan={fingerprint:'bound-plan'};
 const retry=learningRetry({api:async(...args)=>calls.push(args),getPlan:()=>plan,confirm:text=>{seen.push(text);return false},describePlan:()=> '分类1会话，试写12次'});
 await retry({id:'old-job',kind:'learn'});assert.deepEqual(calls,[]);assert.match(seen[0],/分类1会话/);
 const accepted=learningRetry({api:async(...args)=>calls.push(args),getPlan:()=>plan,confirm:()=>true,describePlan:()=>''});
 await accepted({id:'old-job',kind:'learn'});
 assert.deepEqual(calls,[['resume',{job_id:'old-job',use_current_model:true,confirm_plan:'bound-plan'}]]);
});
