"""Small, atomic request boundary for local external report clients.

Only deterministic store operations run here. Existing Worker jobs still own
research, generation, review and Word production.
"""
from contextlib import contextmanager
import hashlib
import json
import re

from .store import Store, Conflict, dump, now
from .execution_records import sanitize

SCHEMA = '''CREATE TABLE IF NOT EXISTS external_requests(
 request_id TEXT PRIMARY KEY, request_hash TEXT NOT NULL,
 action TEXT NOT NULL, result TEXT NOT NULL, created TEXT NOT NULL)'''
MUTATIONS = {'submit', 'revise', 'export'}
FIELDS = {
    'inspect': set(),
    'source': {'source_id'},
    'submit': {'requirements', 'source_ids'},
    'revise': {'base_version', 'editor_document'},
    'export': {'version_id'},
    'query': {'job_id'},
    'read': {'version_id'},
}


class _RequestStore(Store):
    """Reuse Store methods within this request's already-owned transaction.

    This view never initializes a workspace or owns a connection. The limited
    action set below must not call models, schema initializers or chat notices.
    Reads use the same connection so run creation is visible to job admission.
    """
    def __init__(self, store, connection):
        self.root, self.db, self.connection = store.root, store.db, connection

    @contextmanager
    def tx(self):
        yield self.connection

    def rows(self, query, args=()):
        return [dict(row) for row in self.connection.execute(query, args)]


def capabilities():
    return {'protocol': 1, 'actions': list(FIELDS), 'request_id_required': sorted(MUTATIONS),
            'export_kind': 'working_draft', 'starts_service': False}


def _operation(store, action, body):
    if action == 'inspect':
        briefs = store.rows("SELECT b.id,b.run_id,b.detail FROM briefs b JOIN runs r ON r.id=b.run_id WHERE r.mode='normal' ORDER BY b.rowid DESC LIMIT 20")
        sources = store.rows('SELECT id,name,status,error FROM sources ORDER BY rowid DESC LIMIT 100')
        for source in sources:
            source['error'] = sanitize(source['error'])
        return {'sources': sources,
                'reports': [{'version_id': b['id'], 'run_id': b['run_id'], 'title': json.loads(b['detail']).get('title', '')} for b in briefs],
                'limits': {'sources': 100, 'reports': 20}}
    if action == 'source':
        from .projections import source_details
        source, provenance, original = source_details(store, body['source_id'])
        return {'source_id': source['id'], 'name': source['name'], 'status': source['status'],
                'text': store.source_text(source['id']), 'provenance': provenance,
                'original_url': '/api/source-original?id=' + source['id'] if original else None}
    if action == 'submit':
        ids = body.get('source_ids', [])
        if not isinstance(ids, list) or not all(isinstance(s, str) for s in ids):
            raise ValueError('source_ids 必须是来源 ID 数组')
        run = store.create_run(body['requirements'], ids, research_protocol='quality_v1')
        job = store.enqueue('generate', {'run_id': run['id']})
        return {'status': 'accepted', 'job_id': job['id'], 'run_id': run['id']}
    if action == 'revise':
        if not isinstance(body.get('editor_document'), dict):
            raise ValueError('editor_document 必须是完整富文档对象')
        brief = store.revise(body['base_version'], editor_document=body['editor_document'], author='agent')
        return {'status': 'saved', 'version_id': brief['id'], 'run_id': brief['run_id']}
    if action == 'export':
        from .export_jobs import enqueue_export
        job = enqueue_export(store, body['version_id'])
        return {'status': 'accepted', 'job_id': job['id'], 'version_id': body['version_id'],
                'export_kind': 'working_draft'}
    if action == 'read':
        from .document_model import brief_document
        brief = store.one('briefs', body['version_id'])
        return {'version_id': brief['id'], 'run_id': brief['run_id'],
                'parent_id': brief['parent_id'], 'author': brief['author'],
                'title': json.loads(brief['detail']).get('title', ''),
                'markdown': brief['markdown'], 'editor_document': brief_document(brief)}
    if action == 'query':
        job = store.one('jobs', body['job_id'])
        payload = json.loads(job['payload'])
        result = json.loads(job['result'] or '{}')
        value = {key: job[key] for key in ('id', 'kind', 'status', 'created', 'updated')}
        value['job_id'] = value.pop('id')
        value['error'] = sanitize(job['error']) if job['error'] else None
        value['run_id'] = payload.get('run_id')
        value['version_id'] = result.get('version_id') or payload.get('version_id')
        value['terminal'] = job['status'] in ('complete', 'failed', 'interrupted', 'cancelled')
        # A saved draft can finish generation with an incomplete evaluation.
        # Expose these public outcomes without returning host logs or reasoning.
        value.update({key: sanitize(result[key]) for key in
                      ('revision_status', 'revision_message', 'original_version_id') if key in result})
        if isinstance(result.get('scoring'), dict):
            value['scoring'] = {key: sanitize(result['scoring'][key]) for key in
                                ('status', 'error') if key in result['scoring']}
        if value['run_id']:
            versions = store.rows('SELECT id FROM briefs WHERE run_id=? ORDER BY rowid DESC LIMIT 1', (value['run_id'],))
            value['latest_version_id'] = versions[0]['id'] if versions else None
        if job['kind'] == 'export_docx' and job['status'] == 'complete':
            from .export_jobs import output_path
            path = output_path(store, job)
            valid = path.is_file() and hashlib.sha256(path.read_bytes()).hexdigest() == result.get('sha256')
            value['artifact_available'] = valid
            if valid:
                value['download_url'] = '/api/export-file?job=' + job['id']
                value['sha256'] = result['sha256']
            else:
                value['artifact_error'] = '导出文件缺失或已变化；本请求未自动重建'
        return value
    raise ValueError('不支持的外部操作')


