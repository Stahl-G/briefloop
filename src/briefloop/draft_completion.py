"""Opt-in draft-first delivery and durable, version-bound check continuation.

Uses the existing assess job and post-writing pipeline. A saved draft is never
an independent fact check, and completing this job does not create a release.
"""
import hashlib
import json
from .store import dump, Conflict


def source_metadata(store, source_id):
    source=store.one('sources',source_id)
    return {key:source[key] for key in ('id','name','url','hash','status')}


def source_identity(store, source_id):
    from .media import source_files
    result=source_metadata(store,source_id)
    try:
        _,_,original=source_files(store,source_id)
        store.source_text(source_id)  # Validate the saved text hash as well.
        result['original_hash']=hashlib.sha256(original.read_bytes()).hexdigest() if original else None
    except (ValueError,OSError) as exc:
        result['error']=str(exc)
    return result


def binding(store, version_id, *, metadata_only=False):
    brief=store.one('briefs',version_id);run=store.one('runs',brief['run_id'])
    ids=sorted(store.source_ids(run['id']))
    return {'version_id': version_id, 'brief_hash': brief['hash'],
            'detail_hash':hashlib.sha256(brief['detail'].encode()).hexdigest(),
            'requirements':json.loads(run['requirements']),
            'sources':([source_metadata(store,sid) for sid in ids]
                       if metadata_only else [source_identity(store,sid) for sid in ids]),
            'source_timing':store.rows('SELECT * FROM source_snapshot_metadata WHERE source_id IN ('+','.join('?' for _ in ids)+') ORDER BY source_id,rowid',ids) if ids else []}


def record_admitted_sources(store, connection, run_id, reservation, records):
    # _RequestStore reads the just-inserted rows in the same transaction.
    from .external_requests import _RequestStore
    key='research_requests:'+run_id
    saved=connection.execute('SELECT value FROM meta WHERE key=?',(key,)).fetchone()
    if not saved:return  # Legacy requests have no stage ownership to claim.
    ledger=json.loads(saved['value']);entry=ledger.get(reservation['request_id'])
    if not entry or entry.get('status') not in ('completed','failed'):return
    if any(entry.get(key)!=reservation.get(key) for key in ('round_id','stage')):return
    view=_RequestStore(store,connection)
    entry['admitted_sources']=[source_identity(view,row['id']) for row,_ in records]
    connection.execute('UPDATE meta SET value=? WHERE key=?',(dump(ledger),key))


def check_binding(store,job):
    file=store.root/'jobs'/job['id']/'checks-input.json'
    return json.loads(file.read_text(encoding='utf-8')) if file.exists() else json.loads(job['payload'])['checks_input']


def accept_stage_sources(store,job):
    """Only this continuation's metered fact-check receipts may expand scope."""
    payload=json.loads(job['payload'])
    if not payload.get('continuation_of'):return
    expected=check_binding(store,job);actual=binding(store,payload['version_id'])
    if actual==expected:return
    # Source timing of old evidence cannot change silently either.
    old={item['id']:item for item in expected['sources']}
    current={item['id']:item for item in actual['sources']}
    stable=lambda value:{key:item for key,item in value.items() if key not in ('sources','source_timing')}
    if stable(actual)!=stable(expected) or any(current.get(sid)!=item for sid,item in old.items()):
        raise Conflict('原稿、原来源或要求已变化，请重新选择当前材料进行检查')
    old_timing=[item for item in actual['source_timing'] if item['source_id'] in old]
    if old_timing!=expected['source_timing']:
        raise Conflict('原来源时间信息已变化，请重新选择当前材料进行检查')
    children=store.rows("SELECT payload FROM jobs WHERE kind='fact_check' AND json_extract(payload,'$.parent_job_id')=? AND json_extract(payload,'$.version_id')=?",(job['id'],payload['version_id']))
    stages={json.loads(child['payload']).get('stage_id') for child in children}-{None}
    ledger=store.meta('research_requests:'+payload['run_id']) or {}
    admitted={item['id']:item for request in ledger.values()
              if request.get('stage')=='fact_check' and request.get('round_id') in stages
              and request.get('operation')=='pages' and request.get('status') in ('completed','failed')
              for item in request.get('admitted_sources',[])}
    added=set(current)-set(old)
    if not added or any(admitted.get(sid)!=current[sid] for sid in added):
        raise Conflict('发现不属于本次事实核查接纳记录的新来源，请重新选择当前材料进行检查')
    folder=store.root/'jobs'/job['id'];folder.mkdir(exist_ok=True)
    path=folder/'checks-input.json';temporary=folder/'checks-input.tmp'
    temporary.write_text(dump(actual),encoding='utf-8');temporary.replace(path)
    store.event(job['id'],'checks_sources_admitted',{'source_ids':sorted(added),'stage_ids':sorted(stages)})


