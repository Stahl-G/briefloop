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


