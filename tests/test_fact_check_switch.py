"""Fact-check defaults, explicit choices and offline linkage."""
import json

import pytest

from briefloop.store import Store


def _requirements(**extra):
    base = {'title': 'T', 'objective': 'o', 'allow_web': True}
    base.update(extra)
    return base


def test_fact_check_defaults_on_and_preserves_explicit_choices(tmp_path):
    store = Store(tmp_path)
    source = store.add_source('Synthetic', 'Revenue was USD 12 million.')
    assert store.settings()['fact_checker'] is True
    assert Store(tmp_path / 'second').settings()['fact_checker'] is True
    run = store.create_run(_requirements(), [source['id']])
    assert json.loads(run['requirements'])['fact_check'] is True
    store.set_meta('settings', {**store.settings(), 'fact_checker': False})
    assert Store(tmp_path).settings()['fact_checker'] is False
    run = store.create_run(_requirements(), [source['id']])
    assert json.loads(run['requirements'])['fact_check'] is False
    store.set_meta('settings', {**store.settings(), 'fact_checker': True})
    run = store.create_run(_requirements(), [source['id']])
    assert json.loads(run['requirements'])['fact_check'] is True  # workspace default
    run = store.create_run(_requirements(fact_check=False), [source['id']])
    assert json.loads(run['requirements'])['fact_check'] is False  # task overrides


def test_default_check_does_not_enable_offline_or_fast_reports(tmp_path):
    from briefloop.store import OfflineFactCheck
    store=Store(tmp_path)
    source=store.add_source('Synthetic', 'Only local supplied material.')
    for extra in ({'allow_web':False}, {'completion_mode':'fast'}, {'completion_mode':'fast_web'}):
        run=store.create_run(_requirements(**extra), [source['id']])
        assert json.loads(run['requirements'])['fact_check'] is False
    with pytest.raises(OfflineFactCheck):
        store.create_run(_requirements(allow_web=False, fact_check=True), [source['id']])


def test_new_request_policy_does_not_reinterpret_saved_runs(tmp_path):
    from briefloop.models import Requirements
    store = Store(tmp_path)
    source = store.add_source('Local note', 'The local source is retained.')
    requested = _requirements(allow_web=False, fact_check=False)
    direct = store.create_run(requested, [source['id']])
    standard = store.create_run({**requested, 'completion_mode': 'standard'}, [source['id']])
    assert json.loads(direct['requirements'])['completion_mode'] == 'direct'
    assert json.loads(standard['requirements'])['completion_mode'] == 'standard'
    historical = json.loads(standard['requirements'])
    historical.pop('completion_mode')
    with store.tx() as connection:
        connection.execute('UPDATE runs SET requirements=? WHERE id=?', (json.dumps(historical), standard['id']))
    reopened = Store(tmp_path)
    saved = json.loads(reopened.one('runs', standard['id'])['requirements'])
    assert Requirements.model_validate(saved).completion_mode == 'standard'
    clone = reopened._create_learning_run(standard['id'], [source['id']])
    assert json.loads(clone['requirements'])['completion_mode'] == 'standard'
    assert reopened.source_ids(direct['id']) == [source['id']]
    reference = store.add_source('Style reference', 'A short reference style.')
    with_reference = store.create_run({**requested, 'reference_source_ids': [reference['id']]}, [source['id']])
    quarter = store.create_run({**requested, 'workflow_id': 'business_report', 'workflow_variant': 'industry_quarterly'}, [source['id']])
    assert json.loads(with_reference['requirements'])['completion_mode'] == 'standard'
    assert json.loads(quarter['requirements'])['completion_mode'] == 'standard'
    with pytest.raises(ValueError, match='风格参考'):
        store.create_run({**requested, 'completion_mode': 'direct', 'reference_source_ids': [reference['id']]}, [source['id']])
