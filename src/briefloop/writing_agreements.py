"""Explicit writing agreements, independent of paid feedback learning.

Entries are user instructions, never source-backed facts or tool permissions.
Only new runs resolve live entries; existing runs retain their frozen snapshot.
"""
import json
from .store import dump, now, uid

KEY = 'writing_agreements'


def report_version(store, version_id):
    try:return store.one('briefs',version_id)
    except ValueError as exc:raise ValueError('报告版本不存在，请重新选择报告') from exc


def series_root(store, version_id):
    brief=report_version(store,version_id)
    if not brief:raise ValueError('报告版本不存在，请重新选择报告')
    run_id=brief['run_id'];seen=set()
    while run_id not in seen:
        seen.add(run_id)
        run=store.one('runs',run_id)
        if not run:raise ValueError('报告任务不存在，无法读取写作约定')
        req=json.loads(run['requirements'])
        parent=req.get('previous_report_version_id')
        if not parent:return run_id
        brief=report_version(store,parent)
        if not brief:raise ValueError('关联的往期报告不存在，请重新选择报告')
        run_id=brief['run_id']
    raise ValueError('往期报告关联存在循环，无法确定约定范围')


def listing(store, version_id=None):
    root=series_root(store,version_id) if version_id else None
    return [item for item in store.meta(KEY,[]) if item['active'] and
            (item['scope']=='workspace' or (root and item['root_run_id']==root))]


def remember(store, version_id, text, *, scope='series'):
    if scope not in ('series','workspace'):raise ValueError('请选择这份报告及后续期，或本工作区')
    if not isinstance(text,str) or not text.strip():raise ValueError('请填写要沿用的写作要求')
    text=text.strip()
    if len(text)>1000:raise ValueError('请将这条写作要求控制在 1000 字以内')
    root=series_root(store,version_id)
    with store.tx() as c:
        saved=c.execute('SELECT value FROM meta WHERE key=?',(KEY,)).fetchone()
        items=json.loads(saved['value']) if saved else []
        for item in items:
            if item['active'] and item['scope']==scope and (scope=='workspace' or item['root_run_id']==root) and item['text']==text:
                return item
        item={'id':uid('agreement'),'text':text,'scope':scope,'root_run_id':root,
              'origin_version_id':version_id,'active':True,'created':now()}
        items.append(item)
        c.execute('INSERT OR REPLACE INTO meta VALUES(?,?)',(KEY,dump(items)))
    return item


def revoke(store, identity):
    with store.tx() as c:
        row=c.execute('SELECT value FROM meta WHERE key=?',(KEY,)).fetchone()
        items=json.loads(row['value']) if row else []
        item=next((x for x in items if x['id']==identity),None)
        if not item:raise ValueError('写作约定不存在')
        if item['active']:
            item.update(active=False,revoked_at=now())
            c.execute('UPDATE meta SET value=? WHERE key=?',(dump(items),KEY))
    return item


def freeze(store, requirements):
    """Ignore client-supplied snapshots; resolve authoritative active entries."""
    entries=listing(store,requirements.previous_report_version_id or None)
    excluded=set(requirements.writing_agreement_exclusions)
    if not excluded.issubset({x['id'] for x in entries}):
        raise ValueError('跳过的约定已变化，请重新查看本期约定')
    requirements.writing_agreements=[{key:x[key] for key in ('id','text','scope','root_run_id','origin_version_id')}
                                     for x in entries if x['id'] not in excluded]


def preferences(requirements):
    """Deterministic writer/evaluator contract, deduplicated without paraphrase."""
    return list(dict.fromkeys([*requirements.get('writing_preferences',[]),
                              *[x['text'] for x in requirements.get('writing_agreements',[])]]))
