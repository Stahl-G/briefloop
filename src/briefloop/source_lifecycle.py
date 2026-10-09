"""Reversible library organization, independent of evidence and extraction state."""
from .store import dump, now

PREFIX = 'source_archive:'


def annotate(store, sources):
    archived = {r['key'][len(PREFIX):]: r['value'] for r in store.rows(
        "SELECT key,value FROM meta WHERE key LIKE 'source_archive:%'")}
    import json
    return [{**s, 'archived_at': json.loads(archived[s['id']]) if s['id'] in archived else None} for s in sources]


def change(store, source_ids, archived):
    if type(archived) is not bool or not isinstance(source_ids, list) or not 1 <= len(source_ids) <= 200:
        raise ValueError('请选择 1–200 个来源，并明确归档或恢复')
    if any(not isinstance(sid, str) or not sid for sid in source_ids):
        raise ValueError('来源 ID 无效')
    ids = list(dict.fromkeys(source_ids))
    with store.tx() as c:
        found = {r['id'] for r in c.execute('SELECT id FROM sources WHERE id IN (' + ','.join('?' for _ in ids) + ')', ids)}
        if found != set(ids):
            raise ValueError('部分来源不存在，请刷新后重试')
        for sid in ids:
            if archived:
                c.execute('INSERT OR IGNORE INTO meta(key,value) VALUES(?,?)', (PREFIX + sid, dump(now())))
            else:
                c.execute('DELETE FROM meta WHERE key=?', (PREFIX + sid,))
    return {'source_ids': ids, 'archived': archived}


def report_source_ids(store, run_id):
    run = store.one('runs', run_id)
    if run.get('mode', 'normal') != 'normal' or run_id in store.deleted_reports():
        raise ValueError('报告不可用')
    return store.source_ids(run_id)
