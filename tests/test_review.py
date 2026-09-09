import json
import pytest
from briefloop.store import Store
from briefloop.review import build_packet,accept_review,respond,review_status,ReviewOutput
from briefloop.opencode_harness import _permission_rules
from briefloop.backends.opencode_server import OpencodeServerClient,OpencodeError


def fixture(tmp_path):
    store=Store(tmp_path);src=store.add_source('Source','Revenue 12 million USD.')
    run=store.create_run({'title':'Report','objective':'Explain'},[src['id']])
    brief=store.publish(run['id'],{'title':'Report','markdown':'Revenue 12 million USD.'})
    job=store.enqueue('assess',{'version_id':brief['id']})
    folder=store.root/'jobs'/job['id'];fp,files=build_packet(store,brief['id'],folder)
    with store.tx() as c:c.execute('INSERT INTO reviews VALUES(?,?,?,?,?,?,?,?,?)',('review_test',brief['id'],job['id'],fp,'running',json.dumps({'packet_path':str((folder/'packet').relative_to(store.root)),'files':files}),None,'2026','2026'))
    value={'fingerprint':fp,'version_id':brief['id'],'status':'complete','summary':'One issue','coverage_scan_complete':True,
           'assessment':{'brief_hash':brief['hash'],'status':'complete','summary':'Issue','overall':'建议修改','evidence':3,'coverage':3,'analysis':3,'expression':3},
           'findings':[{'kind':'insufficient_evidence','severity':'major','description':'Need support','evidence':'Source only contains one value','report_quote':'Revenue 12 million USD.'}]}
    return store,src,brief,value


def test_review_bound_to_exact_version_and_author_cannot_close(tmp_path):
    store,source,brief,value=fixture(tmp_path)
    with pytest.raises(ValueError,match='未绑定'):accept_review(store,'review_test',{**value,'fingerprint':'other'})
    accept_review(store,'review_test',value)
    accept_review(store,'review_test',value)
    assert len(store.rows('SELECT id FROM assessments WHERE version_id=?',(brief['id'],)))==1
    finding=review_status(store,brief['id'])['findings'][0]
    revised=store.revise(brief['id'],'Revenue corrected.')
    response=respond(store,finding['id'],revised['id'],'corrected','See revised sentence')
    assert response['status']=='addressed_pending_review'
    assert review_status(store,revised['id'])['findings'][0]['status']=='addressed_pending_review'
    with pytest.raises(ValueError,match='不可覆盖'):accept_review(store,'review_test',{**value,'summary':'Different'})


def test_review_rejects_source_change_and_strict_native_permissions(tmp_path):
    store,source,brief,value=fixture(tmp_path)
    (store.root/source['path']).write_text('Revenue 120 million USD.')
    with pytest.raises(ValueError,match='依据发生变化'):accept_review(store,'review_test',value)
    rules=_permission_rules({'review_root':str(tmp_path/'packet'),'permission':'read-only'},False,tmp_path)
    assert rules[0]=={'permission':'*','action':'deny','pattern':'*'}
    assert all(r['permission'] in ('read','external_directory') for r in rules if r['action']=='allow')
    client=object.__new__(OpencodeServerClient);calls=[]
    def request(method,path,body):
        calls.append(body);raise OpencodeError('Rejected',status=400)
    client._request=request
    with pytest.raises(OpencodeError):client.create_session('review',permission=rules,require_permissions=True)
    assert len(calls)==1 and 'permission' in calls[0]


def test_manifest_tamper_and_real_tool_history_binding(tmp_path):
    from briefloop.chat_store import ChatStore
    from briefloop.execution_records import journal_tool
    store,source,brief,value=fixture(tmp_path)
    saved=store.rows('SELECT data FROM reviews WHERE id=?',('review_test',))[0]
    packet=store.root/json.loads(saved['data'])['packet_path'];(packet/'history/versions.json').write_text('[]')
    with pytest.raises(ValueError,match='核查包文件'):accept_review(store,'review_test',value)
    chat=ChatStore(store);session=chat.create('Current',{},store.root)
    job=store.rows('SELECT id FROM jobs')[0]['id'];chat.event(session['id'],'job/attached',{'jobId':job})
    journal_tool(chat,session['id'],'turn','tool-1','bash',{'command':'python calculate.py'},'result=50%',status='completed',exit_code=0)
    other=chat.create('Other',{},store.root)
    journal_tool(chat,other['id'],'other','tool-2','bash',{'command':'outside'},'not this report',status='completed')
    fp,files=build_packet(store,brief['id'],store.root/'another-review')
    tool_files=[name for name in files if name.startswith('history/tools/')]
    assert len(tool_files)==1
    recorded=(store.root/'another-review/packet'/tool_files[0]).read_text()
    assert 'result=50%' in recorded and 'not this report' not in recorded
    packet=tmp_path/'symlink-packet';packet.mkdir();(packet/'escape').symlink_to(tmp_path/'outside')
    with pytest.raises(ValueError,match='符号链接'):_permission_rules({'review_root':str(packet),'permission':'read-only'},False,tmp_path)
    from briefloop.execution_records import sanitize
    redacted=sanitize({'command':'DEEPSEEK_API_KEY="fake-key-for-regression" python report.py','output':'{"api_key":"fake-other-key", "revenue": 12}'})
    assert 'fake-key' not in redacted['command'] and 'fake-other-key' not in redacted['output']
    assert 'report.py' in redacted['command'] and '"revenue": 12' in redacted['output']


