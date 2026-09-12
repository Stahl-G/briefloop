import test from 'node:test';
import assert from 'node:assert/strict';
import {reviewPending,withoutSupersededRetries} from '../frontend/review-status.js';
test('editing a scored report never inherits a running parent job',()=>{
 const jobs=[{kind:'generate',status:'running',payload:JSON.stringify({run_id:'run'})},{kind:'review',status:'complete',payload:{version_id:'old'}}];
 assert.equal(reviewPending({id:'edited',run_id:'run'},jobs),false);
 jobs.push({kind:'review',status:'queued',payload:JSON.stringify({version_id:'edited'})});
 assert.equal(reviewPending({id:'edited',run_id:'run'},jobs),true);
 assert.equal(reviewPending({id:'old',run_id:'run'},jobs),false);
});

test('report progress keeps only the successor in a retry chain, including terminal retries',()=>{
 for(const status of ['queued','running','complete','failed']){
  const old={id:'old',kind:'learn',status:'cancelled',payload:{feedback_ids:['feedback-a']}};
  const middle={id:'middle',kind:'learn',status:'failed',payload:JSON.stringify({retry_of_job_id:'old'})};
  const latest={id:'latest',kind:'learn',status,payload:{retry_of_job_id:'middle'}};
  const unrelated={id:'other-report',kind:'review',status:'failed',payload:{version_id:'other'}};
  const history=[latest,middle,old,unrelated];
  const frozen=JSON.stringify(history);
  assert.deepEqual(withoutSupersededRetries(history),[latest,unrelated]);
  assert.equal(JSON.stringify(history),frozen);
  // A successor outside the selected report's scope cannot hide its task.
  assert.deepEqual(withoutSupersededRetries([old,unrelated]),[old,unrelated]);
 }
});
