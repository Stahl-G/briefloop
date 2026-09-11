"""Managed-tool budgets: real SQLite contention with fake HTTP only."""
from concurrent.futures import ThreadPoolExecutor
import json
import threading
import pytest
from pathlib import Path
from briefloop import research_budget as budget, sources, tavily
from briefloop.models import Requirements,RESEARCH_BUDGET_PRESETS
from briefloop.store import Store,dump


def make_run(tmp_path,limits):
    store=Store(tmp_path/'workspace')
    store.set_meta('settings',{**store.settings(),'search_provider':'tavily'})
    run=store.create_run({'title':'test','objective':'research','allow_web':True,'research_budget':limits},[])
    store.enqueue('generate',{'run_id':run['id']})
    return store,run


def test_parallel_failed_searches_share_a_hard_request_limit(tmp_path,monkeypatch):
    store,run=make_run(tmp_path,{'search_requests':2,'candidate_urls':10,'source_pages':2})
    calls=[];lock=threading.Lock()
    def failure(*args,**kwargs):
        with lock:calls.append('attempt')
        raise tavily.TavilyError('synthetic HTTP failure')
    monkeypatch.setattr(tavily,'_post',failure)
    def invoke(number):
        try:return tavily.search('q'+str(number),store=store,run_id=run['id'])['status']
        except tavily.TavilyError:return 'failed'
    with ThreadPoolExecutor(max_workers=6) as pool:outcomes=list(pool.map(invoke,range(6)))
    assert outcomes.count('failed')==2 and outcomes.count('budget_exhausted')==4
    assert len(calls)==2
    state=budget.snapshot(store,run['id'])
    assert state['used']['search_requests']==2 and state['remaining']['search_requests']==0
    files=[p for p in (store.root/'discovery'/run['id']).glob('*.json') if not p.name.endswith('.request.json')]
    assert len(files)==2
    records=[p for p in (store.root/'discovery'/run['id']).glob('*.request.json')]
    assert len(records)==2
    import json as _json
    for record in records:
        data=_json.loads(record.read_text())
        assert data['outcome']=='failed' and data['failure_kind']=='provider_error'
        assert data['query'].startswith('q') and data['provider']=='tavily' and data['operation']=='search'
        assert data['local_request_id'] and data['provider_request_id'] is None


def test_candidate_overflow_is_explicit_and_complete_response_is_retained(tmp_path,monkeypatch):
    store,run=make_run(tmp_path,{'search_requests':5,'candidate_urls':2,'source_pages':2})
    response={'results':[{'url':'https://example.test/'+str(i),'content':'snippet'} for i in range(4)],'request_id':'provider-id'}
    raw=json.dumps(response).encode();requests=[]
    def search_response(endpoint,payload,**kwargs):requests.append(payload);return response,raw
    monkeypatch.setattr(tavily,'_post',search_response)
    result=tavily.search('query',max_results=10,store=store,run_id=run['id'])
    assert requests[0]['max_results']==2
    assert result['status']=='budget_exhausted' and len(result['results'])==2
    assert len(result['unadmitted_urls'])==2 and Path(result['discovery_path']).read_bytes()==raw
    assert result['budget']['used']['candidate_urls']==2
    assert tavily.search('again',store=store,run_id=run['id'])['status']=='budget_exhausted'
    assert len(requests)==1
    admitted=budget.record_candidates(store,run['id'],['https://example.test/0#section'])
    assert not admitted['unadmitted_urls'] and admitted['budget']['used']['candidate_urls']==2


