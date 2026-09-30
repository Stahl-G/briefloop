import test from 'node:test';
import assert from 'node:assert/strict';
import {verificationBadge} from '../frontend/skill-verification.js';

test('partial flags and unknown coverage never display acceptance or effectiveness',()=>{
 assert.equal(verificationBadge(undefined),'');
 const pending=verificationBadge({status:'pending',observed:0,unknown:2,rate:null,flagged_reports:1,repeats:1});
 assert.match(pending,/待验收/);assert.match(pending,/未知 2 份/);assert.match(pending,/明确标出复发 1 次/);
 assert.match(pending,/双模型 family 校准/);assert.doesNotMatch(pending,/复发率|已采用|未见改善|可回退/);
 for(const status of ['adopted','not_improving']){
  const legacy=verificationBadge({status,label:'old',observed:2,rate:0,legacy_status_corrected:true});
  assert.match(legacy,/待验收/);assert.match(legacy,/旧自动状态不证明验收/);
  assert.doesNotMatch(legacy,/复发率|已采用|未见改善|可回退/);
 }
 assert.match(verificationBadge({status:'pending',registration_conflict:true,excluded:3}),/绑定待澄清/);
 assert.match(verificationBadge({status:'untracked'}),/没有可追踪的改动/);
});
