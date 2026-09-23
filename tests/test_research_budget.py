"""Managed-tool budgets: real SQLite contention with fake HTTP only."""
from concurrent.futures import ThreadPoolExecutor
import json
import multiprocessing
import threading
import time
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
    assert requests[0]['max_results']==10
    assert result['status']=='budget_exhausted' and len(result['results'])==2
    assert len(result['unadmitted_urls'])==2 and Path(result['discovery_path']).read_bytes()==raw
    assert result['budget']['used']['candidate_urls']==2
    assert tavily.search('again',store=store,run_id=run['id'])['status']=='budget_exhausted'
    assert len(requests)==1
    admitted=budget.record_candidates(store,run['id'],['https://example.test/0#section'])
    assert not admitted['unadmitted_urls'] and admitted['budget']['used']['candidate_urls']==2


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


def test_failed_direct_then_extract_and_cache_count_one_page_per_run(tmp_path,monkeypatch):
    limits={'search_requests':2,'candidate_urls':4,'source_pages':1}
    store,run=make_run(tmp_path,limits);fetches=[];extracts=[]
    url='https://example.test/report'
    def failed_fetch(value,**_):fetches.append(value);raise OSError('synthetic direct failure')
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


def test_parallel_same_url_fetch_uses_one_network_request(tmp_path,monkeypatch):
    store,run=make_run(tmp_path,{'search_requests':2,'candidate_urls':4,'source_pages':1})
    entered=threading.Event();release=threading.Event();calls=[]
    def response(url,**_):
        calls.append(url);entered.set()
        assert release.wait(5)
        return b'one retained body','text/plain','utf-8'
    monkeypatch.setattr(sources,'_fetch_bytes',response)
    url='https://example.test/report'
    with ThreadPoolExecutor(max_workers=2) as pool:
        first=pool.submit(sources.fetch_for_run,Store(store.root),run['id'],url+'#first')
        assert entered.wait(5)
        second=pool.submit(sources.fetch_for_run,Store(store.root),run['id'],url+'#second')
        time.sleep(.1)
        release.set()
        a,b=first.result(timeout=5),second.result(timeout=5)
    assert len(calls)==1
    assert a['id']==b['id']
    assert budget.snapshot(store,run['id'])['used']['source_pages']==1


def test_separate_processes_share_the_page_claim(tmp_path):
    store,run=make_run(tmp_path,{'search_requests':2,'candidate_urls':4,'source_pages':1})
    ctx=multiprocessing.get_context('spawn')
    entered=ctx.Event();waiting=ctx.Event();release=ctx.Event();count=ctx.Value('i',0);outputs=ctx.Queue()
    url='https://example.test/process'
    first=ctx.Process(target=_process_fetch,args=(str(store.root),run['id'],url+'#a',entered,release,count,outputs))
    second=ctx.Process(target=_process_fetch,args=(str(store.root),run['id'],url+'#b',entered,release,count,outputs,waiting))
    first.start()
    try:
        assert entered.wait(10)
        second.start()
        assert waiting.wait(10)
        release.set()
        first.join(10);second.join(10)
        assert first.exitcode==second.exitcode==0
        a,b=outputs.get(timeout=2),outputs.get(timeout=2)
        assert not a.get('error') and not b.get('error')
        assert a['id']==b['id'] and count.value==1
    finally:
        release.set()
        if first.is_alive():first.terminate();first.join()
        if second.is_alive():second.terminate();second.join()


def test_parallel_extract_claims_only_unowned_canonical_urls(tmp_path,monkeypatch):
    store,run=make_run(tmp_path,{'search_requests':2,'candidate_urls':4,'source_pages':2})
    entered=threading.Event();waiting=threading.Event();release=threading.Event();calls=[]
    original_wait=budget.wait_for_claim
    def observed_wait(*args,**kwargs):
        waiting.set()
        return original_wait(*args,**kwargs)
    monkeypatch.setattr(budget,'wait_for_claim',observed_wait)
    def response(endpoint,payload,**_):
        calls.append(list(payload['urls']))
        if len(calls)==1:
            entered.set();assert release.wait(5)
        result={'results':[{'url':url,'raw_content':'body for '+url} for url in payload['urls']]}
        return result,json.dumps(result).encode()
    monkeypatch.setattr(tavily,'_post',response)
    common='https://example.test/common';other='https://example.test/other'
    with ThreadPoolExecutor(max_workers=2) as pool:
        first=pool.submit(tavily.extract,Store(store.root),[common+'#one'],run_id=run['id'])
        assert entered.wait(5)
        second=pool.submit(tavily.extract,Store(store.root),[common+'#two',other],run_id=run['id'])
        assert waiting.wait(5)
        release.set()
        a,b=first.result(timeout=5),second.result(timeout=5)
    assert sum(budget.canonical_url(url)==common for batch in calls for url in batch)==1
    assert calls[0]==[common+'#one']
    assert sum(url==other for batch in calls for url in batch)==1
    assert len({item['id'] for item in a['sources']+b['sources']})==2
    assert budget.snapshot(store,run['id'])['used']['source_pages']==2


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


