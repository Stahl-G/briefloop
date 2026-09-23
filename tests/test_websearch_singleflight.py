"""In-flight managed search reuse, with real SQLite contention and fake providers."""
from concurrent.futures import ThreadPoolExecutor
import json
import multiprocessing
from pathlib import Path
import threading
import time

import pytest

from briefloop import duckduckgo, research_budget as budget, research_plan as plan, tavily, websearch
from briefloop.store import Store


LIMITS={'search_requests':2,'candidate_urls':8,'source_pages':2}


def make_run(tmp_path,*,provider='tavily',limits=None,quality=False):
    store=Store(tmp_path/'workspace')
    store.set_meta('settings',{**store.settings(),'search_provider':provider})
    run=store.create_run({'title':'test','objective':'research','allow_web':True,
                          'research_budget':limits or LIMITS},[],
                         **({'research_protocol':'quality_v1'} if quality else {}))['id']
    store.enqueue('generate',{'run_id':run})
    if quality:plan.freeze(store,run)
    return store,run


def _response(urls):
    value={'results':[{'url':url,'title':'Title','content':'snippet'} for url in urls],
           'request_id':'provider-1'}
    return value,json.dumps(value).encode()


def _ddg_html():
    html='<div class="result"><h2 class="result__title"><a class="result__a" href="https://example.test/a">A</a></h2></div>'
    return html,html.encode()


def test_waiter_reports_missing_saved_result_as_local_error(tmp_path,monkeypatch):
    store,run=make_run(tmp_path)
    monkeypatch.setattr(budget,'wait_for_search_claim',lambda *_args: {
        'outcome':'success','result_path':str(tmp_path/'missing-result.json'),
        'request_record_path':'synthetic-request-record'})
    with pytest.raises(websearch.SearchError) as caught:
        websearch._joined_search(store,run,{'claim_key':'key','waiting':'owner'},tavily,'tavily')
    assert caught.value.failure_kind=='local_error'
    assert caught.value.request_record_path=='synthetic-request-record'


def _process_search(workspace,run_id,entered,release,waiting,count,outputs,second):
    from briefloop import duckduckgo as child_ddg, research_budget as child_budget, websearch as child_search
    from briefloop.store import Store as ChildStore
    if second:
        original=child_budget.wait_for_search_claim
        def observe(*args,**kwargs):
            waiting.set()
            return original(*args,**kwargs)
        child_budget.wait_for_search_claim=observe
    def provider(_form):
        with count.get_lock():count.value+=1
        entered.set()
        if not release.wait(10):raise RuntimeError('synthetic provider timed out')
        return _ddg_html()
    child_ddg._post=provider
    try:outputs.put(child_search.search('same query',store=ChildStore(workspace),run_id=run_id))
    except Exception as exc:outputs.put({'error':repr(exc)})


def test_same_query_threads_share_one_request_and_canonical_result(tmp_path,monkeypatch):
    store,run=make_run(tmp_path,limits={'search_requests':1,'candidate_urls':3,'source_pages':1})
    entered=threading.Event();waiting=threading.Event();release=threading.Event();calls=[]
    original=budget.wait_for_search_claim
    def observe(*args,**kwargs):waiting.set();return original(*args,**kwargs)
    monkeypatch.setattr(budget,'wait_for_search_claim',observe)
    def provider(*_args,**_kwargs):
        calls.append(1);entered.set();assert release.wait(5)
        return _response(['https://EXAMPLE.test/a#first','https://example.test/a#second'])
    monkeypatch.setattr(tavily,'_post',provider)
    with ThreadPoolExecutor(max_workers=2) as pool:
        first=pool.submit(websearch.search,'same query',store=Store(store.root),run_id=run)
        assert entered.wait(5)
        second=pool.submit(websearch.search,'same query',store=Store(store.root),run_id=run)
        assert waiting.wait(5)
        release.set();a=first.result(timeout=5);b=second.result(timeout=5)
    assert len(calls)==1 and a['local_request_id']==b['local_request_id']
    assert b['reused_inflight'] is True
    assert [row['url'] for row in a['results']]==['https://EXAMPLE.test/a#first']
    assert budget.snapshot(store,run)['used']=={'search_requests':1,'candidate_urls':1,'source_pages':0}
    assert len(list((store.root/'discovery'/run).glob('*.request.json')))==1
    assert len(json.loads(Path(a['discovery_path']).read_text())['results'])==2


