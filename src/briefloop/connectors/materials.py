"""Run-scoped material acquisition through the existing source and review chain.

Only the trusted host may freeze/revoke grants. Agents receive a bound run/grant,
not permission to choose either identity or to raise these explicit budgets.
"""
from contextlib import ExitStack
import hashlib
import json
import os

from .config import ConnectorError
from .grants import Grants, encoded, digest
from . import receipts
from ..media import safe_source_path
from ..store import dump, now, content_hash


def _write_once(path, body):
    """Return whether this operation created the immutable, fsynced file."""
    try:
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        if path.read_bytes() != body:
            raise ConnectorError('已保存材料文件发生变化。', code='receipt_integrity')
        return False
    try:
        with os.fdopen(descriptor, 'wb') as stream:
            stream.write(body)
            stream.flush()
            os.fsync(stream.fileno())
    except BaseException:
        path.unlink(missing_ok=True)
        raise
    return True


def _text(result, method, target):
    payload = result.get('payload') or {}
    pieces = []
    blocks = payload.get('contents', []) if method == 'read' else payload.get('content', [])
    for block in blocks:
        if method == 'read':
            if block.get('uri') != target or not isinstance(block.get('text'), str):
                raise ConnectorError('资源不是当前已授权 URI 的文本快照，需另行提取。', code='extraction_required')
            pieces.append(block['text'])
        elif block.get('type') == 'text' and isinstance(block.get('text'), str):
            pieces.append(block['text'])
        elif block.get('type') == 'resource' and isinstance(block.get('resource', {}).get('text'), str):
            pieces.append(block['resource']['text'])
        else:
            raise ConnectorError('回执含未接通的非文本材料；已保留原回执。', code='extraction_required')
    if method == 'call' and payload.get('structuredContent') is not None:
        pieces.append(encoded(payload['structuredContent']).decode('utf-8'))
    text = '\n\n'.join(pieces)
    if not text.strip():
        raise ConnectorError('回执没有可接纳的文本材料。', code='empty_material')
    return text


