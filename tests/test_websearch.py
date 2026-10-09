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


def test_ddg_parallel_queries_each_keep_the_requested_candidate_share(tmp_path, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    import threading
    from briefloop import research_budget
    from briefloop import research_plan
    store = Store(tmp_path)
    run = store.create_run({'title': '并行检索', 'objective': '覆盖三个问题',
        'allow_web': True, 'search_policy': {'primary_provider': 'duckduckgo', 'native_search_enabled': False},
        'research_budget': {'search_requests': 3, 'candidate_urls': 15, 'source_pages': 6}},
        [], research_protocol='quality_v1')
    research_plan.freeze(store, run['id'], preset='quick',
                         structure={'breadth': 3, 'depth': 1, 'parallel': 3})
    ready = threading.Barrier(3)
    def post(form):
        response = ddg_response([f'https://example.test/{form["q"]}/{i}' for i in range(10)])
        ready.wait(timeout=5)
        return response
    monkeypatch.setattr(duckduckgo, '_post', post)
    def search(query):
        return websearch.search(query, provider='duckduckgo', max_results=5,
                                store=store, run_id=run['id'])
    with ThreadPoolExecutor(max_workers=3) as pool:
        results = list(pool.map(search, ['company', 'market', 'policy']))
    assert [len(result['results']) for result in results] == [5, 5, 5]
    assert all(result['status']=='ok' for result in results)
    assert research_budget.snapshot(store, run['id'])['used']['candidate_urls'] == 15
    assert research_budget.snapshot(store, run['id'])['used']['search_requests'] == 3
    for query, result in zip(['company', 'market', 'policy'], results):
        # The complete provider receipt is still available for audit.
        raw = (store.root/result['discovery_path']).read_text()
        assert f'{query}%2F9' in raw
