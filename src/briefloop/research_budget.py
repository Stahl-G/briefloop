"""Per-run managed-tool budgets, reserved through the Store's SQLite transaction."""
import json
import threading
import time
from contextlib import contextmanager
from urllib.parse import urlsplit,urlunsplit
from .models import ResearchBudget
from .store import dump,uid,now

KINDS=('search_requests','candidate_urls','source_pages')
PAGE_LEASE_SECONDS=90
PAGE_WAIT_SECONDS=690


class BudgetExhausted(ValueError):
    def __init__(self,resource,state):
        super().__init__('本轮 '+resource+' 预算已用完；保留现有证据并交接，不再扩大研究')
        self.result={'status':'budget_exhausted','resource':resource,'remaining':state['remaining'],
                     'budget':state,'message':str(self)}


def canonical_url(url):
    if not isinstance(url,str):raise ValueError('来源 URL 必须是文本')
    parts=urlsplit(url.strip())
    if parts.scheme.lower() not in ('http','https') or not parts.netloc:raise ValueError('来源必须是 HTTP(S) URL')
    host=parts.hostname
    if not host:raise ValueError('来源必须包含主机名')
    try:port=parts.port
    except ValueError:raise ValueError('来源端口无效') from None
    userinfo,marker,_=parts.netloc.rpartition('@')
    authority=(userinfo+'@' if marker else '')+('['+host+']' if ':' in host else host)
    if port is not None:authority+=':'+str(port)
    return urlunsplit((parts.scheme.lower(),authority,parts.path or '/',parts.query,''))


def _granted_limits(stage):
    extra={kind:0 for kind in KINDS}
    source=stage.get('budget_source') or {}
    grants=([source] if source.get('kind')=='user_grant' else [])+list(stage.get('grants') or [])
    for grant in grants:
        for kind in KINDS:extra[kind]+=int((grant.get('limits') or {}).get(kind,0) or 0)
    return extra


def _meter_state(connection,run_id):
    row=connection.execute('SELECT value FROM meta WHERE key=?',('research_budget:'+run_id,)).fetchone()
    state=json.loads(row['value']) if row else {'search_requests':0,'candidate_urls':[],'source_pages':[]}
    if (not isinstance(state,dict) or type(state.get('search_requests')) is not int
            or state['search_requests']<0 or any(not isinstance(state.get(k),list) for k in KINDS[1:])):
        raise ValueError('研究预算记录无效，未重置计数')
    return state


def fact_check_budget_offset(connection,run_id,history,*,admission_usage=None):
    """Freeze already-consumed allowances, never a closed stage's unused grant.

    The run meter stays cumulative. Base budget is consumed first; only the
    excess that earlier grants actually paid for offsets a new stage's ceiling.
    Legacy unlimited research has no base ceiling, so its existing usage is an
    offset when an explicit grant establishes the new stage's first hard limit.
    This is captured at admission, not recalculated as the new stage spends.
    """
    row=connection.execute('SELECT requirements FROM runs WHERE id=?',(run_id,)).fetchone()
    requirements=json.loads(row['requirements'])
    if admission_usage is None:
        state=_meter_state(connection,run_id)
        admission_usage={'search_requests':state['search_requests'],
                         **{kind:len(state[kind]) for kind in KINDS[1:]}}
    base=requirements.get('research_budget')
    if base is None:return admission_usage
    previous=[_granted_limits(stage) for stage in history]
    return {kind:min(sum(grant[kind] for grant in previous),max(0,admission_usage[kind]-base[kind]))
            for kind in KINDS}