def test_same_query_separate_processes_share_request(tmp_path):
    store,run=make_run(tmp_path,provider='duckduckgo',limits={'search_requests':1,'candidate_urls':3,'source_pages':1})
    ctx=multiprocessing.get_context('spawn')
    entered=ctx.Event();release=ctx.Event();waiting=ctx.Event();count=ctx.Value('i',0);outputs=ctx.Queue()
    first=ctx.Process(target=_process_search,args=(str(store.root),run,entered,release,waiting,count,outputs,False))
    second=ctx.Process(target=_process_search,args=(str(store.root),run,entered,release,waiting,count,outputs,True))
    first.start()
    try:
        assert entered.wait(10)
        second.start();assert waiting.wait(10)
        release.set();first.join(10);second.join(10)
        assert first.exitcode==second.exitcode==0
        a,b=outputs.get(timeout=2),outputs.get(timeout=2)
        assert not a.get('error') and not b.get('error')
        assert a['local_request_id']==b['local_request_id'] and count.value==1
        assert budget.snapshot(store,run)['used']['search_requests']==1
    finally:
        release.set()
        for child in (first,second):
            if child.is_alive():child.terminate();child.join()


def test_options_key_file_and_purpose_keep_distinct_requests(tmp_path,monkeypatch):
    store,run=make_run(tmp_path,limits={'search_requests':5,'candidate_urls':8,'source_pages':2})
    calls=[]
    def provider(*args,**kwargs):calls.append((args,kwargs));return _response(['https://example.test/a'])
    monkeypatch.setattr(tavily,'_post',provider)
    inputs=[{'include_domains':['one.test']},{'include_domains':['two.test']},
            {'include_domains':['one.test'],'purpose':'coverage_probe'},
            {'include_domains':['one.test'],'key_file':Path('/tmp/provider-a')},
            {'include_domains':['one.test'],'key_file':Path('/tmp/provider-b')}]
    # Sequential calls are deliberately never cached by an in-flight claim.
    for options in inputs:websearch.search('same',store=store,run_id=run,**options)
    assert len(calls)==5 and budget.snapshot(store,run)['used']['search_requests']==5
    assert not store.rows('SELECT * FROM search_claims')


def test_different_options_do_not_join_while_first_is_in_flight(tmp_path,monkeypatch):
    store,run=make_run(tmp_path)
    entered=threading.Event();release=threading.Event();calls=[]
    def provider(_endpoint,parameters,**_kwargs):
        calls.append(parameters['include_domains'])
        if parameters['include_domains']==['one.test']:
            entered.set();assert release.wait(5)
        return _response(['https://example.test/a'])
    monkeypatch.setattr(tavily,'_post',provider)
    with ThreadPoolExecutor(max_workers=2) as pool:
        first=pool.submit(websearch.search,'same',include_domains=['one.test'],store=Store(store.root),run_id=run)
        assert entered.wait(5)
        second=pool.submit(websearch.search,'same',include_domains=['two.test'],store=Store(store.root),run_id=run)
        b=second.result(timeout=5)
        release.set();a=first.result(timeout=5)
    assert a['local_request_id']!=b['local_request_id']
    assert calls==[['one.test'],['two.test']]
    assert budget.snapshot(store,run)['used']['search_requests']==2


def test_failure_is_shared_then_later_explicit_retry_is_new_request(tmp_path,monkeypatch):
    store,run=make_run(tmp_path)
    entered=threading.Event();waiting=threading.Event();release=threading.Event();calls=[]
    original=budget.wait_for_search_claim
    def observe(*args,**kwargs):waiting.set();return original(*args,**kwargs)
    monkeypatch.setattr(budget,'wait_for_search_claim',observe)
    def provider(*_args,**_kwargs):
        calls.append(1)
        if len(calls)==1:
            entered.set();assert release.wait(5)
            raise tavily._marked('synthetic rate limit','rate_limit',429)
        return _response(['https://example.test/retry'])
    monkeypatch.setattr(tavily,'_post',provider)
    def invoke():
        try:return websearch.search('same',store=Store(store.root),run_id=run)
        except websearch.SearchError as exc:return exc
    with ThreadPoolExecutor(max_workers=2) as pool:
        first=pool.submit(invoke);assert entered.wait(5)
        second=pool.submit(invoke);assert waiting.wait(5)
        release.set();a=first.result(timeout=5);b=second.result(timeout=5)
    assert a.failure_kind==b.failure_kind=='rate_limit'
    assert a.request_record_path==b.request_record_path
    assert len(calls)==1 and budget.snapshot(store,run)['used']['search_requests']==1
    retry=invoke()
    assert retry['status']=='ok' and retry['local_request_id']!=json.loads(Path(a.request_record_path).read_text())['local_request_id']
    assert len(calls)==2 and budget.snapshot(store,run)['used']['search_requests']==2


