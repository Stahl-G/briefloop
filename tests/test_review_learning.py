"""Learning consumes accepted historical packets and like-for-like sources."""
import json

import pytest

from briefloop.learning import _baseline_for_attempt, _experience, _generate_trial
from briefloop.review import accept_review, build_packet, respond, review_status
from briefloop.review_learning import record_verified_corrections, source_snapshot
from briefloop.store import Store, dump, now


def attempt(tmp_path):
    store=Store(tmp_path);source=store.add_source('Facts','Revenue 12 million USD.')
    run=store.create_run({'title':'Report','objective':'Explain revenue'},[source['id']])
    job=store.enqueue('generate',{'run_id':run['id']})
    brief=store.publish(run['id'],{'title':'Report','markdown':'Revenue 12 million USD.'},version_id='brief_'+job['id'][4:])
    payload={**json.loads(job['payload']),'skill_id':None}
    return store,source,run,job,brief,payload


def test_baseline_requires_attempt_sources_and_actual_refined_version(tmp_path):
    store,source,run,job,brief,payload=attempt(tmp_path)
    refined=store.publish(run['id'],{'title':'Report','markdown':'Revenue was 12 million USD.'},parent_id=brief['id'])
    saved=source_snapshot(store,run['id'])
    store.update_job(job['id'],'complete',result={'version_id':refined['id'],'source_snapshot':saved})
    assert _baseline_for_attempt(store,run,payload)['id']==refined['id']
    later=store.add_source('Later correction','Revenue corrected to 120 million USD.')
    store.attach_source(run['id'],later['id'])
    # The case passed by callers may be the original run row; acquired sources
    # must still be part of the intended material unless explicitly filtered.
    assert _baseline_for_attempt(store,run,payload) is None
    assert source_snapshot(store,run['id'])!=saved
    filtered={**run,'learning_source_ids':[source['id']]}
    assert _baseline_for_attempt(store,filtered,payload)['id']==refined['id']
    store.update_job(job['id'],'complete',result={'version_id':refined['id'],'source_snapshot':None})
    assert _baseline_for_attempt(store,filtered,payload) is None


def test_legacy_input_can_only_prove_complete_saved_ids_and_hashes(tmp_path):
    store,source,run,job,brief,payload=attempt(tmp_path)
    store.update_job(job['id'],'complete',result={'version_id':brief['id']})
    assert _baseline_for_attempt(store,run,payload) is None
    folder=store.root/'jobs'/job['id'];folder.mkdir()
    legacy={'sources':[{'id':source['id'],'hash':source['hash'],'original_path':None}]}
    path=folder/'input.json';path.write_text(dump(legacy))
    assert _baseline_for_attempt(store,run,payload)['id']==brief['id']
    later=store.add_source('Supplement','A new metric.')
    store.attach_source(run['id'],later['id'])
    assert _baseline_for_attempt(store,run,payload) is None
    path.write_text(dump({'sources':legacy['sources']+[{'id':later['id']}]}))
    assert _baseline_for_attempt(store,run,payload) is None


def test_original_hash_change_and_legacy_missing_raw_hash_invalidate_baseline(tmp_path):
    store,source,run,job,brief,payload=attempt(tmp_path)
    original=store.root/'sources'/(source['id']+'.bin');original.write_bytes(b'Original input')
    saved=source_snapshot(store,run['id'])
    store.update_job(job['id'],'complete',result={'version_id':brief['id'],'source_snapshot':saved})
    assert _baseline_for_attempt(store,run,payload)['id']==brief['id']
    original.write_bytes(b'Changed raw input, extraction still identical')
    assert _baseline_for_attempt(store,run,payload) is None
    original.write_bytes(b'Original input')
    store.update_job(job['id'],'complete',result={'version_id':brief['id']})
    folder=store.root/'jobs'/job['id'];folder.mkdir()
    (folder/'input.json').write_text(dump({'sources':[{'id':source['id'],'hash':source['hash'],'original_path':str(original)}]}))
    assert _baseline_for_attempt(store,run,payload) is None


def save_review(store,brief,identity):
    folder=store.root/identity;fingerprint,files=build_packet(store,brief['id'],folder)
    with store.tx() as connection:
        connection.execute('INSERT INTO reviews VALUES(?,?,?,?,?,?,?,?,?)',
                           (identity,brief['id'],None,fingerprint,'running',dump({'packet_path':identity+'/packet','files':files}),None,now(),now()))
    return {'version_id':brief['id'],'fingerprint':fingerprint,'status':'complete','summary':'Synthetic check',
            'coverage_scan_complete':True,'assessment':{'brief_hash':brief['hash'],'status':'complete','summary':'Check',
                                                     'overall':'建议修改','evidence':3,'coverage':3,'analysis':3,'expression':3}}


