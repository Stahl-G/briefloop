"""Single-context direct writing keeps durable sources, checks and the one revision."""
import json
import threading

import pytest

from briefloop.store import Store, dump, now
from briefloop.runtime import Worker


class Author:
    def __init__(self, store, source_id):
        self.store=store;self.source_id=source_id;self.cancelled=threading.Event();self.calls=[]

    def execute(self, job, prompt, folder, *args, **kwargs):
        self.calls.append((folder.name, job))
        if folder.name=='direct-writing':
            assert job['plain_tools'] and job['plain_output']=='response.txt'
            from briefloop.chat_store import ChatStore
            chat=ChatStore(self.store)
            session=chat.create('Controlled author', {}, folder)
            message=chat.message(session['id'], 'Controlled task', status='completed', prompt=prompt)
            (folder/'conversation.json').write_text(dump({'job_id':job['id'], 'session_id':session['id'], 'message_id':message['id']}))
            (folder/'temporal_claims.json').write_text(dump([{'statement':'原稿已生成的日期', 'source_id':self.source_id,
                'event_date':'2026-10-06', 'published_at':'2026-10-09', 'fetched_at':'author estimate', 'locator':'line 2'}]))
            (folder/'response.txt').write_text(
                f'# 季度观察\n\n**本周发生：**10月6日。收入 120 万元，同比增长 20%。[@{self.source_id}]\n\n另一说法尚未保存原文。[@src_invented]\n')
            from briefloop.version_execution import record_plain_output
            record_plain_output(self.store, job, folder.name, (folder/'response.txt').read_text(), session['id'], message['id'])
        elif folder.name=='evidence':
            (folder/'response.txt').write_text(dump({'citations':[
                {'source_id':'S1','report_quote':'收入 120 万元，同比增长 20%。','excerpt':'收入 120 万元，同比增长 20%'}],
                'number_bindings':[], 'temporal_claims':[{'statement':'收入披露', 'source_id':'S1',
                    'event_date':'2026-10-06', 'published_at':'2026-10-09', 'fetched_at':'invented',
                    'news_basis':'first_disclosure', 'news_date':'2026-10-09', 'news_note':'首次披露收入',
                    'report_quote':'收入 120 万元，同比增长 20%。', 'excerpt':'收入 120 万元，同比增长 20%'}]}))
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
    execute = runtime.execute
    def partial_dates(job, prompt, folder, *args, **kwargs):
        result = execute(job, prompt, folder, *args, **kwargs)
        if folder.name == 'direct-writing':
            path = folder / 'temporal_claims.json'
            claims = json.loads(path.read_text())
            claims[0].update(news_date='2026-10-09', locator='author section')
            path.write_text(dump(claims))
        elif folder.name == 'evidence':
            path = folder / 'response.txt'
            evidence = json.loads(path.read_text())
            evidence['temporal_claims'].append({
                'statement': '原稿已生成的日期', 'source_id': 'S1', 'event_date': '',
                'published_at': '2026-10-10', 'locator': '',
                'report_quote': '收入 120 万元，同比增长 20%。', 'excerpt': '收入 120 万元，同比增长 20%'})
            path.write_text(dump(evidence))
        return result
    runtime.execute = partial_dates
    assert not store.meta('scout_coverage_version:'+run['id'])
    result=worker.generate(job)
    brief=store.one('briefs',result['version_id'])
    assert f'[@{source["id"]}]' in brief['markdown'] and 'src_invented' not in brief['markdown']
    claims=json.loads(brief['detail'])['temporal_claims']
    assert claims[0]['event_date']=='2026-10-06' and claims[0]['published_at']=='2026-10-09'
    assert claims[0]['fetched_at']==''  # Local material has no acquisition timestamp.
    assert any({'type':'bold'} in n.get('marks',[]) for block in json.loads(brief['editor_document'])['content'] for n in block.get('content',[]))
    notes=json.loads(brief['detail'])['research_notes']
    assert any(n['kind']=='direct_unknown_citations' and n['source_ids']==['src_invented'] for n in notes)
    checks=json.loads(store.one('jobs',result['checks_job_id'])['payload'])
    assert checks['fast_evidence'] is True and checks['auto_revision'] is True
    store.update_job(result['checks_job_id'],'running')
    outcome=worker.assess(store.one('jobs',result['checks_job_id']))
    assert outcome['checks_state']=='complete'
    enriched=store.one('briefs',outcome['version_id'])
    assert enriched['markdown']==brief['markdown']
    claims=json.loads(enriched['detail'])['temporal_claims']
    assert len(claims)==2 and all(c['fetched_at']=='' for c in claims)
    assert claims[0]['news_date']=='2026-10-09' and claims[0]['locator']
    preserved = next(c for c in claims if c['statement'] == '原稿已生成的日期')
    assert preserved['event_date'] == '2026-10-06' and preserved['news_date'] == '2026-10-09'
    assert preserved['published_at'] == '2026-10-10' and preserved['locator'] == 'line 2'
    assert [name for name,_ in runtime.calls]==['direct-writing','evidence','evaluation']