def dispatch(store, request):
    if not isinstance(request, dict):
        raise ValueError('请求必须是 JSON 对象')
    if request.get('workspace_id') != store.meta('workspace_id'):
        raise Conflict('工作区身份已变化，请重新连接已授权的工作区')
    action = request.get('action')
    if not isinstance(action, str) or action not in FIELDS:
        raise ValueError('不支持的外部操作')
    if set(request) - FIELDS[action] - {'workspace_id', 'action', 'request_id'}:
        raise ValueError('请求含有当前操作不支持的字段')
    required = FIELDS[action] - ({'source_ids'} if action == 'submit' else set())
    if not required.issubset(request):
        raise ValueError('缺少必需字段：' + ', '.join(sorted(required - set(request))))
    for field in required & {'source_id', 'job_id', 'version_id', 'base_version'}:
        if not isinstance(request[field], str) or not request[field].strip():
            raise ValueError(field + ' 必须是非空 ID 字符串')
    if action == 'submit' and not isinstance(request['requirements'], dict):
        raise ValueError('requirements 必须是对象')
    if action not in MUTATIONS:
        return _operation(store, action, request)
    rid = request.get('request_id')
    if not isinstance(rid, str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}', rid):
        raise ValueError('写操作需要稳定的 request_id（1–128 个字母、数字或 ._:-）')
    digest = hashlib.sha256(dump(request).encode()).hexdigest()
    with store.tx() as connection:
        connection.execute(SCHEMA)
        previous = connection.execute('SELECT * FROM external_requests WHERE request_id=?', (rid,)).fetchone()
        if previous:
            if previous['request_hash'] != digest:
                raise Conflict('该 request_id 已用于不同内容；请核对原请求')
            return {**json.loads(previous['result']), 'replayed': True}
        result = _operation(_RequestStore(store, connection), action, request)
        connection.execute('INSERT INTO external_requests VALUES(?,?,?,?,?)', (rid, digest, action, dump(result), now()))
        return {**result, 'replayed': False}