def origin(store, version_id):
    brief = store.one('briefs', version_id)
    run = store.one('runs', brief['run_id'])
    if json.loads(run['requirements']).get('completion_mode') not in ('draft_first','fast','fast_web','direct'):
        raise ValueError('这份报告未选择先交初稿，请使用现有评分或审阅入口')
    rows = store.rows("SELECT * FROM jobs WHERE kind='generate' AND json_extract(payload,'$.run_id')=? ORDER BY rowid DESC",
                      (run['id'],))
    ancestor = brief
    while ancestor:
        for job in rows:
            path=store.root/'jobs'/job['id']/'generated-source-snapshots.json'
            saved=json.loads(path.read_text(encoding='utf-8')) if path.exists() else {}
            if ancestor['id']=='brief_'+job['id'][4:] or ancestor['id'] in saved:
                return job
        ancestor=store.one('briefs',ancestor['parent_id']) if ancestor.get('parent_id') else None
    raise ValueError('找不到这份初稿的原执行配置，不能使用其他生成任务的配置代替')


def save_deferred(store, job, brief, folder):
    from .delivery_checks import brief_checks
    # These are deterministic checks, not a semantic score or delivery approval.
    checks = brief_checks(store, brief['id'])
    result = {'version_id': brief['id'], 'checks_state': 'deferred',
              'checked_scope': 'structure_references_numbers_layout',
              'unchecked_scope': ['independent_assessment', 'semantic_fact_review', 'automatic_revision'],
              'checks_input': binding(store, brief['id']), 'deterministic_checks': checks}
    (folder / 'draft-first.json').write_text(dump(result), encoding='utf-8')
    store.event(job['id'], 'checks_deferred', {'version_id': brief['id'],
                'message': '初稿已保存，可编辑和下载；只完成自动检查，独立核验尚未开始。'})
    return result


class ExistingContinuation(Exception):
    def __init__(self, job_id):
        self.job_id = job_id


def enqueue(store, version_id, *, automatic=False):
    parent = origin(store, version_id)
    brief=store.one('briefs',version_id)
    mode=json.loads(store.one('runs',brief['run_id'])['requirements']).get('completion_mode')
    fast=mode in ('fast','fast_web')
    automatic=bool(automatic and (fast or mode=='direct'))
    if parent['status'] in ('queued', 'running') and not automatic:
        raise ValueError('作者仍在完成初稿，请等本轮写作结束后继续检查')
    # Preserve every execution choice instead of inheriting today's settings.
    original = json.loads(parent['payload'])
    keys = ('run_id', 'runtime', 'agent_backend', 'role_models', 'review_runtime',
            'review_mode', 'search_provider', 'search_policy', 'session_id',
            'auto_revision', 'max_parallel')
    payload = {key: original[key] for key in keys if key in original}
    if fast:
        payload.update(fast_evidence=True,auto_revision=False)
    elif mode=='direct':
        payload.update(fast_evidence=True)
    snapshot = binding(store, version_id)
    identity = hashlib.sha256(dump([parent['id'], snapshot, payload]).encode()).hexdigest()
    payload.update(version_id=version_id, continuation_of=parent['id'],
                   checks_input=snapshot, checks_identity=identity)

    def once(connection, job_id, frozen):
        # Store.enqueue holds BEGIN IMMEDIATE. Concurrent clicks/processes roll
        # back the speculative insert and return the same saved job, even after
        # failure/cancellation: resumption must use the ordinary resume action.
        for saved in connection.execute("SELECT * FROM jobs WHERE id<>? AND kind='assess' AND json_extract(payload,'$.continuation_of')=? ORDER BY rowid",(job_id,parent['id'])):
            previous=dict(saved);outcome=json.loads(previous['result'] or '{}')
            if check_binding(store,previous)==snapshot or outcome.get('checks_input')==snapshot:
                raise ExistingContinuation(previous['id'])
        row = connection.execute("SELECT id FROM jobs WHERE id<>? AND kind='assess' AND json_extract(payload,'$.checks_identity')=? ORDER BY rowid LIMIT 1",
                                 (job_id, identity)).fetchone()
        if row:
            raise ExistingContinuation(row['id'])
        active = connection.execute("SELECT id FROM jobs WHERE id<>? AND status IN ('queued','running') AND json_extract(payload,'$.continuation_of')=?",
                                    (job_id, parent['id'])).fetchone()
        if active:
            raise ValueError('这份报告仍有完整检查任务，请先等待或停止它')
        status = connection.execute('SELECT status FROM jobs WHERE id=?', (parent['id'],)).fetchone()
        if automatic and status['status'] in ('cancelled','interrupted','failed'):
            raise ValueError('原写作任务已停止，未启动后台检查')
        if status['status'] in ('queued', 'running') and not automatic:
            raise ValueError('原写作任务已恢复，请等它完成')
    try:
        return store.enqueue('assess', payload, before_commit=once)
    except ExistingContinuation as found:
        return store.one('jobs', found.job_id)


