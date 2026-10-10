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
