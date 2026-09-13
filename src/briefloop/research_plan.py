"""Frozen research plans, round admission and request bookkeeping.

A quality-protocol run (``research_protocol=quality_v1``) may not start any
run-scoped controlled network operation until its plan is frozen. Freezing reads
only the already-authorized budget: an agent may propose structure, but cannot
expand budget or replace the frozen host/model/provider. The first round is
created by freezing; later rounds are added by the round scheduler.
"""
import hashlib
import json
from .store import dump, uid, now

PROTOCOL = 'quality_v1'
BUDGET_FIELDS = ('search_requests', 'candidate_urls', 'source_pages')
STRUCTURE_FIELDS = ('breadth', 'depth', 'parallel')
# Execution outcomes of the fact-check stage; they say why a check stopped, never
# whether a claim held (that judgement belongs to the Reviewer).
FACT_CHECK_STATUSES = ('completed', 'cancelled', 'failed', 'budget_exhausted')

# Product starting values, not quality-tuned optima. Per-round breadth is advisory;
# the run budget below is the hard ceiling.
PRESETS = {
    'quick': {'breadth': 3, 'depth': 1, 'parallel': 2, 'search_requests': 6, 'candidate_urls': 30, 'source_pages': 12},
    'standard': {'breadth': 8, 'depth': 3, 'parallel': 2, 'search_requests': 30, 'candidate_urls': 150, 'source_pages': 60},
    'deep': {'breadth': 12, 'depth': 4, 'parallel': 2, 'search_requests': 80, 'candidate_urls': 400, 'source_pages': 150},
}
DEFAULT_PRESET = 'standard'


class AdmissionError(ValueError):
    """A run-scoped controlled operation was refused before any network call."""

    def __init__(self, message, *, code='not_admitted'):
        super().__init__(message)
        self.code = code


def _plan_key(run_id): return 'research_plan:' + run_id
def _protocol_key(run_id): return 'research_protocol:' + run_id
def _requests_key(run_id): return 'research_requests:' + run_id
def _grant_key(run_id): return 'fact_check_grant:' + run_id


def frozen(store, run_id):
    return store.meta(_plan_key(run_id))


def is_quality(store, run_id):
    return store.meta(_protocol_key(run_id)) == PROTOCOL


def mark_protocol(store, run_id, protocol=PROTOCOL):
    store.set_meta(_protocol_key(run_id), protocol)


def authorized_budget(requirements):
    """The budget already stored on the run, or None for legacy runs."""
    value = requirements.get('research_budget')
    if value is None:
        return None
    return {field: int(value[field]) for field in BUDGET_FIELDS}


def _owner_job(store, run_id):
    for row in store.rows("SELECT * FROM jobs WHERE kind='generate' ORDER BY rowid DESC"):
        if json.loads(row['payload']).get('run_id') == run_id:
            return row
    return None


def _runtime_snapshot(store, job):
    settings = store.settings()
    payload = json.loads(job['payload']) if job else {}
    runtime = payload.get('runtime') or {}
    return {
        'agent_backend': payload.get('agent_backend') or settings.get('agent_backend', 'codex'),
        'search_provider': payload.get('search_provider') or settings.get('search_provider', 'native'),
        'role_models': payload.get('role_models') or {},
        'model': runtime.get('model') or settings.get('model'),
        'max_parallel': int(payload.get('max_parallel', settings.get('max_parallel', 4))),
    }


def _fingerprint(value):
    return hashlib.sha256(dump(value).encode()).hexdigest()