def test_direct_claim_key_preserves_userinfo_case_and_network_uses_original_url(tmp_path,monkeypatch):
    store,run=make_run(tmp_path,{'search_requests':2,'candidate_urls':4,'source_pages':2})
    original='https://Reader:CaseSecret@EXAMPLE.test:0443/report#one'
    same='https://Reader:CaseSecret@example.TEST:443/report#two'
    different='https://reader:CaseSecret@example.test:443/report'
    key='https://Reader:CaseSecret@example.test:443/report'
    assert budget.canonical_url(original)==key
    assert budget.canonical_url(different)!=key
    calls=[]
    def response(url,**_):
        calls.append(url)
        return b'body','text/plain','utf-8'
    monkeypatch.setattr(sources,'_fetch_bytes',response)
    first=sources.fetch_for_run(store,run['id'],original)
    reused=sources.fetch_for_run(store,run['id'],same)
    assert first['id']==reused['id'] and reused['reused']
    sources.fetch_for_run(store,run['id'],different)
    assert calls==[original,different]
    assert store.meta('research_budget:'+run['id'])['source_pages']==[key,budget.canonical_url(different)]


def test_tavily_batch_uses_first_original_url_per_canonical_key(tmp_path,monkeypatch):
    store,run=make_run(tmp_path,{'search_requests':2,'candidate_urls':4,'source_pages':2})
    original='https://Reader:CaseSecret@EXAMPLE.test:0443/report#one'
    same='https://Reader:CaseSecret@example.TEST:443/report#two'
    other='https://example.test/other'
    calls=[]
    def provider(endpoint,payload,**_):
        calls.append(list(payload['urls']))
        response={'results':[{'url':url,'raw_content':'body'} for url in payload['urls']]}
        return response,json.dumps(response).encode()
    monkeypatch.setattr(tavily,'_post',provider)
    result=tavily.extract(store,[original,same,other],run_id=run['id'])
    assert calls==[[original,other]]
    assert [row['url'] for row in result['sources']]==[original,other]
    assert budget.snapshot(store,run['id'])['used']['source_pages']==2


def test_waiter_reports_failed_tavily_source_in_extraction_failed_urls(tmp_path,monkeypatch):
    store,run=make_run(tmp_path,{'search_requests':2,'candidate_urls':4,'source_pages':1})
    url='https://example.test/empty'
    started=threading.Event();waiting=threading.Event();release=threading.Event();calls=[]
    original_wait=budget.wait_for_claim
    def observed_wait(*args,**kwargs):
        waiting.set();return original_wait(*args,**kwargs)
    monkeypatch.setattr(budget,'wait_for_claim',observed_wait)
    def provider(endpoint,payload,**_):
        calls.append(payload['urls'])
        started.set();assert release.wait(5)
        response={'results':[{'url':url,'raw_content':''}]}
        return response,json.dumps(response).encode()
    monkeypatch.setattr(tavily,'_post',provider)
    with ThreadPoolExecutor(max_workers=2) as pool:
        owner=pool.submit(tavily.extract,Store(store.root),[url],run_id=run['id'])
        assert started.wait(5)
        waiter=pool.submit(tavily.extract,Store(store.root),[url+'#reader'],run_id=run['id'])
        assert waiting.wait(5)
        release.set()
        first,second=owner.result(timeout=5),waiter.result(timeout=5)
    assert calls==[[url]]
    assert first['extraction_failed_urls']==[url]
    assert second['sources'][0]['status']=='failed' and second['sources'][0]['reused'] is True
    assert second['extraction_failed_urls']==[url]


@pytest.mark.parametrize('budget_refusal',[False,True])
def test_cached_source_survives_other_waited_failure(tmp_path,monkeypatch,budget_refusal):
    store,run=make_run(tmp_path,{'search_requests':2,'candidate_urls':4,'source_pages':1})
    a='https://example.test/cached';b='https://example.test/busy';c='https://example.test/extra'
    cached=store.add_source('cached','ready body',url=a)
    store.attach_source(run['id'],cached['id'])
    started=threading.Event();waiting=threading.Event();release=threading.Event()
    original_wait=budget.wait_for_claim
    def observed_wait(*args,**kwargs):
        waiting.set();return original_wait(*args,**kwargs)
    monkeypatch.setattr(budget,'wait_for_claim',observed_wait)
    def provider(endpoint,payload,**_):
        assert payload['urls']==[b]
        started.set();assert release.wait(5)
        raise tavily._marked('synthetic waited failure','provider_error')
    monkeypatch.setattr(tavily,'_post',provider)
    with ThreadPoolExecutor(max_workers=2) as pool:
        owner=pool.submit(tavily.extract,Store(store.root),[b],run_id=run['id'])
        assert started.wait(5)
        mixed=pool.submit(tavily.extract,Store(store.root),[a,b]+([c] if budget_refusal else []),run_id=run['id'])
        assert waiting.wait(5)
        release.set()
        with pytest.raises(tavily.TavilyError):owner.result(timeout=5)
        result=mixed.result(timeout=5)
    assert [row['id'] for row in result['sources']]==[cached['id']]
    assert result['waiting_errors'][b]=='synthetic waited failure'
    assert b in result['unprocessed_urls'] and result['partial'] is True
    assert result['extraction_failed_urls']==[]
    if budget_refusal:
        assert result['status']=='budget_exhausted' and c in result['unprocessed_urls']
    else:
        assert result['status']=='partial' and result['outcome']=='partial'