def test_local_export_does_not_need_a_selected_model(tmp_path):
    from briefloop.export_jobs import enqueue_export
    from briefloop.runtime import Worker
    store,source,brief,value=fixture(tmp_path)
    store.set_meta('settings',{**store.settings(),'model':'','model_selection_required':True})
    job=enqueue_export(store,brief['id'])
    assert 'runtime' not in json.loads(job['payload'])
    store.update_job(job['id'],'failed',error='Synthetic temporary file failure')
    assert Worker(store).resume(job['id'])['status']=='queued'


def test_review_queue_deduplicates_and_parent_stop_cancels_pending_review(tmp_path):
    from briefloop.review import enqueue_review
    from briefloop.runtime import Worker
    store,source,brief,value=fixture(tmp_path)
    parent=store.enqueue('generate',{'run_id':brief['run_id']})
    one=enqueue_review(store,brief['id'],payload={'parent_job_id':parent['id']})
    two=enqueue_review(store,brief['id'],payload={'parent_job_id':parent['id']})
    assert one['id']==two['id']
    Worker(store).stop_job(parent['id'])
    assert store.one('jobs',one['id'])['status']=='cancelled'


def test_only_review_of_exact_response_closes_finding(tmp_path):
    from briefloop.review import respond,review_status
    store,source,brief,value=fixture(tmp_path);accept_review(store,'review_test',value)
    finding=review_status(store,brief['id'])['findings'][0]
    revised=store.revise(brief['id'],'The sentence was removed.')
    response=respond(store,finding['id'],revised['id'],'removed','Removed unsupported claim')
    folder=store.root/'recheck';fingerprint,files=build_packet(store,revised['id'],folder)
    with store.tx() as c:c.execute('INSERT INTO reviews VALUES(?,?,?,?,?,?,?,?,?)',('review_recheck',revised['id'],None,fingerprint,'running',json.dumps({'packet_path':'recheck/packet','files':files}),None,'2026','2026'))
    result={**value,'fingerprint':fingerprint,'version_id':revised['id'],'assessment':{**value['assessment'],'brief_hash':revised['hash']},
            'findings':[{**value['findings'][0],'response_to':finding['id'],'resolution':'resolved'}]}
    with pytest.raises(ValueError,match='response_to'):accept_review(store,'review_recheck',result)
    assert review_status(store,revised['id'])['findings'][0]['status']=='addressed_pending_review'
    result['findings'][0]['response_to']=response['id'];accept_review(store,'review_recheck',result)
    assert review_status(store,revised['id'])['findings'][0]['status']=='resolved'
    assert review_status(store,brief['id'])['findings'][0]['status']=='open'


def saved_review(store,brief,folder_name,identity):
    folder=store.root/folder_name;fingerprint,files=build_packet(store,brief['id'],folder)
    with store.tx() as c:c.execute('INSERT INTO reviews VALUES(?,?,?,?,?,?,?,?,?)',(identity,brief['id'],None,fingerprint,'running',json.dumps({'packet_path':folder_name+'/packet','files':files}),None,'2026','2026'))
    return folder,fingerprint


def test_response_admission_rejects_conflicting_or_superseded_decisions(tmp_path):
    store,source,brief,value=fixture(tmp_path);accept_review(store,'review_test',value)
    finding=review_status(store,brief['id'])['findings'][0]
    revised=store.revise(brief['id'],'The unsupported sentence was removed.')
    response=respond(store,finding['id'],revised['id'],'removed','Removed unsupported claim')
    assert respond(store,finding['id'],revised['id'],'removed','Removed unsupported claim')['id']==response['id']
    _,fp=saved_review(store,revised,'recheck','review_recheck')
    result={**value,'fingerprint':fp,'version_id':revised['id'],'assessment':{**value['assessment'],'brief_hash':revised['hash']},
            'findings':[{**value['findings'][0],'response_to':response['id'],'resolution':'resolved'}],
            'response_checks':[{'response_id':response['id'],'decision':'unresolved','reason':'Still unsupported'}]}
    with pytest.raises(ValueError,match='矛盾'):accept_review(store,'review_recheck',result)
    assert review_status(store,revised['id'])['findings'][0]['status']=='addressed_pending_review'
    respond(store,finding['id'],revised['id'],'disagree','Correction withdrawn; explanation changed')
    result['response_checks'][0]['decision']='resolved'
    with pytest.raises(ValueError,match='处理说明已更新'):accept_review(store,'review_recheck',result)
    assert store.rows('SELECT result FROM reviews WHERE id=?',('review_recheck',))[0]['result'] is None


