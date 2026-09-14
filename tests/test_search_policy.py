"""One frozen policy across channels, shared admission, provenance and native permissions."""
import json
from concurrent.futures import ThreadPoolExecutor
import pytest
from briefloop import bocha, websearch, research_budget, search_policy
from briefloop.models import SearchPolicy
from briefloop.store import Store
from briefloop.opencode_harness import _permission_rules


def run_with_policy(tmp_path, policy, limit=2):
    store=Store(tmp_path/'workspace')
    store.set_meta('settings',{**store.settings(),'search_policy':policy,'search_provider':policy['primary_provider']})
    run=store.create_run({'title':'公共材料测试','objective':'跨市场核对','allow_web':True,
        'research_budget':{'search_requests':limit,'candidate_urls':10,'source_pages':5}},[])
    job=store.enqueue('generate',{'run_id':run['id']})
    return store,run,job


def test_frozen_channels_shared_concurrent_budget_and_duplicate_provenance(tmp_path,monkeypatch):
    policy={'primary_provider':'tavily','supplemental_providers':['bocha'],'native_search_enabled':True}
    store,run,job=run_with_policy(tmp_path,policy)
    store.set_meta('settings',{**store.settings(),'search_policy':{'primary_provider':'duckduckgo'}})
    expected=SearchPolicy.model_validate(policy).model_dump()
    assert search_policy.for_run(store,run['id'])==expected
    assert json.loads(store.enqueue('fact_check',{'run_id':run['id']})['payload'])['search_policy']==expected
    calls=[]
    class Provider:
        @staticmethod
        def validate_search(q,opts):return opts
        @staticmethod
        def call_search(q,params,**kw):
            calls.append(q)
            return {},b'{}','request',None
        @staticmethod
        def rows(parsed):return [{'url':'https://example.org/doc?utm_source=test','kind':'search_snippet'}]
        @staticmethod
        def search_note():return 'discovery only'
    monkeypatch.setattr(websearch,'provider_module',lambda provider:Provider)
    with ThreadPoolExecutor(max_workers=2) as pool:
        results=list(pool.map(lambda p:websearch.search('公开材料',provider=p,store=store,run_id=run['id'],purpose='coverage_probe',reason='检查中文遗漏'),['tavily','bocha']))
    results.append(websearch.search('追加',provider='tavily',store=store,run_id=run['id']))
    assert len(calls)==2
    assert len([r for r in results if r['status']=='budget_exhausted'])==1
    state=research_budget.snapshot(store,run['id'])
    assert state['used']=={'search_requests':2,'candidate_urls':1,'source_pages':0}
    assert state['scope']['native_search_requests'] is None
    origins=search_policy.annotate_sources(store,[{'url':'https://example.org/doc?utm_source=test'}])[0]['discovery_providers']
    assert set(origins)=={'tavily','bocha'}
    assert len(search_policy.activity(store,run['id'])['records'])==2
    with pytest.raises(ValueError,match='不是 DuckDuckGo'):
        websearch.search('不允许',provider='duckduckgo',store=store,run_id=run['id'])


def test_managed_supplement_works_with_native_primary_and_failures_survive(tmp_path,monkeypatch):
    store,run,_=run_with_policy(tmp_path,{'primary_provider':'native','supplemental_providers':['bocha']})
    def fail(*a,**kw):raise bocha._marked('博查请求失败（HTTP 429）','rate_limit',429)
    monkeypatch.setattr(bocha,'call_search',fail)
    with pytest.raises(websearch.SearchError):
        websearch.search('中文信息',provider='bocha',store=store,run_id=run['id'],purpose='gap_repair',reason='缺中文原文')
    assert research_budget.snapshot(store,run['id'])['used']['search_requests']==1
    record=search_policy.activity(store,run['id'])['records'][0]
    assert record['http_status']==429 and record['failure_kind']=='rate_limit'
    assert record['purpose']=='gap_repair' and record['reason']=='缺中文原文'
    assert record['elapsed_ms']>=0


def test_native_permission_switch_never_opens_reviewer(tmp_path):
    config={'permission':'workspace-write','search_policy':{'primary_provider':'tavily'}}
    def denies(rules):return any(r['permission']=='websearch' and r['action']=='deny' for r in rules)
    assert denies(_permission_rules(config,True,tmp_path))
    config['search_policy']['native_search_enabled']=True
    assert not denies(_permission_rules(config,True,tmp_path))
    assert denies(_permission_rules(config,False,tmp_path))
    config['search_policy']['coverage_mode']='primary_only'
    assert denies(_permission_rules(config,True,tmp_path))
    config['review_root']=str(tmp_path);config['review_worktree']=str(tmp_path)
    rules=_permission_rules(config,True,tmp_path)
    assert rules[0]=={'permission':'*','action':'deny','pattern':'*'}
    assert all(r['permission'] in ('*','read','external_directory') for r in rules)
    assert search_policy.native_allowed({'search_provider':'codex'},True)


