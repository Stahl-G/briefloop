import json
import threading
from concurrent.futures import ThreadPoolExecutor
from briefloop.store import Store, dump
from briefloop.version_execution import describe, publication, chat_publication, record_plain_output
from briefloop.chat_store import ChatStore
from briefloop.document_model import markdown_document


def run(store):
    return store.create_run({'title':'Synthetic','objective':'Test'},[])['id']


def job(store, rid, identity='job_abc', model='writer-before', backend='codex'):
    payload={'run_id':rid,'agent_backend':backend,'runtime':{'model':model,'reasoning_effort':'high'},'role_models':{'evaluator':{'model':'NOT-WRITER'}}}
    with store.tx() as c:
        c.execute('INSERT INTO jobs VALUES(?,?,?,?,?,?,?,?)',(identity,'generate','complete',dump(payload),'{}',None,'2026','2026'))
    return store.one('jobs',identity)


def publish(store,rid,writer=None,vid='original',body='Original',parent=None,author='agent'):
    return store.publish(rid,{'title':'Synthetic','editor_document':markdown_document(body)},version_id=vid,parent_id=parent,author=author,writer=writer)


def message(store,model='frozen-message',mid='msg'):
    chat=ChatStore(store);session=chat.create('Synthetic',{'backend':'codex','model':'today'},store.root)
    with store.tx() as c:
        c.execute("INSERT INTO chat_messages(id,session_id,role,text,status,mode,source_ids,created,updated,runtime,turn_id) VALUES(?,?,'user','Revise','delivered','normal','[]','2026','2026',?,?)",(mid,session['id'],dump({'backend':'codex','model':model,'effort':'low','api_key':'sk-hidden'}),mid))
        c.execute("UPDATE chat_sessions SET turn_id=? WHERE id=?",(mid,session['id']))
    return chat,session['id'],mid


def test_atomic_receipt_keeps_frozen_writer_and_manual_origin(tmp_path):
    store=Store(tmp_path);rid=run(store);writer=job(store,rid)
    original=publish(store,rid,publication(store,writer))
    store.set_meta('settings',{'model':'current-default','reasoning_effort':'low'})
    assert describe(store,original)['configuration']['model']=='writer-before'
    manual=store.revise(original['id'],editor_document=markdown_document('Human'))
    info=describe(store,manual)
    assert info['mode']=='manual' and info['configuration'] is None
    assert info['original_configuration']['model']=='writer-before'
    assert store.brief_view(original['id'])['execution_revision']>0


def test_concurrent_same_id_publication_has_one_owner_even_if_unknown(tmp_path):
    store=Store(tmp_path);rid=run(store);a=job(store,rid);b=job(store,rid,'job_b','other')
    barrier=threading.Barrier(2)
    def save(writer):
        barrier.wait();return publish(store,rid,writer)
    with ThreadPoolExecutor(max_workers=2) as pool:
        results=list(pool.map(save,[publication(store,a),publication(store,b)]))
    receipts=store.rows("SELECT data FROM events WHERE kind='writer_version'")
    assert len(receipts)==1 and results[0]['id']==results[1]['id']
    first=describe(store,results[0])['configuration']
    publish(store,rid,publication(store,b))
    assert describe(store,results[0])['configuration']==first
    unknown=publish(store,rid,vid='unknown')
    publish(store,rid,publication(store,a),vid='unknown')
    assert describe(store,unknown)['configuration'] is None


def test_receipt_and_body_rollback_together(tmp_path,monkeypatch):
    from briefloop import version_execution
    store=Store(tmp_path);rid=run(store)
    real=version_execution.insert_receipt
    def fail(*args,**kwargs):
        real(*args,**kwargs);raise RuntimeError('forced receipt failure')
    monkeypatch.setattr(version_execution,'insert_receipt',fail)
    import pytest
    with pytest.raises(RuntimeError):publish(store,rid)
    assert store.rows('SELECT id FROM briefs')==[]
    assert store.rows("SELECT seq FROM events WHERE kind='writer_version'")==[]


def test_restored_draft_does_not_take_reported_model_from_rotated_conversation(tmp_path):
    store=Store(tmp_path);rid=run(store);writer=job(store,rid)
    chat,sid,mid=message(store,mid='new-message')
    chat.event(sid,'runtime/reported',{'message_id':mid,'backend':'codex','model':'actual-new','source':'codex.turn_response'})
    folder=store.root/'jobs'/writer['id'];folder.mkdir(parents=True)
    (folder/'conversation.json').write_text(dump({'job_id':writer['id'],'session_id':sid,'message_id':mid}))
    # Frozen job requests remain honest; mutable stage files cannot add a new
    # message's host confirmation to a restored artifact.
    brief=publish(store,rid,publication(store,writer))
    config=describe(store,brief)['configuration']
    assert config['model']=='writer-before' and config['reported']=={}


def test_chat_revision_binds_active_message_and_noop_preserves_owner(tmp_path):
    store=Store(tmp_path);rid=run(store);original=publish(store,rid)
    chat,sid,mid=message(store)
    chat.event(sid,'runtime/reported',{'message_id':'unrelated','model':'wrong','source':'codex.turn_response'})
    chat.event(sid,'runtime/reported',{'message_id':mid,'backend':'codex','model':'actual','source':'codex.turn_response'})
    writer=chat_publication(store,sid,mid)
    unchanged=store.revise(original['id'],editor_document=markdown_document('Original'),author='agent',writer=writer)
    assert unchanged['id']==original['id'] and describe(store,unchanged)['configuration'] is None
    revised=store.revise(original['id'],editor_document=markdown_document('Changed'),author='agent',writer=writer)
    config=describe(store,revised)['configuration']
    assert config['model']=='frozen-message' and config['reported']['model']=='actual'
    assert 'hidden' not in json.dumps(config)


