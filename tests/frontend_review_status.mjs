import test from 'node:test';
import assert from 'node:assert/strict';
import {reviewPending,withoutSupersededRetries,factCheckHTML,locatorText} from '../frontend/review-status.js';
import {reviewResultHTML} from '../frontend/review-results.js';
const renderer={reviewResultHTML};

test('review results render frozen new and legacy checks, not current run requirements',()=>{
 const review={status:'complete',review_mode:'standard',review_backend:'opencode',protocol:'clauses_v1',requirement_items:[{requirement_id:'r1',kind:'objective',text:'Frozen <objective>'}],
  clause_items:[{clause_id:'c1',kind:'reader_content',source_quote:'Frozen <content>',instruction:'Explain it'},
                {clause_id:'c2',kind:'research_method',source_quote:'Frozen method',instruction:'Check source'}],
  result:{summary:'Saved summary',coverage_scan_complete:true,clause_checks:[
   {clause_id:'c1',status:'missing',reason:'Missing <reason>',basis:['<script>basis</script>']},
   {clause_id:'c2',status:'unverified',reason:'No execution record',basis:['Unavailable record']}],
   requirement_checks:[{requirement_id:'r1',status:'partial',reason:'Legacy saved reason'}]}};
 const html=renderer.reviewResultHTML(review,{complete:'已返回审阅结果'},{requirement_items:[{requirement_id:'r1',text:'Wrong current mapping'}]});
 for(const text of ['Frozen &lt;content&gt;','Frozen method','Frozen &lt;objective&gt;','读者内容','研究方法',
                    '未完成','未核验','部分完成','Missing &lt;reason&gt;','&lt;script&gt;basis&lt;/script&gt;','Legacy saved reason'])assert.ok(html.includes(text),text);
 assert.ok(!html.includes('<script>')&&!html.includes('Wrong current mapping')&&!html.includes('尚无逐项要求核查结果'));
 assert.ok(html.includes('已返回审阅结果')&&!html.includes('全部要求已完成'));
 assert.ok(html.includes('普通审阅')&&html.includes('执行后端：opencode')&&!html.includes('严格审阅'));
 const legacy=renderer.reviewResultHTML({...review,protocol:'legacy',result:{requirement_checks:review.result.requirement_checks}},{});
 assert.ok(legacy.includes('Frozen &lt;objective&gt;')&&legacy.includes('Legacy saved reason'));
});

test('missing frozen indexes preserve checks with explicit unknown IDs; only empty checks show empty state',()=>{
 const result={clause_checks:[{clause_id:'<unknown-clause>',status:'unverified',reason:'Saved reason',basis:['Saved basis']}],
  requirement_checks:[{requirement_id:'<unknown-requirement>',status:'missing',reason:'Saved legacy reason'}]};
 const html=renderer.reviewResultHTML({status:'complete',requirement_index_error:'Frozen index <unavailable>',result},{});
 for(const text of ['未知条款 ID','&lt;unknown-clause&gt;','未知要求 ID','&lt;unknown-requirement&gt;',
                    'Saved reason','Saved basis','Saved legacy reason','Frozen index &lt;unavailable&gt;'])assert.ok(html.includes(text),text);
 assert.ok(!html.includes('尚无逐项要求核查结果'));
 assert.ok(renderer.reviewResultHTML({status:'queued',result:{}},{}).includes('尚无逐项要求核查结果'));
});
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

