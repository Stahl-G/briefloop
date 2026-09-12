"""Trusted task authorization and capability-scoped material tools.

The host owns bind/access/revoke. Agent dispatch accepts no task/grant identity.
Capabilities are process-local; resume must ask the trusted host for fresh access.
"""
import json
import secrets
import threading
from .config import ConnectorError
from .materials import ConnectorMaterials
from ..store import now


class TaskMaterials:
    def __init__(self, store, service):
        self.store = store
        self.materials = ConnectorMaterials(store, service)
        self._access = {}
        self._lock = threading.Lock()
        with store.tx() as c:
            c.execute('''CREATE TABLE IF NOT EXISTS connector_task_bindings(
                job_id TEXT PRIMARY KEY REFERENCES jobs(id), run_id TEXT NOT NULL REFERENCES runs(id),
                grant_id TEXT NOT NULL UNIQUE REFERENCES connector_grants(id), created TEXT NOT NULL)''')

    def _job(self, job_id, *, queued=False):
        job = self.store.one('jobs', job_id)
        if job['kind'] != 'generate' or job['status'] not in (('queued',) if queued else ('queued', 'running')):
            raise ConnectorError('仅未结束的报告生成任务可读取连接器；审阅只使用快照。', code='task_not_available')
        run_id = json.loads(job['payload']).get('run_id')
        if not run_id:
            raise ConnectorError('任务没有绑定报告。', code='invalid_task')
        self.store.one('runs', run_id)
        return run_id

    def _binding(self, job_id):
        rows = self.store.rows('SELECT * FROM connector_task_bindings WHERE job_id=?', (job_id,))
        if not rows:
            raise ConnectorError('本任务没有连接器授权。', code='not_granted')
        return rows[0]

    def bind(self, job_id, selections, *, max_calls, max_total_bytes):
        run_id = self._job(job_id, queued=True)
        if self.store.rows('SELECT job_id FROM connector_task_bindings WHERE job_id=?', (job_id,)):
            raise ConnectorError('本任务授权已冻结，不可覆盖或提额。', code='binding_frozen')
        grant = self.materials.freeze(run_id, selections, max_calls=max_calls, max_total_bytes=max_total_bytes)
        try:
            with self.store.tx() as c:
                job = c.execute('SELECT * FROM jobs WHERE id=?', (job_id,)).fetchone()
                if job['status'] != 'queued' or job['kind'] != 'generate' or json.loads(job['payload']).get('run_id') != run_id:
                    raise ConnectorError('任务已经开始，请在任务启动前选择材料。', code='task_started')
                c.execute('INSERT INTO connector_task_bindings VALUES(?,?,?,?)', (job_id, run_id, grant['id'], now()))
        except Exception:
            self.materials.revoke(grant['id'], run_id)
            raise
        self.store.event(job_id, 'connector_task_bound', {'run_id': run_id, 'grant_id': grant['id']})
        return self.status(job_id)

    def status(self, job_id):
        row = self._binding(job_id)
        return {'job_id': job_id, 'run_id': row['run_id'], **self.materials.status(row['grant_id'], row['run_id'])}

    def access(self, job_id):
        """Host-only: hand this capability to the generating process, never Reviewer."""
        run_id = self._job(job_id)
        row = self._binding(job_id)
        if row['run_id'] != run_id:
            raise ConnectorError('任务报告绑定发生变化。', code='invalid_task')
        self.materials.grants.get(row['grant_id'], run_id, active=True)
        token = secrets.token_urlsafe(32)
        with self._lock:
            self._access[token] = job_id
        return {'access_token': token, 'tool_path': '/api/connectors/task-tool'}

    def revoke(self, job_id):
        row = self._binding(job_id)
        result = self.materials.revoke(row['grant_id'], row['run_id'])
        with self._lock:
            self._access = {token: task for token, task in self._access.items() if task != job_id}
        return result

    def dispatch(self, token, request):
        with self._lock:
            job_id = self._access.get(token)
        if not job_id:
            raise ConnectorError('任务连接器访问凭据无效。', code='invalid_access')
        run_id = self._job(job_id)
        row = self._binding(job_id)
        if run_id != row['run_id']:
            raise ConnectorError('任务报告绑定发生变化。', code='invalid_task')
        actions = {'status': {'action'}, 'receipt': {'action', 'receipt_id'},
                   'read': {'action', 'connector_id', 'uri', 'request_id'},
                   'call': {'action', 'connector_id', 'name', 'arguments', 'request_id'}}
        if not isinstance(request, dict) or request.get('action') not in actions or set(request) != actions[request['action']]:
            raise ConnectorError('材料工具只接受已绑定任务的读取、调用、回执和状态参数。', code='invalid_request')
        action = request['action']
        self.materials.grants.get(row['grant_id'], run_id, active=True)
        args = {key: value for key, value in request.items() if key != 'action'}
        return getattr(self.materials, action)(row['grant_id'], run_id, **args)
