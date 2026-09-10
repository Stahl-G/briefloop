import json
import threading
import pytest
from briefloop.store import Store
from briefloop.runtime import Worker
from briefloop.company_context import propose, snapshot, resolve_conflict


def setup(tmp_path):
    store=Store(tmp_path);store.set_meta('settings',{**store.settings(),'company_context_enabled':False});source=store.add_source('Current evidence','Revenue 12')
    run=store.create_run({'title':'Report','objective':'Explain','writing_mode':'internal_report'},[source['id']])
    return store,run,source


def test_background_public_update_and_user_conflict_preserve_history(tmp_path):
    store,run,source=setup(tmp_path)
    store.set_meta('settings',{**store.settings(),'company_context_enabled':True})
    public=store.add_source('PR','Capacity 12',url='https://example.test/pr')
    first=propose(store,{'key':'capacity','value':'12','source_id':public['id'],'effective_date':'2026-09-01','origin':'public'})
    pending=propose(store,{'key':'capacity','value':'14','source_id':source['id'],'effective_date':'2026-09-01','origin':'user'})
    assert pending['status']=='pending' and snapshot(store)['facts'][0]['value']=='12'
    after=resolve_conflict(store,pending['id'],True)
    assert after['facts'][0]['value']=='12' and after['pending']
    conflict=store.rows('SELECT id,status FROM conflicts')[0]
    assert conflict['status']=='addressed_pending_review'
    from briefloop.conflicts import accept_check
    with store.tx() as c:accept_check(store,c,conflict['id'],'adopt_new','Synthetic reviewed evidence','review_fixture',pending['id'])
    assert snapshot(store)['facts'][0]['value']=='14' and not snapshot(store)['pending']
    assert store.rows('SELECT * FROM company_facts WHERE id=?',(first['id'],))[0]['value']=='12'
    historical=propose(store,{'key':'capacity','value':'8','source_id':public['id'],'effective_date':'2025-09-01','origin':'public'})
    assert historical['status']=='pending' and snapshot(store)['facts'][0]['value']=='14'
    assert len(store.rows("SELECT id FROM conflicts WHERE status!='resolved'"))==1


class RevisionRuntime:
    def __init__(self,store,user_edit=False):self.store=store;self.calls=[];self.cancelled=threading.Event();self.user_edit=user_edit
    def execute(self,job,prompt,folder,on_tick=lambda:None,**kwargs):
        self.calls.append(folder.name)
        if folder.name=='revision':
            pack=json.loads((folder/'input.json').read_text());brief=pack['brief']
            if self.user_edit:self.store.revise(brief['id'],'USER CORRECTION')
            (folder/'draft.json').write_text(json.dumps({'title':'Report','editor_document':{'type':'doc','content':[{'type':'paragraph','content':[{'type':'text','text':'Corrected report'}]}]}}))
        elif job.get('readonly_output'):
            pack=json.loads((folder/'packet'/'target.json').read_text());index=json.loads((folder/'packet'/'index.json').read_text())
            (folder/'review.json').write_text(json.dumps({'version_id':pack['version_id'],'fingerprint':index['fingerprint'],'status':'complete','summary':'Synthetic review','coverage_scan_complete':True,'assessment':{'brief_hash':pack['brief_hash'],'summary':'still a minor issue','overall':'建议修改','evidence':4,'coverage':4,'analysis':4,'expression':4}}))
        else:
            pack=json.loads((folder/'input.json').read_text());sha=pack['brief']['hash']
            (folder/'assessment.json').write_text(json.dumps({'brief_hash':sha,'summary':'still a minor issue','overall':'建议修改','evidence':4,'coverage':4,'analysis':4,'expression':4}))
        return {'returncode':0}


@pytest.mark.parametrize('user_edit',[False,True])
def test_one_revision_only_and_user_edit_wins(tmp_path,user_edit):
    store,run,source=setup(tmp_path);job=store.enqueue('generate',{'run_id':run['id']})
    brief=store.publish(run['id'],{'title':'Report','markdown':'Original'},version_id='brief_'+job['id'][4:])
    store.assess(brief['id'],{'brief_hash':brief['hash'],'summary':'needs a correction','overall':'建议修改','evidence':3,'coverage':3,'analysis':3,'expression':3})
    runtime=RevisionRuntime(store,user_edit);worker=Worker(store,runtime);folder=worker.folder(job)
    result=worker.auto_revise(job,brief,folder)
    latest=store.rows('SELECT * FROM briefs WHERE run_id=? ORDER BY rowid DESC LIMIT 1',(run['id'],))[0]
    if user_edit:
        assert result['revision_status']=='suggestion' and latest['markdown']=='USER CORRECTION'
        assert runtime.calls==['revision']
    else:
        assert result['version_id']==latest['id'] and latest['parent_id']==brief['id']
        assert runtime.calls==['revision','review']
        again=worker.auto_revise(job,brief,folder)
        assert again['version_id']==latest['id'] and len(runtime.calls)==2


def test_company_gate_requires_choice_and_source_bound_maintenance(tmp_path):
    from briefloop.company_context import complete_review
    store=Store(tmp_path)
    public=store.add_source('Company PR','Capacity 12',url='https://example.test/pr')
    req={'title':'Internal report','objective':'Explain changes','writing_mode':'internal_report','organization':'Example'}
    with pytest.raises(ValueError,match='先选择'):store.create_run(req,[public['id']])
    store.set_meta('settings',{**store.settings(),'company_context_enabled':True})
    run=store.create_run(req,[public['id']])
    with pytest.raises(ValueError,match='维护尚未完成'):store.publish(run['id'],{'title':'Report','markdown':'Body'})
    reviews=[{'source_id':public['id'],'result':'used','note':'Company capacity disclosure'}]
    with pytest.raises(ValueError,match='company_update'):complete_review(store,run['id'],reviews,'Capacity updated')
    propose(store,{'key':'capacity','value':'12','source_id':public['id'],'effective_date':'2026-09-01','origin':'public'})
    checked=complete_review(store,run['id'],reviews,'Capacity updated')
    brief=store.publish(run['id'],{'title':'Report','markdown':'Body'})
    assert json.loads(brief['detail'])['company_context']['revision']==checked['revision']
    propose(store,{'key':'capacity','value':'14','source_id':public['id'],'effective_date':'2026-09-02','origin':'public'})
    assert json.loads(store.one('briefs',brief['id'])['detail'])['company_context']['review']['context']['facts'][0]['value']=='12'
    second=store.create_run(req,[public['id']])
    with pytest.raises(ValueError,match='维护尚未完成'):store.publish(second['id'],{'title':'Second','markdown':'Body'})
    store.set_meta('settings',{**store.settings(),'company_context_enabled':False})
    declined=store.create_run(req,[public['id']])
    store.publish(declined['id'],{'title':'Declined','markdown':'Body'})
