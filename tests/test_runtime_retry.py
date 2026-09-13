"""A model switch creates a new task while preserving frozen failure evidence."""
import json
import threading
from concurrent.futures import ThreadPoolExecutor

import pytest
from wikiskill import feedback_loop, product

from briefloop.learning import enqueue_feedback, learn
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
    job=enqueue_feedback(store)
    store.update_job(job['id'],'interrupted',error='Synthetic interrupted maintainer')
    return store.one('jobs',job['id']),fid


def test_review_retry_has_current_configuration_fresh_packet_and_one_link(tmp_path):
    store,brief=workspace(tmp_path)
    old=enqueue_review(store,brief['id'],payload={'attempt':4,'parent_job_id':'old-parent'})
    store.update_job(old['id'],'failed',error='Synthetic failure')
    store.event(old['id'],'error',{'message':'Frozen history'})
    old=store.one('jobs',old['id']);events=store.rows('SELECT * FROM events WHERE job_id=?',(old['id'],))
    folder=store.root/'jobs'/old['id'];folder.mkdir();(folder/'review.json').write_text('old invalid result')
    switch_model(store)
    barrier=threading.Barrier(2)
    def retry(_):
        barrier.wait()
        return Worker(store).retry_with_current_model(old['id'])
    with ThreadPoolExecutor(max_workers=2) as pool:jobs=list(pool.map(retry,range(2)))
    new=jobs[0];assert new['id']==jobs[1]['id'] and new['id']!=old['id']
    payload=json.loads(new['payload'])
    assert payload['retry_of_job_id']==old['id'] and payload['version_id']==brief['id']
    assert payload['runtime']['model']=='deepseek/deepseek-v4.1-flash'
    assert all(role['model']==payload['runtime']['model'] for role in payload['role_models'].values())
    assert 'parent_job_id' not in payload and 'attempt' not in payload and 'review_input' not in payload
    class Capture:
        def execute(self,job,prompt,folder,*args,**kwargs):
            target=json.loads((folder/'packet'/'target.json').read_text())
            assert target['version_id']==brief['id']
            raise RuntimeError('Fresh packet captured; no model called')
    with pytest.raises(RuntimeError,match='Fresh packet captured'):
        run_review(store,Capture(),new,brief['id'],store.root/'jobs'/new['id'])
    assert store.one('jobs',old['id'])==old
    assert store.rows('SELECT * FROM events WHERE job_id=?',(old['id'],))==events
    assert (folder/'review.json').read_text()=='old invalid result'


def test_learning_retry_transfers_only_frozen_batch_and_inherits_feedback_once(tmp_path):
    store,brief=workspace(tmp_path);old,fid=failed_learning(store,brief)
    study=store.root/'jobs'/old['id']/'study'
    feedback_loop.begin(study,feedback=[{'text':'Keep the currency explicit','source':fid}])
    store.set_meta('last_study',str(study))
    before={str(p.relative_to(study)):p.read_bytes() for p in study.rglob('*') if p.is_file()}
    new_fid=store.comment(brief['id'],'Additional feedback belongs to the next batch')['id']
    switch_model(store);worker=Worker(store);new=worker.retry_with_current_model(old['id'])
    frozen=json.loads(old['payload']);payload=json.loads(new['payload'])
    for key in ('feedback_ids','k','targets','skill_id'):assert payload[key]==frozen[key]
    assert payload['runtime']['model']=='deepseek/deepseek-v4.1-flash'
    assert store.rows('SELECT * FROM feedback WHERE id=?',(fid,))[0]['batch_id']==new['id']
    assert store.rows('SELECT * FROM feedback WHERE id=?',(new_fid,))[0]['batch_id'] is None
    assert worker.retry_with_current_model(old['id'])['id']==new['id']
    with pytest.raises(ValueError,match='移交'):worker.resume(old['id'])
    class Stopped:
        cancelled=threading.Event()
    runtime=Stopped();runtime.cancelled.set()
    with pytest.raises(InterruptedError):learn(store,runtime,new)
    state=feedback_loop.work(store.root/'jobs'/new['id']/'study')
    assert [f['source'] for f in state['feedback']]==[fid]
    assert store.one('jobs',old['id'])==old
    assert {str(p.relative_to(study)):p.read_bytes() for p in study.rglob('*') if p.is_file()}==before


@pytest.mark.parametrize('reason',['moved','completed_study','adopted','new_batch'])
def test_learning_retry_rejects_consumed_or_superseded_batch_atomically(tmp_path,reason):
    store,brief=workspace(tmp_path);old,fid=failed_learning(store,brief)
    if reason=='moved':
        with store.tx() as c:c.execute('UPDATE feedback SET batch_id=? WHERE id=?',('newer-batch',fid))
    elif reason=='completed_study':
        study=store.root/'jobs'/old['id']/'study'
        feedback_loop.begin(study,feedback=[{'text':'Synthetic feedback','source':fid}])
        with product.locked(study):product._event(study,product._load(study),'phase',{'phase':'complete'})
        store.set_meta('last_study',str(study))
    elif reason=='adopted':store.event(old['id'],'adoption_processed',{'applied':False})
    else:
        newer=store.enqueue('learn',{'feedback_ids':[],'k':1,'targets':[],'skill_id':None})
        store.update_job(newer['id'],'failed')
    before=store.rows('SELECT * FROM jobs');feedback=store.rows('SELECT * FROM feedback WHERE id=?',(fid,))[0]
    with pytest.raises(ValueError):Worker(store).retry_with_current_model(old['id'])
    assert store.rows('SELECT * FROM jobs')==before
    assert store.rows('SELECT * FROM feedback WHERE id=?',(fid,))[0]==feedback


def test_retry_rejects_active_and_unsupported_jobs(tmp_path):
    store,brief=workspace(tmp_path);worker=Worker(store)
    active=enqueue_review(store,brief['id'])
    with pytest.raises(ValueError,match='失败'):worker.retry_with_current_model(active['id'])
    other=store.enqueue('generate',{'run_id':brief['run_id']});store.update_job(other['id'],'failed')
    with pytest.raises(ValueError,match='仅支持'):worker.retry_with_current_model(other['id'])
