import test from 'node:test';
import assert from 'node:assert/strict';
import {createAssessmentChecks} from '../frontend/assessment-checks.js';
const esc=s=>String(s).replaceAll('&','&amp;').replaceAll('<','&lt;').replaceAll('>','&gt;');
const panel=createAssessmentChecks({esc});
test('saved checks show unresolved and missing followup without claiming review completion',()=>{
 const html=panel.render({overall:'达到要求',checks:[{name:'摘要与正文',status:'needs_attention',reason:'<条件仍被遗漏>',consistency_note:'相关发现尚未处理'},{name:'前次发现复核',result:'not_checked'},{name:'引用定位',result:'passed'}]});
 assert.match(html,/2 项需查看/);assert.match(html,/尚未复核/);assert.match(html,/相关发现尚未处理/);
 assert.match(html,/不等于独立审阅或联网事实核查完成/);
 assert.match(html,/&lt;条件仍被遗漏&gt;/);assert.doesNotMatch(html,/<条件仍被遗漏>/);
 assert.equal(panel.render({overall:'达到要求'}),'');
});