def restore_fact_check_budget_offset(connection,run_id,plan):
    """Recover an old active stage once, using its existing request ledger.

    Searches charge one per reservation, so subtracting this stage's requests
    recovers its admission meter exactly. Candidate/page counters are unique
    URL counts; old reservations did not record their cardinality. Recover those
    fields only when this stage has no corresponding requests, otherwise retain
    zero credit rather than manufacture additional authorization. The first
    writer saves this fixed result before reserving or adding any new allowance.
    """
    stage=plan.get('fact_check') or {}
    if stage.get('status')!='active' or 'budget_offset' in stage:return False
    state=_meter_state(connection,run_id)
    row=connection.execute('SELECT value FROM meta WHERE key=?',('research_requests:'+run_id,)).fetchone()
    requests=json.loads(row['value']) if row else {}
    current=[entry for entry in requests.values() if entry.get('round_id')==stage['stage_id']]
    searches=sum(entry.get('operation')=='search' for entry in current)
    pages=any(entry.get('operation')=='pages' for entry in current)
    admission_usage={'search_requests':max(0,state['search_requests']-searches),
                     'candidate_urls':0 if searches else len(state['candidate_urls']),
                     'source_pages':0 if pages else len(state['source_pages'])}
    stage['budget_offset']=fact_check_budget_offset(connection,run_id,plan.get('fact_check_history') or [],
                                                    admission_usage=admission_usage)
    stage['budget_offset_basis']={'kind':'legacy_request_ledger',
        'unresolved_fields':(['candidate_urls'] if searches else [])+(['source_pages'] if pages else [])}
    return True


def _fact_check_grant_limits(connection,run_id,*,persist_offset=False):
    """User-granted additions attached to an ACTIVE fact-check stage (KINDS ints).

    A user grant is spendable budget: while the stage runs, its amounts are
    added to the meter's limits. The initial admission with kind=user_grant and
    later stage['grants'] additions both count; once the stage closes the
    unused additions lapse, so nothing silently expands the task budget forever.
    A reopened stage also carries a fixed offset for previously consumed grants;
    otherwise their cumulative charges would consume the new grant a second time.
    """
    row=connection.execute("SELECT value FROM meta WHERE key=?",('research_plan:'+run_id,)).fetchone()
    if row is None:return None
    try:plan=json.loads(row['value']) or {};stage=plan.get('fact_check') or {}
    except (TypeError,ValueError):return None
    if stage.get('status')!='active':return None
    if restore_fact_check_budget_offset(connection,run_id,plan) and persist_offset:
        connection.execute('UPDATE meta SET value=? WHERE key=?',(dump(plan),'research_plan:'+run_id))
    extra=_granted_limits(stage)
    for kind in KINDS:extra[kind]+=int((stage.get('budget_offset') or {}).get(kind,0))
    return extra if any(extra.values()) else None


def _load(store,connection,run_id,*,persist_offset=False,include_fact_check_grants=True):
    row=connection.execute('SELECT requirements FROM runs WHERE id=?',(run_id,)).fetchone()
    if row is None:raise KeyError('Run not found')
    requirements=json.loads(row['requirements'])
    # Missing on old records means unlimited; never infer a preset retroactively.
    limits=(ResearchBudget.model_validate(requirements['research_budget']).model_dump()
            if 'research_budget' in requirements else None)
    extra=_fact_check_grant_limits(connection,run_id,persist_offset=persist_offset) if include_fact_check_grants else None
    if extra is not None:
        # A run without an authorized budget starts its fact-check stage from
        # zero plus the grant; the user's explicit amount is the ceiling.
        base=limits if limits is not None else {kind:0 for kind in KINDS}
        limits={kind:base[kind]+extra[kind] for kind in KINDS}
    key='research_budget:'+run_id
    state=_meter_state(connection,run_id)
    return key,limits,state


def _save(connection,key,state):
    connection.execute('INSERT OR REPLACE INTO meta(key,value) VALUES(?,?)',(key,dump(state)))


def spent(store,run_id):
    """Raw KINDS counters for admission checks; provider display scope never changes them."""
    state=store.meta('research_budget:'+run_id) or {'search_requests':0,'candidate_urls':[],'source_pages':[]}
    return {'search_requests':state['search_requests'],'candidate_urls':len(state['candidate_urls']),
            'source_pages':len(state['source_pages'])}