def freeze(store, run_id, *, preset=None, structure=None, owner_job_id=None):
    """Freeze one research plan for the run. Same input is idempotent; different input is refused."""
    run = store.one('runs', run_id)
    authorized = authorized_budget(json.loads(run['requirements']))
    chosen = dict(PRESETS.get(preset or DEFAULT_PRESET, PRESETS[DEFAULT_PRESET]))
    budget = dict(authorized) if authorized is not None else {field: chosen[field] for field in BUDGET_FIELDS}
    if structure:
        for field in STRUCTURE_FIELDS:
            if field in structure:
                chosen[field] = int(structure[field])
    for field in STRUCTURE_FIELDS:
        if chosen[field] < 1:
            raise ValueError('研究结构必须是正整数：' + field)
    # Breadth is guidance; small or zero authorized budgets remain valid.
    # The managed-tool reservation enforces the unchanged run budget.
    if not structure and budget['search_requests'] < chosen['breadth'] * chosen['depth']:
        chosen['depth'] = max(1, min(chosen['depth'], budget['search_requests']))
        chosen['breadth'] = max(1, min(chosen['breadth'], budget['search_requests'] // chosen['depth']))
    job = _owner_job(store, run_id)
    snapshot = {
        'research_protocol': PROTOCOL,
        'run_id': run_id,
        'owner_generate_job_id': owner_job_id or (job['id'] if job else None),
        'authorized_budget': budget,
        'structure': {field: chosen[field] for field in STRUCTURE_FIELDS},
        'budget': budget,
        'preset_id': preset or DEFAULT_PRESET,
        'frozen_runtime': _runtime_snapshot(store, job),
    }
    fingerprint = _fingerprint(snapshot)
    with store.tx() as connection:
        existing = _read_plan(connection, run_id)
        if existing:
            if existing.get('plan_fingerprint') == fingerprint:
                return existing
            raise ValueError('该任务已冻结了不同的研究计划，不能改写')
        # The deep preset pre-creates its whole round structure at freeze time;
        # other presets keep the single round created on demand.
        expanded = preset == 'deep'
        snapshot['plan_fingerprint'] = fingerprint
        rounds = {}
        for index in range(1, (chosen['depth'] if expanded else 1) + 1):
            rounds[uid('round')] = {'status': 'active' if index == 1 else 'pending', 'index': index,
                                    'created': now() if index == 1 else None, 'target_gap_ids': [],
                                    'tasks': _allocated_slots(store, run_id, index, chosen['breadth']) if expanded else [],
                                    'gaps': [], 'outcome': None}
        snapshot['current_round_id'] = next(identity for identity, info in rounds.items() if info['index'] == 1)
        snapshot['rounds'] = rounds
        snapshot['created'] = now()
        connection.execute('INSERT OR REPLACE INTO meta(key,value) VALUES(?,?)', (_plan_key(run_id), dump(snapshot)))
        connection.execute('INSERT OR REPLACE INTO meta(key,value) VALUES(?,?)', (_protocol_key(run_id), dump(PROTOCOL)))
    return snapshot


def admission(store, connection, run_id, operation):
    """Return (admitting id, stage) — a round with 'research', the fact-check
    stage id with 'fact_check' — or (None, None) for a legacy run.

    Must run inside the caller's reservation transaction so an admitted request
    cannot race a plan change or a closed stage.
    """
    marker = connection.execute('SELECT value FROM meta WHERE key=?', (_protocol_key(run_id),)).fetchone()
    if marker is None:
        return None, None
    try:
        protocol = json.loads(marker['value'])
    except (TypeError, ValueError):
        return None, None
    if protocol != PROTOCOL:
        return None, None
    row = connection.execute('SELECT value FROM meta WHERE key=?', (_plan_key(run_id),)).fetchone()
    if row is None:
        raise AdmissionError('本轮尚未冻结研究计划；受控联网前必须先冻结', code='plan_missing')
    plan = json.loads(row['value'])
    stage = plan.get('fact_check') or {}
    if stage.get('status') == 'active':
        return stage['stage_id'], 'fact_check'
    round_id = plan.get('current_round_id')
    info = (plan.get('rounds') or {}).get(round_id) or {}
    if not round_id or info.get('status') != 'active':
        if stage:
            raise AdmissionError('核查阶段已结束，不再接纳新的受控联网请求', code='fact_check_closed')
        raise AdmissionError('当前没有可用的联网轮次；受控联网已停止', code='round_closed')
    return round_id, 'research'


def record_request(store, connection, run_id, request_id, operation, round_id, stage='research'):
    """Persist a reserved controlled request in the same transaction as admission."""
    key = _requests_key(run_id)
    row = connection.execute('SELECT value FROM meta WHERE key=?', (key,)).fetchone()
    data = json.loads(row['value']) if row else {}
    data[request_id] = {'operation': operation, 'round_id': round_id, 'stage': stage,
                        'status': 'reserved', 'created': now()}
    connection.execute('INSERT OR REPLACE INTO meta(key,value) VALUES(?,?)', (key, dump(data)))


def settle_request(store, run_id, request_id, status, *, failure_kind=None, record_path=None):
    """Settle a reserved request after the network call; never settles someone else's id.

    Returns False without writing when the request belongs to a fact-check stage
    that already closed: a result arriving after cancellation is not admitted.
    """
    if not request_id:
        return True
    with store.tx() as connection:
        key = _requests_key(run_id)
        row = connection.execute('SELECT value FROM meta WHERE key=?', (key,)).fetchone()
        data = json.loads(row['value']) if row else {}
        entry = data.get(request_id) or {}
        if entry.get('stage') == 'fact_check':
            plan_row = connection.execute('SELECT value FROM meta WHERE key=?', (_plan_key(run_id),)).fetchone()
            stage = (json.loads(plan_row['value']) if plan_row else {}).get('fact_check') or {}
            if stage.get('stage_id') == entry.get('round_id') and stage.get('status') != 'active':
                return False
        entry.update({'status': status, 'updated': now()})
        if failure_kind is not None:
            entry['failure_kind'] = failure_kind
        if record_path is not None:
            entry['request_record_path'] = record_path
        data[request_id] = entry
        connection.execute('INSERT OR REPLACE INTO meta(key,value) VALUES(?,?)', (key, dump(data)))
    return True


def pending_requests(store, run_id):
    return store.meta(_requests_key(run_id)) or {}


def _read_plan(connection, run_id):
    row = connection.execute('SELECT value FROM meta WHERE key=?', (_plan_key(run_id),)).fetchone()
    return json.loads(row['value']) if row else None


def _save_plan(connection, run_id, plan):
    connection.execute('INSERT OR REPLACE INTO meta(key,value) VALUES(?,?)', (_plan_key(run_id), dump(plan)))


def _round_dir(store, run_id, index):
    return store.root / 'research' / run_id / 'rounds' / str(index)


def _allocated_slots(store, run_id, index, breadth):
    """Per-round Scout slots fixed by the frozen structure, not by later proposals."""
    directory = _round_dir(store, run_id, index)
    return [{'task_id': uid('task'), 'slot_id': f'scout-{number}',
             'directory': str(directory / f'scout-{number}')} for number in range(1, breadth + 1)]


def _write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix('.tmp')
    temporary.write_text(dump(value), encoding='utf-8')
    temporary.replace(path)


def begin_round(store, run_id, *, target_gap_ids=None, tasks=None, job_id=None):
    """Open the next research round. Repeats with the same target are idempotent.

    Refuses to open a round while another is active, beyond the depth limit, or
    when the referenced gaps do not exist in an earlier round.
    """
    # Admission and mutation share the same write transaction as network reservations.
    with store.tx() as connection:
        plan = _read_plan(connection, run_id)
        if plan is None:
            raise AdmissionError('本轮尚未冻结研究计划；不能开始研究轮次', code='plan_missing')
        rounds = plan.get('rounds') or {}
        target = list(target_gap_ids or [])
        active = plan.get('current_round_id')
        if active and (rounds.get(active) or {}).get('status') == 'active':
            current = rounds[active]
            if list(current.get('target_gap_ids') or []) == target:
                return {'round_id': active, 'index': current['index'], 'tasks': current.get('tasks', []), 'idempotent': True}
            raise AdmissionError('上一轮尚未结束，不能开始新一轮', code='round_open')
        if (plan.get('fact_check') or {}).get('status') == 'active':
            # Billing attribution stays unambiguous: no Scout round may restart
            # inside an admitted fact-check stage.
            raise AdmissionError('核查阶段进行中，不能开始新的研究轮次', code='fact_check_active')
        # Pending rounds are the pre-created future of an expanded deep plan,
        # not progress: the next index follows the highest round actually opened.
        index = max([int(r.get('index', 0)) for r in rounds.values() if r.get('status') != 'pending'] or [0]) + 1
        if index > int(plan['structure']['depth']):
            raise AdmissionError('已达到本任务的最大联网轮次', code='depth_reached')
        known = {gap['id'] for round_info in rounds.values() for gap in round_info.get('gaps', [])}
        unknown = [gap for gap in target if gap not in known]
        if unknown:
            raise ValueError('下一轮引用了不存在的缺口：' + ', '.join(unknown))
        pending = next((identity for identity, info in rounds.items()
                        if info.get('status') == 'pending' and int(info.get('index') or 0) == index), None)
        if pending is not None:
            # Activate the round pre-created by freeze; its slot allocation is
            # part of the frozen structure and later proposals cannot replace it.
            rounds[pending]['status'] = 'active'
            rounds[pending]['created'] = now()
            rounds[pending]['target_gap_ids'] = target
            plan['current_round_id'] = pending
            _save_plan(connection, run_id, plan)
            round_id, allocated = pending, rounds[pending].get('tasks', [])
        else:
            round_id = uid('round')
            directories = [(t or {}).get('slot_id') for t in (tasks or [])]
            allocated = []
            for position, directory in enumerate(directories):
                path = _round_dir(store, run_id, index) / ('scout-' + str(position + 1))
                allocated.append({'task_id': uid('task'), 'slot_id': directory, 'directory': str(path)})
            rounds[round_id] = {'status': 'active', 'index': index, 'created': now(),
                                'target_gap_ids': target, 'tasks': allocated, 'gaps': [], 'outcome': None}
            plan['rounds'] = rounds
            plan['current_round_id'] = round_id
            _save_plan(connection, run_id, plan)
    _write_json(_round_dir(store, run_id, index) / 'manifest.json',
                {'round_id': round_id, 'index': index, 'status': 'active',
                 'target_gap_ids': target, 'tasks': allocated, 'created': now()})
    if job_id:
        store.event(job_id, 'research_round', {'action': 'begin', 'round_id': round_id, 'index': index})
    return {'round_id': round_id, 'index': index, 'tasks': allocated, 'idempotent': False}


def _validate_gap(store, run_id, gap):
    if not isinstance(gap, dict) or not str(gap.get('description', '')).strip():
        raise ValueError('缺口需要说明（description）')
    allowed = set(store.source_ids(run_id))
    for source_id in gap.get('source_ids', []):
        if source_id not in allowed:
            raise ValueError('缺口来源不属于本轮报告：' + str(source_id))
    for claim_id in gap.get('related_claim_ids', []):
        from .evidence import record
        if record(store, 'claims', claim_id)['run_id'] != run_id:
            raise ValueError('缺口关联的主张属于另一报告：' + str(claim_id))


def finish_round(store, run_id, *, round_id=None, gaps=None, summary='', job_id=None):
    """Close a round, assign real gap ids and freeze its outcome. Idempotent per round."""
    # Admission and mutation share the same write transaction as network reservations.
    with store.tx() as connection:
        plan = _read_plan(connection, run_id)
        if plan is None:
            raise AdmissionError('本轮尚未冻结研究计划', code='plan_missing')
        rounds = plan.get('rounds') or {}
        round_id = round_id or plan.get('current_round_id')
        if round_id is None and rounds:
            # A replay after closing must return the already assigned gap identities.
            # Prefer the latest closed round: an expanded plan may still hold
            # higher-index pending rounds that were never activated.
            closed = [identity for identity, info in rounds.items() if info.get('status') == 'closed']
            round_id = max(closed or rounds, key=lambda identity: rounds[identity].get('index', 0))
        info = rounds.get(round_id)
        if not info:
            raise ValueError('轮次不存在：' + str(round_id))
        if info.get('status') != 'active':
            return {'round_id': round_id, 'index': info['index'], 'gaps': info.get('gaps', []), 'idempotent': True}
        records = []
        for gap in gaps or []:
            _validate_gap(store, run_id, gap)
            records.append({**gap, 'id': uid('gap'), 'round_id': round_id, 'round_index': info['index'], 'created': now()})
        info['gaps'] = records
        info['status'] = 'closed'
        info['closed'] = now()
        info['outcome'] = {'summary': summary, 'gap_ids': [record['id'] for record in records], 'closed_at': now()}
        plan['current_round_id'] = None
        _save_plan(connection, run_id, plan)
    _write_json(_round_dir(store, run_id, info['index']) / 'outcome.json',
                {'round_id': round_id, 'index': info['index'], 'summary': summary, 'gaps': records, 'closed_at': now()})
    if job_id:
        store.event(job_id, 'research_round', {'action': 'finish', 'round_id': round_id,
                                               'index': info['index'], 'gap_ids': [record['id'] for record in records]})
    return {'round_id': round_id, 'index': info['index'], 'gaps': records, 'idempotent': False}


def round_usage(store, run_id, round_id):
    entries = pending_requests(store, run_id)
    used = {'search': 0, 'pages': 0}
    for entry in entries.values():
        if entry.get('round_id') == round_id:
            operation = entry.get('operation')
            used[operation] = used.get(operation, 0) + 1
    plan = frozen(store, run_id) or {}
    return {'round_id': round_id, 'search': used.get('search', 0), 'pages': used.get('pages', 0),
            'breadth': (plan.get('structure') or {}).get('breadth')}


def _fact_check_budget_source(value):
    """Validate {'kind': task_reserve|user_grant, 'limits': KINDS ints}."""
    if not isinstance(value, dict) or value.get('kind') not in ('task_reserve', 'user_grant'):
        raise AdmissionError('核查预算来源需要 kind=task_reserve 或 user_grant', code='fact_check_budget_source')
    limits = value.get('limits')
    try:
        share = {field: int(limits[field]) for field in BUDGET_FIELDS}
    except (AttributeError, KeyError, TypeError, ValueError):
        raise AdmissionError('核查预算份额需要 ' + '/'.join(BUDGET_FIELDS) + ' 三项整数',
                             code='fact_check_budget_source') from None
    if any(count < 0 for count in share.values()) or not any(count > 0 for count in share.values()):
        raise AdmissionError('核查预算份额各项非负且至少一项为正', code='fact_check_budget_source')
    return {'kind': value['kind'], 'limits': share}


def admit_fact_check(store, run_id, budget_source, *, job_id=None):
    """Admit an explicit fact-check stage onto the frozen plan. Repeats reuse the stage.

    The stage never disguises itself as a Scout round and never bills outside the
    run's KINDS meter: its controlled requests are recorded against the stage id.
    ``budget_source`` records where the money comes from — a share reserved from
    the task's total budget, or an amount the user explicitly granted on top.
    """
    run = store.one('runs', run_id)
    requirements = json.loads(run['requirements'])
    if not requirements.get('allow_web', False):
        raise AdmissionError('离线任务不能接纳联网核查阶段', code='fact_check_offline')
    source = _fact_check_budget_source(budget_source)
    if source['kind'] == 'task_reserve':
        if authorized_budget(requirements) is None:
            # A run without an authorized budget has no number to reserve from;
            # inventing a default share would silently add SAFE-style budget.
            raise AdmissionError('本任务没有已授权预算，不能预留核查份额；需用户明确追加额度',
                                 code='fact_check_no_budget')

    def mutate(plan):
        existing = plan.get('fact_check')
        if existing:
            return False, {'stage_id': existing['stage_id'], 'status': existing['status'],
                           'budget_source': existing['budget_source'], 'idempotent': True}
        rounds = plan.get('rounds') or {}
        active = plan.get('current_round_id')
        if active and (rounds.get(active) or {}).get('status') == 'active':
            raise AdmissionError('研究轮次尚未收束，不能接纳核查阶段', code='fact_check_round_open')
        if source['kind'] == 'task_reserve':
            from .research_budget import spent
            used = spent(store, run_id)
            for field in BUDGET_FIELDS:
                if source['limits'][field] > max(0, plan['budget'][field] - used[field]):
                    raise AdmissionError('核查预留份额超过任务剩余预算：' + field,
                                         code='fact_check_share_exceeds_budget')
        stage = {'stage_id': uid('fchk'), 'status': 'active', 'budget_source': source,
                 'created': now(), 'closed': None, 'outcome': None}
        plan['fact_check'] = stage
        return True, {**stage, 'idempotent': False}

    result = _save_plan(store, run_id, mutate, missing='本轮尚未冻结研究计划；不能接纳核查阶段')
    if not result['idempotent'] and job_id:
        store.event(job_id, 'fact_check', {'action': 'admit', 'stage_id': result['stage_id'],
                                           'budget_source': result['budget_source']})
    return result


def finish_fact_check(store, run_id, *, status, summary='', job_id=None):
    """Close the fact-check stage with an execution status, never a factual verdict.

    Idempotent per stage: a replay returns the recorded outcome unchanged, so a
    late second close cannot rewrite 'cancelled' into 'completed'.
    """
    if status not in FACT_CHECK_STATUSES:
        raise ValueError('核查阶段结束状态必须是 ' + '/'.join(FACT_CHECK_STATUSES))

    def mutate(plan):
        stage = plan.get('fact_check')
        if not stage:
            raise AdmissionError('尚未接纳核查阶段', code='fact_check_missing')
        if stage['status'] != 'active':
            return False, {'stage_id': stage['stage_id'], 'status': stage['status'],
                           'outcome': stage['outcome'], 'idempotent': True}
        usage = {'search': 0, 'pages': 0}
        for entry in pending_requests(store, run_id).values():
            if entry.get('round_id') == stage['stage_id']:
                operation = entry.get('operation')
                usage[operation] = usage.get(operation, 0) + 1
        stage['status'] = status
        stage['closed'] = now()
        stage['outcome'] = {'status': status, 'summary': summary, 'usage': usage, 'closed_at': stage['closed']}
        return True, {'stage_id': stage['stage_id'], 'status': status,
                      'outcome': stage['outcome'], 'idempotent': False}

    result = _save_plan(store, run_id, mutate, missing='本轮尚未冻结研究计划')
    if not result['idempotent'] and job_id:
        store.event(job_id, 'fact_check', {'action': 'finish', 'stage_id': result['stage_id'], 'status': status})
    return result


def pending_fact_check_grant(store, run_id):
    """A user grant recorded while research rounds were still open, if any."""
    grant = store.meta(_grant_key(run_id))
    return grant if isinstance(grant, dict) and grant.get('limits') else None


def add_fact_check_grant(store, run_id, limits, *, job_id=None):
    """Record an explicit user budget addition for fact checking.

    The addition is real spendable budget (research_budget adds it to the run's
    KINDS meter while the stage is active), not a bookkeeping label. Where it
    lands depends on the stage: no stage yet — admit immediately when rounds
    have closed, otherwise keep it pending for the Worker's admission; active —
    append to the running stage; exhausted — reopen a fresh stage so the check
    can continue. Completed/cancelled/failed stages are never reopened here.
    Returns {'status': 'admitted'|'pending'|'active'|'reopened', ...}.
    """
    source = _fact_check_budget_source({'kind': 'user_grant', 'limits': limits})
    grant = {'limits': source['limits'], 'created': now()}
    run = store.one('runs', run_id)
    requirements = json.loads(run['requirements'])
    if not requirements.get('allow_web', False):
        raise AdmissionError('离线任务不能追加联网核查预算', code='fact_check_offline')
    plan = frozen(store, run_id)
    if plan is None:
        raise AdmissionError('本轮尚未冻结研究计划；不能追加核查预算', code='plan_missing')
    stage = plan.get('fact_check')
    if stage is None:
        try:
            admitted = admit_fact_check(store, run_id, {'kind': 'user_grant', 'limits': grant['limits']}, job_id=job_id)
        except AdmissionError as exc:
            if exc.code != 'fact_check_round_open':
                raise
            store.set_meta(_grant_key(run_id), grant)
            if job_id:
                store.event(job_id, 'fact_check', {'action': 'grant_pending', 'limits': grant['limits']})
            return {'status': 'pending', 'limits': grant['limits']}
        return {'status': 'admitted', 'stage_id': admitted['stage_id'], 'limits': grant['limits']}
    if stage.get('status') == 'active':
        def append(plan):
            current = plan.get('fact_check')
            if not current or current['stage_id'] != stage['stage_id'] or current.get('status') != 'active':
                raise AdmissionError('核查阶段已变化，请刷新后重试', code='fact_check_conflict')
            current.setdefault('grants', []).append(grant)
            return True, {'stage_id': stage['stage_id'], 'status': 'active'}
        _save_plan(store, run_id, append)
        if job_id:
            store.event(job_id, 'fact_check', {'action': 'grant_added', 'stage_id': stage['stage_id'], 'limits': grant['limits']})
        return {'status': 'active', 'stage_id': stage['stage_id'], 'limits': grant['limits']}
    if stage.get('status') == 'budget_exhausted':
        def reopen(plan):
            current = plan.get('fact_check')
            if not current or current['stage_id'] != stage['stage_id'] or current['status'] != 'budget_exhausted':
                raise AdmissionError('核查阶段已变化，请刷新后重试', code='fact_check_conflict')
            plan.setdefault('fact_check_history', []).append(current)
            fresh = {'stage_id': uid('fchk'), 'status': 'active',
                     'budget_source': {'kind': 'user_grant', 'limits': grant['limits']},
                     'created': now(), 'closed': None, 'outcome': None}
            plan['fact_check'] = fresh
            return True, fresh
        fresh = _save_plan(store, run_id, reopen)
        if job_id:
            store.event(job_id, 'fact_check', {'action': 'grant_reopened', 'stage_id': fresh['stage_id'],
                                               'limits': grant['limits'], 'previous_stage_id': stage['stage_id']})
        return {'status': 'reopened', 'stage_id': fresh['stage_id'], 'limits': grant['limits']}
    raise AdmissionError('核查阶段已以 ' + str(stage.get('status')) + ' 收束，追加预算不能重开；需重新核查请重新生成',
                         code='fact_check_closed')


def consume_pending_fact_check_grant(store, run_id, admitted_stage_id):
    """Drop a pending grant after the Worker admitted its stage with it.

    Admission is idempotent, so a crash between admitting and dropping only
    replays into the same stage; the grant is only dropped when that stage is
    really the one on the plan now.
    """
    stage = (frozen(store, run_id) or {}).get('fact_check') or {}
    if stage.get('stage_id') != admitted_stage_id:
        return False
    with store.tx() as connection:
        connection.execute('DELETE FROM meta WHERE key=?', (_grant_key(run_id),))
    return True


def search_slots_left(store, connection, run_id, round_id):
    """Remaining search attempts in the round, or None when no breadth is set."""
    plan_row = connection.execute('SELECT value FROM meta WHERE key=?', (_plan_key(run_id),)).fetchone()
    plan = json.loads(plan_row['value']) if plan_row else {}
    breadth = int((plan.get('structure') or {}).get('breadth') or 0)
    if not breadth:
        return None
    row = connection.execute('SELECT value FROM meta WHERE key=?', (_requests_key(run_id),)).fetchone()
    data = json.loads(row['value']) if row else {}
    used = sum(1 for entry in data.values() if entry.get('round_id') == round_id and entry.get('operation') == 'search')
    return breadth - used


def status(store, run_id):
    return {'protocol': store.meta(_protocol_key(run_id)), 'plan': frozen(store, run_id)}
