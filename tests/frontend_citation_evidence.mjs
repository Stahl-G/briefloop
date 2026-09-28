import test from 'node:test';
import assert from 'node:assert/strict';
import {createCitationEvidence} from '../frontend/citation-evidence.js';
const esc=s=>String(s).replaceAll('&','&amp;').replaceAll('<','&lt;').replaceAll('>','&gt;').replaceAll('"','&quot;');
const view=createCitationEvidence({esc});
test('evidence shows saved claim with escaped source context without implying approval',()=>{
 const ref={source_id:'s1',report_quote:'收入可能增长。',excerpt:'可能增长',locator:'line 3-3',source_title:'季度材料',source_context:'<script>\n公司：合成示例',context_locator:'line 1-4'};
 const html=view.render([ref],[{id:'s1',name:'季度材料'}],'收入可能增长。');
 assert.match(html,/正文结论/);assert.match(html,/收入可能增长。/);assert.match(html,/&lt;script&gt;<br>公司/);
 assert.match(html,/不等于结论得到支持/);assert.doesNotMatch(html,/<script>/);
 assert.match(view.render([ref],[],'收入已经增长。'),/对应正文已变化/);
 assert.equal(view.render([]),'尚无引用记录');
 assert.doesNotMatch(view.render([{source_id:'s1',excerpt:'旧引文'}]),/结论与原文依据/);
});
