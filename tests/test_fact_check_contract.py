"""Phase C1 fact-check result contract: offline, no model and no network.

One synthetic Chinese report covers the claim matrix of the plan's first
acceptance stage: unit conversion, fiscal vs calendar year, parent vs
subsidiary, a correction notice, a media reprint, a true contradiction, an
attributed forecast and internal information that must never reach a search box.
Only contract behaviour is verified — scope judgements in the fixtures are
inputs, not assertions about the real world.
"""
import json
import pytest
from briefloop import fact_check, research_budget, research_plan
from briefloop.evidence import bind_claim, blocks, create_claim, create_span, digest
from briefloop.store import Store

# key, 报告主张句, 绑定锚点引文（块内唯一）
PLAN=[
    ('unit','公司2026财年上半年营业收入650万美元，约合人民币4,700万元。','650万美元'),
    ('fiscal','公司2026自然年度营业收入为12.0百万美元。','自然年度'),
    ('parent','母公司2026财年营业收入为30.0百万美元。','30.0百万美元'),
    ('corrected','公司2026财年营业收入为12.0百万美元。','12.0百万美元'),
    ('reprint','多家媒体报道公司2026财年营业收入为12.0百万美元。','多家媒体报道'),
    ('contradiction','公司2026财年营业收入为15.0百万美元。','15.0百万美元'),
    ('forecast','管理层预计2027财年营业收入同比增长约20%。','同比增长约20%'),
    ('unknown','公司2026财年研发费用率与行业平均水平相当。','研发费用率'),
    ('internal','公司计划第四季度裁员5%。','裁员5%'),
]


def checked(tmp_path):
    """A workspace with originals, bound claims, an admitted stage and one real stage query."""
    store=Store(tmp_path)
    sources={
        'annual':store.add_source('公司2026财年年度公告',
            '母公司2026财年（截至2026年3月31日）营业收入12.0百万美元；子公司同期营业收入30.0百万美元。\n'
            '2026财年上半年（截至2025年9月30日）营业收入6.5百万美元。\n'
            '管理层预计2027财年营业收入同比增长约20%。'),
        'correction':store.add_source('更正公告','更正：母公司2026财年营业收入由12.0百万美元更正为13.5百万美元。'),
        'media':store.add_source('财经媒体转载','据公司公告，公司2026财年营业收入为12.0百万美元。'),
        'internal':store.add_source('内部经营纪要','内部未公开：公司计划第四季度裁员5%。')}
    run=store.create_run({'title':'公司研究','objective':'核对公开主张','allow_web':True,'fact_check':True,
        'report_date':'2026-09-01','research_budget':{'search_requests':8,'candidate_urls':40,'source_pages':8}},
        [sources['annual']['id']],research_protocol='quality_v1')
    for key in ('correction','media','internal'):store.attach_source(run['id'],sources[key]['id'])
    research_plan.freeze(store,run['id'])
    research_plan.finish_round(store,run['id'])
    stage=research_plan.admit_fact_check(store,run['id'],{'kind':'task_reserve',
        'limits':{'search_requests':4,'candidate_urls':20,'source_pages':4}})
    brief=store.publish(run['id'],{'title':'公司研究','editor_document':{'type':'doc','content':[
        {'type':'paragraph','content':[{'type':'text','text':sentence}]} for _,sentence,_ in PLAN]}})
    block_ids=list(blocks(json.loads(brief['editor_document'])))
    claims={}
    for (key,sentence,quote),block_id in zip(PLAN,block_ids):
        claim=create_claim(store,run['id'],{'statement':sentence,'kind':'fact'})
        bind_claim(store,brief['id'],claim['id'],block_id,quote)
        claims[key]={'id':claim['id'],'block':block_id,'quote':quote}
    job=store.enqueue('fact_check',{'run_id':run['id'],'runtime':{'model':'contract-test-model'}})
    query=research_budget.reserve_search(store,run['id'],3)  # a real query admitted to the fact-check stage
    return {'store':store,'run':run,'brief':brief,'stage':stage,'sources':sources,'claims':claims,'job':job,'query':query}


def _span(store,source,line):
    return create_span(store,{'source_id':source['id'],'locator':{'kind':'text','start_line':line,'end_line':line}})['id']