def test_invalid_provider_url_fails_and_releases_claim(tmp_path,monkeypatch):
    store,run=make_run(tmp_path,quality=True)
    calls=[]
    def provider(*_args,**_kwargs):
        calls.append(1)
        return _response(['' if len(calls)==1 else 'https://example.test/valid'])
    monkeypatch.setattr(tavily,'_post',provider)
    with pytest.raises(websearch.SearchError) as caught:
        websearch.search('same',store=store,run_id=run)
    assert caught.value.failure_kind=='invalid_response'
    assert not store.rows('SELECT * FROM search_claims')
    record=json.loads(Path(caught.value.request_record_path).read_text())
    assert record['raw_response_path'] and Path(record['raw_response_path']).exists()
    assert record['failure_kind']=='invalid_response'
    ledger=plan.pending_requests(store,run)
    assert ledger[record['local_request_id']]['status']=='failed'
    out=websearch.search('same',store=store,run_id=run)
    assert out['status']=='ok' and len(calls)==2


def test_non_bytes_provider_response_is_invalid_not_local_disk_error(tmp_path,monkeypatch):
    store,run=make_run(tmp_path,quality=True)
    monkeypatch.setattr(tavily,'call_search',lambda *_a,**_kw:({'results':[]},'not bytes',None,None))
    with pytest.raises(websearch.SearchError,match='原始响应格式无效') as caught:
        websearch.search('same',store=store,run_id=run)
    assert caught.value.failure_kind=='invalid_response'
    assert next(iter(plan.pending_requests(store,run).values()))['status']=='failed'
    assert not store.rows('SELECT * FROM search_claims')


def test_stage_close_rejects_owner_and_waiter_without_new_stage_charge(tmp_path,monkeypatch):
    store,run=make_run(tmp_path,quality=True)
    entered=threading.Event();waiting=threading.Event();release=threading.Event()
    original=budget.wait_for_search_claim
    def observe(*args,**kwargs):waiting.set();return original(*args,**kwargs)
    monkeypatch.setattr(budget,'wait_for_search_claim',observe)
    def provider(*_args,**_kwargs):
        entered.set();assert release.wait(5)
        return _response(['https://example.test/late'])
    monkeypatch.setattr(tavily,'_post',provider)
    with ThreadPoolExecutor(max_workers=2) as pool:
        first=pool.submit(websearch.search,'same',store=Store(store.root),run_id=run)
        assert entered.wait(5)
        second=pool.submit(websearch.search,'same',store=Store(store.root),run_id=run)
        assert waiting.wait(5)
        plan.finish_round(store,run)
        release.set();a=first.result(timeout=5);b=second.result(timeout=5)
    assert a['status']==b['status']=='response_rejected'
    assert a['local_request_id']==b['local_request_id']
    assert budget.snapshot(store,run)['used']['candidate_urls']==0
    assert budget.snapshot(store,run)['used']['search_requests']==1


def test_expired_owner_is_fenced_from_later_claim(tmp_path):
    store,run=make_run(tmp_path,quality=True)
    identity={'provider':'tavily','query':'same','parameters':{'max_results':5},'purpose':'primary','gap_id':None}
    first=budget.claim_search(store,run,identity,5)
    with store.tx() as connection:
        connection.execute('UPDATE search_claims SET expires_at=0 WHERE run_id=? AND claim_key=?',
                           (run,first['claim_key']))
    second=budget.claim_search(Store(store.root),run,identity,5)
    assert second['owner']!=first['owner'] and second['waiting'] is None
    old=budget.record_candidates(store,run,['https://example.test/old'],reservation=first['reservation'],
                                 claim_key=first['claim_key'],claim_owner=first['owner'])
    new=budget.record_candidates(store,run,['https://example.test/new'],reservation=second['reservation'],
                                 claim_key=second['claim_key'],claim_owner=second['owner'])
    assert old['accepted'] is False and old['rejection_reason']=='search_claim_expired'
    assert new['accepted'] is True
    assert budget.finish_search_claim(store,run,first['claim_key'],first['owner'],outcome='failed',error='old') is False
    assert budget.finish_search_claim(store,run,second['claim_key'],second['owner'],outcome='success') is True
    assert budget.snapshot(store,run)['used']['candidate_urls']==1
    ledger=plan.pending_requests(store,run)
    assert ledger[first['reservation']['request_id']]['status']=='response_rejected'
    assert ledger[second['reservation']['request_id']]['status']=='completed'


def test_new_stage_cannot_join_old_stage_search(tmp_path):
    store,run=make_run(tmp_path,quality=True)
    identity={'provider':'tavily','query':'same','parameters':{'max_results':5},'purpose':'primary','gap_id':None}
    first=budget.claim_search(store,run,identity,5)
    plan.finish_round(store,run)
    plan.admit_fact_check(store,run,{'kind':'task_reserve',
                                     'limits':{'search_requests':1,'candidate_urls':3,'source_pages':1}})
    second=budget.claim_search(store,run,identity,5)
    assert second['waiting'] is None and second['claim_key']!=first['claim_key']
    old=budget.record_candidates(store,run,['https://example.test/old'],reservation=first['reservation'],
                                 claim_key=first['claim_key'],claim_owner=first['owner'])
    new=budget.record_candidates(store,run,['https://example.test/new'],reservation=second['reservation'],
                                 claim_key=second['claim_key'],claim_owner=second['owner'])
    assert old['accepted'] is False and new['accepted'] is True
    assert budget.snapshot(store,run)['used']['candidate_urls']==1


