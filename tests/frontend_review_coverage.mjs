import test from 'node:test';
import assert from 'node:assert/strict';
import {reviewCoverageHTML} from '../frontend/review-coverage.js';
import {reviewResultHTML} from '../frontend/review-results.js';
import {observationHTML} from '../frontend/jev-checks.js';
import {esc} from '../frontend/dom.js';

test('review keeps support, contradiction, insufficient and unknown distinct, with scope',()=>{
 const review={claim_items:[{claim_id:'a',statement:'<b>Company A</b>'}],result:{coverage_scan_complete:true,
  claim_checks:[{claim_id:'a',status:'supported_for_scope',reason:'Only the stated period'},
   {claim_id:'b',status:'contradicted',reason:'Wrong project'},
   {claim_id:'c',status:'insufficient_evidence',reason:'Missing context'},
   {claim_id:'d',status:'unknown',reason:'Sources conflict'}],unchecked:['Unexamined conclusion']}};
 const html=reviewResultHTML(review,{});
 for(const text of ['证据支持（限所查范围）','证据矛盾','证据不足','无法判断','未查事项另列','&lt;b&gt;Company A&lt;/b&gt;','Unexamined conclusion'])assert.ok(html.includes(text),text);
 assert.ok(!html.includes('<b>Company A</b>'));
 assert.match(reviewCoverageHTML({result:{}}),/未列出问题不表示全文已核实/);
});

test('Jev unknown, stale and provider failure never look like a review pass',()=>{
 const html=observationHTML({status:'failed',stale:true,error:'<private>',result:{items:[
  {statement:'Target <80>',status:'not_checked',execution:'uncertain',reason:'Request outcome unknown'},
  {statement:'Forecast',status:'supported_for_scope',actual_model:'fixture',probabilities:{supported_for_scope:.8,unknown:.2}},
 ]}},esc);
 for(const text of ['尚未核查','预检未完成','本次不能沿用','&lt;private&gt;','Target &lt;80&gt;','不是真实性概率','不改变正式交付状态'])assert.ok(html.includes(text),text);
 assert.ok(!html.includes('核验通过'));
});
