"""A model switch creates a new task while preserving frozen failure evidence."""
import json
import threading
from concurrent.futures import ThreadPoolExecutor

import pytest
from wikiskill import feedback_loop, product

from briefloop.learning import enqueue_feedback, learn
from briefloop.learning_budget import plan as learning_plan
from briefloop.review import enqueue_review, run_review
from briefloop.runtime import Worker
from briefloop.store import Store


def workspace(tmp_path):
    store=Store(tmp_path)
    store.set_meta('settings',{**store.settings(),'auto_learn':False,'company_context_enabled':False})
    source=store.add_source('Synthetic','Revenue was USD 12 million.')
    run=store.create_run({'title':'Synthetic','objective':'Explain revenue'},[source['id']])
    brief=store.publish(run['id'],{'title':'Synthetic','markdown':'Revenue was USD 12 million.'})
    return store,brief


def switch_model(store):
    store.set_meta('settings',{**store.settings(),'agent_backend':'opencode',
        'model':'deepseek/deepseek-v4.1-flash','role_models':{},'k':3,'skill_targets':['analyst']})


def failed_learning(store,brief):
    fid=store.comment(brief['id'],'Keep the currency explicit')['id']
    job=enqueue_feedback(store,confirmed_plan=learning_plan(store.settings())['fingerprint'])
    store.update_job(job['id'],'interrupted',error='Synthetic interrupted maintainer')
    return store.one('jobs',job['id']),fid


def test_learning_retry_needs_a_new_confirmation_and_preserves_old_batch(tmp_path):
    from briefloop.learning_budget import LearningAuthorizationRequired,verify
    store, brief = workspace(tmp_path)
    store.update_settings({'model':'test-runtime'})
    original, fid = failed_learning(store, brief)
    frozen = original['payload']
    switch_model(store)
    worker = object.__new__(Worker)
    worker.store=store;worker._claim_lock=threading.RLock();worker.current=None;worker._review_jobs={}
    with pytest.raises(LearningAuthorizationRequired):
        worker.retry_with_current_model(original['id'])
    assert store.one('jobs',original['id'])['payload'] == frozen
    assert store.rows('SELECT batch_id FROM feedback WHERE id=?',(fid,))[0]['batch_id'] == original['id']
    confirmed = learning_plan(store.settings())['fingerprint']
    retry = worker.retry_with_current_model(original['id'],confirmed_plan=confirmed)
    value = json.loads(retry['payload'])
    verify(value['authorization'],value['budget'])
    assert value['retry_of_job_id'] == original['id'] and value['k'] == 3
    assert value['runtime']['model'] == store.settings()['model']
    assert store.one('jobs',original['id'])['payload'] == frozen
    assert store.rows('SELECT batch_id FROM feedback WHERE id=?',(fid,))[0]['batch_id'] == retry['id']
