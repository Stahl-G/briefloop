"""Does an adopted skill actually stop the same edit from coming back? (#858)

Passing the offline pairwise comparison only makes a skill 'pending'. Every
later report written with it is an observation once the user has saved a
revision for it (classified in the next learning batch) or formally
delivered it. The triage Evaluator marks which new edits repeat an edit the
skill was learned from; Python computes the recurrence rate with a fixed rule:

- fewer than MIN_OBSERVED reports observed: 'pending' (shown as not yet verified);
- rate <= MAX_RATE: 'adopted';
- otherwise 'not_improving': the user is told once and can roll back.

A skill learned only from comments has no edits to track: 'untracked'.
The rate is repeats / (learned edits x observed reports); before the skill,
each learned edit had to be made once in its report, i.e. a rate of 1.
"""
import json

from .store import dump, now

MIN_OBSERVED = 2
MAX_RATE = 0.5
LABELS = {'pending': '待验证', 'adopted': '已采用', 'not_improving': '未见改善', 'untracked': '无可追踪改动'}


def register(connection, skill_id, reader_id, learned_edit_ids, job_id):
    """Called inside the adoption transaction; a re-adopted skill keeps its first record."""
    connection.execute('INSERT OR IGNORE INTO skill_verifications VALUES(?,?,?,?,?,?,?)',
                       (skill_id, reader_id, 'pending' if learned_edit_ids else 'untracked',
                        dump(sorted(set(learned_edit_ids))), job_id, now(), now()))


def _record(store, skill_id):
    rows = store.rows('SELECT * FROM skill_verifications WHERE skill_id=?', (skill_id,))
    return rows[0] if rows else None


def learned_for_run(store, run_id):
    """The edits the skill of this report was learned from, for the triage input."""
    run = store.one('runs', run_id)
    record = _record(store, run['skill_id']) if run['skill_id'] else None
    if record is None:
        return []
    ids = json.loads(record['learned_edits'])
    from .revision_edits import _view
    out = []
    for eid in ids:
        rows = store.rows('SELECT * FROM revision_edits WHERE id=?', (eid,))
        if rows:
            view = _view(rows[0])
            out.append({key: view[key] for key in ('id', 'heading', 'op', 'before', 'after', 'category')})
    return out


def _released_runs(store):
    if not store.rows("SELECT name FROM sqlite_master WHERE type='table' AND name='releases'"):
        return set()
    return {row['run_id'] for row in store.rows(
        "SELECT DISTINCT b.run_id FROM releases r JOIN briefs b ON b.id=r.version_id WHERE r.status='released'")}


def measure(store, skill_id):
    record = _record(store, skill_id)
    if record is None:
        return None
    learned = set(json.loads(record['learned_edits']))
    runs = [row['id'] for row in store.rows(
        "SELECT id FROM runs WHERE skill_id=? AND mode='normal' AND created>=? ORDER BY created", (skill_id, record['created']))]
    released = _released_runs(store)
    observed, repeats = 0, 0
    for run_id in runs:
        edits = store.rows('SELECT e.data FROM revision_edits e JOIN feedback f ON f.id=e.feedback_id '
                           'JOIN briefs b ON b.id=f.version_id WHERE b.run_id=?', (run_id,))
        if not edits and run_id not in released:
            continue
        observed += 1
        seen = set()
        for row in edits:
            seen.update(set(json.loads(row['data']).get('repeats') or []) & learned)
        repeats += len(seen)
    rate = repeats / (len(learned) * observed) if learned and observed else None
    if not learned:
        status = 'untracked'
    elif observed < MIN_OBSERVED:
        status = 'pending'
    else:
        status = 'adopted' if rate <= MAX_RATE else 'not_improving'
    return {'skill_id': skill_id, 'reader_id': record['reader_id'], 'status': status, 'label': LABELS[status],
            'learned': len(learned), 'reports': len(runs), 'observed': observed, 'repeats': repeats,
            'rate': rate, 'min_observed': MIN_OBSERVED, 'max_rate': MAX_RATE}


def refresh(store):
    """Re-measure every tracked skill; persist status changes and tell the user
    once when a skill does not reduce the edits it was learned from."""
    results = {}
    for row in store.rows('SELECT skill_id,status FROM skill_verifications'):
        value = measure(store, row['skill_id'])
        results[row['skill_id']] = value
        if value['status'] != row['status']:
            with store.tx() as c:
                c.execute('UPDATE skill_verifications SET status=?,updated=? WHERE skill_id=?', (value['status'], now(), row['skill_id']))
            if value['status'] == 'not_improving':
                from .notifications import post
                post(store, f"skill-not-improving:{row['skill_id']}", 'learning', '技能没有减少同类改动',
                     body=f"{row['skill_id']} 在 {value['observed']} 份后续报告里复发率为 {value['rate']:.0%}，可在经验页回退到之前的技能。",
                     severity='info')
    return results
