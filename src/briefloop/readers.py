"""Reader profiles: who reads a workspace's reports and what they use them for (#858).

A profile keeps only what changes how a report should be written: the
decisions the reader makes with it and their preferences. No personal or
interpersonal notes. A run freezes the profile it was written for, so a later
edit to the profile does not rewrite what an old report was asked to do.

Each reader also has its own learning scope. Reader-specific edits are learned
in that reader's WikiSkill chain and produce that reader's skill; general
edits keep the workspace chain. The two never inherit from each other, so a
preference one reader has does not leak into every other report.
"""
import json

from .store import dump, now, uid

FIELDS = {'name': 80, 'decisions': 1000, 'preferences': 2000}


def _clean(value, limit, label):
    text = str(value or '').strip()
    if len(text) > limit:
        raise ValueError(f'{label}不能超过 {limit} 字')
    return text


def save(store, *, reader_id=None, name, decisions='', preferences=''):
    values = {'name': _clean(name, FIELDS['name'], '读者名称'), 'decisions': _clean(decisions, FIELDS['decisions'], '决策用途'),
              'preferences': _clean(preferences, FIELDS['preferences'], '偏好')}
    if not values['name']:
        raise ValueError('请填写读者名称')
    with store.tx() as c:
        clash = c.execute("SELECT id FROM readers WHERE name=? AND status='active' AND id!=?", (values['name'], reader_id or '')).fetchone()
        if clash:
            raise ValueError('已有同名读者档案')
        if reader_id:
            if not c.execute("SELECT id FROM readers WHERE id=? AND status='active'", (reader_id,)).fetchone():
                raise ValueError('没有这个读者档案')
            c.execute('UPDATE readers SET name=?,decisions=?,preferences=?,updated=? WHERE id=?',
                      (values['name'], values['decisions'], values['preferences'], now(), reader_id))
        else:
            reader_id = uid('reader')
            c.execute('INSERT INTO readers VALUES(?,?,?,?,?,?,?)',
                      (reader_id, values['name'], values['decisions'], values['preferences'], 'active', now(), now()))
    return profile(store, reader_id)


def archive(store, reader_id):
    with store.tx() as c:
        if not c.execute("UPDATE readers SET status='archived',updated=? WHERE id=? AND status='active'", (now(), reader_id)).rowcount:
            raise ValueError('没有这个读者档案')
    return {'id': reader_id, 'status': 'archived'}


def profile(store, reader_id, *, active_only=True):
    rows = store.rows('SELECT * FROM readers WHERE id=?' + (" AND status='active'" if active_only else ''), (reader_id,))
    if not rows:
        raise ValueError('没有这个读者档案')
    row = rows[0]
    return {key: row[key] for key in ('id', 'name', 'decisions', 'preferences', 'status')}


def frozen(store, reader_id):
    """What a run stores: the profile as it was when the report was requested."""
    value = profile(store, reader_id)
    return {key: value[key] for key in ('id', 'name', 'decisions', 'preferences')}


def skill_for(store, reader_id):
    """The skill a new run for this reader starts from: the reader's own, else the workspace's."""
    if reader_id:
        own = (store.meta('reader_skills') or {}).get(reader_id)
        if own:
            return own
    return store.meta('active_skill')


def bind_skill(store, reader_id, skill_id):
    profile(store, reader_id)
    if skill_id:
        store.one('skills', skill_id)
    with store.tx() as c:
        row = c.execute("SELECT value FROM meta WHERE key='reader_skills'").fetchone()
        skills = json.loads(row['value']) if row else {}
        if skill_id:
            skills[reader_id] = skill_id
        else:
            skills.pop(reader_id, None)
        c.execute("INSERT OR REPLACE INTO meta VALUES('reader_skills',?)", (dump(skills),))
    store.event(None, 'reader_skill_binding', {'reader_id': reader_id, 'skill_id': skill_id})


def scope_meta(key, reader_id):
    """Per-scope meta key: the workspace scope keeps the historical key."""
    return key if not reader_id else f'{key}:{reader_id}'


def wiki_path(store, reader_id):
    return store.root / 'wiki' / ('index.md' if not reader_id else f'reader-{reader_id}.md')


def listing(store):
    skills = store.meta('reader_skills') or {}
    rows = store.rows("SELECT * FROM readers WHERE status='active' ORDER BY created")
    out = []
    for row in rows:
        path = wiki_path(store, row['id'])
        out.append({key: row[key] for key in ('id', 'name', 'decisions', 'preferences')} |
                   {'skill_id': skills.get(row['id']), 'wiki': path.read_text(encoding='utf-8') if path.exists() else ''})
    return out