def test_direct_needs_offline_material_and_native_writer_gets_only_metered_tools(tmp_path):
    store=Store(tmp_path)
    with pytest.raises(ValueError,match='离线时需要已有材料'):
        store.create_run({'title':'T','objective':'O','allow_web':False,'completion_mode':'direct'},[])
    from briefloop.native_roles import runner_tool_specs
    names=lambda config:[t['name'] for t in runner_tool_specs('quick_writer',None,config)]
    assert names({}) == []  # Ordinary quick writing stays tool-free.
    assert names({'direct':True,'allow_web':True,'search_channels':['tavily']})[:2]==['source_read','source_grep']
    assert {'web_search','add_url'} <= set(names({'direct':True,'allow_web':True,'search_channels':['tavily']}))
    assert 'web_search' not in names({'direct':True,'allow_web':True,'search_channels':[]})
    assert not {'web_search','add_url'} & set(names({'direct':True,'allow_web':False}))


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


def test_direct_skill_binding_and_trial_skip_duplicate_evaluation(tmp_path):
    from briefloop.skill_delivery import verify
    store,source,_,_,runtime,worker=setup(tmp_path)
    enabled={'id':'skill_enabled','content':'以收入变化分析业务。','targets':dump(['analyst','scout'])}
    candidate={'id':'candidate_new','content':'以经营范围分析变化。','targets':dump(['analyst'])}
    with store.tx() as c:
        c.execute('INSERT INTO skills VALUES(?,?,?,?,?,?)', (enabled['id'],None,enabled['content'],enabled['targets'],'controlled',now()))
    store.bind_skill(enabled['id'])
    for override, expected in [('enabled',enabled['id']), (None,None), (candidate,candidate['id'])]:
        run=store.create_run({'title':'季度观察','objective':'解释收入变化','allow_web':False,
                              'fact_check':False,'completion_mode':'direct'}, [source['id']])
        payload={'run_id':run['id'],'agent_backend':'codex','single_evaluation':False}
        if override!='enabled':payload['skill_override']=override
        job=store.enqueue('generate',payload)
        store.update_job(job['id'],'running')
        result=worker.generate(job,score=False)
        brief=store.one('briefs',result['version_id'])
        receipt=verify(store,brief)
        assert receipt['skill_id']==expected
        assert json.loads(brief['detail'])['temporal_claims']
        assert result['checks_state']=='deferred' and 'checks_job_id' not in result
        assert not store.rows("SELECT id FROM jobs WHERE kind='assess' AND json_extract(payload,'$.continuation_of')=?", (job['id'],))
    assert [name for name,_ in runtime.calls]==['direct-writing','evidence']*3


def test_learning_refuses_candidate_that_host_did_not_receive(tmp_path):
    from briefloop import learning
    store,_,run,job,runtime,worker=setup(tmp_path)
    candidate={'id':'candidate_missing','content':'检查期间变化。','targets':dump(['analyst'])}
    payload=json.loads(job['payload'])
    payload.update(skill_override=candidate, single_evaluation=False)
    with store.tx() as c:
        c.execute('UPDATE jobs SET payload=? WHERE id=?', (dump(payload),job['id']))
    original=runtime.execute
    def dropped(job,prompt,folder,*args,**kwargs):
        if folder.name=='direct-writing':prompt=prompt.split('<briefloop-method>')[0].rstrip()
        return original(job,prompt,folder,*args,**kwargs)
    runtime.execute=dropped
    result=worker.generate(store.one('jobs',job['id']),score=False)
    brief=store.one('briefs',result['version_id'])
    pairs=[{'case_id':run['id'],'baseline':brief,'candidate':brief}]
    study=tmp_path/'study'
    (study/'candidate').mkdir(parents=True)
    (study/'candidate'/'SKILL.md').write_text(candidate['content'])
    parent=store.enqueue('learn',{'skill_id':None,'targets':['analyst'],'feedback_ids':[]})
    comparison=tmp_path/'jobs'/parent['id']/'round-1'/'comparison'
    comparison.mkdir(parents=True)
    (comparison/'input.json').write_text(dump(pairs))
    state={'history':[{'accepted':True,'round':1,'skill':{'file':'candidate/SKILL.md'}}]}
    before=len(runtime.calls)
    with pytest.raises(ValueError,match='实际送达'):
        learning.compare(store,runtime,parent,pairs,comparison,'codex')
    with pytest.raises(ValueError,match='实际送达'):
        learning.apply_accepted(store,parent,study,state)
    assert len(runtime.calls)==before and store.meta('active_skill') is None
    assert not store.rows("SELECT id FROM skills WHERE id LIKE 'skill_%'")