def reviewed_response(tmp_path,decision='resolved'):
    store,source,run,job,brief,payload=attempt(tmp_path)
    initial=save_review(store,brief,'review_before')
    initial['findings']=[{'kind':'insufficient_evidence','severity':'major','description':'Scope unsupported',
                          'evidence':'The original only supports the stated period.','report_quote':'Revenue 12 million USD.'}]
    accept_review(store,'review_before',initial)
    finding=review_status(store,brief['id'])['findings'][0]
    revised=store.publish(run['id'],{'title':'Report','markdown':'Revenue was 12 million USD in the stated period.'},parent_id=brief['id'])
    response=respond(store,finding['id'],revised['id'],'corrected','Added the original period and removed broader claims.')
    final=save_review(store,revised,'review_after')
    final['response_checks']=[{'response_id':response['id'],'decision':decision,'reason':'Compared saved original and revision.'}]
    accept_review(store,'review_after',final)
    return store,source,run,brief,revised,response,final


def test_verified_hook_is_idempotent_and_historical_replay_uses_packet_only(tmp_path,monkeypatch):
    store,source,run,before,after,response,result=reviewed_response(tmp_path)
    first=record_verified_corrections(store,'review_after')
    assert len(first)==1 and len(store.rows("SELECT id FROM feedback WHERE kind='review_correction'"))==1
    feedback=store.rows('SELECT * FROM feedback WHERE id=?',(first[0],))[0]
    original=json.loads(feedback['data'])
    assert original['change_kind']=='verified_revision'
    assert original['sources'][0]['id']==source['id']
    later=store.add_source('Subsequent event','New quarter changed the outlook.')
    store.attach_source(run['id'],later['id'])
    (store.root/source['path']).write_text('Later local corruption must not rewrite saved evidence.')
    # Simulate recovery after Review was committed but feedback was not. Reading
    # today's originals would now fail; the accepted packet is still intact.
    with store.tx() as connection:connection.execute('DELETE FROM feedback WHERE id=?',(first[0],))
    monkeypatch.setattr(store,'source_text',lambda *_: (_ for _ in ()).throw(AssertionError('Historical replay read live sources')))
    assert record_verified_corrections(store,'review_after')==first
    restored=json.loads(store.rows('SELECT data FROM feedback WHERE id=?',(first[0],))[0]['data'])
    assert restored==original and later['id'] not in dump(restored)
    experiences,_=_experience(store,{'payload':dump({'feedback_ids':first})})
    experience=json.loads(experiences[0]['text'])
    assert experience['sources']==original['sources'] and experience['before_text']==before['markdown']
    assert experience['after_text']==after['markdown']
    assert record_verified_corrections(store,'review_after')==first


def test_unresolved_is_not_success_and_packet_history_tampering_is_rejected(tmp_path):
    store,source,run,before,after,response,result=reviewed_response(tmp_path/'unresolved',decision='unresolved')
    assert record_verified_corrections(store,'review_after')==[]
    assert not store.rows("SELECT id FROM feedback WHERE kind='review_correction'")
    store,source,run,before,after,response,result=reviewed_response(tmp_path/'resolved')
    path=store.root/'review_after/packet/history/responses.json'
    path.write_text('[]')
    with pytest.raises(ValueError,match='核查包文件'):
        record_verified_corrections(store,'review_after')


def test_cached_trial_uses_actual_result_version_and_rejects_source_drift(tmp_path):
    store,source,case,_,_,payload=attempt(tmp_path)
    trial_run=store.create_run(json.loads(case['requirements']),[source['id']],mode='trial')
    trial=store.enqueue('generate',{'run_id':trial_run['id']})
    early=store.publish(trial_run['id'],{'title':'First','markdown':'First draft'},version_id='brief_'+trial['id'][4:])
    final=store.publish(trial_run['id'],{'title':'Final','markdown':'Final draft'},parent_id=early['id'])
    snapshot=source_snapshot(store,trial_run['id'])
    store.update_job(trial['id'],'complete',result={'version_id':final['id'],'source_snapshot':snapshot})
    folder=store.root/'trial-stage';folder.mkdir()
    (folder/'trial.json').write_text(dump({'run_id':trial_run['id'],'job_id':trial['id'],'source_snapshot':snapshot}))
    result=_generate_trial(store,{'payload':dump(payload),'_runtime':object()},case,None,folder,'baseline')
    assert result['id']==final['id']
    added=store.add_source('New','New facts');store.attach_source(case['id'],added['id'])
    with pytest.raises(ValueError,match='来源快照已变化'):
        _generate_trial(store,{'payload':dump(payload),'_runtime':object()},case,None,folder,'baseline')
