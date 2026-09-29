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


