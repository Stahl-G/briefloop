"""Review isolation mode is frozen without weakening evidence/version gates."""
import json
import pytest

from briefloop.store import Store, dump
from briefloop.review import enqueue_review, run_review, get_review, review_status
from briefloop.review_capability import ReviewBackendUnsupported, restricted_review, review_available

pytestmark=pytest.mark.real_review_capabilities


def brief_case(tmp_path, backend='briefloop-native'):
    store=Store(tmp_path)
    store.update_settings({'agent_backend':backend,'model':'fixture/model' if backend!='codex' else 'fixture-model',
                           'model_selection_required':False,'company_context_enabled':False})
    source=store.add_source('Fixture','Revenue was 12.')
    run=store.create_run({'title':'Report','objective':'Check revenue'},[source['id']])
    brief=store.publish(run['id'],{'title':'Report','markdown':'Revenue was 12.'})
    return store,source,run,brief


def test_standard_available_without_claiming_strict_and_strict_never_falls_back(tmp_path,monkeypatch):
    monkeypatch.setattr('briefloop.review_capability._opencode_major',lambda:2)
    store,source,run,brief=brief_case(tmp_path,'opencode')
    assert store.settings()['review_mode']=='standard'
    assert review_available('codex') and review_available('opencode')
    assert not restricted_review('codex') and not restricted_review('opencode')
    assert restricted_review('briefloop-native')
    standard=enqueue_review(store,brief['id'])
    assert json.loads(standard['payload'])['review_mode']=='standard'
    store.update_settings({'review_mode':'strict'})  # Settings may keep an unsupported choice.
    with pytest.raises(ReviewBackendUnsupported):enqueue_review(store,brief['id'])
    with pytest.raises(ReviewBackendUnsupported):
        store.create_run({'title':'Fact check','objective':'Check','allow_web':True,'fact_check':True},[source['id']])
    assert len(store.rows('SELECT * FROM runs'))==1
    assert len(store.rows('SELECT * FROM jobs'))==1
    assert json.loads(store.one('jobs',standard['id'])['payload'])['review_mode']=='standard'
    # An explicit strict route must not accidentally inherit the available main engine.
    with pytest.raises(ReviewBackendUnsupported):
        enqueue_review(store,brief['id'],payload={'agent_backend':'briefloop-native','review_mode':'strict',
            'review_runtime':{'backend':'opencode','model':'fixture/model'}})


def test_mode_and_backend_are_in_job_identity_and_frozen_parent_wins(tmp_path):
    store,_,run,brief=brief_case(tmp_path)
    standard=enqueue_review(store,brief['id'],payload={'review_mode':'standard'})
    strict=enqueue_review(store,brief['id'],payload={'review_mode':'strict'})
    assert strict['id']!=standard['id']
    assert enqueue_review(store,brief['id'],payload={'review_mode':'strict'})['id']==strict['id']
    parent=store.enqueue('generate',{'run_id':run['id'],'review_mode':'standard'})
    frozen=json.loads(parent['payload'])
    store.update_settings({'review_mode':'strict'})
    child=enqueue_review(store,brief['id'],payload={**frozen,'parent_job_id':parent['id']})
    assert json.loads(child['payload'])['review_mode']=='standard'


class ReplyRuntime:
    def __init__(self,store,brief):
        import threading
        self.store=store;self.brief=brief;self.calls=[];self.cancelled=threading.Event()
    def execute(self,stage,prompt,folder,**kwargs):
        self.calls.append((stage,prompt))
        review=get_review(self.store,stage['review_id'])
        output={'version_id':self.brief['id'],'fingerprint':review['fingerprint'],'status':'incomplete',
                'summary':'Fixture only','unchecked_items':[{'description':'Fixture has not semantically reviewed content','importance':'core'}]}
        (folder/'review.json').write_text(dump(output),encoding='utf-8')


def test_new_review_records_actual_mode_and_strict_cannot_reuse_standard_output(tmp_path):
    store,_,_,brief=brief_case(tmp_path)
    job=enqueue_review(store,brief['id'],payload={'review_mode':'standard'})
    folder=store.root/'jobs'/job['id'];runtime=ReplyRuntime(store,brief)
    saved=run_review(store,runtime,job,brief['id'],folder)
    assert get_review(store,saved['id'])['data']['review_mode']=='standard'
    assert get_review(store,saved['id'])['data']['review_backend']=='briefloop-native'
    view=review_status(store,brief['id'])['reviews'][0]
    assert view['review_mode']=='standard' and view['review_backend']=='briefloop-native'
    assert '普通模式不承诺' in runtime.calls[0][1]
    assert '只有read工具可用' not in runtime.calls[0][1]
    changed={**job,'payload':dump({**json.loads(job['payload']),'review_mode':'strict'})}
    before=(folder/'review.json').read_bytes()
    with pytest.raises(ValueError,match='不能把普通或历史审阅复用为严格审阅'):
        run_review(store,runtime,changed,brief['id'],folder)
    assert len(runtime.calls)==1 and (folder/'review.json').read_bytes()==before


