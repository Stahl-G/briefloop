import json

import pytest

from briefloop.next_report import prepare
from briefloop.store import Store


def prior(store):
    source = store.add_source('Previous-period source', 'Last period: 12 deliveries.')
    run = store.create_run({'title': 'September review', 'objective': 'Review delivery risks',
                            'period': '2026-09', 'allow_web': False, 'fact_check': False,
                            'key_questions': ['Which orders need follow-up?'],
                            'manual_sections': ['Decision'], 'writing_preferences': ['Keep uncertainties visible']},
                           [source['id']])
    brief = store.publish(run['id'], {'title': 'September review', 'markdown': 'Last period: 12 deliveries.'})
    return source, run, brief


def test_preview_reuses_contract_without_sources_jobs_or_frozen_checks(tmp_path):
    store = Store(tmp_path)
    source, run, brief = prior(store)
    snapshot = store.one('runs', run['id'])
    remembered = store.meta('requirements')
    result = prepare(store, brief['id'])
    req = result['requirements']
    assert req['key_questions'] == ['Which orders need follow-up?']
    assert req['manual_sections'] == ['Decision']
    assert req['writing_preferences'] == ['Keep uncertainties visible']
    assert req['title'] == req['period'] == req['period_start'] == req['period_end'] == ''
    assert not req['allow_web'] and not req['fact_check']
    assert result['source_ids'] == req['reference_source_ids'] == []
    assert not {'time_context', 'workflow_snapshot', 'reader_profile', 'research_budget'} & req.keys()
    assert result['previous']['hash'] == brief['hash']
    assert store.one('runs', run['id']) == snapshot
    assert store.meta('requirements') == remembered
    assert not store.rows('SELECT * FROM jobs')
    assert len(store.rows('SELECT * FROM sources')) == 1


def test_new_period_requires_confirmation_and_keeps_exact_origin(tmp_path):
    store = Store(tmp_path)
    _, old_run, brief = prior(store)
    new_source = store.add_source('Current material', 'This period: 14 deliveries.')
    req = prepare(store, brief['id'])['requirements'] | {'title': 'October review'}
    with pytest.raises(ValueError, match='本期时间范围'):
        store.create_run(req, [new_source['id']])
    with pytest.raises(ValueError, match='往期报告内容已变化'):
        store.create_run(req | {'period': '2026-10', 'previous_report_hash': '0' * 64}, [new_source['id']])
    run = store.create_run(req | {'period': '2026-10'}, [new_source['id']])
    saved = json.loads(run['requirements'])
    assert saved['previous_report_version_id'] == brief['id']
    assert saved['previous_report_hash'] == brief['hash']
    assert store.source_ids(run['id']) == [new_source['id']]
    assert json.loads(store.one('runs', old_run['id'])['requirements'])['period'] == '2026-09'
    remembered = store.meta('requirements')
    assert 'previous_report_version_id' not in remembered
    assert 'previous_report_hash' not in remembered


def test_archived_reader_is_not_reactivated_and_legacy_shape_stays_clean(tmp_path):
    from briefloop.readers import archive, save
    store = Store(tmp_path)
    source = store.add_source('Source', 'Synthetic facts.')
    reader = save(store, name='Procurement', decisions='Prioritize follow-up')
    run = store.create_run({'title': 'Review', 'objective': 'Risks', 'reader_id': reader['id']}, [source['id']])
    brief = store.publish(run['id'], {'title': 'Review', 'markdown': 'Facts.'})
    assert prepare(store, brief['id'])['requirements']['reader_id'] == reader['id']
    archive(store, reader['id'])
    result = prepare(store, brief['id'])
    assert result['reader'] is None and 'reader_id' not in result['requirements']
    legacy = json.loads(store.one('runs', run['id'])['requirements'])
    assert 'previous_report_version_id' not in legacy and 'previous_report_hash' not in legacy


def test_conversation_context_is_bound_to_saved_contract_and_excludes_old_facts(tmp_path):
    from briefloop.next_report import conversation_request
    store = Store(tmp_path)
    source, run, brief = prior(store)
    prompt = conversation_request(store, '本期十月，关注交付变化',
                                  {'version_id': brief['id'], 'hash': brief['hash'],
                                   'requirements': {'allow_web': True}, 'title': 'ignored browser title'})
    assert brief['id'] in prompt and brief['hash'] in prompt
    assert 'Review delivery risks' in prompt
    assert 'Last period: 12 deliveries.' not in prompt and source['id'] not in prompt
    assert 'ignored browser title' not in prompt
    assert not store.rows('SELECT * FROM jobs')
    with pytest.raises(ValueError, match='往期报告内容已变化'):
        conversation_request(store, 'next', {'version_id': brief['id'], 'hash': 'wrong'})
