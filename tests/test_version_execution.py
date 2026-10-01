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
    assert describe(store,original)['configuration']=={'backend':'codex','model':'writer-before','effort':'high','kind':'frozen_request','reported':{}}
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
        c.execute("INSERT INTO chat_messages(id,session_id,role,text,status,mode,source_ids,created,updated,runtime) VALUES('msg',?,'user','Revise','delivered','normal','[]','2026','2026',?)",(session['id'],dump({'backend':'codex','model':'frozen-message','effort':'low','api_key':'sk-hidden'})))
        c.execute("UPDATE chat_messages SET turn_id='native-turn' WHERE id='msg'")
        c.execute("UPDATE chat_sessions SET turn_id='native-turn' WHERE id=?",(session['id'],))
    record_chat(store,brief,session['id'],'msg')
    info=describe(store,brief)['configuration']
    assert info=={'backend':'codex','model':'frozen-message','effort':'low','kind':'frozen_request','reported':{}}
    assert 'hidden' not in json.dumps(info)


def test_legacy_refinement_uses_hash_receipt_and_evidence_preserves_prose_writer(tmp_path):
    store=Store(tmp_path);original=seed(store);writer=job(store)
    revised=seed(store,'brief_0123456789abcdef',parent=original['id'])
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


def test_native_root_requires_explicit_admitted_copy_not_hash_only(tmp_path):
    from briefloop.document_model import document_hash, markdown_document
    from briefloop.version_execution import record_copy
    store=Store(tmp_path);brief=seed(store);writer=job(store,payload={'run_id':'run','agent_backend':'briefloop-native','runtime':{'model':'main'}})
    document=markdown_document('Original');brief['hash']=document_hash(document)
    with store.tx() as c:c.execute('UPDATE briefs SET hash=? WHERE id=?',(brief['hash'],brief['id']))
    folder=store.root/'jobs'/writer['id']/'analyst';folder.mkdir(parents=True)
    (folder/'draft.json').write_text(dump({'editor_document':document}))
    chat=ChatStore(store);session=chat.create('Writer',{'backend':'briefloop-native','model':'today'},tmp_path)
    with store.tx() as c:
        c.execute("INSERT INTO chat_messages(id,session_id,role,text,status,mode,source_ids,created,updated,runtime) VALUES('writer',?,'user','Write','completed','normal','[]','2026','2026',?)",(session['id'],dump({'backend':'briefloop-native','model':'actual-analyst','effort':'max'})))
    (folder/'conversation.json').write_text(dump({'job_id':writer['id'],'session_id':session['id'],'message_id':'writer'}))
    assert describe(store,brief)['configuration'] is None
    (folder/'draft-accepted.json').write_text(dump({'attempt_id':'writer','revision':'accepted'}))
    record_copy(store,writer,{'editor_document':document},folder)
    record(store,brief,writer)
    info=describe(store,brief)['configuration']
    assert info['model']=='actual-analyst' and info['attribution']=='admitted_analyst'


def test_ambiguous_legacy_snapshots_do_not_choose_first_job(tmp_path):
    store=Store(tmp_path);brief=seed(store,'brief_0123456789abcdef');a=job(store)
    b=job(store,'job_second',payload={'run_id':'run','agent_backend':'codex','runtime':{'model':'other'}})
    for writer in (a,b):
        folder=store.root/'jobs'/writer['id'];folder.mkdir(parents=True)
        (folder/'generated-source-snapshots.json').write_text(dump({brief['id']:{'brief_hash':brief['hash']}}))
    assert describe(store,brief)['configuration'] is None


def test_message_report_is_exact_and_preferred_without_changing_request(tmp_path):
    store=Store(tmp_path);brief=seed(store,'revision');chat=ChatStore(store)
    session=chat.create('Writer',{'backend':'codex','model':'today'},tmp_path)
    with store.tx() as c:
        c.execute("INSERT INTO chat_messages(id,session_id,role,text,status,mode,source_ids,created,updated,runtime,turn_id) VALUES('writer',?,'user','Revise','delivered','normal','[]','2026','2026',?,'native-turn')",(session['id'],dump({'backend':'codex','model':'default','effort':'none'})))
        c.execute("UPDATE chat_sessions SET turn_id='native-turn' WHERE id=?",(session['id'],))
    chat.event(session['id'],'runtime/reported',{'message_id':'unrelated','backend':'codex','model':'wrong','source':'codex.turn_response'})
    chat.event(session['id'],'runtime/reported',{'message_id':'writer','backend':'codex','model':'actual','source':'codex.turn_response'})
    record_chat(store,brief,session['id'],'writer')
    config=describe(store,brief)['configuration']
    assert config['model']=='default' and config['effort']=='none'
    assert config['reported']['model']=='actual' and 'effort' not in config['reported']


def test_noop_native_revision_never_attributes_old_version_to_current_message(tmp_path):
    from types import SimpleNamespace
    from briefloop.native_orchestrator import action
    store=Store(tmp_path);run=store.create_run({'title':'Synthetic','objective':'Test'},[])
    from briefloop.document_model import markdown_document
    brief=store.publish(run['id'],{'title':'Synthetic','editor_document':markdown_document('Original')})
    chat=ChatStore(store);session=chat.create('Writer',{'backend':'codex','model':'today'},tmp_path)
    with store.tx() as c:
        c.execute("INSERT INTO chat_messages(id,session_id,role,text,status,mode,source_ids,created,updated,runtime,turn_id) VALUES('writer',?,'user','Revise','delivered','normal','[]','2026','2026',?,'native-turn')",(session['id'],dump({'backend':'codex','model':'today'})))
        c.execute("UPDATE chat_sessions SET turn_id='native-turn' WHERE id=?",(session['id'],))
    result=action(store,{'native_role':'chat','permission':'workspace-write','session_id':session['id'],'attempt_id':'writer','_harness':SimpleNamespace(cancel_requested=lambda _:False)}, {'request':{'action':'revise_document','base_version':brief['id'],'editor_document':json.loads(brief['editor_document'])}})
    assert store.rows("SELECT seq FROM events WHERE kind='writer_version'")==[]
    assert describe(store,brief)['configuration'] is None


def test_cli_action_ignores_adjacent_untrusted_conversation_marker(tmp_path):
    from briefloop.chat_tools import workspace_action
    store=Store(tmp_path);run=store.create_run({'title':'Synthetic','objective':'Test'},[])
    original=store.publish(run['id'],{'title':'Synthetic','markdown':'Original'})
    from briefloop.document_model import markdown_document
    path=store.root/'revision.json';path.write_text(dump(markdown_document('Changed')))
    (path.parent/'conversation.json').write_text(dump({'session_id':'other','message_id':'other'}))
    saved=workspace_action(store,{'action':'revise_document','base_version':original['id'],'document_file':str(path)})
    assert saved['id']!=original['id']
    assert saved['execution_provenance']['configuration'] is None