def test_expired_page_owner_cannot_bind_after_takeover(tmp_path):
    store,run=make_run(tmp_path,{'search_requests':2,'candidate_urls':4,'source_pages':1})
    url='https://example.test/report'
    old=budget.claim_pages(store,run['id'],[url])
    old_pending=sources.PendingSources(store)
    old_source=old_pending.add_source('old','old body',url=url)
    with store.tx() as connection:
        connection.execute('UPDATE page_claims SET expires_at=0 WHERE run_id=? AND url=?',(run['id'],url))
    new=budget.claim_pages(Store(store.root),run['id'],[url])
    assert new['claimed']==[url] and new['owner']!=old['owner']
    assert old_pending.admit(run['id'],old['reservation'],claim_owner=old['owner'],claimed_urls=[url]) is False
    assert old_source['id'] not in store.source_ids(run['id'])
    new_pending=sources.PendingSources(store)
    current=new_pending.add_source('new','current body',url=url)
    assert new_pending.admit(run['id'],new['reservation'],claim_owner=new['owner'],claimed_urls=[url]) is True
    assert store.source_ids(run['id'])==[current['id']]
    assert budget.snapshot(store,run['id'])['used']['source_pages']==1


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


def test_tavily_waiting_on_failed_direct_fetch_retries_provider(tmp_path,monkeypatch):
    store,run=make_run(tmp_path,{'search_requests':2,'candidate_urls':4,'source_pages':1})
    started=threading.Event();release=threading.Event();calls=[]
    def failed_fetch(url,**_):
        started.set();assert release.wait(5)
        raise OSError('direct reader failed')
    def provider(endpoint,payload,**_):
        calls.append(payload['urls'])
        response={'results':[{'url':payload['urls'][0],'raw_content':'provider body'}]}
        return response,json.dumps(response).encode()
    monkeypatch.setattr(sources,'_fetch_bytes',failed_fetch)
    monkeypatch.setattr(tavily,'_post',provider)
    url='https://example.test/report'
    with ThreadPoolExecutor(max_workers=2) as pool:
        direct=pool.submit(sources.fetch_for_run,Store(store.root),run['id'],url)
        assert started.wait(5)
        fallback=pool.submit(tavily.extract,Store(store.root),[url+'#section'],run_id=run['id'])
        time.sleep(.1);release.set()
        failed,result=direct.result(timeout=5),fallback.result(timeout=5)
    assert failed['status']=='failed'
    assert result['sources'][0]['status']=='ready'
    assert calls==[[url+'#section']]
    assert budget.snapshot(store,run['id'])['used']['source_pages']==1


def test_explicit_native_scope_and_legacy_missing_budget_are_explicit(tmp_path):
    assert Requirements(title='weekly',objective='research').research_budget.model_dump()==RESEARCH_BUDGET_PRESETS['weekly']
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
    assert record['parameters']['start_date']==json.loads(run['requirements'])['time_context']['start'][:10]
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


def test_unknown_failure_kind_is_rejected_and_cli_payload_is_structured():
    with pytest.raises(ValueError):
        tavily._marked('x','not_a_kind')
    from briefloop import cli
    exc=tavily._marked('auth failed','auth',401)
    payload=cli._tavily_failure('search',exc)
    assert payload=={'provider':'tavily','operation':'search','status':'failed','failure_kind':'auth','http_status':401,'error':'auth failed','request_record_path':None}


def test_extract_separates_budget_refusal_from_extraction_failure(tmp_path,monkeypatch):
    store,run=make_run(tmp_path,{'search_requests':2,'candidate_urls':10,'source_pages':2})
    monkeypatch.setattr(tavily,'_post',lambda *args,**kwargs:({'results':[{'url':'https://example.test/a','raw_content':''}],'request_id':'p1'},b'{}'))
    out=tavily.extract(store,['https://example.test/a'],run_id=run['id'])
    assert out['extraction_failed_urls']==['https://example.test/a']
    assert 'unadmitted_urls' not in out
    record=json.loads(Path(out['request_record_path']).read_text())
    assert record['unadmitted_urls']==[] and record['extraction_failed_urls']==['https://example.test/a']
