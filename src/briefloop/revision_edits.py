"""Single edits of a user revision, their kind, and where they go (#858).

A saved revision used to reach WikiSkill as one whole diff, so a corrected
number and a changed tone were learned the same way. Here a revision is split
into block-level edits (deterministic), the independent Evaluator classifies
each edit as taste, fact correction or reader-specific (a model judgment), and
Python routes them with fixed rules:

- taste edits become writing feedback for the workspace's WikiSkill chain;
- reader-specific edits of a report written for a saved reader go to that
  reader's own chain (readers.py); without a saved reader they stay general;
- fact corrections stay in the workspace's correction ledger and never become
  writing skills (a wrong number is a source/check problem, not a style);
- an edit the Evaluator is unsure about and that is large enough to matter is
  held back and asked once in the feedback panel. A skipped question keeps the
  edit out of learning; an answer re-enters it as new feedback.
"""
from difflib import SequenceMatcher
import json
import re

from .store import dump, now, uid

CATEGORIES = ('taste', 'fact_correction', 'reader_specific')
LEARNED = ('taste', 'reader_specific')
CATEGORY_LABELS = {'taste': '口味', 'fact_correction': '事实纠错', 'reader_specific': '读者特定'}
# An unsure edit below this many changed characters is not worth a question;
# the Evaluator's best guess is used instead.
QUESTION_MIN_CHARS = 30
NUMBER = re.compile(r'[-+]?\d+(?:[.,]\d+)*%?')


def _blocks(markdown):
    return [block.strip() for block in re.split(r'\n\s*\n', markdown or '') if block.strip()]


def _changed_chars(before, after):
    matcher = SequenceMatcher(None, before, after, autojunk=False)
    same = sum(size for _, _, size in matcher.get_matching_blocks())
    return max(len(before), len(after)) - same


def _heading_before(blocks, index):
    for block in reversed(blocks[:index]):
        if block.startswith('#'):
            return block.lstrip('#').strip()
    return ''


def segment(before, after):
    """Block-level edits between two markdown texts, in document order."""
    a, b = _blocks(before), _blocks(after)
    edits = []

    def add(op, i, j, old, new):
        heading = _heading_before(a, i) if op != 'insert' else _heading_before(b, j)
        edits.append({'edit_id': f'e{len(edits) + 1}', 'op': op, 'heading': heading, 'before': old, 'after': new,
                      'numbers_changed': NUMBER.findall(old) != NUMBER.findall(new),
                      'changed_chars': _changed_chars(old, new)})

    for tag, i1, i2, j1, j2 in SequenceMatcher(None, a, b, autojunk=False).get_opcodes():
        if tag == 'equal':
            continue
        old, new = a[i1:i2], b[j1:j2]
        paired = min(len(old), len(new)) if tag == 'replace' else 0
        for k in range(paired):
            add('replace', i1 + k, j1 + k, old[k], new[k])
        for k in range(paired, len(old)):
            add('delete', i1 + k, j1, old[k], '')
        for k in range(paired, len(new)):
            add('insert', i2, j1 + k, '', new[k])
    return edits


def triage_errors(result, items):
    """Why a triage result cannot be saved: every edit exactly once, a known
    category, a confidence flag and a reason."""
    if not isinstance(result, dict) or not isinstance(result.get('edits'), list):
        return '结果需要 edits 列表'
    expected = {(item['feedback_id'], edit['edit_id']) for item in items for edit in item['edits']}
    learned = {item['feedback_id']: {e['id'] for e in item.get('learned_edits') or []} for item in items}
    seen = []
    for entry in result['edits']:
        if not isinstance(entry, dict):
            return 'edits 每项需要是对象'
        key = (entry.get('feedback_id'), entry.get('edit_id'))
        if key not in expected:
            return f'未知的改动 {key}'
        if entry.get('category') not in CATEGORIES:
            return f'{key}：category 只能是 {"、".join(CATEGORIES)}'
        if type(entry.get('confident')) is not bool:
            return f'{key}：confident 需要 true 或 false'
        if not isinstance(entry.get('reason'), str) or not entry['reason'].strip():
            return f'{key}：reason 需要写明判断依据'
        allowed = learned.get(key[0], set())
        repeats = entry.get('repeats', [])
        if not isinstance(repeats, list) or not set(repeats) <= allowed:
            return f'{key}：repeats 只能列出该改稿 learned_edits 里的 id（没有重复则 []）'
        seen.append(key)
    if len(seen) != len(set(seen)) or set(seen) != expected:
        missing = sorted(expected - set(seen))
        return f'每处改动须且只能给一条结果；缺少 {missing[:5]}' if missing else '同一改动给了多条结果'
    return None


