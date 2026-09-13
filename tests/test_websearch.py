"""Multi-provider search behavior: keyless DuckDuckGo and identical budget metering."""
import json
from pathlib import Path
import pytest
from briefloop import duckduckgo,tavily,websearch
from briefloop.models import Settings
from briefloop.store import Store

LIMITS={'search_requests':2,'candidate_urls':3,'source_pages':2}


def make_run(tmp_path,provider,limits=LIMITS):
    store=Store(tmp_path/('ws-'+provider))
    store.set_meta('settings',{**store.settings(),'search_provider':provider})
    run=store.create_run({'title':'test','objective':'research','allow_web':True,'research_budget':limits},[])
    store.enqueue('generate',{'run_id':run['id']})
    return store,run


def ddg_response(urls):
    from urllib.parse import quote
    rows=[]
    for index,url in enumerate(urls):
        redirect='//duckduckgo.com/l/?uddg='+quote(url,safe='')+'&amp;rut=h'+str(index)
        rows.append('<div class="result"><h2 class="result__title">'
                    f'<a rel="nofollow" class="result__a" href="{redirect}">Title {index}</a></h2>'
                    f'<a class="result__snippet" href="{redirect}">Snippet {index}</a></div>')
    html='<html><body>'+''.join(rows)+'</body></html>'
    return html,html.encode()


def test_duckduckgo_search_runs_without_any_tavily_key(tmp_path,monkeypatch):
    monkeypatch.delenv('TAVILY_API_KEY',raising=False)
    monkeypatch.setattr(tavily,'_read_key',lambda *args,**kwargs:('',None))
    assert tavily.key_status()['configured'] is False
    store,run=make_run(tmp_path,'duckduckgo')
    assert Settings.model_validate({'search_provider':'duckduckgo'}).search_provider=='duckduckgo'
    assert store.search_provider_for_run(run['id'])=='duckduckgo'
    html,raw=ddg_response(['https://example.test/first','https://example.test/direct'])
    forms=[]
    monkeypatch.setattr(duckduckgo,'_post',lambda form:(forms.append(form) or (html,raw)))
    result=websearch.search('solar capacity',time_range='week',include_domains=['example.test'],
                            max_results=5,store=store,run_id=run['id'])
    assert forms==[{'q':'solar capacity site:example.test','kl':'wt-wt','df':'w'}]
    assert [row['url'] for row in result['results']]==['https://example.test/first','https://example.test/direct']
    first=result['results'][0]
    assert first['title']=='Title 0' and first['snippet']=='Snippet 0'
    assert first['kind']=='search_snippet' and first['score'] is None and first['published_date'] is None
    assert result['provider']=='duckduckgo' and result['status']=='ok' and result['usage'] is None
    assert 'add-url' in result['note'] and tavily.key_status()['configured'] is False
    assert Path(result['discovery_path']).read_bytes()==raw
    record=json.loads(Path(result['request_record_path']).read_text())
    assert record['provider']=='duckduckgo' and record['operation']=='search' and record['outcome']=='success'
    assert record['parameters']['include_domains']==['example.test'] and record['parameters']['start_date'] is None
    from briefloop import research_budget as budget
    state=budget.snapshot(store,run['id'])
    assert state['used']=={'search_requests':1,'candidate_urls':2,'source_pages':0}
    assert state['scope']['search_provider']=='duckduckgo'


