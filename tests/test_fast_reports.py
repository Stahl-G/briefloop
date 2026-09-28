"""Direct prose publication, asynchronous evidence and concurrent-edit safety."""
import json
import threading
from zipfile import ZipFile

import pytest

from briefloop.store import Store, dump, Conflict
from briefloop.runtime import Worker
from briefloop.draft_completion import enqueue, status


class Writer:
    def __init__(self, store):
        self.store=store;self.cancelled=threading.Event();self.calls=[];self.edit=None

    def execute(self, job, prompt, folder, *args, **kwargs):
        self.calls.append(folder.name)
        if folder.name=='fast-writing':
            assert job['allow_web'] is False and job['native_packet']['role']=='quick_writer'
            assert '收入 120 万元，同比增长 20%' in prompt
            (folder/'response.txt').write_text('# 季度观察\n\n收入 120 万元，同比增长 20%。[S1]\n\n增长原因仍需进一步材料说明。')
            if self.edit:self.edit()
        elif folder.name=='evidence':
            if self.edit:self.edit()
            (folder/'response.txt').write_text(dump({'citations':[
                {'source_id':'S1','excerpt':'收入 120 万元，同比增长 20%'},
                {'source_id':'S1','excerpt':'不存在的引文'}],
                'number_bindings':[{'source_id':'S1','source_excerpt':'收入 120 万元，同比增长 20%',
                    'report_quote':'收入 120 万元，同比增长 20%。','number_text':'120 万元','value':120,'unit':'万元'}]}))
        elif folder.name=='evaluation':
            value=json.loads((folder/'input.json').read_text())
            (folder/'assessment.json').write_text(dump({'brief_hash':value['brief']['hash'],'status':'complete',
                'summary':'Controlled check only','overall':'建议修改','evidence':3,'coverage':3,'analysis':3,'expression':3}))
        else:raise AssertionError('Unexpected stage: '+folder.name)
        return {'status':'complete','returncode':0}


def setup(tmp_path, *, internal=False):
    store=Store(tmp_path)
    store.set_meta('settings',{**store.settings(),'company_context_enabled':False,'auto_learn':False})
    source=store.add_source('Synthetic quarterly disclosure','本测试材料为虚构。\n收入 120 万元，同比增长 20%\n以上为本季度实际数。')
    req={'title':'季度观察','objective':'解释收入变化，保留限制','allow_web':False,'fact_check':False,'completion_mode':'fast'}
    if internal:req['writing_mode']='internal_report'
    run=store.create_run(req,[source['id']])
    job=store.enqueue('generate',{'run_id':run['id'],'auto_revision':True,'agent_backend':'codex'})
    runtime=Writer(store);worker=Worker(store,runtime)
    store.update_job(job['id'],'running')
    return store,source,run,job,runtime,worker


def test_one_prose_turn_publishes_downloadable_draft_and_queues_checks_once(tmp_path):
    store,source,run,job,runtime,worker=setup(tmp_path)
    result=worker.generate(job)
    assert runtime.calls==['fast-writing']
    brief=store.one('briefs',result['version_id'])
    assert '[@'+source['id']+']' in brief['markdown']
    assert not store.rows('SELECT * FROM assessments')
    checks=store.one('jobs',result['checks_job_id'])
    assert checks['kind']=='assess' and json.loads(checks['payload'])['auto_revision'] is False
    assert json.loads(checks['payload'])['runtime']==json.loads(job['payload'])['runtime']
    assert worker.generate(job)['checks_job_id']==checks['id']
    assert runtime.calls==['fast-writing']
    from briefloop.export_jobs import enqueue_export,generate_word
    export=enqueue_export(store,brief['id']);store.update_job(export['id'],'running')
    artifact=generate_word(store,export,threading.Event())
    with ZipFile(store.root/artifact['path']) as z:assert 'word/document.xml' in z.namelist()


def finish(store,worker,result):
    job=store.one('jobs',result['checks_job_id']);store.update_job(job['id'],'running')
    outcome=worker.assess(job)
    store.update_job(job['id'],'complete',result=outcome)
    return outcome


