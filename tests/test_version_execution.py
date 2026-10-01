import json
from briefloop.store import Store, dump
from briefloop.version_execution import describe, record, record_chat
from briefloop.chat_store import ChatStore


def seed(store, vid='brief_abc', *, author='agent', parent=None, body='Original'):
    with store.tx() as c:
        c.execute("INSERT OR IGNORE INTO runs(id,requirements,source_ids,skill_id,created,mode) VALUES('run',?, '[]',NULL,'2026','normal')", (dump({'title':'Synthetic','objective':'Test'}),))
        c.execute('INSERT INTO briefs VALUES(?,?,?,?,?,?,?,?,?)', (vid,'run',parent,author,body,vid+'hash','{}',None,'2026'))
    return store.one('briefs',vid)


def job(store, identity='job_abc', *, kind='generate', payload=None):
    value=payload or {'run_id':'run','agent_backend':'codex','runtime':{'model':'writer-before','reasoning_effort':'high'},'role_models':{'evaluator':{'model':'NOT-WRITER'}}}
    with store.tx() as c:
        c.execute('INSERT INTO jobs VALUES(?,?,?,?,?,?,?,?)',(identity,kind,'complete',dump(value),'{}',None,'2026','2026'))
    return store.one('jobs',identity)


def test_historical_versions_keep_frozen_writer_and_manual_origin(tmp_path):
    store=Store(tmp_path);original=seed(store);job(store)
    store.set_meta('settings',{'model':'current-default','reasoning_effort':'low'})
    assert describe(store,original)['configuration']=={'backend':'codex','model':'writer-before','effort':'high'}
    manual=seed(store,'human',author='user',parent=original['id'])
    info=describe(store,manual)
    assert info['mode']=='manual' and info['configuration'] is None
    assert info['original_configuration']['model']=='writer-before'
    imported=seed(store,'import',author='import',parent=manual['id'])
    assert describe(store,imported)['mode']=='imported'
    unknown=seed(store,'brief_missing',parent=imported['id'])
    assert describe(store,unknown)['configuration'] is None


def test_writer_receipt_is_exact_and_analyst_differs_from_evaluator(tmp_path):
    store=Store(tmp_path);brief=seed(store,'custom')
    writer=job(store,payload={'run_id':'run','agent_backend':'briefloop-native','runtime':{'model':'main'},'role_models':{'analyst':{'model':'analyst','model_variant':'max'},'evaluator':{'model':'evaluator'}}})
    record(store,brief,writer,role='analyst')
    assert describe(store,brief)['configuration']['model']=='analyst'
    store.event(writer['id'],'writer_version',{'version_id':'custom','brief_hash':'wrong','configuration':{'model':'fake'}})
    assert describe(store,brief)['configuration']['model']=='analyst'
    old=seed(store,'brief_abc_analyst',parent='custom')
    assert describe(store,old)['configuration']['model']=='analyst'
    assessed=seed(store,'brief_review',parent=old['id'])
    job(store,'job_review',kind='assess',payload={'run_id':'run','version_id':assessed['id'],'runtime':{'model':'evaluator'}})
    assert describe(store,assessed)['configuration'] is None


def test_chat_revision_uses_active_message_snapshot_not_session_defaults(tmp_path):
    store=Store(tmp_path);brief=seed(store,'chat_revision')
    chat=ChatStore(store);session=chat.create('Synthetic',{'backend':'codex','model':'today'},tmp_path)
    with store.tx() as c:
        c.execute("INSERT INTO chat_messages(id,session_id,role,text,status,mode,source_ids,created,updated,runtime) VALUES('msg',?,'user','Revise','delivered','normal','[]','2026','2026',?)",(session['id'],dump({'backend':'codex','model':'frozen-message','reasoning_effort':'low','api_key':'sk-hidden'})))
        c.execute("UPDATE chat_sessions SET turn_id='msg' WHERE id=?",(session['id'],))
    record_chat(store,brief,session['id'],'msg')
    info=describe(store,brief)['configuration']
    assert info=={'backend':'codex','model':'frozen-message','effort':'low'}
    assert 'hidden' not in json.dumps(info)


def test_legacy_refinement_uses_hash_receipt_and_evidence_preserves_prose_writer(tmp_path):
    store=Store(tmp_path);original=seed(store);writer=job(store)
    revised=seed(store,'random_refinement',parent=original['id'])
    folder=store.root/'jobs'/writer['id'];folder.mkdir(parents=True)
    path=folder/'generated-source-snapshots.json'
    path.write_text(dump({revised['id']:{'brief_hash':'wrong'}}))
    assert describe(store,revised)['configuration'] is None
    path.write_text(dump({revised['id']:{'brief_hash':revised['hash']}}))
    assert describe(store,revised)['configuration']['model']=='writer-before'
    continuation=job(store,'job_checks',kind='assess',payload={'run_id':'run','continuation_of':writer['id'],'runtime':{'model':'main-checks'},'role_models':{'evaluator':{'model':'review-only'}}})
    evidence=seed(store,'brief_checks_evidence',parent=original['id'])
    record(store,evidence,continuation)
    assert describe(store,evidence)['configuration']['model']=='writer-before'
    revision=seed(store,'brief_checks_r1',parent=evidence['id'],body='Revised')
    assert describe(store,revision)['configuration']['model']=='main-checks'  # main frozen runtime writes _r1, not role_models.evaluator


def test_native_root_uses_exact_independent_writer_conversation(tmp_path):
    from briefloop.document_model import document_hash, markdown_document
    store=Store(tmp_path);brief=seed(store);writer=job(store)
    document=markdown_document('Original');brief['hash']=document_hash(document)
    with store.tx() as c:c.execute('UPDATE briefs SET hash=? WHERE id=?',(brief['hash'],brief['id']))
    folder=store.root/'jobs'/writer['id']/'analyst';folder.mkdir(parents=True)
    (folder/'draft.json').write_text(dump({'editor_document':document}))
    (folder/'conversation.json').write_text(dump({'job_id':writer['id'],'backend':'briefloop-native','runtime':{'model':'actual-analyst','effort':'max'}}))
    info=describe(store,brief)['configuration']
    assert info=={'backend':'briefloop-native','model':'actual-analyst','effort':'max'}
    record(store,brief,writer)
    assert describe(store,brief)['configuration']==info