def test_budget_metering_is_identical_for_tavily_and_duckduckgo(tmp_path,monkeypatch):
    urls=['https://example.test/1','https://example.test/2','https://example.test/3']
    outcomes={}
    for provider in ('tavily','duckduckgo'):
        store,run=make_run(tmp_path,provider,{'search_requests':2,'candidate_urls':6,'source_pages':2})
        calls=[]
        if provider=='tavily':
            monkeypatch.setattr(tavily,'_read_key',lambda *args,**kwargs:('tvly-test','test'))
            payload={'results':[{'title':'T','url':url,'content':'s'} for url in urls],'request_id':'pid','usage':{}}
            monkeypatch.setattr(tavily,'_post',lambda endpoint,body,key_file=None:(calls.append(body) or (payload,json.dumps(payload).encode())))
            invoke=lambda query: tavily.search(query,store=store,run_id=run['id'])
        else:
            html,raw=ddg_response(urls)
            monkeypatch.setattr(duckduckgo,'_post',lambda form:(calls.append(form) or (html,raw)))
            invoke=lambda query: websearch.search(query,store=store,run_id=run['id'])
        assert invoke('one')['status']=='ok'
        assert invoke('two')['status']=='ok'      # same URLs: candidates deduplicate
        assert invoke('three')['status']=='budget_exhausted'
        assert len(calls)==2                       # the third request is stopped before any HTTP call
        from briefloop import research_budget as budget
        snapshot=budget.snapshot(store,run['id'])
        assert snapshot['used']=={'search_requests':2,'candidate_urls':3,'source_pages':0}
        assert snapshot['remaining']=={'search_requests':0,'candidate_urls':3,'source_pages':2}
        records=[json.loads(path.read_text()) for path in (store.root/'discovery'/run['id']).glob('*.request.json')]
        assert {record['provider'] for record in records}=={provider}
        outcomes[provider]={'snapshot':{key:snapshot[key] for key in ('used','remaining','exhausted','exhausted_resources')},
                            'records':sorted((record['outcome'],record['operation'],record['parameters']['max_results']) for record in records)}
    assert outcomes['tavily']==outcomes['duckduckgo']


def test_web_search_resolves_the_frozen_provider_and_rejects_native(tmp_path,monkeypatch):
    store,run=make_run(tmp_path,'native')
    with pytest.raises(websearch.SearchError) as error:
        websearch.search('query',store=store,run_id=run['id'])
    assert '原生' in str(error.value)
    store,run=make_run(tmp_path,'duckduckgo')
    with pytest.raises(websearch.SearchError) as mismatch:
        websearch.search('query',provider='tavily',store=store,run_id=run['id'])
    assert '不是 Tavily' in str(mismatch.value)


def test_duckduckgo_failures_are_classified_and_still_metered(tmp_path,monkeypatch):
    store,run=make_run(tmp_path,'duckduckgo')
    def blocked(form):raise duckduckgo.marked('DuckDuckGo 拒绝了本次查询（HTTP 403）','rate_limit',403)
    monkeypatch.setattr(duckduckgo,'_post',blocked)
    with pytest.raises(websearch.SearchError) as error:
        websearch.search('blocked query',store=store,run_id=run['id'])
    assert error.value.failure_kind=='rate_limit' and error.value.status==403
    assert error.value.provider=='duckduckgo'
    from briefloop import research_budget as budget
    state=budget.snapshot(store,run['id'])
    assert state['used']['search_requests']==1     # failed requests keep the charge
    records=[json.loads(path.read_text()) for path in (store.root/'discovery'/run['id']).glob('*.request.json')]
    assert [(record['outcome'],record['failure_kind']) for record in records]==[('failed','rate_limit')]


def test_duckduckgo_extract_registers_pages_through_the_metered_direct_fetch(tmp_path,monkeypatch):
    from briefloop import research_budget as budget
    from briefloop import sources
    store,run=make_run(tmp_path,'duckduckgo')
    page=b'<html><head><title>Example Doc</title></head><body><p>Capacity 45 MW.</p></body></html>'
    monkeypatch.setattr(sources,'_fetch_bytes',lambda url:(page,'text/html; charset=utf-8','utf-8'))
    result=websearch.extract(store,['https://example.test/doc'],run_id=run['id'])
    assert result['provider']=='duckduckgo' and result['outcome']=='success'
    source=result['sources'][0]
    assert source['status']=='ready' and 'Capacity 45 MW' in store.source_text(source['id'])
    provenance=json.loads((store.root/'sources'/(source['id']+'.provenance.json')).read_text())
    assert provenance['original_kind']=='http_response'
    assert budget.snapshot(store,run['id'])['used']['source_pages']==1
    assert Path(result['request_record_path']).exists()
    again=websearch.extract(store,['https://example.test/doc'],run_id=run['id'])
    assert again['sources'][0]['reused'] is True
    assert budget.snapshot(store,run['id'])['used']['source_pages']==1