test('fact-check panel renders per-claim candidates, separate execution status and reviewer conclusions',()=>{
 const data={version_id:'v1',records:[{id:'f1',version_id:'v1',stage_id:'s',as_of:'2026-09-01',
  snapshot:{model:'m',search_provider:'native'},execution:{status:'budget_exhausted',summary:'搜索预算耗尽，保留已核证据交接'},
  covers_version:true,
  candidates:[
   {claim_id:'c1',statement:'公司2026财年营业收入为12.0百万美元。',status:'contradicted',
    reason:'更正公告将2026财年母公司收入更正为13.5百万美元',query_ids:['search_1'],anchors:[{quote:'12.0百万美元'}],
    spans:[{span_id:'s1',source_id:'src1',source_name:'更正公告',locator:{kind:'text',start_line:1,end_line:1}}],
    reviewer:{status:'contradicted',reason:'对照更正公告原件接纳'}},
   {claim_id:'c2',statement:'公司2026财年研发费用率与行业平均水平相当。',status:'unknown',reason:'公开材料未披露口径',query_ids:[],anchors:[],spans:[],reviewer:null}],
  unselected:[{claim_id:'c3',statement:'公司计划第四季度裁员5%。',reason:'内部未公开信息不进搜索框'}],
  unchecked:[],created:'2026'}]};
 const html=factCheckHTML(data);
 // 每条候选一条卡片：候选状态、依据、原文链接（来源按钮）、Reviewer 结论分别在场
 assert.match(html,/候选 · 与来源不一致/);assert.match(html,/候选 · 无法判断/);
 assert.match(html,/更正公告将2026财年母公司收入更正为13.5百万美元/);
 assert.match(html,/data-source="src1"/);assert.match(html,/原文 · 更正公告（第 1 行）/);
 assert.match(html,/Reviewer 结论：与来源不一致 — 对照更正公告原件接纳/);
 assert.match(html,/尚无独立审阅结论/);
 // 未查主张带原因；执行状态单独一行，只说明收束原因
 assert.match(html,/未查主张/);assert.match(html,/内部未公开信息不进搜索框/);
 assert.match(html,/执行状态：预算耗尽 — 搜索预算耗尽，保留已核证据交接/);
 assert.match(html,/不代表主张真假/);
 // 文案纪律与正文边界：不出现核验断言，也不把核查写进正文提示
 assert.ok(!html.includes('事实有误')&&!html.includes('核验通过'));
 assert.match(html,/核查详情不写入报告正文/);
 // 没有核查记录时不渲染面板
 assert.equal(factCheckHTML({version_id:'v1',records:[]}),'');
 // 旧稿记录明确标注，不算本稿已核查
 const stale=factCheckHTML({version_id:'v2',records:[{...data.records[0],version_id:'v1',covers_version:false,candidates:[],unselected:[],unchecked:[],execution:{status:'completed',summary:''}}]});
 assert.match(stale,/本稿不算已核查/);
 assert.equal(locatorText({kind:'text',start_line:2,end_line:3}),'第 2–3 行');
});

test('fact-check panel shows the stage state and the user grant entry when it can be spent',()=>{
 // 耗尽的阶段：展示状态与追加入口，说明追加只在阶段期间并入计量
 const exhausted=factCheckHTML({version_id:'v1',enabled:true,stage:{stage_id:'s1',status:'budget_exhausted',
  budget_source:{kind:'task_reserve',limits:{}},grants:[],outcome:{}},records:[]});
 assert.match(exhausted,/核查阶段预算耗尽/);assert.match(exhausted,/预算来源：任务预留份额/);
 assert.match(exhausted,/data-fact-grant="v1"/);assert.match(exhausted,/追加核查预算（6 次搜索 · 30 候选 · 12 正文页）/);
 assert.match(exhausted,/不代表主张真假/);
 // 进行中的阶段同样可追加；已完成的阶段不再出现按钮
 const active=factCheckHTML({version_id:'v1',enabled:true,stage:{stage_id:'s2',status:'active',
  budget_source:{kind:'user_grant',limits:{}},grants:[{limits:{}}],outcome:null},records:[]});
 assert.match(active,/核查阶段进行中/);assert.match(active,/预算来源：用户明确追加，另追加 1 次/);
 assert.match(active,/data-fact-grant="v1"/);
 const done=factCheckHTML({version_id:'v1',enabled:true,stage:{stage_id:'s3',status:'completed',
  budget_source:{kind:'task_reserve',limits:{}},grants:[],outcome:{}},records:[]});
 assert.match(done,/核查阶段已完成/);assert.ok(!done.includes('data-fact-grant'));
 // 开关开启但阶段未接纳：提示自动执行，可预登记追加
 const pre=factCheckHTML({version_id:'v1',enabled:true,stage:null,pending_grant:{limits:{search_requests:2,candidate_urls:10,source_pages:2}},records:[]});
 assert.match(pre,/已开启，交付检查前自动执行/);assert.match(pre,/已登记追加核查预算（2 次搜索 · 10 候选 · 2 正文页）/);
 assert.match(pre,/将在核查阶段接纳时并入计量/);assert.match(pre,/data-fact-grant="v1"/);
 // 未开启且无记录：不渲染
 assert.equal(factCheckHTML({version_id:'v1',enabled:false,stage:null,records:[]}),'');
});
