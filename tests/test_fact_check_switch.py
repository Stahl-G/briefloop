"""Phase A fact-check switch: default off, persistence, offline linkage."""
import http.client
import json
import threading

import pytest

from briefloop.store import Store


def test_new_reports_allow_web_by_default_but_explicit_offline_stays_off():
    from briefloop.models import Requirements
    assert Requirements(title='T', objective='o').allow_web is True
    assert Requirements(title='T', objective='o', allow_web=False).allow_web is False


def _requirements(**extra):
    base = {'title': 'T', 'objective': 'o', 'allow_web': True}
    base.update(extra)
    return base


def test_fact_check_defaults_off_and_follows_workspace_default(tmp_path):
    store = Store(tmp_path)
    source = store.add_source('Synthetic', 'Revenue was USD 12 million.')
    assert store.settings()['fact_checker'] is False
    assert Store(tmp_path / 'second').settings()['fact_checker'] is False
    run = store.create_run(_requirements(), [source['id']])
    assert json.loads(run['requirements'])['fact_check'] is False
    store.set_meta('settings', {**store.settings(), 'fact_checker': True})
    run = store.create_run(_requirements(), [source['id']])
    assert json.loads(run['requirements'])['fact_check'] is True  # workspace default
    run = store.create_run(_requirements(fact_check=False), [source['id']])
    assert json.loads(run['requirements'])['fact_check'] is False  # task overrides


