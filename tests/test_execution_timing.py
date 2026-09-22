import json

from briefloop.execution_timing import policy
from briefloop.store import Store


def test_report_time_choices_are_frozen_not_changed_by_workspace_defaults(tmp_path):
    store = Store(tmp_path)
    store.set_meta('settings', {**store.settings(), 'timeout_minutes': 10})
    run = store.create_run({'title': '周报', 'objective': '回答本期变化', 'allow_web': True}, [])
    job = store.enqueue('generate', {'run_id': run['id']})
    # An explicit hard limit on a later task must not stop the existing report.
    store.set_meta('settings', {**store.settings(), 'timeout_minutes': 30, 'hard_timeout_minutes': 1})
    assert policy(store, job_id=job['id']) == {'target_minutes': 10, 'hard_timeout_minutes': 0}
    next_run = store.create_run({'title': '下期', 'objective': '变化', 'allow_web': True}, [])
    saved = json.loads(next_run['requirements'])
    assert (saved['target_minutes'], saved['hard_timeout_minutes']) == (30, 1)


def test_explicit_task_choice_overrides_defaults_and_old_run_has_no_new_limit(tmp_path):
    store = Store(tmp_path)
    store.set_meta('settings', {**store.settings(), 'hard_timeout_minutes': 1})
    run = store.create_run({'title': 'T', 'objective': 'o', 'allow_web': True,
                           'target_minutes': 0, 'hard_timeout_minutes': 0}, [])
    job = store.enqueue('generate', {'run_id': run['id']})
    assert policy(store, job_id=job['id']) == {'target_minutes': 0, 'hard_timeout_minutes': 0}
    req = json.loads(run['requirements'])
    del req['target_minutes'], req['hard_timeout_minutes']
    with store.tx() as c:
        c.execute('UPDATE runs SET requirements=? WHERE id=?', (json.dumps(req), run['id']))
    assert policy(store, job_id=job['id']) == {'target_minutes': None, 'hard_timeout_minutes': 0}