def claim_revision(store, job):
    """One automatic author attempt for the entire original draft-first chain."""
    chain = json.loads(job['payload'])['continuation_of']
    key = 'draft_first_revision:' + chain
    with store.tx() as connection:
        row = connection.execute('SELECT value FROM meta WHERE key=?', (key,)).fetchone()
        if row:
            return json.loads(row['value']) == job['id']
        connection.execute('INSERT INTO meta VALUES(?,?)', (key, dump(job['id'])))
    return True


def verify_input(store, job):
    payload = json.loads(job['payload'])
    if payload.get('continuation_of') and binding(store, payload['version_id']) != check_binding(store,job):
        raise Conflict('稿件或来源已变化；已完成记录保留，请对当前材料重新发起完整检查')


def resume_incomplete_output(job, folder, name):
    """A user-resumed continuation may finish an incomplete successful turn."""
    payload=json.loads(job['payload'])
    if not payload.get('continuation_of') or int(payload.get('attempt',1))<=1:return False
    path=folder/name
    if not path.exists():return False
    raw=path.read_bytes()
    try:incomplete=json.loads(raw).get('status')=='incomplete'
    except (ValueError,AttributeError):return False
    if not incomplete:return False
    archive=folder/'attempts';archive.mkdir(exist_ok=True)
    saved=archive/(path.stem+'-'+hashlib.sha256(raw).hexdigest()+'.json')
    if not saved.exists():saved.write_bytes(raw)
    consumed=archive/(path.stem+'-resume-'+str(payload['attempt'])+'.json')
    try:
        with consumed.open('x',encoding='utf-8') as stream:stream.write(dump({'attempt':payload['attempt'],'original':saved.name}))
    except FileExistsError:return False
    return True


def require_fact_check_settled(store, job):
    payload=json.loads(job['payload'])
    if not payload.get('continuation_of'):return
    children=store.rows("SELECT status,error FROM jobs WHERE kind='fact_check' AND json_extract(payload,'$.parent_job_id')=? AND json_extract(payload,'$.version_id')=? ORDER BY rowid DESC LIMIT 1",(job['id'],payload['version_id']))
    if children and children[0]['status']!='complete':
        raise ValueError('联网事实核查未完成，稿件及已取材料保留；请恢复本检查任务。'+str(children[0]['error'] or ''))
    req=json.loads(store.one('runs',payload['run_id'])['requirements'])
    if req.get('fact_check') and store.rows('SELECT id FROM claims WHERE run_id=? LIMIT 1',(payload['run_id'],)):
        from .research_plan import frozen
        stage=(frozen(store,payload['run_id']) or {}).get('fact_check') or {}
        if stage.get('status')!='completed':
            raise ValueError('联网事实核查阶段尚未完成（'+str(stage.get('status') or '未接纳')+'）；请先处理核查预算或中断状态，再恢复检查。')


