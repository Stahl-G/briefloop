"""Fast web path uses real metering/storage with controlled external transports."""
import json
import pytest
from briefloop import duckduckgo, sources, research_budget, fast_research
from briefloop.store import Store, dump
from briefloop.runtime import Worker
from test_fast_reports import Writer, finish
from test_websearch import ddg_response


class WebWriter(Writer):
    selection=['C1','C2']
    def execute(self, job, prompt, folder, *args, **kwargs):
        if folder.name in ('query-plan','selection'):
            self.calls.append(folder.name)
            assert job['allow_web'] is False
            if folder.name=='query-plan':
                assert 'LOCAL_PRIVATE_BODY' not in prompt
                value={'queries':[{'provider':'duckduckgo','query':'public quarterly results','reason':'primary disclosure'}]}
            else:value={'candidate_ids':self.selection,'gaps':['The cause of growth is not disclosed.']}
            (folder/'response.txt').write_text(dump(value))
            return {'status':'complete'}
        if folder.name=='fast-writing':assert 'Snippet 0' not in prompt
        return super().execute(job,prompt,folder,*args,**kwargs)


def setup(tmp_path,monkeypatch,*,invalid_selection=False):
    store=Store(tmp_path)
    req={'title':'Public report','objective':'Explain reported quarterly revenue','completion_mode':'fast_web',
         'fact_check':False,'search_policy':{'primary_provider':'duckduckgo','native_search_enabled':False}}
    run=store.create_run(req,[],research_protocol='quality_v1')
    job=store.enqueue('generate',{'run_id':run['id'],'agent_backend':'codex'})
    store.update_job(job['id'],'running')
    runtime=WebWriter(store);worker=Worker(store,runtime)
    if invalid_selection:runtime.selection=['https://unselected.invalid']
    searches=[];reads=[]
    html,raw=ddg_response(['https://example.test/disclosure','https://example.test/unavailable'])
    monkeypatch.setattr(duckduckgo,'_post',lambda form:(searches.append(form) or (html,raw)))
    def fetch(url,**kwargs):
        reads.append(url)
        if url.endswith('unavailable'):raise ValueError('Source unavailable')
        return '<html><title>Public disclosure</title><p>收入 120 万元，同比增长 20%</p></html>'.encode(),'text/html','utf-8'
    monkeypatch.setattr(sources,'_fetch_bytes',fetch)
    return store,run,job,runtime,worker,searches,reads


def test_web_flow_retains_failed_pages_excludes_snippets_and_resumes_without_research(tmp_path,monkeypatch):
    store,run,job,runtime,worker,searches,reads=setup(tmp_path,monkeypatch)
    result=worker.generate(job)
    assert runtime.calls==['query-plan','selection','fast-writing']
    assert len(searches)==1 and len(reads)==2
    assert research_budget.snapshot(store,run['id'])['used']=={'search_requests':1,'candidate_urls':2,'source_pages':2}
    assert [store.one('sources',sid)['status'] for sid in store.source_ids(run['id'])].count('failed')==1
    original=store.one('briefs',result['version_id'])
    notes=json.loads(original['detail'])['research_notes']
    assert 'Source unavailable' in dump(notes)
    assert worker.generate(job)['checks_job_id']==result['checks_job_id']
    assert len(searches)==1 and len(reads)==2
    store.update_job(job['id'],'complete',result=result)
    outcome=finish(store,worker,result)
    assert store.one('briefs',outcome['version_id'])['markdown']==original['markdown']
    assert runtime.calls==['query-plan','selection','fast-writing','evidence','evaluation']
    assert not store.rows('SELECT * FROM reviews')


def test_admission_freezes_policy_and_never_expands_a_smaller_budget(tmp_path):
    store=Store(tmp_path)
    req={'title':'R','objective':'O','completion_mode':'fast_web','fact_check':False,
         'search_policy':{'primary_provider':'duckduckgo'},'research_budget':{'search_requests':1,'candidate_urls':2,'source_pages':1}}
    run=store.create_run(req,[])
    frozen=json.loads(run['requirements'])
    assert frozen['allow_web'] and frozen['target_minutes']==10
    assert frozen['research_budget']==req['research_budget']
    store.set_meta('settings',{**store.settings(),'search_provider':'native','search_policy':{'primary_provider':'native'}})
    assert json.loads(store.enqueue('generate',{'run_id':run['id']})['payload'])['search_policy']['primary_provider']=='duckduckgo'
    with pytest.raises(ValueError,match='需要允许公开检索'):store.create_run({**req,'allow_web':False},[])
    with pytest.raises(ValueError,match='搜索渠道'):store.create_run({**req,'search_policy':{'primary_provider':'native'}},[])
    with pytest.raises(ValueError,match='非零'):store.create_run({**req,'research_budget':{'search_requests':0,'candidate_urls':1,'source_pages':1}},[])


