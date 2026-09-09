"""Requirement handoff checks, not automated judgments of prose quality."""
from copy import deepcopy
import pytest
from briefloop.deliverable_spec import (
    instructions, reader_contract_schema, resolve, validate_reader_contract,
)


def example():
    req = {'title': 'Internal report', 'objective': '说明客户交付的变化。区分计划与实际，不把计划当成交付。',
           'writing_mode': 'internal_report', 'audience': '管理层',
           'key_questions': ['客户认证进展如何？'], 'writing_preferences': ['自然段落，不写研究过程清单'],
           'manual_sections': ['融资进度']}
    spec = resolve(req)
    by_kind = {item['kind']: item for item in spec['requirement_items']}
    clauses = [
        {'requirement_id': by_kind['objective']['requirement_id'], 'kind': 'reader_content',
         'source_quote': '说明客户交付的变化。', 'instruction': '说明本期交付相对上期的变化及经营影响。'},
        {'requirement_id': by_kind['objective']['requirement_id'], 'kind': 'research_method',
         'source_quote': '区分计划与实际，不把计划当成交付。', 'instruction': '核对已交付和计划的阶段，保留原文状态。'},
    ]
    for field, kind in [('question', 'reader_content'), ('manual', 'manual_assignment'), ('writing', 'writing_preference')]:
        item = by_kind[field]
        clauses.append({'requirement_id': item['requirement_id'], 'kind': kind,
                        'source_quote': item['text'], 'instruction': item['text']})
    return req, spec, {'source_fingerprint': reader_contract_schema(spec)['properties']['source_fingerprint']['const'], 'clauses': clauses}


def test_contract_classifies_method_without_turning_it_into_reader_content():
    req, spec, value = example()
    contract = validate_reader_contract(spec, value)
    compiled = resolve(req, reader_contract=contract)
    assert compiled['objective'] == req['objective']
    assert [item['text'] for item in compiled['requirement_items']] == [item['text'] for item in spec['requirement_items']]
    assert {c['kind'] for c in contract['clauses']} == {'reader_content', 'research_method', 'writing_preference', 'manual_assignment'}
    value['clauses'][0]['instruction'] = 'mutated later'
    assert compiled['reader_contract']['clauses'][0]['instruction'] != 'mutated later'
    assert next(x for x in compiled['requirement_items'] if x['kind'] == 'manual')['mode'] == 'manual'
    # The manual slot remains a user assignment, not an unanswered research question.
    assert compiled['manual_sections'] == ['融资进度']
    # Every role must receive this exact requirement identity, regardless of wording.
    for role in ('orchestrator', 'scout', 'analyst', 'evaluator', 'reviewer', 'revision'):
        assert value['source_fingerprint'] in instructions(compiled, role)


def test_contract_rejects_changed_requirements_forged_quotes_and_silent_omissions():
    req, spec, value = example()
    changed = resolve({**req, 'objective': req['objective'] + '另查价格。'})
    with pytest.raises(ValueError, match='要求版本'):
        validate_reader_contract(changed, value)
    forged = deepcopy(value)
    forged['clauses'][1]['source_quote'] = '用户已同意省略交付问题'
    with pytest.raises(ValueError, match='逐字存在'):
        validate_reader_contract(spec, forged)
    omitted = deepcopy(value)
    omitted['clauses'].pop()
    with pytest.raises(ValueError, match='遗漏原始要求'):
        validate_reader_contract(spec, omitted)
    altered = deepcopy(value)
    altered['clauses'][2]['kind'] = 'research_method'
    with pytest.raises(ValueError, match='不可被重新分类'):
        validate_reader_contract(spec, altered)
    downgraded = deepcopy(value)
    downgraded['clauses'][2]['mode'] = 'optional'
    with pytest.raises(ValueError, match='字段无效'):
        validate_reader_contract(spec, downgraded)


def test_publication_rejects_unbound_contract(tmp_path):
    from briefloop.store import Store
    store=Store(tmp_path);source=store.add_source('Source','Evidence')
    run=store.create_run({'title':'Report','objective':'Explain changes'},[source['id']])
    with pytest.raises(ValueError,match='产物约定'):
        store.publish(run['id'],{'title':'Report','markdown':'Draft','reader_contract':{'source_fingerprint':'wrong','clauses':[]}})
    assert not store.rows('SELECT id FROM briefs')