def resume_cancelled_stage(connection, job):
    """Explicit parent resume restores only its own cancelled stage/budget.

    Keep the cancellation history and request ledger. No new allowance or stage
    is created, and a budget-exhausted/completed stage is never reopened here.
    """
    payload=json.loads(job['payload'])
    if not payload.get('continuation_of'):return
    from .research_plan import _read_plan, _save_plan
    plan=_read_plan(connection,payload['run_id']) or {}
    stage=plan.get('fact_check') or {}
    if stage.get('status')!='cancelled':return
    owned=connection.execute("SELECT id FROM jobs WHERE kind='fact_check' AND status='cancelled' AND json_extract(payload,'$.parent_job_id')=? AND json_extract(payload,'$.version_id')=? AND json_extract(payload,'$.stage_id')=?",(job['id'],payload['version_id'],stage.get('stage_id'))).fetchone()
    if not owned:return
    stage.setdefault('interrupted_closures',[]).append({key:stage.get(key) for key in ('status','closed','outcome')})
    stage.update(status='active',closed=None,outcome=None)
    _save_plan(connection,payload['run_id'],plan)


def execute(worker, job):
    store = worker.store
    payload = json.loads(job['payload'])
    brief = store.one('briefs', payload['version_id'])
    folder = worker.folder(job)
    accept_stage_sources(store,job)  # Recover a committed receipt before the next paid stage.
    expected = check_binding(store,job)
    # Fail closed before a new model call, including on resume. Only recorded
    # additions by this job's fact-check stage may extend the frozen input.
    actual = binding(store, brief['id'])
    if actual != expected:
        raise Conflict('稿件或来源已变化，未开始新的模型调用；请对当前版本重新发起完整检查')
    if worker.runtime.cancelled.is_set() or worker.stopping.is_set():
        raise InterruptedError('检查已停止，初稿保留')
    store.event(job['id'], 'checks_started', {'version_id': brief['id'],
                'origin_job_id': payload['continuation_of']})
    if payload.get('fast_evidence'):
        from .fast_reports import enrich
        brief=enrich(worker,job,brief,folder)
    result = worker.complete_draft_checks(job, brief, folder)
    verify_input(store, job)
    final_id = result['version_id']
    # Incomplete assessment must stay visibly incomplete rather than being
    # described as successful full verification by the enclosing job status.
    rows = store.rows('SELECT data FROM assessments WHERE version_id=? ORDER BY rowid DESC LIMIT 1', (final_id,))
    complete = bool(rows and json.loads(rows[0]['data']).get('status') == 'complete')
    if result.get('revision_status') in ('suggestion', 'user_edit'):
        complete = False
    state = 'complete' if complete and result.get('scoring', {}).get('status') != 'incomplete' else 'incomplete'
    if state=='incomplete' and result.get('revision_status') not in ('user_edit','suggestion'):
        raise ValueError('检查未全部完成，初稿和检查记录已保留；可恢复本检查任务')
    result.update(checks_state=state, checks_input=binding(store, final_id),
                  continuation_of=payload['continuation_of'])
    store.event(job['id'], 'checks_finished', {'version_id': final_id, 'checks_state': state})
    return result


def status(store, version_id):
    brief = store.one('briefs', version_id)
    req = json.loads(store.one('runs', brief['run_id'])['requirements'])
    if req.get('completion_mode') not in ('draft_first','fast','fast_web','direct'):
        return {'mode': 'standard'}
    parent = origin(store, version_id)
    result = {'mode': req['completion_mode'], 'origin_job_id': parent['id'],
              'state': 'writing' if parent['status'] in ('queued', 'running') else 'deferred'}
    rows = store.rows("SELECT * FROM jobs WHERE kind='assess' AND json_extract(payload,'$.continuation_of')=? ORDER BY rowid DESC", (parent['id'],))
    # Polling shows saved completion records; never re-read source bodies or
    # originals. Admission/execution/release still perform full content checks.
    current = binding(store, version_id,metadata_only=True)
    def matches(saved):
        if not saved:return False
        projected={**saved,'sources':[{key:item.get(key) for key in ('id','name','url','hash','status')} for item in saved.get('sources',[])]}
        return projected==current
    for row in rows:
        outcome = json.loads(row['result'] or '{}')
        task = json.loads(row['payload'])
        if row['status'] in ('queued', 'running'):
            return {**result, 'state': 'checking', 'job_id': row['id'], 'checked_version': task['version_id']}
        if matches(check_binding(store,row)) or matches(outcome.get('checks_input')):
            return {**result, 'state': outcome.get('checks_state', 'incomplete') if row['status'] == 'complete' else row['status'],
                    'job_id': row['id'], 'error': row.get('error'),
                    'checked_version': outcome.get('version_id',task['version_id']) if matches(outcome.get('checks_input')) else task['version_id']}
    return result