def good_result(world):
    store=world['store'];sources=world['sources'];c=world['claims'];q=world['query']['request_id']
    annual=sources['annual']
    candidates=[
        {'claim_id':c['unit']['id'],'status':'supported_for_scope',
         'reason':'公告披露2026财年上半年收入6.5百万美元，与650万美元同一金额、仅单位呈现不同',
         'span_ids':[_span(store,annual,2)],'query_ids':[q]},
        {'claim_id':c['fiscal']['id'],'status':'contradicted',
         'reason':'公告口径为截至2026年3月31日的财年，不是自然年度',
         'span_ids':[_span(store,annual,1)],'query_ids':[q]},
        {'claim_id':c['parent']['id'],'status':'contradicted',
         'reason':'30.0百万美元是子公司同期收入，不是母公司',
         'span_ids':[_span(store,annual,1)]},
        {'claim_id':c['corrected']['id'],'status':'contradicted',
         'reason':'更正公告将2026财年母公司收入更正为13.5百万美元',
         'span_ids':[_span(store,sources['correction'],1)]},
        {'claim_id':c['reprint']['id'],'status':'supported_for_scope',
         'reason':'媒体确实转述了12.0百万美元的旧口径；转载不另计独立来源',
         'span_ids':[_span(store,sources['media'],1)]},
        {'claim_id':c['contradiction']['id'],'status':'contradicted',
         'reason':'原始公告为12.0百万美元，与15.0百万美元相矛盾',
         'span_ids':[_span(store,annual,1)]},
        {'claim_id':c['forecast']['id'],'status':'supported_for_scope',
         'reason':'公告确有该表述；归因预测只核出处与表述，不判未来是否实现',
         'span_ids':[_span(store,annual,3)]},
        {'claim_id':c['unknown']['id'],'status':'unknown',
         'reason':'公开材料未披露研发费用率口径，无法判断；不折算为真假','span_ids':[],'query_ids':[q]}]
    selected=[c[key]['id'] for key,_,_ in PLAN if key!='internal']
    return {'version_id':world['brief']['id'],'stage_id':world['stage']['stage_id'],
            'selection':{'claim_ids':selected,
                         'unselected':[{'claim_id':c['internal']['id'],'reason':'内部未公开信息不进搜索框，保留内部原件核对'}]},
            'candidates':candidates,'execution':{'status':'completed','summary':'8条公开主张核查完成'}}


def test_full_contract_passes_and_records_version_identity(tmp_path):
    world=checked(tmp_path);store=world['store'];c=world['claims']
    admitted=fact_check.submit_result(store,world['run']['id'],good_result(world),job_id=world['job']['id'])
    record=admitted['record']
    assert record['execution']['status']=='completed' and record['unchecked']==[]
    assert record['stage_id']==world['stage']['stage_id']
    assert record['as_of']=='2026-09-01'  # 未显式给出时取报告截至日
    assert record['snapshot']=={'model':'contract-test-model','search_provider':'native'}  # 任务入队时冻结的模型与搜索源
    by_claim={item['claim_id']:item for item in record['candidates']}
    binding=store.rows('SELECT * FROM claim_bindings WHERE claim_id=?',(c['unit']['id'],))[0]
    unit=by_claim[c['unit']['id']]
    assert unit['anchors']==[{'block_id':c['unit']['block'],'quote':c['unit']['quote'],
                              'quote_hash':digest(c['unit']['quote']),'block_hash':binding['block_hash']}]
    assert by_claim[c['unknown']['id']]['status']=='unknown' and by_claim[c['unknown']['id']]['span_ids']==[]  # unknown 原样保留
    assert record['selection']['unselected'][0]['reason'].startswith('内部未公开')
    saved=fact_check.get_record(store,record['id'])
    assert fact_check.covers(store,saved,world['brief']['id']) is True
    assert research_plan.frozen(store,world['run']['id'])['fact_check']['status']=='completed'
    actions=[json.loads(row['data'])['action'] for row in store.rows("SELECT data FROM events WHERE kind='fact_check' ORDER BY rowid")]
    assert 'result' in actions  # jobs/events 复用：接纳入库有事件可查


