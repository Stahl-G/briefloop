import test from 'node:test';
import assert from 'node:assert/strict';
import {verificationBadge} from '../frontend/skill-verification.js';

test('a skill shows not-yet-verified until enough reports, then its recurrence rate',()=>{
 assert.equal(verificationBadge(undefined),'');
 const pending=verificationBadge({status:'pending',label:'待验证',observed:1,min_observed:2,rate:0});
 assert.match(pending,/未核验：已观察 1\/2 份/);assert.doesNotMatch(pending,/已采用/);
 assert.match(verificationBadge({status:'adopted',label:'已采用',observed:3,rate:0.25}),/复发率 25% · 3 份报告/);
 assert.match(verificationBadge({status:'not_improving',label:'未见改善',observed:2,rate:1}),/可回退/);
 assert.match(verificationBadge({status:'untracked',label:'无可追踪改动',observed:0,rate:null}),/只来自评论/);
});