def test_failed_direct_then_extract_and_cache_count_one_page_per_run(tmp_path,monkeypatch):
    limits={'search_requests':2,'candidate_urls':4,'source_pages':1}
    store,run=make_run(tmp_path,limits);fetches=[];extracts=[]
    url='https://example.test/report'
    def failed_fetch(value):fetches.append(value);raise OSError('synthetic direct failure')
    monkeypatch.setattr(sources,'_fetch_bytes',failed_fetch)
    failed=sources.fetch_for_run(store,run['id'],url)
    assert failed['status']=='failed'
    def extract_response(endpoint,payload,**kwargs):
        extracts.append(payload)
        result={'results':[{'url':url,'raw_content':'verified source body'}]}
        return result,json.dumps(result).encode()
    monkeypatch.setattr(tavily,'_post',extract_response)
    extracted=tavily.extract(store,[url],run_id=run['id'])
    assert extracted['sources'][0]['status']=='ready'
    assert budget.snapshot(store,run['id'])['used']['source_pages']==1
    assert tavily.extract(store,[url],run_id=run['id'])['reused']
    assert sources.fetch_for_run(store,run['id'],url)['reused']
    assert len(fetches)==len(extracts)==1
    assert sources.fetch_for_run(store,run['id'],'https://example.test/extra')['status']=='budget_exhausted'
    assert tavily.extract(store,['https://example.test/extra'],run_id=run['id'])['status']=='budget_exhausted'
    assert len(fetches)==len(extracts)==1
    other=store.create_run({'title':'other','objective':'research','allow_web':True,'research_budget':limits},[])
    sources.fetch_for_run(store,other['id'],url)
    assert len(fetches)==2 and budget.snapshot(store,other['id'])['used']['source_pages']==1
    assert store.one('sources',failed['id'])['status']=='failed'


def test_defaults_native_scope_and_legacy_missing_budget_are_explicit(tmp_path):
    assert Requirements(title='weekly',objective='research').research_budget.model_dump()==RESEARCH_BUDGET_PRESETS['weekly']
    store=Store(tmp_path/'workspace')
    run=store.create_run({'title':'native','objective':'research','allow_web':True},[])
    current=budget.snapshot(store,run['id'])
    assert current['used']['search_requests'] is None and current['remaining']['candidate_urls'] is None
    assert current['scope']['native_codex_search_metered'] is False
    raw=json.loads(run['requirements']);raw.pop('research_budget')
    with store.tx() as connection:connection.execute('UPDATE runs SET requirements=? WHERE id=?',(dump(raw),run['id']))
    for i in range(20):budget.reserve_pages(store,run['id'],['https://example.test/'+str(i)])
    assert budget.snapshot(store,run['id'])['limits'] is None
    assert budget.snapshot(store,run['id'])['remaining']['source_pages'] is None
    assert 'research_budget' not in json.loads(store.one('runs',run['id'])['requirements'])


def test_http_failure_kinds_are_stable():
    assert tavily._http_kind(401)=='auth' and tavily._http_kind(403)=='auth'
    assert tavily._http_kind(402)=='quota' and tavily._http_kind(429)=='rate_limit'
    assert tavily._http_kind(500)=='provider_error' and tavily._http_kind(400)=='provider_error'


def test_failed_search_records_kind_and_parameters(tmp_path,monkeypatch):
    store,run=make_run(tmp_path,{'search_requests':2,'candidate_urls':10,'source_pages':2})
    def failure(*args,**kwargs):raise tavily._marked('auth failed','auth',401)
    monkeypatch.setattr(tavily,'_post',failure)
    with pytest.raises(tavily.TavilyError) as info:
        tavily.search('blocked query',topic='news',start_date='2026-09-05',end_date='2026-09-11',store=store,run_id=run['id'])
    assert info.value.failure_kind=='auth'
    record=json.loads(Path(info.value.request_record_path).read_text())
    assert record['outcome']=='failed' and record['failure_kind']=='auth'
    assert record['query']=='blocked query' and record['parameters']['topic']=='news'
    assert record['parameters']['start_date']=='2026-09-05'
    assert record['raw_response_path'] is None and record['provider_request_id'] is None


def test_success_envelope_separates_local_and_provider_request_ids(tmp_path,monkeypatch):
    store,run=make_run(tmp_path,{'search_requests':2,'candidate_urls':10,'source_pages':2})
    response={'results':[{'url':'https://example.test/a','content':'snippet'}],'request_id':'provider-abc','usage':{'credits':1}}
    raw=json.dumps(response).encode()
    monkeypatch.setattr(tavily,'_post',lambda *args,**kwargs:(response,raw))
    out=tavily.search('query',store=store,run_id=run['id'])
    assert out['provider_request_id']=='provider-abc'
    assert out['local_request_id'] and out['local_request_id']!=out['provider_request_id']
    record=json.loads(Path(out['request_record_path']).read_text())
    assert record['outcome']=='success' and record['failure_kind'] is None
    assert record['provider_request_id']=='provider-abc' and record['local_request_id']==out['local_request_id']
    assert record['admitted_urls']==['https://example.test/a'] and record['unadmitted_urls']==[]
    assert record['raw_response_path']==out['discovery_path']
    assert record['parameters']['max_results']==5
