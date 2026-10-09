"""Saving feedback never starts paid learning without a confirmed upper bound (#727)."""
import json

import pytest

from briefloop import learning
from briefloop.learning_budget import (AUTHORIZATION_CODE, LearningAuthorizationRequired, apply_settings_change,
                                       authorization, automatic_allowed, plan, state, verify)
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


def _authorized(store, **extra):
    from briefloop.learning_budget import plan as current_plan
    settings = {**store.settings(), 'auto_learn': True, **extra}
    settings = {**settings, 'auto_learn_authorized_rounds': int(settings['k']),
                'auto_learn_authorized_plan': current_plan(settings)['scope_fingerprint']}
    store.set_meta('settings', settings)
    return store.settings()


def test_a_confirmation_binds_the_plan_the_user_saw(tmp_path):
    from briefloop.learning_budget import authorization
    store = Store(tmp_path)
    settings = _authorized(store, k=1)
    shown = plan(settings)
    # Another window raises the round count before this batch is queued.
    raised = {**settings, 'k': 20}
    with pytest.raises(ValueError) as refused:
        authorization(raised, 'manual', confirmed=shown['fingerprint'])
    assert refused.value.code == AUTHORIZATION_CODE
    assert plan(raised)['max_trial_generations'] == 120 and shown['max_trial_generations'] == 12
    assert authorization(settings, 'manual', confirmed=shown['fingerprint'])['max_trial_generations'] == 12
    # An automatic record authorizes rounds up to the confirmed count, so a lower
    # k stays authorized while a different host or model asks again.
    assert state({**settings, 'k': 1}) == 'authorized'
    assert state({**settings, 'agent_backend': 'opencode'}) == 'plan_changed'
    assert state({**settings, 'role_models': {'evaluator': {'model': 'separate-review-model'}}}) == 'plan_changed'
    assert not automatic_allowed({**settings, 'model': 'another-model'})


def test_the_plan_names_every_model_that_will_bill(tmp_path):
    store = Store(tmp_path)
    settings = {**store.settings(), 'model': 'selected-primary-model',
                'role_models': {'evaluator': {'model': 'separate-review-model'}}}
    store.set_meta('settings', settings)
    shown = plan(store.settings())
    assert shown['model'] == 'selected-primary-model'
    assert shown['role_models']['evaluator']['model'] == 'separate-review-model'
    assert shown['counts'] == 'trial_generations' and shown['price'] == 'unknown'


def test_legacy_automatic_scope_does_not_authorize_new_triage_invocation(tmp_path):
    import hashlib
    store = Store(tmp_path)
    settings = _authorized(store)
    bound = plan(settings)
    old_scope = {key: bound[key] for key in ('cases', 'backend', 'model', 'role_models', 'trial_generations_per_round')}
    old_fingerprint = hashlib.sha256(json.dumps(old_scope, ensure_ascii=False, sort_keys=True).encode()).hexdigest()
    legacy = {**settings, 'auto_learn_authorized_plan': old_fingerprint}
    assert state(legacy) == 'plan_changed' and not automatic_allowed(legacy)
    assert state({**settings, 'auto_learn_authorized_plan': None}) == 'needs_confirmation'
    store.set_meta('settings', legacy)
    _feedback(store)
    _age_feedback(store)
    assert learning.enqueue_feedback(store, automatic=True)['status'] == 'not_authorized'
    assert store.rows("SELECT * FROM jobs WHERE kind='learn'") == []
    assert all(row['batch_id'] is None for row in store.rows('SELECT batch_id FROM feedback'))
    confirmed = apply_settings_change(legacy, {'confirm_learning_rounds': settings['k'], 'confirm_plan': bound['fingerprint']})
    record = authorization(confirmed, 'automatic')
    assert record['triage_turns_per_batch'] == bound['triage_turns_per_batch'] == 1
    assert verify(record, bound) == record


def test_frozen_budget_must_match_the_record_and_include_triage(tmp_path):
    store = Store(tmp_path)
    bound = plan(store.settings())
    record = authorization(store.settings(), 'manual', confirmed=bound['fingerprint'])
    assert verify(record, bound) == record
    old_record = {key: value for key, value in record.items() if key != 'triage_turns_per_batch'}
    with pytest.raises(LearningAuthorizationRequired, match='改动分类调用'):
        verify(old_record)
    for changed in ({key: value for key, value in bound.items() if key != 'triage_turns_per_batch'},
                    {**bound, 'triage_turns_per_batch': 2}, {**bound, 'model': 'unconfirmed-model'},
                    {**bound, 'scope_fingerprint': 'old-scope'}):
        with pytest.raises(LearningAuthorizationRequired, match='冻结调用预算'):
            verify(record, changed)


def test_legacy_frozen_batch_pauses_before_calls_and_keeps_feedback(tmp_path):
    store = Store(tmp_path)
    store.update_settings({'model': 'test-model'})
    _feedback(store)
    bound = plan(store.settings())
    job = learning.enqueue_feedback(store, confirmed_plan=bound['fingerprint'])
    payload = json.loads(job['payload'])
    payload['authorization'].pop('triage_turns_per_batch')
    payload['budget'].pop('triage_turns_per_batch')
    with store.tx() as c:
        c.execute('UPDATE jobs SET payload=? WHERE id=?', (dump(payload), job['id']))
    class NoCalls:
        def execute(self, *args, **kwargs):
            raise AssertionError('An unconfirmed batch must not invoke the runtime')
    with pytest.raises(InterruptedError, match='改动分类调用'):
        learning.learn(store, NoCalls(), store.one('jobs', job['id']))
    assert all(row['batch_id'] == job['id'] for row in store.rows('SELECT batch_id FROM feedback'))
    assert store.meta('last_study') is None