class ConnectorMaterials:
    def __init__(self, store, service):
        self.store, self.service = store, service
        self.grants = Grants(store)

    @staticmethod
    def scope(grant_id):
        return 'material:' + grant_id

    def freeze(self, run_id, selections, *, max_calls, max_total_bytes, grant_id=None):
        if not isinstance(selections, list) or not 1 <= len(selections) <= 32:
            raise ConnectorError('请明确选择本轮需要的连接器资源或工具。', code='invalid_grant')
        snapshots, identities = [], set()
        for item in selections:
            if not isinstance(item, dict) or set(item) - {'connector_id', 'resources', 'tools'}:
                raise ConnectorError('本轮选择格式无效。', code='invalid_grant')
            identity = item.get('connector_id')
            if not isinstance(identity, str) or identity in identities:
                raise ConnectorError('连接器 ID 缺失或重复。', code='invalid_grant')
            identities.add(identity)
            status = self.service.status(identity)
            if not status['enabled'] or status['state'] != 'connected':
                raise ConnectorError('请先连接所选连接器，再冻结本轮授权。', code='disabled')
            selection = {}
            catalog = {}
            for field, key in (('resources', 'uri'), ('tools', 'name')):
                values = item.get(field, [])
                known = {x[key]: x for x in status['capabilities'][field]}
                if (not isinstance(values, list) or len(values) > 200
                        or any(not isinstance(value, str) or value not in known for value in values)):
                    raise ConnectorError('只能授权实际目录中明确选择的资源或工具。', code='invalid_grant')
                selection[field] = sorted(set(values))
                catalog[field] = [known[value] for value in selection[field]]
            if not selection['resources'] and not selection['tools']:
                raise ConnectorError('每个连接器需明确选择资源或工具。', code='invalid_grant')
            config = {key: status[key] for key in ('name', 'transport', 'url', 'command', 'args', 'cwd',
                      'timeout_seconds', 'max_response_bytes') if key in status}
            snapshots.append({'id': identity, 'revision': status['revision'], 'name': status['name'],
                              'transport': status['transport'], 'protocol': status['protocol'],
                              'endpoint': status.get('url') or status.get('command'), 'configuration_hash': digest(config),
                              'max_response_bytes': status['max_response_bytes'],
                              'catalog': catalog, **selection})
        # No await/network under these guards. A config update cannot slip between
        # capture and freeze; sorted lock ordering also supports multiple services.
        with ExitStack() as stack:
            for item in sorted(snapshots, key=lambda row: row['id']):
                stack.enter_context(self.service.material_admission(item['id'], scope_id='freeze',
                                    expected_revision=item['revision'], connection_epoch=None))
            result = self.grants.freeze(run_id, snapshots, max_calls=max_calls,
                                       max_total_bytes=max_total_bytes, grant_id=grant_id)
        self.store.event(None, 'connector_grant_frozen', {'grant_id': result['id'], 'run_id': run_id})
        return result

    def revoke(self, grant_id, run_id):
        result = self.grants.revoke(grant_id, run_id)
        # DB commit precedes service lock acquisition; admission uses the reverse
        # operations only while the revoke no longer owns a DB lock.
        self.service.revoke_scope(self.scope(grant_id))
        self.store.event(None, 'connector_grant_revoked', {'grant_id': grant_id, 'run_id': run_id})
        return result

    def status(self, grant_id, run_id):
        return {'grant': self.grants.get(grant_id, run_id), 'usage': self.grants.usage(grant_id, run_id)}

    def receipt(self, grant_id, run_id, receipt_id):
        self.grants.get(grant_id, run_id)
        row = receipts.load(self.store, receipt_id)
        if row['grant_id'] != grant_id:
            raise ConnectorError('回执不属于本轮授权。', code='invalid_grant')
        return receipts.public(row)

    def read(self, grant_id, run_id, connector_id, uri, *, request_id):
        return self._acquire(grant_id, run_id, {'connector_id': connector_id, 'method': 'read', 'target': uri}, request_id)

    def call(self, grant_id, run_id, connector_id, name, arguments, *, request_id):
        if not isinstance(arguments, dict):
            raise ConnectorError('工具参数必须为对象。', code='invalid_request')
        return self._acquire(grant_id, run_id, {'connector_id': connector_id, 'method': 'call', 'target': name, 'arguments': arguments}, request_id)

    def _acquire(self, grant_id, run_id, request, request_id):
        operation, fresh = self.grants.reserve(grant_id, run_id, request, request_id)
        if not fresh:
            # Even an orphaned in_flight reservation is never sent again. Only an
            # explicit new host request may consume another bounded attempt.
            return receipts.public(receipts.load(self.store, operation['id']))
        grant = self.grants.get(grant_id, run_id)
        selected = next(x for x in grant['data']['connectors'] if x['id'] == request['connector_id'])
        scope = self.scope(grant_id)
        try:
            self.grants.get(grant_id, run_id, active=True)
            if request['method'] == 'read':
                result = self.service.read(request['connector_id'], request['target'], scope_id=scope, expected_revision=selected['revision'])
            else:
                result = self.service.call(request['connector_id'], request['target'], request['arguments'], scope_id=scope, expected_revision=selected['revision'])
        except ConnectorError as exc:
            result = {'delivery': 'unknown', 'error': {'code': exc.code, 'message': str(exc)}}
        except Exception:
            # Preserve ambiguity rather than blindly replaying a transport failure.
            result = {'delivery': 'unknown', 'error': {'code': 'call_interrupted', 'message': '调用中断；不会自动重发。'}}
        try:
            receipts.save(self.store, operation, result)
            if result.get('delivery') != 'received' or result.get('error') or result.get('is_error') or result.get('payload', {}).get('isError'):
                raise ConnectorError('本次调用未返回可接纳的成功材料。', code='upstream_result')
            text = _text(result, request['method'], request['target'])
            self._admit(grant, selected, operation, request, result, text)
        except (ConnectorError, ValueError, OSError) as exc:
            receipts.reject(self.store, operation['id'], getattr(exc, 'code', 'admission_failed'))
        row = receipts.load(self.store, operation['id'])
        self.store.event(None, 'connector_material_result', {'run_id': run_id, **receipts.public(row)})
        return receipts.public(row)

    def _admit(self, grant, selected, operation, request, result, text):
        sid = 'src_' + operation['id'].split('_', 1)[1]
        # This self-contained envelope is the source original. The SDK-decoded
        # payload stays unchanged, with its own hash separate from the envelope.
        origin = {'receipt_id': operation['id'], 'grant_id': grant['id'],
                  'grant_snapshot_hash': grant['data']['snapshot_hash'], 'run_id': grant['run_id'],
                  'connector_id': selected['id'], 'connector_revision': selected['revision'],
                  'connector_name': selected['name'], 'protocol': selected['protocol'],
                  'endpoint': selected['endpoint'], 'configuration_hash': selected['configuration_hash'],
                  'fetched_at': operation['created'], 'request_hash': operation['request_hash'],
                  'method': request['method'], 'target': request['target']}
        raw = encoded({'representation': 'sdk_decoded', 'origin': origin,
                       'payload_sha256': result['sha256'], 'payload': result['payload']})
        original = safe_source_path(self.store, f'sources/{sid}.original.json', must_exist=False)
        metadata_path = safe_source_path(self.store, f'sources/{sid}.provenance.json', must_exist=False)
        text_path = safe_source_path(self.store, f'sources/{sid}.txt', must_exist=False)
        metadata = {'original_kind': 'mcp_receipt', 'representation': 'sdk_decoded',
                    'original_path': str(original.relative_to(self.store.root)),
                    'raw_sha256': hashlib.sha256(raw).hexdigest(), 'text_sha256': content_hash(text),
                    'media_type': 'application/json', 'needs_visual': False, 'pages': None,
                    'extractor': 'mcp text and structured content', 'extraction_status': 'ready',
                    'material_status': 'unassessed', 'payload_sha256': result['sha256'], **origin}
        created = []
        try:
            with self.service.material_admission(selected['id'], scope_id=self.scope(grant['id']),
                    expected_revision=selected['revision'], connection_epoch=result.get('connection_epoch')):
                with self.store.tx() as connection:
                    self.grants.get(grant['id'], grant['run_id'], connection=connection, active=True)
                    for path, body in ((original, raw), (metadata_path, encoded(metadata))):
                        if _write_once(path, body): created.append(path)
                    if not text_path.exists(): created.append(text_path)
                    source = self.store.add_source(f"{selected['name']} · {request['target']}"[:500], text,
                                source_id=sid, connection=connection)
                    if source['hash'] != content_hash(text):
                        raise ConnectorError('来源快照 ID 冲突。', code='source_conflict')
                    connection.execute('INSERT OR IGNORE INTO run_sources VALUES(?,?)', (grant['run_id'], sid))
                    connection.execute("UPDATE connector_operations SET status='admitted',source_id=?,updated=? WHERE id=?", (sid, now(), operation['id']))
        except BaseException:
            # Never delete reused snapshots. A crash can leave unreferenced files;
            # they have no source row and remain recoverable forensic artifacts.
            if not self.store.rows('SELECT id FROM sources WHERE id=?', (sid,)):
                for path in created: path.unlink(missing_ok=True)
            raise