def pending(store, feedback_ids):
    """Revision feedback in this batch that has not been split and classified yet,
    with its edits and the run context the Evaluator needs."""
    items = []
    for fid in feedback_ids:
        row = store.rows('SELECT * FROM feedback WHERE id=?', (fid,))[0]
        if row['kind'] != 'revision' or store.rows('SELECT id FROM revision_edits WHERE feedback_id=? LIMIT 1', (fid,)):
            continue
        data = json.loads(row['data'])
        after = store.one('briefs', row['version_id'])
        before = store.one('briefs', data['before'])
        edits = segment(before['markdown'], after['markdown'])
        if not edits:
            continue
        run = store.one('runs', after['run_id'])
        requirements = json.loads(run['requirements'])
        from .skill_verification import learned_for_run
        items.append({'feedback_id': fid, 'run_id': run['id'], 'reader_id': (requirements.get('reader_profile') or {}).get('id'),
                      'learned_edits': learned_for_run(store, run['id']),
                      'reader': {key: requirements.get(key, '') for key in ('title', 'objective', 'audience')},
                      'source_ids': store.source_ids(run['id']), 'edits': edits})
    return items


def record(store, items, result, *, job_id):
    """Save the Evaluator's classification and apply the fixed routing rules."""
    error = triage_errors(result, items)
    if error:
        raise ValueError('改动分类结果无效：' + error)
    verdicts = {(e['feedback_id'], e['edit_id']): e for e in result['edits']}
    rows = []
    for item in items:
        for edit in item['edits']:
            verdict = verdicts[(item['feedback_id'], edit['edit_id'])]
            ask = not verdict['confident'] and edit['changed_chars'] >= QUESTION_MIN_CHARS
            data = {**edit, 'model_category': verdict['category'], 'confident': verdict['confident'],
                    'reason': verdict['reason'].strip(), 'run_id': item['run_id'], 'reader_id': item.get('reader_id'),
                    'repeats': sorted(set(verdict.get('repeats') or [])),
                    'triage_job': job_id}
            routed = not ask and verdict['category'] == 'reader_specific' and item.get('reader_id')
            rows.append((uid('edit'), item['feedback_id'], edit['edit_id'], dump(data),
                         None if ask else verdict['category'], None if ask else 'evaluator',
                         'asked' if ask else 'routed' if routed else 'classified', now(), now()))
    with store.tx() as c:
        c.executemany('INSERT OR IGNORE INTO revision_edits VALUES(?,?,?,?,?,?,?,?,?)', rows)
        # Reader-specific edits leave this batch as feedback of their reader's scope.
        by_reader = {}
        for row in rows:
            if row[6] == 'routed':
                by_reader.setdefault((row[1], json.loads(row[3])['reader_id']), []).append(row[0])
        for (fid, reader_id), edit_ids in by_reader.items():
            version = c.execute('SELECT version_id FROM feedback WHERE id=?', (fid,)).fetchone()['version_id']
            c.execute('INSERT INTO feedback VALUES(?,?,?,?,?,?)', (uid('feedback'), version, 'revision_edit',
                      dump({'edit_ids': edit_ids, 'reader_id': reader_id}), None, now()))


