"""Managed-tool budgets: real SQLite contention with fake HTTP only."""
from concurrent.futures import ThreadPoolExecutor
import json
import threading
import pytest
from pathlib import Path
from briefloop import research_budget as budget, sources, tavily
from briefloop.store import Store,dump


def make_run(tmp_path,limits):
    store=Store(tmp_path/'workspace')
    store.set_meta('settings',{**store.settings(),'search_provider':'tavily'})
    run=store.create_run({'title':'test','objective':'research','allow_web':True,'research_budget':limits},[])
    store.enqueue('generate',{'run_id':run['id']})
    return store,run


def _process_fetch(workspace,run_id,url,entered,release,count,outputs,waiting=None):
    from briefloop import sources as child_sources
    from briefloop import research_budget as child_budget
    from briefloop.store import Store as ChildStore
    if waiting is not None:
        original_wait=child_budget.wait_for_claim
        def observed_wait(*args,**kwargs):
            waiting.set()
            return original_wait(*args,**kwargs)
        child_budget.wait_for_claim=observed_wait
    def response(_url,**_):
        with count.get_lock():count.value+=1
        entered.set()
        if not release.wait(10):raise OSError('synthetic reader timed out')
        return b'one process response','text/plain','utf-8'
    child_sources._fetch_bytes=response
    try:outputs.put(child_sources.fetch_for_run(ChildStore(workspace),run_id,url))
    except Exception as exc:outputs.put({'error':repr(exc)})


def test_known_top_result_does_not_hide_new_candidate_when_one_slot_remains(tmp_path,monkeypatch):
    from briefloop import research_plan as plan
    limits={'search_requests':2,'candidate_urls':2,'source_pages':1}
    store=Store(tmp_path/'workspace')
    store.set_meta('settings',{**store.settings(),'search_provider':'tavily'})
    run=store.create_run({'title':'quality','objective':'research','allow_web':True,
                          'fact_check':False,'research_budget':limits},[],research_protocol='quality_v1')['id']
    plan.freeze(store,run)
    known='https://example.test/known';new='https://example.test/new';overflow='https://example.test/overflow'
    requests=[]
    def search_response(endpoint,payload,**_):
        requests.append(payload['max_results'])
        ranked=([known] if len(requests)==1 else [known,new,overflow])
        response={'results':[{'url':url,'content':'snippet'} for url in ranked[:payload['max_results']]]}
        return response,json.dumps(response).encode()
    monkeypatch.setattr(tavily,'_post',search_response)
    first=tavily.search('first',max_results=1,store=store,run_id=run)
    assert first['status']=='ok' and [row['url'] for row in first['results']]==[known]
    second=tavily.search('second',max_results=3,store=store,run_id=run)
    assert requests==[1,3]  # One remaining admission slot must not truncate provider ranking.
    assert second['status']=='budget_exhausted'
    assert [row['url'] for row in second['results']]==[known,new]
    assert second['unadmitted_urls']==[overflow]
    current=budget.snapshot(store,run)
    assert current['used']=={'search_requests':2,'candidate_urls':2,'source_pages':0}
    assert current['stages']['research']['search_requests']==2
    assert current['remaining']=={'search_requests':0,'candidate_urls':0,'source_pages':1}
    assert tavily.search('third',store=store,run_id=run)['status']=='budget_exhausted'
    assert requests==[1,3]