def test_contract_rejects_fake_ids_bare_urls_and_missing_coverage(tmp_path):
    world=checked(tmp_path);store=world['store'];c=world['claims']
    stray=store.add_source('其他任务来源','与本研究无关的样例内容。')
    stray_span=_span(store,stray,1)
    result=good_result(world)
    result['candidates'][0]['span_ids']=['https://example.com/announcement']  # 裸 URL
    result['candidates'][1]['span_ids']=['span_does_not_exist']  # 未登记 span
    result['candidates'][2]['span_ids']=[stray_span]  # 不属于本任务的来源
    result['candidates'][3]['status']='completed'  # 执行状态写进了候选
    result['candidates'][4]['query_ids']=['search_made_up']  # 编造查询
    result['candidates']=result['candidates'][:7]  # 丢掉 unknown 候选，制造 completed 缺覆盖
    result['candidates'].append({'claim_id':c['internal']['id'],'status':'supported_for_scope','reason':'越界候选'})
    unbound=create_claim(store,world['run']['id'],{'statement':'未绑定到正文的补充主张。','kind':'fact'})
    result['selection']['claim_ids'].append(unbound['id'])  # 不属于本版本
    result['selection']['unselected'][0].pop('reason')  # 未选主张缺原因
    with pytest.raises(fact_check.FactCheckError) as info:
        fact_check.submit_result(store,world['run']['id'],result,job_id=world['job']['id'])
    codes={error['code'] for error in info.value.errors}
    assert {'bare_url','unknown_span','span_source_not_in_run','status_invalid','unknown_query',
            'coverage_missing','candidate_outside_selection','claim_not_in_version',
            'unselected_reason_missing'} <= codes
    assert all(error['path'] and error['message'] for error in info.value.errors)
    assert research_plan.frozen(store,world['run']['id'])['fact_check']['status']=='active'  # 拒绝后阶段不动
    assert store.rows('SELECT * FROM fact_checks')==[]


def test_execution_and_fact_judgements_stay_separate(tmp_path):
    world=checked(tmp_path);store=world['store']
    result=good_result(world)
    result['execution']={'status':'supported_for_scope','summary':'把事实判断写进了执行状态'}
    with pytest.raises(fact_check.FactCheckError) as blocked:
        fact_check.check_fact_result(store,world['run']['id'],result)
    assert blocked.value.errors[0]['code']=='execution_status'
    assert '不判断主张真假' in blocked.value.errors[0]['message']


def test_cancelled_stage_refuses_late_result(tmp_path):
    world=checked(tmp_path);store=world['store']
    research_plan.finish_fact_check(store,world['run']['id'],status='cancelled',summary='任务已停止')
    with pytest.raises(research_plan.AdmissionError) as info:
        fact_check.submit_result(store,world['run']['id'],good_result(world))
    assert info.value.code=='fact_check_closed'
    assert store.rows('SELECT * FROM fact_checks')==[]  # 迟到结果不接纳、不留记录


def test_budget_exhausted_result_admitted_as_handoff(tmp_path):
    world=checked(tmp_path);store=world['store']
    result=good_result(world)
    kept={item['claim_id'] for item in result['candidates'][:3]}
    result['candidates']=result['candidates'][:3]
    result['execution']={'status':'budget_exhausted','summary':'搜索预算耗尽，保留已核证据交接'}
    admitted=fact_check.submit_result(store,world['run']['id'],result,job_id=world['job']['id'])
    record=admitted['record']
    assert record['execution']['status']=='budget_exhausted' and len(record['candidates'])==3
    assert set(record['unchecked'])==set(result['selection']['claim_ids'])-kept  # 未核主张按未核查交接，不折算判断
    assert all(item['status'] in fact_check.CANDIDATE_STATUSES for item in record['candidates'])
    assert research_plan.frozen(store,world['run']['id'])['fact_check']['status']=='budget_exhausted'


def test_revised_draft_needs_a_new_check_identity(tmp_path):
    world=checked(tmp_path);store=world['store']
    admitted=fact_check.submit_result(store,world['run']['id'],good_result(world))
    record=fact_check.get_record(store,admitted['record']['id'])
    doc=json.loads(world['brief']['editor_document'])
    doc['content'][0]['content'][0]['text']='公司2026财年上半年营业收入650万美元，约合人民币3,900万元。'
    revised=store.revise(world['brief']['id'],editor_document=doc)
    assert fact_check.covers(store,record,revised['id']) is False  # 改稿后旧核查不算新稿已核查
    assert fact_check.covers(store,record,world['brief']['id']) is True
    assert [row['id'] for row in fact_check.records_for(store,world['run']['id'])]==[record['id']]  # 旧核查仍可查
    with pytest.raises(research_plan.AdmissionError) as info:  # v1 一个阶段一次接纳；重查需新阶段
        fact_check.submit_result(store,world['run']['id'],{**good_result(world),'version_id':revised['id']})
    assert info.value.code=='fact_check_closed'
