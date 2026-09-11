import json
import pytest
from briefloop.models import Requirements, BriefDraft
from briefloop.industry_data import prepare_report_data


def test_industry_custom_length_and_report_date():
    req=Requirements(title='定期行业报告',objective='跟踪供需变化',report_profile='industry_periodic',industry='材料',report_date='2026-09-10',reference_source_ids=['style'])
    assert (req.target_words,req.max_words)==(5000,5500)
    assert Requirements(**{**req.model_dump(),'target_words':900,'max_words':1200}).target_words==900
    assert Requirements(title='简报',objective='摘要').target_words==1500
    with pytest.raises(ValueError):Requirements(title='报告',objective='摘要',report_date='2026-02-30')


def test_changes_are_derived_and_incomparable_values_remain_gaps():
    base=dict(metric='指标',unit='%',current=4.5,current_date='2026-09-08',previous=4,previous_date='2026-09-01',source_id='s1',previous_unit='%',previous_tax_basis='',previous_category='actual',comparable=True)
    prepared=prepare_report_data({'records':[{**base,'comparison':'bp'},{**base,'comparison':'pct'},{**base,'comparison':'pct','previous':0},{**base,'comparison':'difference','previous_unit':'USD'}]})
    assert [r['change'] for r in prepared['calculations']]==[50,12.5,None,None]
    assert len(prepared['gaps'])==2
    assert '[@s1]' in prepared['markdown']
    draft=BriefDraft(title='报告',markdown='正文',report_data={'records':[base]})
    assert draft.model_dump(mode='json')['report_data']['records'][0]['current_date']=='2026-09-08'
    with pytest.raises(ValueError):prepare_report_data({'records':[{**base,'derived':100}]})
    with pytest.raises(ValueError):prepare_report_data({'records':[{**base,'current':float('nan')}]})


def test_runtime_separates_reference_and_evaluator_recomputes(tmp_path):
    from briefloop.store import Store
    from briefloop.runtime import generation_prompt,assessment_prompt
    store=Store(tmp_path/'workspace')
    evidence=store.add_source('当期数据','本期值 12，上期 10。')
    reference=store.add_source('旧版风格','仅供结构参考')
    run=store.create_run(dict(title='行业报告',objective='跟踪指标',report_profile='industry_periodic',reference_source_ids=[reference['id']]),[evidence['id']])
    folder=store.root/'jobs'/'local-check';folder.mkdir()
    generation_prompt(store,run,folder)
    pack=json.loads((folder/'input.json').read_text())
    assert [s['id'] for s in pack['reference_sources']]==[reference['id']]
    assert [s['id'] for s in pack['sources']]==[evidence['id']]
    assert pack['report_profile']['id']=='industry_periodic'
    brief=store.publish(run['id'],dict(title='报告',markdown='指标12',report_data={'records':[dict(metric='指标',unit='台',current=12,current_date='2026-09-08',source_id=evidence['id'])]}))
    assessment_prompt(store,brief,folder)
    pack=json.loads((folder/'input.json').read_text())
    assert pack['report_data']['records'][0]['current']==12
    assert len(pack['report_data']['calculations'])==1
    assert 'retrieval_skill' not in pack


def test_forecast_vintage_and_reverse_comparison_are_visible_gaps():
    row=dict(metric='需求',unit='台',current=12,current_date='2027-12-31',source_id='s1',category='forecast')
    missing=prepare_report_data({'records':[row]})
    assert 'as_of' in missing['gaps'][0]
    assert '预测' in missing['markdown']
    dated=prepare_report_data({'records':[{**row,'as_of':'2026-09-08'}]})
    assert not dated['gaps'] and '2026-09-08' in dated['markdown']
    reversed_dates=prepare_report_data({'records':[{**row,'as_of':'2026-09-08','previous':10,'previous_date':'2028-12-31','comparison':'pct','comparable':True,'previous_unit':'台','previous_tax_basis':'','previous_category':'forecast'}]})
    assert reversed_dates['calculations'][0]['change'] is None
    assert '晚于' in reversed_dates['gaps'][0]


def test_missing_current_date_becomes_a_gap_not_a_draft_failure():
    # A model that emits only as_of must not fail the entire report: the record
    # falls into the data gaps and the draft still validates.
    record={'metric':'指标','unit':'%','current':4.5,'as_of':'2026-09-08','source_id':'s1'}
    prepared=prepare_report_data({'records':[record]})
    assert prepared['gaps'] and 'current_date' in prepared['gaps'][0]
    assert prepared['calculations'][0]['change'] is None
    assert '未注明' in prepared['markdown']
    draft=BriefDraft(title='报告',markdown='正文',report_data={'records':[record]})
    assert draft.report_data.records[0].current_date is None