def test_bocha_request_mapping_and_malformed_response_redaction(monkeypatch):
    monkeypatch.setattr(bocha,'_read_key',lambda *a,**kw:('private-test-key','test'))
    captured=[]
    response={'code':200,'data':{'webPages':{'value':[{'name':'中文公告','url':'https://example.org/doc','summary':'线索'}]}}}
    class Response:
        def __enter__(self):return self
        def __exit__(self,*a):pass
        def read(self,*a):return json.dumps(response).encode()
    class Opener:
        def open(self,request,**kw):captured.append(request);return Response()
    monkeypatch.setattr(bocha.urllib.request,'build_opener',lambda *args:Opener())
    parameters=bocha.validate_search('中文公告',{'max_results':3,'search_depth':'basic','topic':'general','include_domains':['example.org'],'exclude_domains':[],'time_range':None,'start_date':'2026-09-01','end_date':'2026-09-14'})
    parsed,*_=bocha.call_search('中文公告',parameters)
    assert bocha.rows(parsed)[0]['kind']=='search_snippet'
    body=json.loads(captured[0].data)
    assert body['freshness']=='2026-09-01..2026-09-14' and body['include']=='example.org'
    assert captured[0].full_url=='https://api.bocha.cn/v1/web-search'
    for bad in ([],{'webPages':'invalid'},{'webPages':{'value':'invalid'}}):
        response['data']=bad
        with pytest.raises(websearch.SearchError) as error:bocha.call_search('中文公告',parameters)
        assert error.value.failure_kind=='invalid_response' and 'private-test-key' not in str(error.value)


def test_zhipu_engine_frozen_request_shape_and_no_silent_filter_relaxation(tmp_path,monkeypatch):
    from briefloop import zhipu
    s,r,_=run_with_policy(tmp_path,{'primary_provider':'zhipu','zhipu_engine':'search_pro_sogou'})
    s.set_meta('settings',{**s.settings(),'search_policy':{'primary_provider':'zhipu','zhipu_engine':'search_std'}})
    monkeypatch.setattr(zhipu,'_read_key',lambda *a,**kw:('fixture-secret','file'))
    captured=[]
    class Response:
        def __enter__(self):return self
        def __exit__(self,*a):pass
        def read(self,*a):return json.dumps({'id':'search1','search_result':[{'title':'原始文档','link':'https://example.org','content':'摘要'}]}).encode()
    class Opener:
        def open(self,request,**kw):captured.append(request);return Response()
    monkeypatch.setattr(zhipu.urllib.request,'build_opener',lambda *a:Opener())
    result=websearch.search('中文资料',provider='zhipu',store=s,run_id=r['id'],time_range='week',include_domains=['example.org'],max_results=3)
    body=json.loads(captured[0].data)
    assert body['search_engine']=='search_pro_sogou' and body['count']==10
    assert body['search_domain_filter']=='example.org' and body['search_recency_filter']=='oneWeek'
    assert result['results'][0]['kind']=='search_snippet'
    with pytest.raises(websearch.SearchError,match='绝对日期'):
        websearch.search('资料',store=s,run_id=r['id'],start_date='2026-09-01')
    assert len(captured)==1 and research_budget.snapshot(s,r['id'])['used']['search_requests']==1


def test_bocha_http_quota_is_not_misreported_as_invalid_key(tmp_path,monkeypatch):
    from io import BytesIO
    from urllib.error import HTTPError
    monkeypatch.setattr(bocha,'_read_key',lambda *a,**kw:('fixture-key','file'))
    class Opener:
        def open(self,*a,**kw):raise HTTPError('https://api.bocha.cn',403,'Forbidden',{},BytesIO(b'{"message":"You do not have enough money"}'))
    monkeypatch.setattr(bocha.urllib.request,'build_opener',lambda *a:Opener())
    s,r,_=run_with_policy(tmp_path,{'primary_provider':'bocha'})
    with pytest.raises(websearch.SearchError,match='余额不足') as error:websearch.search('公开资料',store=s,run_id=r['id'])
    assert error.value.failure_kind=='quota' and 'fixture-key' not in str(error.value)


def test_new_default_combines_tavily_and_native_without_enabling_paid_supplements(tmp_path):
    store=Store(tmp_path/'defaults')
    assert store.settings()['search_provider']=='tavily'
    run=store.create_run({'title':'默认搜索','objective':'公开资料','allow_web':True},[])
    job=store.enqueue('generate',{'run_id':run['id']})
    policy=json.loads(job['payload'])['search_policy']
    assert search_policy.allowed(policy)==['tavily','native']
    assert policy['supplemental_providers']==[]
    assert search_policy.native_allowed({'search_policy':policy},True)
    assert not search_policy.native_allowed({'search_provider':'tavily'},True)
    # Legacy job payloads must retain their original exclusive permission.
    with store.tx() as c:
        payload=json.loads(job['payload']);payload.pop('search_policy')
        c.execute('UPDATE jobs SET payload=? WHERE id=?',(json.dumps(payload),job['id']))
    assert search_policy.allowed(search_policy.for_run(store,run['id']))==['tavily']