def test_candidate_selection_cannot_invent_url_and_resume_reuses_search_receipt(tmp_path,monkeypatch):
    store,run,job,runtime,worker,searches,reads=setup(tmp_path,monkeypatch,invalid_selection=True)
    with pytest.raises(ValueError,match='候选 ID'):worker.generate(job)
    assert len(searches)==1 and not reads
    assert list((worker.folder(job)/'fast-web/selection').glob('rejected-*.txt'))
    runtime.selection=['C1'];worker.generate(job)
    assert len(searches)==1 and len(reads)==1


def test_no_readable_original_cannot_publish_search_snippets(tmp_path,monkeypatch):
    store,run,job,runtime,worker,searches,reads=setup(tmp_path,monkeypatch)
    runtime.selection=['C2']
    with pytest.raises(ValueError,match='不能凭搜索摘要'):worker.generate(job)
    assert not store.rows('SELECT * FROM briefs')
    assert (worker.folder(job)/'fast-web/research.json').exists()


def test_cancelled_or_unsettled_request_is_not_sent(tmp_path,monkeypatch):
    store,run,job,runtime,worker,searches,reads=setup(tmp_path,monkeypatch)
    receipt=tmp_path/'receipt.json';called=[]
    runtime.cancelled.set()
    with pytest.raises(InterruptedError):fast_research._once(worker,runtime,receipt,lambda:called.append(1))
    runtime.cancelled.clear();receipt.write_text(dump({'status':'started'}))
    assert fast_research._once(worker,runtime,receipt,lambda:called.append(1))['status']=='uncertain'
    assert not called


def test_allowed_tavily_extract_fallback_keeps_direct_failure_and_unique_page_budget(tmp_path,monkeypatch):
    from briefloop import tavily
    store=Store(tmp_path)
    policy={'primary_provider':'tavily','native_search_enabled':False,'coverage_mode':'primary_only'}
    run=store.create_run({'title':'Report','objective':'Public report','completion_mode':'fast_web','search_policy':policy},[],research_protocol='quality_v1')
    job=store.enqueue('generate',{'run_id':run['id'],'agent_backend':'codex'});store.update_job(job['id'],'running')
    runtime=WebWriter(store);worker=Worker(store,runtime)
    def decision(worker,job,folder,prompt,validate):
        return validate({'queries':[{'provider':'tavily','query':'official documentation'}]} if folder.name=='query-plan' else {'candidate_ids':['C1'],'gaps':[]})
    monkeypatch.setattr(fast_research,'_decision',decision)
    monkeypatch.setattr(tavily,'key_status',lambda:{'configured':True})
    monkeypatch.setattr(tavily,'_read_key',lambda *a,**k:('tvly-test','test'))
    calls=[]
    def post(endpoint,body,**kwargs):
        calls.append(endpoint)
        row={'url':'https://docs.example.com/page','title':'Doc','content':'search snippet'}
        if endpoint=='extract':row['raw_content']='收入 120 万元，同比增长 20%'
        result={'results':[row]};return result,dump(result).encode()
    monkeypatch.setattr(tavily,'_post',post)
    monkeypatch.setattr(sources,'_fetch_bytes',lambda *a,**k:(_ for _ in ()).throw(ValueError('来源地址指向本机或内网，已拒绝读取')))
    outcome=worker.generate(job)
    assert calls==['search','extract']
    assert research_budget.snapshot(store,run['id'])['used']['source_pages']==1
    statuses={store.one('sources',sid)['status'] for sid in store.source_ids(run['id'])}
    assert statuses=={'failed','ready'}
    assert 'Tavily 提取文本' in store.one('briefs',outcome['version_id'])['detail']
    assert not fast_research._public_hostname('http://127.0.0.1/secret')
    assert not fast_research._public_hostname('http://app.internal/secret')
    assert not fast_research._public_hostname('http://user:password@example.com/')