def test_review_recovery_admits_saved_metadata_without_another_model_turn(tmp_path):
    from briefloop.review import run_review,validate_applicable_review
    store,source,brief,value=fixture(tmp_path)
    review=store.rows('SELECT * FROM reviews WHERE id=?',('review_test',))[0]
    job=store.one('jobs',review['job_id']);folder=store.root/'jobs'/job['id']
    (folder/'review-id.json').write_text(json.dumps({'review_id':'review_test'}))
    value['findings'][0].update(dimension='evidence',locator='Source L1')
    original=json.dumps(value);(folder/'review.json').write_text(original)
    (folder/'admission-error.json').write_text(json.dumps({'error':'Old schema rejected locator'}))
    class NoModel:
        def execute(self,*args,**kwargs):raise AssertionError('Saved admissible result must not run a model')
    accepted=run_review(store,NoModel(),job,brief['id'],folder)
    assert accepted['status']=='complete' and accepted['result']['findings'][0]['locator']=='Source L1'
    assert (folder/'review.json').read_text()==original
    validate_applicable_review(store,'review_test',brief['id'])
    (store.root/source['path']).write_text('Revenue changed to 120 million USD.')
    with pytest.raises(ValueError,match='依据发生变化'):run_review(store,NoModel(),job,brief['id'],folder)
    assert store.rows('SELECT status FROM reviews WHERE id=?',('review_test',))[0]['status']=='complete'


def test_conflict_review_is_complete_scoped_and_preserves_admitted_inputs(tmp_path):
    from briefloop.conflicts import create,respond as conflict_response
    from briefloop.review import validate_applicable_review
    store,source,brief,value=fixture(tmp_path)
    conflict=create(store,source_ids=[source['id']],description='Newer official text does not establish equivalent scope',run_id=brief['run_id'])
    conflict_response(store,conflict['id'],'keep_current','User preference still requires review')
    assert store.rows('SELECT status FROM conflicts WHERE id=?',(conflict['id'],))[0]['status']=='addressed_pending_review'
    _,fp=saved_review(store,brief,'conflict-review','review_conflict');result={**value,'fingerprint':fp}
    with pytest.raises(ValueError,match='遗漏冲突'):accept_review(store,'review_conflict',result)
    check={'conflict_id':conflict['id'],'decision':'different_scope','reason':'Source L1 is a different unit, preserve both scoped values'}
    result['conflict_checks']=[check,check]
    with pytest.raises(ValueError,match='重复'):accept_review(store,'review_conflict',result)
    result['conflict_checks']=[check];accept_review(store,'review_conflict',result)
    validate_applicable_review(store,'review_conflict',brief['id'])
    data=json.loads(store.rows('SELECT data FROM conflicts WHERE id=?',(conflict['id'],))[0]['data'])
    assert data['review_history']==[data['review']]
    changed=json.loads(json.dumps(data));changed['review']['reason']='Changed after admission'
    with store.tx() as c:c.execute('UPDATE conflicts SET data=? WHERE id=?',(json.dumps(changed),conflict['id']))
    with pytest.raises(ValueError,match='实际决定不一致'):validate_applicable_review(store,'review_conflict',brief['id'])
    with store.tx() as c:c.execute('UPDATE conflicts SET data=? WHERE id=?',(json.dumps(data),conflict['id']))
    create(store,source_ids=[source['id']],description='Additional unresolved same-period contradiction',run_id=brief['run_id'])
    with pytest.raises(ValueError,match='依据发生变化'):validate_applicable_review(store,'review_conflict',brief['id'])


def test_requirements_cannot_be_downgraded_or_new_findings_silently_closed(tmp_path):
    store,source,brief,value=fixture(tmp_path)
    with pytest.raises(ValueError,match='准确的 response_id'):
        accept_review(store,'review_test',{**value,'findings':[{**value['findings'][0],'resolution':'resolved'}]})
    row=store.rows('SELECT data FROM reviews WHERE id=?',('review_test',))[0]
    packet=store.root/json.loads(row['data'])['packet_path'];target=json.loads((packet/'target.json').read_text())
    identity=target['requirements']['requirement_items'][0]['requirement_id']
    with pytest.raises(ValueError,match='必答要求'):
        accept_review(store,'review_test',{**value,'requirement_checks':[{'requirement_id':identity,'status':'manual','reason':'No answer available'}]})
