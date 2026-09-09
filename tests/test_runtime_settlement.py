import json
import threading
import time

import pytest

from briefloop.store import Store,dump,now
from briefloop.runtime import Worker
from briefloop.review import build_packet,enqueue_review


class NoModel:
    def __init__(self):self.cancelled=threading.Event()
    def cancel(self):self.cancelled.set()
    def execute(self,*args,**kwargs):raise AssertionError('Cached result must not call a model')


def report(store):
    store.set_meta('settings',{**store.settings(),'company_context_enabled':False,'auto_learn':False})
    source=store.add_source('Synthetic source','Revenue was USD 12 million.')
    run=store.create_run({'title':'Synthetic','objective':'Explain revenue','writing_mode':'internal_report'},[source['id']])
    brief=store.publish(run['id'],{'title':'Synthetic','markdown':'Revenue was USD 12 million.'})
    return run,source,brief


def test_parent_resume_reuses_admissible_failed_review_in_background_lane(tmp_path):
    store=Store(tmp_path);run,source,brief=report(store)
    parent=store.enqueue('assess',{'version_id':brief['id']})
    child=enqueue_review(store,brief['id'],payload={**json.loads(parent['payload']),'parent_job_id':parent['id']})
    folder=store.root/'jobs'/child['id'];fp,files=build_packet(store,brief['id'],folder)
    with store.tx() as c:c.execute('INSERT INTO reviews VALUES(?,?,?,?,?,?,?,?,?)',('review_saved',brief['id'],child['id'],fp,'incomplete',dump({'packet_path':str((folder/'packet').relative_to(store.root)),'files':files}),None,now(),now()))
    (folder/'review-id.json').write_text(dump({'review_id':'review_saved'}))
    value={'version_id':brief['id'],'fingerprint':fp,'status':'complete','summary':'Synthetic saved review',
           'assessment':{'brief_hash':brief['hash'],'status':'complete','summary':'Synthetic score','overall':'达到要求','evidence':3,'coverage':3,'analysis':3,'expression':3}}
    (folder/'review.json').write_text(dump(value))
    store.update_job(child['id'],'failed',error='Old schema rejection');store.update_job(parent['id'],'failed',error='Child review failed')
    worker=Worker(store,NoModel());worker._review_runtime=NoModel()
    worker.thread=type('LiveParent',(),{'is_alive':lambda self:True})()
    resumed=worker.resume(parent['id']);worker.review_thread.start()
    try:
        result=worker.assess_version(resumed,brief,store.root/'jobs'/parent['id'],'codex')
        assert result['id']=='review_saved' and result['status']=='complete'
        assert len(store.rows("SELECT id FROM jobs WHERE kind='review'"))==1
        assert store.one('jobs',child['id'])['status']=='complete'
    finally:worker.stopping.set();worker.review_thread.join(timeout=3)


@pytest.mark.parametrize('lane',['primary','review'])
def test_stop_is_persisted_before_transport_finishes_and_settlement_cannot_overwrite(tmp_path,lane):
    store=Store(tmp_path);job=store.enqueue('assess',{});store.update_job(job['id'],'running')
    runtime=NoModel();worker=Worker(store,runtime)
    if lane=='primary':worker.current=job['id']
    else:worker.review_current=job['id'];worker._review_runtime=runtime
    worker.stop_job(job['id'])
    assert store.one('jobs',job['id'])['status']=='cancelled' and runtime.cancelled.is_set()
    worker._settle_job(job['id'],'complete',result={'saved':True},runtime=runtime)
    worker._settle_job(job['id'],'failed',error='Late transport failure')
    assert store.one('jobs',job['id'])['status']=='cancelled'
    other=store.enqueue('assess',{});store.update_job(other['id'],'running');runtime.cancelled.clear()
    worker._settle_job(other['id'],'complete',result={'saved':True},runtime=runtime)
    worker.stop_job(other['id'])
    assert store.one('jobs',other['id'])['status']=='complete'