def learnable(store, feedback_id):
    """Edits of one revision that may become writing feedback, or None when the
    revision was never split (legacy feedback keeps its whole diff)."""
    rows = store.rows('SELECT * FROM revision_edits WHERE feedback_id=? ORDER BY rowid', (feedback_id,))
    if not rows:
        return None
    return [_view(row) for row in rows if row['status'] in ('classified', 'answered') and row['category'] in LEARNED]


def _view(row):
    data = json.loads(row['data'])
    return {'id': row['id'], 'feedback_id': row['feedback_id'], 'edit_id': row['edit_key'], 'op': data['op'],
            'heading': data['heading'], 'before': data['before'], 'after': data['after'],
            'category': row['category'], 'category_label': CATEGORY_LABELS.get(row['category'], ''),
            'decided_by': row['decided_by'], 'status': row['status'], 'reason': data.get('reason', ''),
            'model_category': data.get('model_category'), 'changed_chars': data.get('changed_chars', 0),
            'run_id': data.get('run_id'), 'reader_id': data.get('reader_id')}


def answer(store, edit_id, category):
    """The user's answer to one question. An answer that routes to learning
    enters the next batch as new feedback; skip keeps it out for good."""
    if category not in (*CATEGORIES, 'skip'):
        raise ValueError('未知的改动类别')
    with store.tx() as c:
        row = c.execute('SELECT * FROM revision_edits WHERE id=?', (edit_id,)).fetchone()
        if row is None:
            raise ValueError('没有这处改动')
        if row['status'] != 'asked':
            raise ValueError('这处改动已经处理过')
        status = 'skipped' if category == 'skip' else 'answered'
        c.execute('UPDATE revision_edits SET category=?,decided_by=?,status=?,updated=? WHERE id=?',
                  (None if category == 'skip' else category, 'user', status, now(), edit_id))
        feedback = None
        if category in LEARNED:
            version = c.execute('SELECT version_id FROM feedback WHERE id=?', (row['feedback_id'],)).fetchone()['version_id']
            reader_id = json.loads(row['data']).get('reader_id') if category == 'reader_specific' else None
            feedback = uid('feedback')
            c.execute('INSERT INTO feedback VALUES(?,?,?,?,?,?)', (feedback, version, 'revision_edit',
                      dump({'edit_ids': [edit_id], **({'reader_id': reader_id} if reader_id else {})}), None, now()))
    return {'id': edit_id, 'status': status, 'category': None if category == 'skip' else category, 'feedback_id': feedback}


def snapshot(store):
    """What the feedback panel shows: open questions and the correction ledger."""
    questions = [_view(row) for row in store.rows("SELECT * FROM revision_edits WHERE status='asked' ORDER BY rowid")]
    corrections = [_view(row) for row in store.rows(
        "SELECT * FROM revision_edits WHERE category='fact_correction' ORDER BY rowid DESC LIMIT 50")]
    return {'questions': questions, 'fact_corrections': corrections}


def edits_by_id(store, edit_ids):
    rows = [store.rows('SELECT * FROM revision_edits WHERE id=?', (eid,)) for eid in edit_ids]
    return [_view(r[0]) for r in rows if r and r[0]['status'] in ('answered', 'routed') and r[0]['category'] in LEARNED]


def learned_ids(store, feedback_ids):
    """The edits a learning batch learned from: what an adopted skill is checked against."""
    ids = []
    for fid in feedback_ids:
        rows = store.rows('SELECT * FROM feedback WHERE id=?', (fid,))
        if not rows:
            continue
        row = rows[0]
        if row['kind'] == 'revision':
            ids += [e['id'] for e in learnable(store, fid) or []]
        elif row['kind'] == 'revision_edit':
            ids += [e['id'] for e in edits_by_id(store, json.loads(row['data'])['edit_ids'])]
    return ids
