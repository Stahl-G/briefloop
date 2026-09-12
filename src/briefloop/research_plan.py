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

# Product starting values, not quality-tuned optima. Standard keeps the legacy
# weekly budget (12/60/18); quick and deep are explicit.
PRESETS = {
    'quick': {'breadth': 3, 'depth': 1, 'parallel': 2, 'search_requests': 3, 'candidate_urls': 15, 'source_pages': 6},
    'standard': {'breadth': 6, 'depth': 2, 'parallel': 2, 'search_requests': 12, 'candidate_urls': 60, 'source_pages': 18},
    'deep': {'breadth': 8, 'depth': 3, 'parallel': 2, 'search_requests': 24, 'candidate_urls': 120, 'source_pages': 48},
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
    if chosen['breadth'] * chosen['depth'] > budget['search_requests']:
        raise ValueError('每轮查询尝试（breadth×depth）超过已授权的搜索额度')
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
    snapshot['rounds'] = {round_id: {'status': 'active', 'created': now()}}
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


def status(store, run_id):
    return {'protocol': store.meta(_protocol_key(run_id)), 'plan': frozen(store, run_id)}