@pytest.mark.parametrize('edit_during_repair',[False,True])
def test_resume_repairs_metadata_without_regenerating_or_overwriting_body(tmp_path,edit_during_repair):
    from briefloop.evidence import create_span,create_claim,blocks
    from briefloop.document_model import brief_document
    store=Store(tmp_path);run,source,brief=report(store)
    span=create_span(store,{'source_id':source['id'],'locator':{'kind':'text','start_line':1,'end_line':1}})
    claim=create_claim(store,run['id'],{'statement':'Revenue was USD 12 million.','kind':'fact','supports':[{'span_id':span['id'],'supports_quote':'Revenue was USD 12 million.'}]})
    score={'status':'complete','summary':'Synthetic','overall':'建议修改','evidence':3,'coverage':3,'analysis':3,'expression':3}
    store.assess(brief['id'],{'brief_hash':brief['hash'],**score});job=store.enqueue('revise',{'version_id':brief['id']})
    class RepairRuntime(NoModel):
        def __init__(self):super().__init__();self.calls=[]
        def execute(self,job,prompt,folder,*args,**kwargs):
            self.calls.append(job['kind'])
            if job['kind']=='revise':
                (folder/'draft.json').write_text(dump({'title':'Synthetic revision','markdown':'Revenue was USD 12 million, as reported.'}))
                (folder/'revision_bindings.json').write_text(dump([{'claim_id':claim['id'],'block_id':'wrong-block','quote':'Revenue was USD 12 million'}]))
            else:
                assert job['kind']=='repair_revision_metadata'
                packet=json.loads((folder/'input.json').read_text());revision=store.one('briefs',packet['version_id'])
                if edit_during_repair:store.revise(revision['id'],'USER EDIT')
                bid=next(iter(blocks(brief_document(revision))))
                (folder/'metadata.json').write_text(dump({'version_id':revision['id'],'brief_hash':revision['hash'],'bindings':[{'claim_id':claim['id'],'block_id':bid,'quote':'Revenue was USD 12 million'}],'responses':[]}))
            return {'synthetic':True}
    runtime=RepairRuntime();worker=Worker(store,runtime);folder=worker.folder(job)
    with pytest.raises(ValueError,match='锚点'):worker.auto_revise(job,brief,folder)
    revision=store.one('briefs','brief_'+job['id'][4:]+'_r1');original_hash=revision['hash']
    worker.assess_version=lambda job,revised,folder,backend:store.assess(revised['id'],{'brief_hash':revised['hash'],**score})
    result=worker.auto_revise(job,brief,folder)
    assert runtime.calls==['revise','repair_revision_metadata']
    assert store.one('briefs',revision['id'])['hash']==original_hash
    assert list((folder/'revision/metadata-attempts').glob('*.json'))
    latest=store.rows('SELECT * FROM briefs WHERE run_id=? ORDER BY rowid DESC LIMIT 1',(run['id'],))[0]
    if edit_during_repair:
        assert result['revision_status']=='user_edit' and latest['markdown']=='USER EDIT'
        assert not store.rows('SELECT id FROM claim_bindings WHERE version_id=?',(revision['id'],))
    else:
        assert result['revision_status']=='complete' and latest['id']==revision['id']
        assert len(store.rows('SELECT id FROM claim_bindings WHERE version_id=?',(revision['id'],)))==1
        worker.auto_revise(job,brief,folder)
        assert len(runtime.calls)==2 and len(store.rows('SELECT id FROM briefs WHERE run_id=?',(run['id'],)))==2


def test_generation_source_snapshot_is_frozen_at_admission_and_never_backfilled(tmp_path):
    store=Store(tmp_path);source=store.add_source('Synthetic','Original evidence')
    run=store.create_run({'title':'Synthetic','objective':'Explain'},[source['id']]);job=store.enqueue('generate',{'run_id':run['id']})
    class DraftRuntime(NoModel):
        def execute(self,job,prompt,folder,on_tick=lambda:None,**kwargs):
            (folder/'draft.json').write_text(dump({'title':'Synthetic','markdown':'Saved original conclusion'}));on_tick();return {}
    worker=Worker(store,DraftRuntime());first=worker.generate(job,score=False)
    assert [item['source_id'] for item in first['source_snapshot']]==[source['id']]
    newer=store.add_source('Later addition','Later evidence');store.attach_source(run['id'],newer['id'])
    again=worker.generate(job,score=False)
    assert again['source_snapshot']==first['source_snapshot']
    (worker.folder(job)/'generated-source-snapshots.json').unlink()
    legacy=worker.generate(job,score=False)
    assert 'source_snapshot' not in legacy


