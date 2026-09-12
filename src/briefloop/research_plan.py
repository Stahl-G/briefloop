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
        'max_parallel': int(settings.get('max_parallel', 4)),
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
    existing = frozen(store, run_id)
    if existing:
        if existing.get('plan_fingerprint') == fingerprint:
            return existing
        raise ValueError('该任务已冻结了不同的研究计划，不能改写')
    round_id = uid('round')
    snapshot['plan_fingerprint'] = fingerprint
    snapshot['current_round_id'] = round_id
    snapshot['rounds'] = {round_id: {'status': 'active', 'index': 1, 'created': now(),
                                     'target_gap_ids': [], 'tasks': [], 'gaps': [], 'outcome': None}}
    snapshot['created'] = now()
    with store.tx() as connection:
        connection.execute('INSERT OR REPLACE INTO meta(key,value) VALUES(?,?)', (_plan_key(run_id), dump(snapshot)))
        connection.execute('INSERT OR REPLACE INTO meta(key,value) VALUES(?,?)', (_protocol_key(run_id), dump(PROTOCOL)))
    return snapshot


def admission(store, connection, run_id, operation):
    """Return the active round id for a quality run, or None for a legacy run.

    Must run inside the caller's reservation transaction so an admitted request
    cannot race a plan change or a closed round.
    """
    marker = connection.execute('SELECT value FROM meta WHERE key=?', (_protocol_key(run_id),)).fetchone()
    if marker is None:
        return None
    try:
        protocol = json.loads(marker['value'])
    except (TypeError, ValueError):
        return None
    if protocol != PROTOCOL:
        return None
    row = connection.execute('SELECT value FROM meta WHERE key=?', (_plan_key(run_id),)).fetchone()
    if row is None:
        raise AdmissionError('本轮尚未冻结研究计划；受控联网前必须先冻结', code='plan_missing')
    plan = json.loads(row['value'])
    round_id = plan.get('current_round_id')
    info = (plan.get('rounds') or {}).get(round_id) or {}
    if not round_id or info.get('status') != 'active':
        raise AdmissionError('当前没有可用的联网轮次；受控联网已停止', code='round_closed')
    return round_id


def record_request(store, connection, run_id, request_id, operation, round_id):
    """Persist a reserved controlled request in the same transaction as admission."""
    key = _requests_key(run_id)
    row = connection.execute('SELECT value FROM meta WHERE key=?', (key,)).fetchone()
    data = json.loads(row['value']) if row else {}
    data[request_id] = {'operation': operation, 'round_id': round_id, 'status': 'reserved', 'created': now()}
    connection.execute('INSERT OR REPLACE INTO meta(key,value) VALUES(?,?)', (key, dump(data)))


def settle_request(store, run_id, request_id, status, *, failure_kind=None, record_path=None):
    """Settle a reserved request after the network call; never settles someone else's id."""
    if not request_id:
        return
    with store.tx() as connection:
        key = _requests_key(run_id)
        row = connection.execute('SELECT value FROM meta WHERE key=?', (key,)).fetchone()
        data = json.loads(row['value']) if row else {}
        entry = data.get(request_id) or {}
        entry.update({'status': status, 'updated': now()})
        if failure_kind is not None:
            entry['failure_kind'] = failure_kind
        if record_path is not None:
            entry['request_record_path'] = record_path
        data[request_id] = entry
        connection.execute('INSERT OR REPLACE INTO meta(key,value) VALUES(?,?)', (key, dump(data)))


def pending_requests(store, run_id):
    return store.meta(_requests_key(run_id)) or {}


def _save_plan(store, run_id, plan):
    with store.tx() as connection:
        connection.execute('INSERT OR REPLACE INTO meta(key,value) VALUES(?,?)', (_plan_key(run_id), dump(plan)))


def _round_dir(store, run_id, index):
    return store.root / 'research' / run_id / 'rounds' / str(index)


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
    plan = frozen(store, run_id)
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
    index = max([int(r.get('index', 0)) for r in rounds.values()] or [0]) + 1
    if index > int(plan['structure']['depth']):
        raise AdmissionError('已达到本任务的最大联网轮次', code='depth_reached')
    known = {gap['id'] for round_info in rounds.values() for gap in round_info.get('gaps', [])}
    unknown = [gap for gap in target if gap not in known]
    if unknown:
        raise ValueError('下一轮引用了不存在的缺口：' + ', '.join(unknown))
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
    _save_plan(store, run_id, plan)
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
    plan = frozen(store, run_id)
    if plan is None:
        raise AdmissionError('本轮尚未冻结研究计划', code='plan_missing')
    rounds = plan.get('rounds') or {}
    round_id = round_id or plan.get('current_round_id')
    if round_id is None and rounds:
        # A replay after closing must return the already assigned gap identities.
        round_id = max(rounds, key=lambda identity: rounds[identity].get('index', 0))
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
    _save_plan(store, run_id, plan)
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