def test_local_raw_save_error_settles_without_provider_failure(tmp_path,monkeypatch):
    store,run=make_run(tmp_path,quality=True)
    monkeypatch.setattr(tavily,'_post',lambda *_a,**_kw:_response(['https://example.test/a']))
    def denied(*_args,**_kwargs):raise OSError('synthetic disk error')
    monkeypatch.setattr(budget,'save_discovery',denied)
    with pytest.raises(websearch.SearchError,match='本地原始响应保存失败') as caught:
        websearch.search('same',store=store,run_id=run)
    assert not store.rows('SELECT * FROM search_claims')
    record=json.loads(Path(caught.value.request_record_path).read_text())
    assert record['outcome']=='local_error' and record['failure_kind']=='local_error'
    assert record['raw_response_path'] is None
    assert plan.pending_requests(store,run)[record['local_request_id']]['status']=='failed'
    assert plan.pending_requests(store,run)[record['local_request_id']]['failure_kind']=='local_error'


def test_local_raw_and_record_save_error_still_settles(tmp_path,monkeypatch):
    store,run=make_run(tmp_path,quality=True)
    monkeypatch.setattr(tavily,'_post',lambda *_a,**_kw:_response(['https://example.test/a']))
    def denied(*_args,**_kwargs):raise OSError('synthetic disk error')
    monkeypatch.setattr(budget,'save_discovery',denied)
    monkeypatch.setattr(budget,'save_request_record',denied)
    with pytest.raises(websearch.SearchError,match='本地原始响应保存失败') as caught:
        websearch.search('same',store=store,run_id=run)
    assert caught.value.request_record_path is None
    assert not store.rows('SELECT * FROM search_claims')
    ledger=plan.pending_requests(store,run)
    assert len(ledger)==1 and next(iter(ledger.values()))['status']=='failed'


def test_waiter_sees_local_save_error_without_provider_failure_kind(tmp_path,monkeypatch):
    store,run=make_run(tmp_path,quality=True)
    entered=threading.Event();waiting=threading.Event();release=threading.Event()
    original=budget.wait_for_search_claim
    def observe(*args,**kwargs):waiting.set();return original(*args,**kwargs)
    monkeypatch.setattr(budget,'wait_for_search_claim',observe)
    def provider(*_args,**_kwargs):
        entered.set();assert release.wait(5)
        return _response(['https://example.test/a'])
    def denied(*_args,**_kwargs):raise OSError('synthetic disk error')
    monkeypatch.setattr(tavily,'_post',provider)
    monkeypatch.setattr(budget,'save_discovery',denied)
    def invoke():
        try:return websearch.search('same',store=Store(store.root),run_id=run)
        except websearch.SearchError as exc:return exc
    with ThreadPoolExecutor(max_workers=2) as pool:
        first=pool.submit(invoke);assert entered.wait(5)
        second=pool.submit(invoke);assert waiting.wait(5)
        release.set();a=first.result(timeout=5);b=second.result(timeout=5)
    assert all(isinstance(error,websearch.SearchError) for error in (a,b))
    assert '本地原始响应保存失败' in str(a) and '本地原始响应保存失败' in str(b)
    assert a.failure_kind==b.failure_kind=='local_error'
    from briefloop.cli import _search_failure
    assert _search_failure('search',b)['failure_kind']=='local_error'
    assert budget.snapshot(store,run)['used']['search_requests']==1


def test_after_admission_output_save_error_keeps_request_completed(tmp_path,monkeypatch):
    store,run=make_run(tmp_path,quality=True)
    monkeypatch.setattr(tavily,'_post',lambda *_a,**_kw:_response(['https://example.test/a']))
    def denied(*_args,**_kwargs):raise OSError('synthetic result save error')
    monkeypatch.setattr(budget,'save_search_result',denied)
    with pytest.raises(websearch.SearchError,match='已接纳，但本地回执保存失败'):
        websearch.search('same',store=store,run_id=run)
    assert not store.rows('SELECT * FROM search_claims')
    assert budget.snapshot(store,run)['used']['candidate_urls']==1
    ledger=plan.pending_requests(store,run)
    assert len(ledger)==1 and next(iter(ledger.values()))['status']=='completed'
    record=json.loads(next((store.root/'discovery'/run).glob('*.request.json')).read_text())
    assert record['outcome']=='success' and record['delivery_error']
