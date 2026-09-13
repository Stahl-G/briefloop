"""Host-only frozen run permissions and atomic, durable request reservations."""
import hashlib
import json
import re

from .config import ConnectorError
from ..store import dump, now, uid

SCHEMA = '''
CREATE TABLE IF NOT EXISTS connector_grants(
 id TEXT PRIMARY KEY, run_id TEXT NOT NULL REFERENCES runs(id), status TEXT NOT NULL,
 data TEXT NOT NULL, created TEXT NOT NULL, revoked TEXT);
CREATE TABLE IF NOT EXISTS connector_operations(
 id TEXT PRIMARY KEY, grant_id TEXT NOT NULL REFERENCES connector_grants(id),
 request_id TEXT NOT NULL, request_hash TEXT NOT NULL, request TEXT NOT NULL,
 status TEXT NOT NULL, reserved_bytes INTEGER NOT NULL, charged_bytes INTEGER NOT NULL,
 receipt TEXT, receipt_hash TEXT, source_id TEXT, error TEXT,
 created TEXT NOT NULL, updated TEXT NOT NULL, UNIQUE(grant_id,request_id));
'''


def encoded(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False).encode('utf-8')


def digest(value):
    return hashlib.sha256(encoded(value)).hexdigest()


class Grants:
    def __init__(self, store):
        self.store = store
        with store.tx() as connection:
            connection.executescript(SCHEMA)

    def get(self, grant_id, run_id, *, connection=None, active=False):
        if connection is None:
            rows = self.store.rows('SELECT * FROM connector_grants WHERE id=?', (grant_id,))
        else:
            rows = connection.execute('SELECT * FROM connector_grants WHERE id=?', (grant_id,)).fetchall()
        if not rows or rows[0]['run_id'] != run_id:
            raise ConnectorError('连接授权不属于本轮报告。', code='invalid_grant')
        row = dict(rows[0])
        if active and row['status'] != 'active':
            raise ConnectorError('本轮连接授权已撤销。', code='revoked')
        data = json.loads(row['data'])
        snapshot = {key: value for key, value in data.items() if key != 'snapshot_hash'}
        if data.get('run_id') != run_id or data.get('snapshot_hash') != digest(snapshot):
            raise ConnectorError('冻结授权快照校验失败。', code='grant_integrity')
        return {**row, 'data': data}

    def freeze(self, run_id, snapshots, *, max_calls, max_total_bytes, grant_id=None):
        self.store.one('runs', run_id)
        for value, high in ((max_calls, 1000), (max_total_bytes, 1073741824)):
            if type(value) is not int or not 1 <= value <= high:
                raise ConnectorError('请显式提供有效的本轮调用次数和材料字节预算。', code='invalid_budget')
        if any(item['max_response_bytes'] > max_total_bytes for item in snapshots):
            raise ConnectorError('材料总预算小于一次响应上限，请调整明确额度。', code='invalid_budget')
        identity = grant_id or uid('mcpgrant')
        if not isinstance(identity, str) or not re.fullmatch(r'mcpgrant_[a-zA-Z0-9_-]{1,100}', identity):
            raise ConnectorError('授权 ID 无效。', code='invalid_grant')
        data = {'schema_version': 1, 'run_id': run_id, 'connectors': snapshots,
                'max_calls': max_calls, 'max_total_bytes': max_total_bytes}
        data['snapshot_hash'] = digest(data)
        with self.store.tx() as connection:
            existing = connection.execute('SELECT * FROM connector_grants WHERE id=?', (identity,)).fetchone()
            if existing:
                if existing['data'] != dump(data) or existing['run_id'] != run_id:
                    raise ConnectorError('冻结授权不可被覆盖。', code='grant_conflict')
            else:
                connection.execute('INSERT INTO connector_grants VALUES(?,?,?,?,?,NULL)',
                                   (identity, run_id, 'active', dump(data), now()))
        return self.get(identity, run_id)

    def reserve(self, grant_id, run_id, request, request_id):
        if not isinstance(request_id, str) or not request_id.strip() or len(request_id) > 160:
            raise ConnectorError('调用需要稳定的 request_id。', code='invalid_request')
        request_hash = digest(request)
        with self.store.tx() as connection:
            grant = self.get(grant_id, run_id, connection=connection)
            previous = connection.execute('SELECT * FROM connector_operations WHERE grant_id=? AND request_id=?',
                                          (grant_id, request_id)).fetchone()
            if previous:
                if previous['request_hash'] != request_hash:
                    raise ConnectorError('同一 request_id 不可用于其他调用。', code='request_conflict')
                return dict(previous), False
            if grant['status'] != 'active':
                raise ConnectorError('本轮连接授权已撤销。', code='revoked')
            selected = next((x for x in grant['data']['connectors'] if x['id'] == request['connector_id']), None)
            allowed = 'resources' if request['method'] == 'read' else 'tools'
            if selected is None or request['target'] not in selected[allowed]:
                raise ConnectorError('本轮未授权该连接器或具体资源/工具。', code='not_granted')
            limit = selected['max_response_bytes']
            if len(encoded(request)) > limit:
                raise ConnectorError('请求参数超出已授权单次大小。', code='request_limit')
            usage = connection.execute('SELECT COUNT(*) AS calls,COALESCE(SUM(charged_bytes),0) AS bytes FROM connector_operations WHERE grant_id=?', (grant_id,)).fetchone()
            if usage['calls'] >= grant['data']['max_calls'] or usage['bytes'] + limit > grant['data']['max_total_bytes']:
                raise ConnectorError('本轮连接器调用或材料预算已用完。', code='budget_exhausted')
            identity = uid('mcpreceipt')
            timestamp = now()
            connection.execute('INSERT INTO connector_operations VALUES(?,?,?,?,?,?,?,?,NULL,NULL,NULL,NULL,?,?)',
                               (identity, grant_id, request_id, request_hash, dump(request), 'in_flight', limit, limit, timestamp, timestamp))
            row = dict(connection.execute('SELECT * FROM connector_operations WHERE id=?', (identity,)).fetchone())
        return row, True

    def revoke(self, grant_id, run_id):
        with self.store.tx() as connection:
            self.get(grant_id, run_id, connection=connection)
            connection.execute("UPDATE connector_grants SET status='revoked',revoked=COALESCE(revoked,?) WHERE id=?", (now(), grant_id))
        return self.get(grant_id, run_id)

    def usage(self, grant_id, run_id):
        grant = self.get(grant_id, run_id)
        used = self.store.rows('SELECT COUNT(*) AS calls,COALESCE(SUM(charged_bytes),0) AS bytes FROM connector_operations WHERE grant_id=?', (grant_id,))[0]
        return {'calls': used['calls'], 'charged_bytes': used['bytes'],
                'max_calls': grant['data']['max_calls'], 'max_total_bytes': grant['data']['max_total_bytes']}
