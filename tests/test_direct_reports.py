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


def test_direct_needs_offline_material_and_native_writer_gets_only_metered_tools(tmp_path):
    store=Store(tmp_path)
    with pytest.raises(ValueError,match='离线时需要已有材料'):
        store.create_run({'title':'T','objective':'O','allow_web':False,'completion_mode':'direct'},[])
    from briefloop.native_roles import runner_tool_specs
    from briefloop.agent_prompts import system_prompt
    names=lambda config:[t['name'] for t in runner_tool_specs('quick_writer',None,config)]
    assert names({}) == []  # Ordinary quick writing stays tool-free.
    assert names({'direct':True,'allow_web':True,'search_channels':['tavily']})[:2]==['source_read','source_grep']
    assert {'web_search','add_url'} <= set(names({'direct':True,'allow_web':True,'search_channels':['tavily']}))
    assert 'web_search' not in names({'direct':True,'allow_web':True,'search_channels':[]})
    assert not {'web_search','add_url'} & set(names({'direct':True,'allow_web':False}))
    assert '直写' in system_prompt('quick_writer',direct=True)['text'] and '直写' not in system_prompt('quick_writer')['text']


def test_evidence_windows_keep_cited_lines_of_large_pages_within_bounds():
    from briefloop.direct_reports import evidence_windows, WINDOW_SOURCE
    page='\n'.join(['无关导航文字'*20]*3000+['First Solar 第二季度销售额 10.6 亿美元']+['页脚'*30]*3000)
    rows=evidence_windows([{'alias':'S1','source_id':'src_a','name':'page','hash':'h','text':page}],
                          '销售额为 10.6 亿美元。[@src_a]')
    assert rows[0]['windowed'] and '10.6 亿美元' in rows[0]['text'] and len(rows[0]['text'])<=WINDOW_SOURCE+500


def test_direct_revision_drops_a_reader_contract_the_writer_made_up(tmp_path):
    # Three real luna direct samples failed when the revision copied the
    # requirements into reader_contract; a direct run has no frozen contract.
    store,source,run,job,runtime,worker=setup(tmp_path)
    original=runtime.execute
    def revising(job, prompt, folder, *args, **kwargs):
        if folder.name in ('evaluation','revision-evaluation'):
            value=json.loads((folder/'input.json').read_text())
            (folder/'assessment.json').write_text(dump({'brief_hash':value['brief']['hash'],'status':'complete',
                'summary':'Controlled check only','overall':'建议修改' if folder.name=='evaluation' else '达到要求',
                'evidence':3,'coverage':3,'analysis':3,'expression':3}))
            return {'status':'complete','returncode':0}
        if folder.name=='revision':
            (folder/'draft.json').write_text(dump({'title':'季度观察','markdown':f'# 季度观察\n\n收入 120 万元。[@{source["id"]}]\n',
                'reader_contract':json.loads(store.one('runs',run['id'])['requirements'])}))
            (folder/'responses.json').write_text('[]')
            return {'status':'complete','returncode':0}
        return original(job,prompt,folder,*args,**kwargs)
    runtime.execute=revising
    result=worker.generate(job)
    store.update_job(result['checks_job_id'],'running')
    worker.assess(store.one('jobs',result['checks_job_id']))
    revised=store.rows("SELECT * FROM briefs WHERE id LIKE '%_r1'")
    assert revised and json.loads(revised[0]['detail']).get('reader_contract') is None
