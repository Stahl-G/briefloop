"""Saving feedback never starts paid learning without a confirmed upper bound (#727)."""
import http.client
import json
import threading

import pytest

from briefloop import learning
from briefloop.learning_budget import AUTHORIZATION_CODE, apply_settings_change, automatic_allowed, plan, state
from briefloop.store import Store, dump


def _feedback(store):
    source = store.add_source('Synthetic', 'Project A completed three files.')
    run = store.create_run({'title': 'Synthetic', 'objective': 'Summarize', 'allow_web': False}, [source['id']])
    brief = store.publish(run['id'], {'title': 'Synthetic', 'markdown': 'Project A completed three files.'})
    store.comment(brief['id'], 'Always state the unit.')
    return run, brief


def _age_feedback(store):
    with store.tx() as c:
        c.execute("UPDATE feedback SET created='2026-01-01T00:00:00+00:00'")


def test_new_workspaces_start_with_automatic_learning_off_and_a_stated_bound(tmp_path):
    store = Store(tmp_path)
    settings = store.settings()
    assert settings['auto_learn'] is False and settings['auto_learn_authorized_rounds'] is None
    assert state(settings) == 'off'
    bound = plan(settings)
    assert bound['cases'] == 3 and bound['trial_generations_per_round'] == 6
    # k=1, but an explicit human requirement can add a repair round.
    assert bound['rounds'] == 1 and bound['rounds_with_explicit_requirement'] == 2 and bound['max_trial_generations'] == 12
    assert bound['web'] is False and bound['price'] == 'unknown'
    assert store.snapshot()['learning_authorization'] == {'state': 'off', 'authorized_rounds': None, 'plan': bound}


def test_an_upgraded_workspace_keeps_feedback_but_waits_for_confirmation(tmp_path):
    store = Store(tmp_path)
    # Settings saved by an older version: the old default, with no authorization record.
    legacy = {key: value for key, value in store.settings().items() if key != 'auto_learn_authorized_rounds'}
    store.set_meta('settings', {**legacy, 'auto_learn': True})
    assert state(store.settings()) == 'needs_confirmation' and not automatic_allowed(store.settings())
    _feedback(store)
    _age_feedback(store)
    assert learning.enqueue_feedback(store, automatic=True)['status'] == 'not_authorized'
    assert store.rows("SELECT * FROM jobs WHERE kind='learn'") == []
    assert all(row['batch_id'] is None for row in store.rows('SELECT batch_id FROM feedback'))
    # Re-sending the old value is not a confirmation.
    assert state(apply_settings_change(store.settings(), {'auto_learn': True})) == 'needs_confirmation'


def test_settings_record_authorization_only_from_an_explicit_matching_confirmation():
    current = {'auto_learn': False, 'auto_learn_authorized_rounds': None, 'k': 2}
    with pytest.raises(ValueError) as refused:
        apply_settings_change(current, {'auto_learn': True})
    assert refused.value.code == AUTHORIZATION_CODE
    with pytest.raises(ValueError):
        apply_settings_change(current, {'auto_learn': True, 'confirm_learning_rounds': 1})
    with pytest.raises(ValueError):
        apply_settings_change(current, {'auto_learn': True, 'confirm_learning_rounds': True})
    # The record itself cannot be written directly.
    with pytest.raises(ValueError):
        apply_settings_change(current, {'auto_learn': True, 'auto_learn_authorized_rounds': 20})
    enabled = apply_settings_change(current, {'auto_learn': True, 'confirm_learning_rounds': 2})
    assert state(enabled) == 'authorized' and enabled['auto_learn_authorized_rounds'] == 2
    # A lower k stays inside the bound; a higher one pauses until confirmed again.
    assert state(apply_settings_change(enabled, {'k': 1})) == 'authorized'
    raised = apply_settings_change(enabled, {'k': 5})
    assert state(raised) == 'rounds_exceed' and not automatic_allowed(raised)
    assert state(apply_settings_change(raised, {'auto_learn': True, 'confirm_learning_rounds': 5})) == 'authorized'
    disabled = apply_settings_change(enabled, {'auto_learn': False})
    assert state(disabled) == 'off' and disabled['auto_learn_authorized_rounds'] is None


