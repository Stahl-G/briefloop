"""Single-context direct writing keeps durable sources, checks and the one revision."""
import json
import threading

import pytest

from briefloop.store import Store, dump
from briefloop.runtime import Worker


class Author:
    def __init__(self, store, source_id):
        self.store=store;self.source_id=source_id;self.cancelled=threading.Event();self.calls=[]

    def execute(self, job, prompt, folder, *args, **kwargs):
        self.calls.append((folder.name, job))
        if folder.name=='direct-writing':
            assert job['plain_tools'] and job['plain_output']=='response.txt'
            assert self.source_id in prompt and '怎样才算答完' in prompt
            (folder/'response.txt').write_text(
                f'# 季度观察\n\n收入 120 万元，同比增长 20%。[@{self.source_id}]\n\n另一说法尚未保存原文。[@src_invented]\n')
        elif folder.name=='evidence':
            (folder/'response.txt').write_text(dump({'citations':[
                {'source_id':'S1','report_quote':'收入 120 万元，同比增长 20%。','excerpt':'收入 120 万元，同比增长 20%'}],
                'number_bindings':[]}))
        elif folder.name=='evaluation':
            value=json.loads((folder/'input.json').read_text())
            (folder/'assessment.json').write_text(dump({'brief_hash':value['brief']['hash'],'status':'complete',
                'summary':'Controlled check only','overall':'达到要求','evidence':4,'coverage':4,'analysis':4,'expression':4}))
        else:raise AssertionError('Unexpected stage: '+folder.name)
        return {'status':'complete','returncode':0}


def setup(tmp_path):
    store=Store(tmp_path)
    store.set_meta('settings',{**store.settings(),'company_context_enabled':False,'auto_learn':False,'model':'synthetic','model_selection_required':False})
    source=store.add_source('Synthetic disclosure','本测试材料为虚构。\n收入 120 万元，同比增长 20%\n')
    run=store.create_run({'title':'季度观察','objective':'解释收入变化','allow_web':False,'fact_check':False,
                          'completion_mode':'direct','key_questions':['收入多少？']},[source['id']],research_protocol='quality_v1')
    job=store.enqueue('generate',{'run_id':run['id'],'auto_revision':True,'agent_backend':'codex'})
    runtime=Author(store,source['id']);worker=Worker(store,runtime)
    store.update_job(job['id'],'running')
    return store,source,run,job,runtime,worker


def test_direct_draft_is_saved_with_known_citations_and_checks_keep_revision(tmp_path):
    store,source,run,job,runtime,worker=setup(tmp_path)
    assert not store.meta('scout_coverage_version:'+run['id'])
    result=worker.generate(job)
    brief=store.one('briefs',result['version_id'])
    assert f'[@{source["id"]}]' in brief['markdown'] and 'src_invented' not in brief['markdown']
    notes=json.loads(brief['detail'])['research_notes']
    assert any(n['kind']=='direct_unknown_citations' and n['source_ids']==['src_invented'] for n in notes)
    checks=json.loads(store.one('jobs',result['checks_job_id'])['payload'])
    assert checks['fast_evidence'] is True and checks['auto_revision'] is True
    store.update_job(result['checks_job_id'],'running')
    outcome=worker.assess(store.one('jobs',result['checks_job_id']))
    assert outcome['checks_state']=='complete'
    assert [name for name,_ in runtime.calls]==['direct-writing','evidence','evaluation']


def test_direct_needs_a_tool_capable_host_and_offline_material(tmp_path):
    store=Store(tmp_path)
    with pytest.raises(ValueError,match='离线时需要已有材料'):
        store.create_run({'title':'T','objective':'O','allow_web':False,'completion_mode':'direct'},[])
    store,source,run,job,runtime,worker=setup(tmp_path/'native')
    native=store.enqueue('generate',{'run_id':run['id'],'agent_backend':'briefloop-native','runtime':{'model':'p/synthetic'}})
    with pytest.raises(ValueError,match='CLI'):worker.generate(native)