def test_fast_writer_binds_transport_output_not_outer_conversation(tmp_path):
    store=Store(tmp_path);rid=run(store);writer=job(store,rid)
    chat,sid,mid=message(store,model='quick-model')
    chat.event(sid,'runtime/reported',{'message_id':mid,'backend':'codex','model':'actual-quick','source':'codex.turn_response'})
    record_plain_output(store,writer,'fast-writing','Quick text',sid,mid)
    brief=publish(store,rid,publication(store,writer,plain_output='Quick text'))
    config=describe(store,brief)['configuration']
    assert config['model']=='quick-model' and config['reported']['model']=='actual-quick'
    assert publication(store,writer,plain_output='other')['configuration'] is None


def test_legacy_ids_and_hash_only_source_snapshots_do_not_claim_authorship(tmp_path):
    store=Store(tmp_path);rid=run(store);writer=job(store,rid)
    original=publish(store,rid,vid='brief_abc')
    with store.tx() as c:c.execute("DELETE FROM events WHERE kind='writer_version'")
    folder=store.root/'jobs'/writer['id'];folder.mkdir(parents=True)
    (folder/'generated-source-snapshots.json').write_text(dump({original['id']:{'brief_hash':original['hash']}}))
    assert describe(store,original)['configuration'] is None


def test_cli_revision_cannot_claim_adjacent_conversation(tmp_path):
    from briefloop.chat_tools import workspace_action
    store=Store(tmp_path);rid=run(store);original=publish(store,rid)
    path=store.root/'revision.json';path.write_text(dump(markdown_document('Changed')))
    (path.parent/'conversation.json').write_text(dump({'session_id':'other','message_id':'other'}))
    saved=workspace_action(store,{'action':'revise_document','base_version':original['id'],'document_file':str(path)})
    assert saved['id']!=original['id'] and saved['execution_provenance']['configuration'] is None


def test_evidence_child_keeps_immutable_parent_writer_and_unknown_stays_unknown(tmp_path):
    from briefloop.version_execution import evidence_publication
    store=Store(tmp_path);rid=run(store);writer=job(store,rid)
    original=publish(store,rid,publication(store,writer))
    evidence=publish(store,rid,evidence_publication(store,original),vid='evidence',parent=original['id'])
    assert describe(store,evidence)['configuration']==describe(store,original)['configuration']
    unknown=publish(store,rid,vid='unknown_evidence',parent=evidence['id'])
    assert describe(store,unknown)['configuration'] is None


def test_native_copy_requires_exact_validated_admission_and_unknown_copy_blocks_old_match(tmp_path,monkeypatch):
    from briefloop.version_execution import record_copy
    from briefloop import analyst_drafts
    store=Store(tmp_path);rid=run(store);writer=job(store,rid,backend='briefloop-native')
    chat,sid,mid=message(store)
    folder=store.root/'jobs'/writer['id']/'analyst';folder.mkdir(parents=True)
    value={'title':'Original','editor_document':markdown_document('Original')}
    (folder/'conversation.json').write_text(dump({'job_id':writer['id'],'session_id':sid,'message_id':mid}))
    (folder/'draft-accepted.json').write_text(dump({'attempt_id':mid,'revision':'accepted'}))
    monkeypatch.setattr(analyst_drafts,'submitted',lambda *_:value)
    record_copy(store,writer,value,folder)
    receipt=publication(store,writer,draft=value)
    assert receipt['configuration']['model']=='frozen-message'
    assert publication(store,writer,draft={**value,'title':'Old metadata, same body'})['configuration'] is None
    monkeypatch.setattr(analyst_drafts,'submitted',lambda *_:{'editor_document':markdown_document('Other')})
    record_copy(store,writer,value,folder)
    assert publication(store,writer,draft=value)['configuration'] is None


def test_browsing_metadata_revision_changes_without_touching_body(tmp_path):
    from briefloop.report_browsing import hot_state,context
    store=Store(tmp_path);rid=run(store);brief=publish(store,rid)
    first=hot_state(store,[])['briefs'][0]
    store.event(None,'writer_version',{'version_id':brief['id'],'brief_hash':brief['hash'],'configuration':None})
    second=hot_state(store,[])['briefs'][0]
    assert second['hash']==first['hash'] and second['execution_revision']>first['execution_revision']
    data=context(store,brief['id']);assert data['execution_revision']==second['execution_revision']
    assert data['execution_provenance']['configuration'] is None


def test_analyst_and_revision_use_writer_request_not_evaluator(tmp_path):
    from briefloop.runtime import stage_job
    store=Store(tmp_path);rid=run(store);writer=job(store,rid)
    payload=json.loads(writer['payload']);payload['role_models']['analyst']={'model':'analyst','model_variant':'max'}
    writer={**writer,'payload':dump(payload)}
    staged=stage_job(store,writer,'analyst')
    analyst=publish(store,rid,publication(store,staged,role='analyst'),vid='analyst')
    revision=publish(store,rid,publication(store,writer,revision=True),vid='revision',parent=analyst['id'],body='Revised')
    assert describe(store,analyst)['configuration']['model']=='analyst'
    assert describe(store,analyst)['configuration']['effort']=='max'
    assert describe(store,revision)['configuration']['model']=='writer-before'