def test_extract_keeps_owned_success_when_waited_provider_fails(tmp_path,monkeypatch):
    from briefloop import research_plan as plan
    limits={'search_requests':2,'candidate_urls':4,'source_pages':2}
    store=Store(tmp_path/'workspace')
    store.set_meta('settings',{**store.settings(),'search_provider':'tavily'})
    run=store.create_run({'title':'quality','objective':'research','allow_web':True,
                          'fact_check':False,'research_budget':limits},[],research_protocol='quality_v1')['id']
    plan.freeze(store,run);plan.finish_round(store,run)
    plan.admit_fact_check(store,run,{'kind':'task_reserve','limits':limits})
    a='https://example.test/a';b='https://example.test/b'
    b_started=threading.Event();release_b=threading.Event();a_waiting=threading.Event()
    original_wait=budget.wait_for_claim
    def observed_wait(_store,_run,url,owner):
        if url==b:a_waiting.set()
        return original_wait(_store,_run,url,owner)
    monkeypatch.setattr(budget,'wait_for_claim',observed_wait)
    def provider(endpoint,payload,**_):
        if payload['urls']==[b]:
            b_started.set();assert release_b.wait(5)
            raise tavily._marked('synthetic B provider failure','provider_error')
        assert payload['urls']==[a]
        response={'results':[{'url':a,'raw_content':'A retained body'}],'request_id':'provider-a'}
        return response,json.dumps(response).encode()
    monkeypatch.setattr(tavily,'_post',provider)
    with ThreadPoolExecutor(max_workers=2) as pool:
        b_owner=pool.submit(tavily.extract,Store(store.root),[b],run_id=run)
        assert b_started.wait(5)
        mixed=pool.submit(tavily.extract,Store(store.root),[a,b],run_id=run)
        assert a_waiting.wait(5)
        release_b.set()
        with pytest.raises(tavily.TavilyError):b_owner.result(timeout=5)
        result=mixed.result(timeout=5)
    assert result['status']=='partial' and result['outcome']=='partial'
    assert [(item['url'],item['status']) for item in result['sources']]==[(a,'ready')]
    assert result['unprocessed_urls']==[b] and 'synthetic B' in result['waiting_errors'][b]
    assert store.source_ids(run)==[result['sources'][0]['id']]
    assert store.source_text(result['sources'][0]['id'])=='A retained body'
    record=json.loads(Path(result['request_record_path']).read_text())
    assert record['outcome']=='success' and record['admitted_urls']==[a]
    assert record['unadmitted_urls']==[] and record['raw_response_path']
    assert record['waited_unprocessed_urls']==[b]
    assert plan.pending_requests(store,run)[result['local_request_id']]['status']=='completed'


def test_expired_claim_settles_old_quality_request_before_reclaim(tmp_path,monkeypatch):
    from briefloop import research_plan as plan
    limits={'search_requests':2,'candidate_urls':4,'source_pages':1}
    store=Store(tmp_path/'workspace')
    run=store.create_run({'title':'quality','objective':'research','allow_web':True,
                          'fact_check':False,'research_budget':limits},[],research_protocol='quality_v1')['id']
    plan.freeze(store,run)
    plan.finish_round(store,run)
    plan.admit_fact_check(store,run,{'kind':'task_reserve','limits':limits})
    url='https://example.test/report'
    state={}
    def late_response(_url,**_):
        row=store.rows('SELECT owner,request_id FROM page_claims WHERE run_id=? AND url=?',(run,url))[0]
        state['old_id']=row['request_id']
        assert plan.pending_requests(store,run)[row['request_id']]['status']=='reserved'
        with store.tx() as connection:
            connection.execute('UPDATE page_claims SET expires_at=0 WHERE run_id=? AND url=?',(run,url))
        state['new']=budget.claim_pages(Store(store.root),run,[url])
        expired=plan.pending_requests(store,run)[row['request_id']]
        assert expired['status']=='response_rejected'
        assert expired['rejection_reason']=='page_claim_expired' and expired['response_status']=='unobserved'
        return b'late original response','text/plain','utf-8'
    monkeypatch.setattr(sources,'_fetch_bytes',late_response)
    with pytest.raises(plan.AdmissionError) as rejected:
        sources.fetch_for_run(store,run,url)
    envelope=json.loads(Path(rejected.value.request_record_path).read_text())
    provenance=json.loads((store.root/envelope['provenance_path']).read_text())
    assert (store.root/provenance['original_path']).read_bytes()==b'late original response'
    assert store.source_ids(run)==[]
    settled=plan.pending_requests(store,run)[state['old_id']]
    assert settled['response_status']=='completed'
    current=sources.PendingSources(store)
    new_source=current.add_source('current','current response',url=url)
    new=state['new']
    assert current.admit(run,new['reservation'],claim_owner=new['owner'],claimed_urls=[url])
    assert store.source_ids(run)==[new_source['id']]
    assert budget.snapshot(store,run)['used']['source_pages']==1


def test_explicit_native_scope_and_legacy_missing_budget_are_explicit(tmp_path):
    store=Store(tmp_path/'workspace')
    store.set_meta('settings',{**store.settings(),'search_provider':'native'})
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


def test_unknown_failure_kind_is_rejected_and_cli_payload_is_structured():
    with pytest.raises(ValueError):
        tavily._marked('x','not_a_kind')
    from briefloop import cli
    exc=tavily._marked('auth failed','auth',401)
    payload=cli._tavily_failure('search',exc)
    assert payload=={'provider':'tavily','operation':'search','status':'failed','failure_kind':'auth','http_status':401,'error':'auth failed','request_record_path':None}