@pytest.mark.parametrize('internal',[False,True])
def test_background_adds_located_evidence_and_scores_without_rewriting_prose(tmp_path,internal):
    store,source,run,job,runtime,worker=setup(tmp_path,internal=internal)
    result=worker.generate(job);store.update_job(job['id'],'complete',result=result)
    original=store.one('briefs',result['version_id'])
    outcome=finish(store,worker,result)
    enriched=store.one('briefs',outcome['version_id'])
    assert enriched['parent_id']==original['id'] and enriched['markdown']==original['markdown']
    details=json.loads(enriched['detail'])
    assert details['citations']==[{'source_id':source['id'],'locator':'line 2-2','excerpt':'收入 120 万元，同比增长 20%'}]
    assert len(details['number_bindings'])==1
    assert len(details['research_notes'][-1]['rejected'])==1
    assert len(store.rows('SELECT * FROM assessments'))==1
    assert not store.rows('SELECT * FROM reviews')
    assert runtime.calls==['fast-writing','evidence','evaluation']
    assert enqueue(store,original['id'])['id']==result['checks_job_id']
    assert status(store,enriched['id'])['state']=='complete'
    assert status(store,enriched['id'])['checked_version']==enriched['id']


def test_background_never_overwrites_concurrent_user_edit(tmp_path):
    store,source,run,job,runtime,worker=setup(tmp_path)
    result=worker.generate(job);store.update_job(job['id'],'complete',result=result)
    authored=[]
    runtime.edit=lambda:authored.append(store.revise(result['version_id'],'用户新的正文，保留我写的说明。',allow_markdown_conversion=True))
    outcome=finish(store,worker,result)
    assert outcome['version_id']==result['version_id']
    latest=store.rows('SELECT * FROM briefs ORDER BY rowid DESC LIMIT 1')[0]
    assert latest['id']==authored[0]['id'] and latest['author']=='user'
    assert not store.rows('SELECT id FROM assessments WHERE version_id=?',(latest['id'],))
    assert status(store,latest['id'])['state']=='deferred'


def test_source_changes_reject_before_any_background_model_call(tmp_path):
    store,source,run,job,runtime,worker=setup(tmp_path)
    result=worker.generate(job);store.update_job(job['id'],'complete',result=result)
    (store.root/source['path']).write_text('Changed material')
    with pytest.raises(Conflict):finish(store,worker,result)
    assert runtime.calls==['fast-writing']
    assert store.one('briefs',result['version_id'])


def test_material_change_during_writing_keeps_raw_output_without_publishing(tmp_path):
    store,source,run,job,runtime,worker=setup(tmp_path)
    runtime.edit=lambda:(store.root/source['path']).write_text('Changed material')
    with pytest.raises((ValueError,Conflict)):worker.generate(job)
    assert (worker.folder(job)/'fast-writing'/'response.txt').exists()
    assert not store.rows('SELECT * FROM briefs')
    assert not store.rows("SELECT * FROM jobs WHERE kind='assess'")


def test_fast_admission_does_not_silently_truncate_or_start_web_research(tmp_path):
    store=Store(tmp_path);req={'title':'T','objective':'O','completion_mode':'fast','fact_check':False}
    with pytest.raises(ValueError,match='需要已读取'):store.create_run(req,[])
    big=store.add_source('Large','文'*100001)
    with pytest.raises(ValueError,match='不会截断'):store.create_run(req,[big['id']])
    small=store.add_source('Small','公开合成材料')
    with pytest.raises(ValueError,match='联网事实核查'):store.create_run({**req,'fact_check':True},[small['id']])
    assert not store.rows('SELECT * FROM runs')
    with pytest.raises(ValueError,match='连接器'):store.create_run(req,[small['id']],connector_selection_validated=True)
    store.set_meta('settings',{**store.settings(),'company_context_enabled':None,'fact_checker':True})
    run=store.create_run({**req,'writing_mode':'internal_report','fact_check':None},[small['id']])
    frozen=json.loads(run['requirements'])
    assert frozen['allow_web'] is False and frozen['fact_check'] is False
    assert frozen['company_context_required'] is False