def _stage_usage(store,run_id):
    # Same KINDS meter, split by which stage admitted the request; entries written
    # before the stage tag existed count as research.
    from .research_plan import pending_requests
    stages={'research':{'search_requests':0,'source_pages':0},'fact_check':{'search_requests':0,'source_pages':0}}
    for entry in pending_requests(store,run_id).values():
        stage=entry.get('stage') or 'research'
        if stage=='source_refresh':stages.setdefault(stage,{'search_requests':0,'source_pages':0})
        key={'search':'search_requests','pages':'source_pages'}.get(entry.get('operation'))
        if stage in stages and key:stages[stage][key]+=1
    return stages


def _view(store,run_id,limits,state):
    from .websearch import MANAGED_PROVIDERS
    from .search_policy import for_run,allowed
    policy=for_run(store,run_id);channels=allowed(policy)
    provider=policy['primary_provider']
    used={'search_requests':state['search_requests'],'candidate_urls':len(state['candidate_urls']),
          'source_pages':len(state['source_pages'])}
    remaining={kind:max(0,limits[kind]-used[kind]) if limits is not None else None for kind in KINDS}
    if not any(p in MANAGED_PROVIDERS for p in channels):
        for kind in KINDS[:2]:used[kind]=None;remaining[kind]=None
    exhausted=[kind for kind,value in remaining.items() if value==0]
    return {'limits':limits,'used':used,'remaining':remaining,'exhausted':bool(exhausted),
            'exhausted_resources':exhausted,'stages':_stage_usage(store,run_id),
            'scope':{'search_provider':provider,'allowed_providers':channels,'native_search_enabled':'native' in channels,'native_search_requests':None,
            'search_requests':'managed_provider_only','candidate_urls':'managed_provider_only',
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
    """Charge before the HTTP call; failed requests retain this charge.

    Admission (frozen plan + active round or fact-check stage for quality runs)
    and the request reservation happen in the same transaction, before any
    network call.
    """
    from .research_plan import admission,record_request
    with store.tx() as connection:
        round_id,stage=admission(store,connection,run_id,'search')
        key,limits,state=_load(store,connection,run_id,persist_offset=True)
        if limits is not None:
            for kind in ('search_requests','candidate_urls'):
                used=state[kind] if kind=='search_requests' else len(state[kind])
                if used>=limits[kind]:raise BudgetExhausted(kind,_view(store,run_id,limits,state))
            max_results=min(max_results,limits['candidate_urls']-len(state['candidate_urls']))
        state['search_requests']+=1
        _save(connection,key,state)
        request_id=uid('search')
        if round_id:record_request(store,connection,run_id,request_id,'search',round_id,stage=stage)
    return {'request_id':request_id,'max_results':max_results,'round_id':round_id,'stage':stage}


def record_candidates(store,run_id,urls,*,reservation=None):
    """Concurrent responses may overflow; admission is atomic and overflow explicit."""
    incoming=list(dict.fromkeys(canonical_url(url) for url in urls))
    with store.tx() as connection:
        from .research_plan import admit_response
        accepted=admit_response(connection,run_id,reservation,'search')
        key,limits,state=_load(store,connection,run_id,persist_offset=accepted)
        known=set(state['candidate_urls']);overflow=[]
        for url in incoming:
            if not accepted:
                overflow.append(url);continue
            if url in known:continue
            if limits is not None and len(known)>=limits['candidate_urls']:
                overflow.append(url);continue
            known.add(url);state['candidate_urls'].append(url)
        if accepted:_save(connection,key,state)
        view=_view(store,run_id,limits,state)
    return {'accepted':accepted,'allowed_urls':[url for url in incoming if url not in overflow],
            'unadmitted_urls':overflow,'budget':view}


def _reserve_pages_tx(store,connection,run_id,urls,*,request_id=None,refresh_job_id=None,source_id=None):
    from .research_plan import admission,record_request,source_refresh_binding,AdmissionError
    refresh=None
    if refresh_job_id is not None:
        if len(urls)!=1:
            raise AdmissionError('单次用户刷新只能请求所选来源URL',code='refresh_job_mismatch')
        refresh=source_refresh_binding(connection,run_id,refresh_job_id,source_id,urls[0])
        fixed_id=refresh_job_id+'_refresh_'+str(refresh['attempt'])
        if request_id is not None and request_id!=fixed_id:
            raise AdmissionError('刷新请求身份与任务执行次数不匹配',code='refresh_job_mismatch')
        request_id=fixed_id
        round_id,stage=refresh_job_id,'source_refresh'
    else:
        round_id,stage=admission(store,connection,run_id,'pages')
    key,limits,state=_load(store,connection,run_id,persist_offset=refresh is None,
                           include_fact_check_grants=refresh is None)
    new=[url for url in urls if url not in state['source_pages']]
    if limits is not None and len(state['source_pages'])+len(new)>limits['source_pages']:
        raise BudgetExhausted('source_pages',_view(store,run_id,limits,state))
    state['source_pages'].extend(new)
    _save(connection,key,state)
    request_id=request_id or uid('extract')
    if round_id:record_request(store,connection,run_id,request_id,'pages',round_id,stage=stage,refresh=refresh)
    return {'round_id':round_id,'stage':stage,'request_id':request_id,'refresh':refresh,
            'limits':limits,'state':state}


def reserve_pages(store,run_id,urls,*,request_id=None,refresh_job_id=None,source_id=None):
    """A failed direct fetch and same-URL Extract fallback consume one unique page."""
    urls=list(dict.fromkeys(canonical_url(url) for url in urls))
    with store.tx() as connection:
        data=_reserve_pages_tx(store,connection,run_id,urls,request_id=request_id,
                               refresh_job_id=refresh_job_id,source_id=source_id)
    refresh=data['refresh'];limits=data['limits'];state=data['state']
    view=_view(store,run_id,limits,state) if refresh is not None else snapshot(store,run_id)
    return {**view,'round_id':data['round_id'],'stage':data['stage'],'request_id':data['request_id']}


def claim_pages(store,run_id,urls):
    """Atomically choose this caller's URL subset and reserve only its pages.

    The SQLite row coordinates separate Scout processes. No network or source
    extraction occurs while this transaction holds the writer lock.
    """
    from .sources import existing_for_run
    urls=list(dict.fromkeys(canonical_url(url) for url in urls))
    owner=uid('pageflight');cached={};waiting={};claimed=[];unadmitted=[];reservation=None;exhausted=None
    with store.tx() as connection:
        clock=time.time()
        connection.execute('DELETE FROM page_claim_results WHERE completed_at<?',(clock-86400,))
        for url in urls:
            previous=existing_for_run(store,run_id,url)
            if previous:
                cached[url]=previous;continue
            row=connection.execute('SELECT owner,expires_at,request_id FROM page_claims WHERE run_id=? AND url=?',
                                   (run_id,url)).fetchone()
            if row and row['expires_at']>clock:
                waiting[url]=row['owner']
            else:
                if row:
                    reject_claim_request(connection,run_id,{'request_id':row['request_id']},
                                         response_status='unobserved',reason='page_claim_expired')
                claimed.append(url)
        if claimed:
            try:
                reserved=_reserve_pages_tx(store,connection,run_id,claimed)
            except BudgetExhausted as exc:
                exhausted=exc.result;unadmitted=claimed;claimed=[]
            else:
                for url in claimed:
                    connection.execute('INSERT OR REPLACE INTO page_claims VALUES(?,?,?,?,?)',
                                       (run_id,url,owner,clock+PAGE_LEASE_SECONDS,reserved['request_id']))
                reservation={'round_id':reserved['round_id'],'stage':reserved['stage'],
                             'request_id':reserved['request_id']}
    if reservation:reservation={**snapshot(store,run_id),**reservation}
    return {'owner':owner,'claimed':claimed,'waiting':waiting,'cached':cached,
            'reservation':reservation,'budget_exhausted':exhausted,'unadmitted':unadmitted}


def owns_claims(connection,run_id,owner,urls):
    clock=time.time()
    return all((row:=connection.execute('SELECT owner,expires_at FROM page_claims WHERE run_id=? AND url=?',
                                       (run_id,url)).fetchone()) and row['owner']==owner and row['expires_at']>clock
               for url in urls)


def finish_claims(connection,run_id,owner,urls,*,outcome,source_ids=None,error=None):
    """Publish the result for existing waiters; delete only this owner's rows."""
    clock=time.time();source_ids=source_ids or {}
    for url in urls:
        connection.execute('INSERT OR REPLACE INTO page_claim_results VALUES(?,?,?,?,?,?)',
                           (owner,url,outcome,source_ids.get(url),error,clock))
        connection.execute('DELETE FROM page_claims WHERE run_id=? AND url=? AND owner=?',
                           (run_id,url,owner))


def reject_claim_request(connection,run_id,reservation,*,response_status,reason='page_claim_lost'):
    key='research_requests:'+run_id
    row=connection.execute('SELECT value FROM meta WHERE key=?',(key,)).fetchone()
    data=json.loads(row['value']) if row else {}
    entry=data.get(reservation['request_id'])
    if entry and entry.get('status')=='reserved':
        entry.update(status='response_rejected',response_status=response_status,
                     rejection_reason=reason,updated=now())
        connection.execute('UPDATE meta SET value=? WHERE key=?',(dump(data),key))
    elif entry and entry.get('status')=='response_rejected' and entry.get('rejection_reason')=='page_claim_expired' and response_status!='unobserved':
        entry.update(response_status=response_status,updated=now())
        connection.execute('UPDATE meta SET value=? WHERE key=?',(dump(data),key))


def abort_claims(store,run_id,owner,urls,*,error=None):
    with store.tx() as connection:
        unfinished=[url for url in urls if not connection.execute(
            'SELECT 1 FROM page_claim_results WHERE owner=? AND url=?',(owner,url)).fetchone()]
        finish_claims(connection,run_id,owner,unfinished,outcome='failed',error=error)


def wait_for_claim(store,run_id,url,owner):
    """Wait without a writer lock; an expired owner may be replaced by caller."""
    deadline=time.monotonic()+PAGE_WAIT_SECONDS
    while time.monotonic()<deadline:
        rows=store.rows('SELECT outcome,source_id,error FROM page_claim_results WHERE owner=? AND url=?',
                        (owner,url))
        if rows:return rows[0]
        rows=store.rows('SELECT owner,expires_at FROM page_claims WHERE run_id=? AND url=?',(run_id,url))
        if not rows or rows[0]['owner']!=owner or rows[0]['expires_at']<=time.time():
            rows=store.rows('SELECT outcome,source_id,error FROM page_claim_results WHERE owner=? AND url=?',
                            (owner,url))
            if rows:return rows[0]
            return {'outcome':'stale','source_id':None,'error':None}
        time.sleep(.1)
    return {'outcome':'timeout','source_id':None,'error':'等待同一来源读取超时'}


@contextmanager
def keep_claims_alive(store,run_id,owner,urls):
    """Bound the crash lease without expiring a healthy long PDF extraction."""
    stop=threading.Event()
    def heartbeat():
        while not stop.wait(PAGE_LEASE_SECONDS/3):
            try:
                with store.tx() as connection:
                    for url in urls:
                        connection.execute('UPDATE page_claims SET expires_at=? WHERE run_id=? AND url=? AND owner=?',
                                           (time.time()+PAGE_LEASE_SECONDS,run_id,url,owner))
            except Exception:
                # A lost heartbeat is fenced at response admission.
                pass
    thread=threading.Thread(target=heartbeat,daemon=True)
    thread.start()
    try:yield
    finally:
        stop.set();thread.join(timeout=1)


def save_discovery(store,run_id,request_id,raw):
    folder=store.root/'discovery'/run_id;folder.mkdir(parents=True,exist_ok=True)
    path=folder/(request_id+'.json')
    path.write_bytes(raw)
    return str(path)


def save_request_record(store,run_id,request_id,record):
    """Persist the redacted request envelope next to the raw provider response.

    The envelope records what was actually sent and what happened (parameters,
    local/provider request ids, outcome, failure kind, admitted URLs, budget),
    without credentials or hidden reasoning. Raw responses stay separate so old
    readers are unaffected.
    """
    folder=store.root/'discovery'/run_id;folder.mkdir(parents=True,exist_ok=True)
    path=folder/(request_id+'.request.json')
    path.write_text(dump(record),encoding='utf-8')
    return str(path)
