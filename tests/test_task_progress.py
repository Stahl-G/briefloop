import json
from briefloop.store import Store
from briefloop.task_progress import summary


def test_fast_report_card_does_not_hide_provider_retry_stage(tmp_path):
    from briefloop.store import Store
    from briefloop.task_progress import summary
    store=Store(tmp_path)
    store.set_meta('settings',{**store.settings(),'model':'synthetic'})
    source=store.add_source('Synthetic','Ordered 30, received 26.')
    run=store.create_run({'title':'Synthetic','objective':'Read locally','completion_mode':'fast'},[source['id']])
    job=store.enqueue('generate',{'run_id':run['id']})
    store.update_job(job['id'],'running')
    store.event(job['id'],'runtime_progress',{'stage':'模型服务正在重试','runtime_notice':True,'last_activity':'2026-09-30T09:49:39+00:00'})
    assert summary(store,job['id'])['stage']=='模型服务正在重试'


def test_process_projection_exposes_only_unresolved_source_conflicts(tmp_path):
    from briefloop.conflicts import create
    store = Store(tmp_path)
    store.set_meta('settings', {**store.settings(), 'model': 'synthetic'})
    source = store.add_source('Synthetic public statement', 'Revenue 12.')
    run = store.create_run({'title': 'Synthetic', 'objective': 'Read locally'}, [source['id']])
    job = store.enqueue('generate', {'run_id': run['id']})
    conflict = create(store, source_ids=[source['id']], description='本期与上期口径不同', run_id=run['id'])
    assert summary(store, job['id'])['conflicts'] == [{'text': '本期与上期口径不同'}]
    with store.tx() as connection:
        connection.execute("UPDATE conflicts SET status='resolved' WHERE id=?", (conflict['id'],))
    assert summary(store, job['id'])['conflicts'] == []


def test_learning_progress_follows_inline_trial_then_comparison(tmp_path):
    store=Store(tmp_path)
    store.set_meta('settings',{**store.settings(),'model':'synthetic'})
    source=store.add_source('Synthetic','Target 10, actual 8.')
    run=store.create_run({'title':'Synthetic','objective':'Read locally'},[source['id']])
    owner=store.enqueue('learn',{'k':1})
    store.update_job(owner['id'],'running')
    store.event(owner['id'],'runtime_progress',{'stage':'Proposer 正在提出技能改进','agents':[{'id':'old','role':'Proposer','status':'running'}]})
    store.event(owner['id'],'learning_progress',{'phase':'validation','round':1,'k':1})
    pending=summary(store,owner['id'])
    assert pending['stage']=='正在试写并比较候选'
    assert pending['agents']==[] and pending['session_id'] is None
    trial=store.enqueue('generate',{'run_id':run['id'],'inline_owner_job_id':owner['id']})
    store.update_job(trial['id'],'running')
    store.event(trial['id'],'runtime_started',{'session_id':'trial-session'})
    store.event(trial['id'],'runtime_progress',{'stage':'Analyst 正在撰写简报','agents':[{'id':'writer','role':'Analyst','status':'running'}]})
    live=summary(store,owner['id'])
    assert live['stage']=='验证候选 · Analyst 正在撰写简报'
    assert live['session_id']=='trial-session' and live['agents'][0]['id']=='writer'
    store.update_job(trial['id'],'complete')
    assert summary(store,owner['id'])['stage']=='正在试写并比较候选'
    store.event(owner['id'],'runtime_started',{'session_id':'comparison-session'})
    store.event(owner['id'],'runtime_progress',{'stage':'Evaluator 正在比较新旧稿件'})
    compared=summary(store,owner['id'])
    assert compared['stage']=='Evaluator 正在比较新旧稿件'
    assert compared['session_id']=='comparison-session'
    store.update_job(owner['id'],'complete')
    assert summary(store,owner['id'])['stage']=='任务已结束'
