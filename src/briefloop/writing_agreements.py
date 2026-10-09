"""Explicit writing agreements, independent of paid feedback learning.

Entries are user instructions, never source-backed facts or tool permissions.
Only new runs resolve live entries; existing runs retain their frozen snapshot.
"""
import json
from .store import dump, now, uid

KEY = 'writing_agreements'


def series_root(store, version_id):
    run_id=store.one('briefs',version_id)['run_id'];seen=set()
    while run_id not in seen:
        seen.add(run_id)
        req=json.loads(store.one('runs',run_id)['requirements'])
        parent=req.get('previous_report_version_id')
        if not parent:return run_id
        run_id=store.one('briefs',parent)['run_id']
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


def from_chat(store, request, *, session_id):
    """Keep the actual user quote; sources/worker prompts cannot self-adopt rules."""
    if not session_id:raise ValueError('请从报告页保存约定，或在当前对话明确提出要求')
    if store.rows("SELECT seq FROM chat_events WHERE session_id=? AND kind='session/internal' LIMIT 1",(session_id,)):
        raise ValueError('后台报告角色不能将材料或自己的建议保存为用户约定')
    rows=store.rows("SELECT text FROM chat_messages WHERE session_id=? AND role='user' ORDER BY rowid DESC LIMIT 1",(session_id,))
    quote=request.get('user_quote')
    if not isinstance(quote,str) or not quote.strip() or not rows or quote not in rows[0]['text']:
        raise ValueError('需要本轮用户逐字提出的要求；模型推断或原材料不能作为采用依据')
    if request['action']=='forget_writing':return revoke(store,request['id'])
    return remember(store,request['version_id'],quote,scope=request.get('scope','series'))