def _request(server, path, body, token):
    authority = f'127.0.0.1:{server.server_port}'
    connection = http.client.HTTPConnection('127.0.0.1', server.server_port)
    connection.request('POST', path, body=json.dumps(body), headers={'Host': authority, 'X-BriefLoop-Token': token, 'Origin': 'http://' + authority})
    response = connection.getresponse()
    value = response.status, json.loads(response.read())
    connection.close()
    return value


def test_web_entries_require_confirmation_before_any_learning_job(tmp_path):
    from briefloop.server import make_server, _close_service
    server = make_server(tmp_path / 'workspace', port=0, paused=True)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        connection = http.client.HTTPConnection('127.0.0.1', server.server_port)
        connection.request('GET', '/api/session', headers={'Host': f'127.0.0.1:{server.server_port}'})
        token = json.loads(connection.getresponse().read())['token']
        connection.close()
        store = server.store
        store.set_meta('settings', {**store.settings(), 'model': 'gpt-5.6-luna', 'model_selection_required': False})
        status, payload = _request(server, '/api/settings', {'auto_learn': True}, token)
        assert status == 400 and payload['code'] == AUTHORIZATION_CODE
        assert store.settings()['auto_learn'] is False
        status, payload = _request(server, '/api/settings', {'auto_learn': True, 'confirm_learning_rounds': 1}, token)
        assert status == 200 and payload['auto_learn_authorized_rounds'] == 1
        _feedback(store)
        status, payload = _request(server, '/api/learn', {}, token)
        assert status == 400 and payload['code'] == AUTHORIZATION_CODE
        assert store.rows("SELECT * FROM jobs WHERE kind='learn'") == []
        status, job = _request(server, '/api/learn', {'confirmed': True}, token)
        assert status == 200 and json.loads(job['payload'])['budget'] == plan(store.settings())
    finally:
        server.shutdown()
        thread.join()
        _close_service(server)


def test_authorized_automatic_learning_freezes_the_confirmed_bound(tmp_path):
    store = Store(tmp_path)
    store.set_meta('settings', {**store.settings(), 'model': 'gpt-5.6-luna', 'model_selection_required': False,
                                'auto_learn': True, 'auto_learn_authorized_rounds': 1})
    _feedback(store)
    _age_feedback(store)
    job = learning.enqueue_feedback(store, automatic=True)
    assert json.loads(job['payload'])['budget']['max_trial_generations'] == 12


def test_trial_generations_stop_at_the_frozen_cap_without_new_runs(tmp_path, monkeypatch):
    store = Store(tmp_path)
    source = store.add_source('Facts', 'Synthetic project A completed three files.')
    case = store.create_run({'title': 'Synthetic', 'objective': 'Summarize', 'allow_web': False}, [source['id']])
    job_id = 'job_learning_cap'
    root = store.root / 'jobs' / job_id
    for index in range(2):
        marker = root / f'round-{index}' / 'case' / 'baseline' / 'trial.json'
        marker.parent.mkdir(parents=True)
        marker.write_text('{}')
    budget = {**plan({**store.settings(), 'k': 1}), 'max_trial_generations': 2}
    job = {'id': job_id, '_runtime': object(), 'payload': dump({'k': 1, 'budget': budget, 'runtime': store.runtime_config(),
                                                                 'role_models': store.role_model_config(), 'agent_backend': 'codex'})}
    runs = len(store.rows('SELECT id FROM runs'))
    with pytest.raises(learning.LearningBudgetExhausted, match='试写上限'):
        learning._generate_trial(store, job, case, None, root / 'round-2' / 'case' / 'baseline', 'baseline')
    assert len(store.rows('SELECT id FROM runs')) == runs and store.rows('SELECT * FROM jobs') == []
    # Older batches without a frozen budget derive the same bound from their k.
    assert learning._budget({'k': 3}) == {'rounds_with_explicit_requirement': 3, 'max_trial_generations': 18}