def test_standard_strict_and_unlabelled_history_keep_major_finding_blocker(tmp_path):
    from test_review import fixture
    from briefloop.review import accept_review
    from briefloop.release import eligibility
    store,_,brief,value=fixture(tmp_path)
    accept_review(store,'review_test',value)
    original=store.rows('SELECT data FROM reviews WHERE id=?',('review_test',))[0]['data']
    view=review_status(store,brief['id'])['reviews'][0]
    assert view['review_mode'] is None and view['review_backend'] is None
    before=eligibility(store,brief['id'])
    store.update_settings({'review_mode':'strict'})
    assert eligibility(store,brief['id'])==before
    assert store.rows('SELECT data FROM reviews WHERE id=?',('review_test',))[0]['data']==original
    assert not before['eligible'] and any(x['code']=='finding_unresolved' for x in before['blockers'])
    for mode in ('standard','strict'):
        data={**json.loads(original),'review_mode':mode,'review_backend':'briefloop-native'}
        with store.tx() as c:c.execute('UPDATE reviews SET data=? WHERE id=?',(dump(data),'review_test'))
        gate=eligibility(store,brief['id'])
        assert not gate['eligible'] and any(x['code']=='finding_unresolved' for x in gate['blockers'])


def test_schedule_uses_current_mode_on_fire_then_freezes_that_job(tmp_path):
    from briefloop import schedules
    store,source,_,_=brief_case(tmp_path,'codex')
    saved=schedules.save(store,{'name':'Fixture','config':{'frequency':'daily','start':'2026-09-26T09:00',
        'timezone':'UTC','requirements':{'title':'Scheduled','objective':'Read source','allow_web':False},'source_ids':[source['id']]}})
    store.update_settings({'review_mode':'strict'})
    fired=schedules.fire(store,saved['id'],manual=True,request_id='same-schedule')
    assert fired['status']=='accepted',fired
    assert json.loads(store.one('jobs',fired['job_id'])['payload'])['review_mode']=='strict'
    store.update_settings({'review_mode':'standard'})
    assert json.loads(store.one('jobs',fired['job_id'])['payload'])['review_mode']=='strict'


def test_review_child_reuse_checks_mode_before_returning_completed_child(tmp_path):
    from briefloop.runtime import Worker
    store,_,run,brief=brief_case(tmp_path)
    parent=store.enqueue('generate',{'run_id':run['id'],'review_mode':'standard'})
    runtime=ReplyRuntime(store,brief);worker=Worker(store,runtime)
    standard=worker._review_child(parent,brief)
    result=run_review(store,runtime,standard,brief['id'],store.root/'jobs'/standard['id'])
    store.update_job(standard['id'],'complete',result=result)
    strict_parent={**parent,'payload':dump({**json.loads(parent['payload']),'review_mode':'strict'})}
    strict=worker._review_child(strict_parent,brief)
    assert strict['id']!=standard['id'] and json.loads(strict['payload'])['review_mode']=='strict'
    assert len(runtime.calls)==1


def test_legacy_review_binding_without_mode_resumes_without_relabelling_history(tmp_path,monkeypatch):
    from test_interactive_runtime import legacy_review_case
    from briefloop.interactive_runtime import InteractiveRuntime
    monkeypatch.setattr('briefloop.review_capability._opencode_major',lambda:1)
    store,job,brief,folder,harness=legacy_review_case(tmp_path)
    payload=json.loads(job['payload']);payload.pop('review_mode',None);job={**job,'payload':dump(payload)}
    with store.tx() as c:c.execute('UPDATE jobs SET payload=? WHERE id=?',(job['payload'],job['id']))
    marker=folder/'conversation.json';binding=json.loads(marker.read_text());binding['runtime'].pop('review_mode',None)
    marker.write_text(dump(binding),encoding='utf-8')
    rid=json.loads((folder/'review-id.json').read_text())['review_id']
    data=get_review(store,rid)['data'];data.pop('review_mode',None);data.pop('review_backend',None)
    with store.tx() as c:c.execute('UPDATE reviews SET data=? WHERE id=?',(dump(data),rid))
    store.update_settings({'review_mode':'strict'})
    runtime=InteractiveRuntime(store,backends={'opencode':harness})
    result=run_review(store,runtime,job,brief['id'],folder)
    assert result['id']==rid and len(harness.starts)==2
    assert review_status(store,brief['id'])['reviews'][0]['review_mode'] is None
    assert get_review(store,rid)['data']==data
    strict={**job,'payload':dump({**payload,'review_mode':'strict'})}
    with pytest.raises(ValueError,match='不能把普通或历史审阅复用为严格审阅'):
        run_review(store,runtime,strict,brief['id'],folder)
    assert len(harness.starts)==2


def test_codex_internal_reviewer_really_sends_read_only_and_no_network(tmp_path):
    from test_harness import RPC,until
    from briefloop.harness import HarnessManager
    manager=HarnessManager(Store(tmp_path),RPC)
    try:
        run=manager.start_internal('Synthetic review',runtime={'permission':'read-only','review_mode':'standard'},allow_web=False)
        until(lambda:manager.client is not None and any(method=='turn/start' for method,_ in manager.client.calls))
        thread=next(p for m,p in manager.client.calls if m=='thread/start')
        turn=next(p for m,p in manager.client.calls if m=='turn/start')
        assert thread['sandbox']=='read-only' and thread['approvalPolicy']=='never'
        assert thread['config']['web_search']=='disabled'
        assert turn['sandboxPolicy']=={'type':'readOnly','networkAccess':False}
    finally:manager.close()
