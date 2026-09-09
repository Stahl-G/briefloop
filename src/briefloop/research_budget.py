"""Per-run managed-tool budgets, reserved through the Store's SQLite transaction."""
import json
from urllib.parse import urlsplit,urlunsplit
from .models import ResearchBudget
from .store import dump,uid

KINDS=('search_requests','candidate_urls','source_pages')


class BudgetExhausted(ValueError):
    def __init__(self,resource,state):
        super().__init__('本轮 '+resource+' 预算已用完；保留现有证据并交接，不再扩大研究')
        self.result={'status':'budget_exhausted','resource':resource,'remaining':state['remaining'],
                     'budget':state,'message':str(self)}


def canonical_url(url):
    if not isinstance(url,str):raise ValueError('来源 URL 必须是文本')
    parts=urlsplit(url.strip())
    if parts.scheme.lower() not in ('http','https') or not parts.netloc:raise ValueError('来源必须是 HTTP(S) URL')
    return urlunsplit((parts.scheme.lower(),parts.netloc.lower(),parts.path or '/',parts.query,''))


def _load(store,connection,run_id):
    row=connection.execute('SELECT requirements FROM runs WHERE id=?',(run_id,)).fetchone()
    if row is None:raise KeyError('Run not found')
    requirements=json.loads(row['requirements'])
    # Missing on old records means unlimited; never infer a preset retroactively.
    limits=(ResearchBudget.model_validate(requirements['research_budget']).model_dump()
            if 'research_budget' in requirements else None)
    key='research_budget:'+run_id
    row=connection.execute('SELECT value FROM meta WHERE key=?',(key,)).fetchone()
    state=json.loads(row['value']) if row else {'search_requests':0,'candidate_urls':[],'source_pages':[]}
    if (not isinstance(state,dict) or type(state.get('search_requests')) is not int
            or state['search_requests']<0 or any(not isinstance(state.get(k),list) for k in KINDS[1:])):
        raise ValueError('研究预算记录无效，未重置计数')
    return key,limits,state


def _save(connection,key,state):
    connection.execute('INSERT OR REPLACE INTO meta(key,value) VALUES(?,?)',(key,dump(state)))


def _view(store,run_id,limits,state):
    provider=store.search_provider_for_run(run_id)
    used={'search_requests':state['search_requests'],'candidate_urls':len(state['candidate_urls']),
          'source_pages':len(state['source_pages'])}
    remaining={kind:max(0,limits[kind]-used[kind]) if limits is not None else None for kind in KINDS}
    if provider!='tavily':
        for kind in KINDS[:2]:used[kind]=None;remaining[kind]=None
    exhausted=[kind for kind,value in remaining.items() if value==0]
    return {'limits':limits,'used':used,'remaining':remaining,'exhausted':bool(exhausted),
            'exhausted_resources':exhausted,'scope':{'search_provider':provider,
            'search_requests':'managed_tavily_only','candidate_urls':'managed_tavily_only',
            'source_pages':'managed_unique_urls','native_codex_search_metered':False,
            'legacy_unlimited':limits is None}}


def snapshot(store,run_id):
    # A read-only snapshot does not create counters or alter old run requirements.
    import sqlite3
    from contextlib import closing
    with closing(sqlite3.connect(store.db)) as connection:
        connection.row_factory=sqlite3.Row
        _,limits,state=_load(store,connection,run_id)
    return _view(store,run_id,limits,state)


def reserve_search(store,run_id,max_results):
    """Charge before the HTTP call; failed requests retain this charge."""
    with store.tx() as connection:
        key,limits,state=_load(store,connection,run_id)
        if limits is not None:
            for kind in ('search_requests','candidate_urls'):
                used=state[kind] if kind=='search_requests' else len(state[kind])
                if used>=limits[kind]:raise BudgetExhausted(kind,_view(store,run_id,limits,state))
            max_results=min(max_results,limits['candidate_urls']-len(state['candidate_urls']))
        state['search_requests']+=1
        _save(connection,key,state)
    return {'request_id':uid('search'),'max_results':max_results}


def record_candidates(store,run_id,urls):
    """Concurrent responses may overflow; admission is atomic and overflow explicit."""
    incoming=list(dict.fromkeys(canonical_url(url) for url in urls))
    with store.tx() as connection:
        key,limits,state=_load(store,connection,run_id)
        known=set(state['candidate_urls']);overflow=[]
        for url in incoming:
            if url in known:continue
            if limits is not None and len(known)>=limits['candidate_urls']:
                overflow.append(url);continue
            known.add(url);state['candidate_urls'].append(url)
        _save(connection,key,state)
        view=_view(store,run_id,limits,state)
    return {'allowed_urls':[url for url in incoming if url not in overflow],
            'unadmitted_urls':overflow,'budget':view}


def reserve_pages(store,run_id,urls):
    """A failed direct fetch and same-URL Extract fallback consume one unique page."""
    urls=list(dict.fromkeys(canonical_url(url) for url in urls))
    with store.tx() as connection:
        key,limits,state=_load(store,connection,run_id)
        new=[url for url in urls if url not in state['source_pages']]
        if limits is not None and len(state['source_pages'])+len(new)>limits['source_pages']:
            raise BudgetExhausted('source_pages',_view(store,run_id,limits,state))
        state['source_pages'].extend(new)
        _save(connection,key,state)
    return snapshot(store,run_id)


def save_discovery(store,run_id,request_id,raw):
    folder=store.root/'discovery'/run_id;folder.mkdir(parents=True,exist_ok=True)
    path=folder/(request_id+'.json')
    path.write_bytes(raw)
    return str(path)
