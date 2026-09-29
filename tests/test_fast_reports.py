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
                {'source_id':'S1','report_quote':'收入 120 万元，同比增长 20%。','excerpt':'收入 120 万元，同比增长 20%'},
                {'source_id':'S1','report_quote':'收入 120 万元，同比增长 20%。','excerpt':'不存在的引文'}],
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


def finish(store,worker,result):
    job=store.one('jobs',result['checks_job_id']);store.update_job(job['id'],'running')
    outcome=worker.assess(job)
    store.update_job(job['id'],'complete',result=outcome)
    return outcome


def test_fast_admission_does_not_silently_truncate_or_start_web_research(tmp_path):
    store=Store(tmp_path);req={'title':'T','objective':'O','completion_mode':'fast','fact_check':False}
    with pytest.raises(ValueError,match='需要已读取'):store.create_run(req,[])
    big=store.add_source('Large','文'*100001)
    run=store.create_run(req,[big['id']])
    from briefloop.fast_reports import selected_packet
    assert selected_packet(store,run['id'],[big['id']])[0]['text']=='文'*100001
    small=store.add_source('Small','公开合成材料')
    with pytest.raises(ValueError,match='联网事实核查'):store.create_run({**req,'fact_check':True},[small['id']])
    assert len(store.rows('SELECT * FROM runs'))==1
    with pytest.raises(ValueError,match='连接器'):store.create_run(req,[small['id']],connector_selection_validated=True)
    store.set_meta('settings',{**store.settings(),'company_context_enabled':None,'fact_checker':True})
    run=store.create_run({**req,'writing_mode':'internal_report','fact_check':None},[small['id']])
    frozen=json.loads(run['requirements'])
    assert frozen['allow_web'] is False and frozen['fact_check'] is False
    assert frozen['company_context_required'] is False
