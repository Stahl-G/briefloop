"""Durable SDK-decoded receipts, never described as raw network bytes."""
import hashlib
import json

from .config import ConnectorError
from .grants import encoded, digest
from ..store import dump, now


def save(store, operation, result):
    body = encoded(result)
    payload = result.get('payload')
    if payload is not None:
        raw = encoded(payload)
        if result.get('representation') != 'sdk_decoded' or result.get('sha256') != hashlib.sha256(raw).hexdigest():
            raise ConnectorError('SDK 回执校验失败。', code='receipt_integrity')
        if len(raw) > operation['reserved_bytes']:
            raise ConnectorError('解码回执超出本轮预留大小。', code='response_limit')
    # Unknown post-send results keep their worst-case reservation; do not refund
    # capacity that could encourage an automatic retry after a lost response.
    charged = len(encoded(payload)) if payload is not None else operation['reserved_bytes']
    with store.tx() as connection:
        connection.execute('UPDATE connector_operations SET receipt=?,receipt_hash=?,charged_bytes=?,status=?,updated=? WHERE id=? AND receipt IS NULL',
                           (body.decode('utf-8'), hashlib.sha256(body).hexdigest(), charged, 'received', now(), operation['id']))
    return load(store, operation['id'])


def load(store, receipt_id):
    rows = store.rows('SELECT * FROM connector_operations WHERE id=?', (receipt_id,))
    if not rows:
        raise ConnectorError('未找到已保存回执。', code='receipt_missing')
    row = rows[0]
    if row['receipt'] is not None:
        result = json.loads(row['receipt'])
        if digest(result) != row['receipt_hash']:
            raise ConnectorError('已保存回执哈希不匹配。', code='receipt_integrity')
        row['receipt'] = result
    row['request'] = json.loads(row['request'])
    return row


def reject(store, receipt_id, code):
    with store.tx() as connection:
        connection.execute("UPDATE connector_operations SET status='not_admitted',error=?,updated=? WHERE id=? AND source_id IS NULL", (code, now(), receipt_id))


def public(row):
    return {'receipt_id': row['id'], 'status': row['status'], 'source_id': row['source_id'],
            'delivery': (row.get('receipt') or {}).get('delivery', 'unknown'),
            'error': row.get('error'), 'charged_bytes': row['charged_bytes'],
            'material_status': 'unassessed', 'replayed': False}
