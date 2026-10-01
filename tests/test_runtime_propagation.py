"""User runtime choices survive chat, retry, and frozen Reviewer admission."""
import json
import threading

import pytest

from briefloop.chat_tools import chat_instructions, workspace_action
from briefloop.review import enqueue_review, review_job_payload
from briefloop.review_capability import ReviewBackendUnsupported
from briefloop.runtime import Worker, stage_job
from briefloop.store import Store


def report(store):
    store.update_settings({'model':'main-original','company_context_enabled':False})
    source=store.add_source('Synthetic','Revenue was USD 12 million.')
    run=store.create_run({'title':'Synthetic','objective':'Explain revenue'},[source['id']])
    return store.publish(run['id'],{'title':'Synthetic','markdown':'Revenue was USD 12 million.'})


def retry_worker(store):
    worker=object.__new__(Worker)
    worker.store=store;worker._claim_lock=threading.RLock();worker.current=None;worker._review_jobs={}
    return worker


@pytest.mark.parametrize('main_backend',['codex','claude'])
def test_review_retry_uses_current_independent_reviewer(tmp_path,main_backend):
    store=Store(tmp_path);brief=report(store)
    original=enqueue_review(store,brief['id'])
    store.update_job(original['id'],'failed',error='Synthetic failure')
    store.update_settings({'agent_backend':main_backend,'model':'main-current',
                           'review_runtime':{'backend':'codex','model':'reviewer-current','reasoning_effort':'high'}})
    worker=retry_worker(store)
    retried=worker.retry_with_current_model(original['id'])
    payload=json.loads(retried['payload'])
    assert payload['agent_backend']=='codex'
    assert json.loads(stage_job(store,retried,'evaluator')['payload'])['runtime']=={
        'model':'reviewer-current','reasoning_effort':'high'}
    assert payload['retry_of_job_id']==original['id']
    assert store.one('jobs',original['id'])['payload']==original['payload']
    assert worker.retry_with_current_model(original['id'])['id']==retried['id']


@pytest.mark.real_review_capabilities
def test_review_retry_still_refuses_unsupported_strict_reviewer(tmp_path):
    store=Store(tmp_path);brief=report(store)
    original=enqueue_review(store,brief['id'])
    store.update_job(original['id'],'failed',error='Synthetic failure')
    store.update_settings({'agent_backend':'briefloop-native','model':'synthetic/main',
                           'review_mode':'strict','review_runtime':{'backend':'codex','model':'reviewer-current'}})
    with pytest.raises(ReviewBackendUnsupported):
        retry_worker(store).retry_with_current_model(original['id'])
    assert len(store.rows('SELECT id FROM jobs'))==1


def test_legacy_frozen_review_does_not_read_current_route_or_role_defaults(tmp_path):
    store=Store(tmp_path);brief=report(store)
    legacy={'runtime':{'model':'main-original','reasoning_effort':'low'}}
    store.update_settings({'agent_backend':'claude','model':'main-current','review_mode':'strict',
        'role_models':{'evaluator':{'model':'current-evaluator'}},
        'review_runtime':{'backend':'briefloop-native','model':'synthetic/current-reviewer'}})
    job=enqueue_review(store,brief['id'],payload=legacy)
    payload=json.loads(job['payload'])
    assert payload['agent_backend']=='codex' and payload['review_mode']=='standard'
    assert json.loads(stage_job(store,job,'evaluator')['payload'])['runtime']==legacy['runtime']
    assert legacy=={'runtime':{'model':'main-original','reasoning_effort':'low'}}
    # An explicit frozen independent route remains authoritative.
    selected=review_job_payload(store,{**legacy,'review_mode':'strict',
        'review_runtime':{'backend':'briefloop-native','model':'synthetic/frozen-reviewer'}})
    assert selected['agent_backend']=='briefloop-native' and selected['review_mode']=='strict'
    assert selected['runtime']=={'model':'synthetic/frozen-reviewer'}


@pytest.mark.parametrize(('backend','runtime','expected'),[
    ('codex',{'model':'chosen','effort':'high','service_tier':None},
     {'model':'chosen','reasoning_effort':'high'}),
    ('opencode',{'model':'provider/chosen','variant':'high'},
     {'model':'provider/chosen','model_variant':'high'}),
    ('briefloop-native',{'model':'provider/chosen','variant':None},
     {'model':'provider/chosen'}),
])
def test_first_chat_choice_preserves_effort_and_explicit_defaults(tmp_path,backend,runtime,expected):
    store=Store(tmp_path)
    store.update_settings({'model_selection_required':True,'model_variant':'old-variant','service_tier':'fast'})
    store.confirm_runtime_choice(backend,{'backend':backend,**runtime})
    assert store.settings()['model_selection_required'] is False
    assert store.runtime_config()==expected


@pytest.mark.parametrize(('workspace_tier','chat_tier'),[
    ('default','fast'),('fast','default'),('fast',None),
])
def test_chat_report_request_preserves_service_tier(tmp_path,workspace_tier,chat_tier):
    store=Store(tmp_path);brief=report(store)
    store.update_settings({'service_tier':workspace_tier})
    source_id=store.rows('SELECT id FROM sources')[0]['id']
    runtime={'backend':'codex','model':'chat-model','effort':'high','service_tier':chat_tier}
    line=next(line for line in chat_instructions(store,runtime).splitlines()
              if line.startswith('- {"action":"generate"'))
    advertised=json.JSONDecoder().raw_decode(line[2:])[0]
    admitted=workspace_action(store,{'action':'generate',
        'requirements':{'title':'Synthetic','objective':'Explain revenue','allow_web':False},
        'source_ids':[source_id],'runtime':advertised['runtime']})
    frozen=json.loads(store.one('jobs',admitted['job_id'])['payload'])['runtime']
    assert frozen.get('service_tier')==chat_tier
    assert frozen['model']=='chat-model' and frozen['reasoning_effort']=='high'