class WaitingRuntime(NoModel):
    """Hold a real Worker stage after it has admitted its synthetic draft."""
    def __init__(self,write_draft=False):
        super().__init__();self.write_draft=write_draft;self.entered=threading.Event()
    def execute(self,job,prompt,folder,on_tick=lambda:None,**kwargs):
        if self.write_draft:
            schema=json.loads((folder/'reader_contract.schema.json').read_text())
            spec=json.loads((folder/'input.json').read_text())['deliverable_spec']
            contract={'source_fingerprint':schema['properties']['source_fingerprint']['const'],
                      'clauses':[{'requirement_id':item['requirement_id'],'kind':'reader_content',
                                  'source_quote':item['text'],'instruction':item['text']} for item in spec['requirement_items']]}
            (folder/'plan.json').write_text(dump({'reader_contract':contract}))
            (folder/'draft.json').write_text(dump({'title':'Synthetic','markdown':'Revenue was USD 12 million.'}))
            on_tick()
        self.entered.set()
        if not self.cancelled.wait(10):raise AssertionError('Test did not stop its waiting runtime')
        raise InterruptedError('Synthetic model turn stopped')


def wait_for(check):
    deadline=time.monotonic()+5
    while time.monotonic()<deadline:
        if check():return
        time.sleep(.01)
    assert check()


def test_word_file_completes_while_generation_and_review_still_run(tmp_path):
    from zipfile import ZipFile
    from briefloop.export_jobs import enqueue_export,output_path
    store=Store(tmp_path);run,source,_=report(store)
    generation=store.enqueue('generate',{'run_id':run['id'],'single_evaluation':False})
    primary=WaitingRuntime(write_draft=True);review_runtime=WaitingRuntime()
    worker=Worker(store,primary);worker._review_runtime=review_runtime;worker.start()
    try:
        assert primary.entered.wait(5)
        brief=store.one('briefs','brief_'+generation['id'][4:])
        review=enqueue_review(store,brief['id'])
        assert review_runtime.entered.wait(5)
        export=enqueue_export(store,brief['id'])
        store.revise(brief['id'],'Revenue was USD 99 million in the user revision.')
        wait_for(lambda:store.one('jobs',export['id'])['status'] in ('complete','failed'))
        finished=store.one('jobs',export['id'])
        assert finished['status']=='complete',finished['error']
        with ZipFile(output_path(store,finished)) as archive:
            text=archive.read('word/document.xml').decode()
            assert 'Revenue was USD 12 million.' in text and 'USD 99 million' not in text
        assert json.loads(finished['result'])['version_id']==brief['id']
        assert store.one('jobs',generation['id'])['status']=='running'
        assert store.one('jobs',review['id'])['status']=='running'
        assert not primary.cancelled.is_set() and not review_runtime.cancelled.is_set()
    finally:worker.close()
    assert not worker.file_thread.is_alive()


def test_file_stop_preserves_terminal_state_without_cancelling_model_and_can_resume(tmp_path,monkeypatch):
    from briefloop.export_jobs import enqueue_export,output_path
    store=Store(tmp_path);run,source,_=report(store)
    generation=store.enqueue('generate',{'run_id':run['id'],'single_evaluation':False})
    primary=WaitingRuntime(write_draft=True);worker=Worker(store,primary)
    rendered=threading.Event();release_settlement=threading.Event()
    settle=worker._settle_job
    def delayed_settle(jid,status,**kwargs):
        if store.one('jobs',jid)['kind']=='export_docx' and status=='complete' and not rendered.is_set():
            rendered.set();assert release_settlement.wait(5)
        return settle(jid,status,**kwargs)
    monkeypatch.setattr(worker,'_settle_job',delayed_settle)
    worker.start()
    try:
        assert primary.entered.wait(5)
        export=enqueue_export(store,'brief_'+generation['id'][4:])
        assert rendered.wait(5)
        assert output_path(store,export).is_file()  # The real Word renderer ran.
        worker.stop_job(export['id'])
        assert store.one('jobs',export['id'])['status']=='cancelled'
        assert worker._file_cancelled.is_set() and not primary.cancelled.is_set()
        release_settlement.set();wait_for(lambda:worker.file_current is None)
        assert store.one('jobs',export['id'])['status']=='cancelled'
        assert store.one('jobs',generation['id'])['status']=='running'
        assert worker.resume(export['id'])['id']==export['id']
        wait_for(lambda:store.one('jobs',export['id'])['status']=='complete')
        assert not primary.cancelled.is_set() and store.one('jobs',generation['id'])['status']=='running'
    finally:release_settlement.set();worker.close()
    assert not worker.file_thread.is_alive()
