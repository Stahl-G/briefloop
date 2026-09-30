"""Preliminary recurrence observations, never evidence of skill acceptance.

Edit triage can flag a positive recurrence. It does not inspect every learned
edit across a whole report, so an empty repeats list, a delivery, or a saved
revision cannot establish absence. Full independent report observations and
calibrated acceptance (two model families, human disagreement review, held-out
validation and a measured baseline) are not implemented here. Candidates stay
pending; existing automatic verdicts are corrected without changing skills.
"""
import json

from .store import dump, now

LABELS = {'pending': '待验收', 'untracked': '无可追踪改动'}
CALIBRATION_NOTE = '仅初步观察；尚未完成完整复发核验、双模型 family 校准、人工分歧处理与留出验收。'
LEGACY_NOTE = '旧算法的自动状态不证明验收；已恢复待验收，原记录与技能保留。'


def _json(value, fallback):
    try:
        return json.loads(value)
    except (TypeError, ValueError):
        return fallback


def register(connection, skill_id, reader_id, learned_edit_ids, job_id):
    """Do not attach another scope/batch to a content-addressed skill record."""
    learned = dump(sorted(set(learned_edit_ids)))
    existing = connection.execute('SELECT * FROM skill_verifications WHERE skill_id=?', (skill_id,)).fetchone()
    if existing is None:
        connection.execute('INSERT INTO skill_verifications VALUES(?,?,?,?,?,?,?)',
                           (skill_id, reader_id, 'pending' if learned_edit_ids else 'untracked',
                            learned, job_id, now(), now()))
        return
    if (existing['reader_id'], existing['learned_edits'], existing['job_id']) == (reader_id, learned, job_id):
        return
    # Preserve both the original binding and conflicting raw inputs. Until the
    # registry can distinguish scope + batch, no samples can safely be pooled.
    data = {'skill_id': skill_id, 'reader_id': reader_id, 'learned_edits': _json(learned, []),
            'original_reader_id': existing['reader_id'], 'original_job_id': existing['job_id'],
            'original_status': existing['status'], 'reason': '同一技能内容对应不同读者或学习批次，观察绑定不明确。'}
    encoded = dump(data)
    if not connection.execute("SELECT seq FROM events WHERE kind='skill_verification_registration_conflict' AND job_id IS ? AND data=?",
                              (job_id, encoded)).fetchone():
        connection.execute('INSERT INTO events(job_id,kind,data,created) VALUES(?,?,?,?)',
                           (job_id, 'skill_verification_registration_conflict', encoded, now()))
    connection.execute('UPDATE skill_verifications SET status=?,updated=? WHERE skill_id=?', ('pending', now(), skill_id))


def _record(store, skill_id):
    rows = store.rows('SELECT * FROM skill_verifications WHERE skill_id=?', (skill_id,))
    return rows[0] if rows else None


def _events(store, skill_id, kind):
    return [data for row in store.rows('SELECT data FROM events WHERE kind=?', (kind,))
            if isinstance(data := _json(row['data'], None), dict) and data.get('skill_id') == skill_id]


def _scope(requirements):
    """Exact frozen identity; no model inference that two report series match."""
    profile = requirements.get('reader_profile') or {}
    if not isinstance(profile, dict):
        return None
    reader = profile.get('id') or requirements.get('reader_id')
    if requirements.get('reader_id') and profile.get('id') and requirements['reader_id'] != profile['id']:
        return None
    # There is no explicit series registry today. Exact task/workflow/template
    # fields conservatively avoid pooling unrelated reports for the same reader.
    keys = ('series_id', 'report_profile', 'workflow_id', 'workflow_variant', 'writing_mode',
            'organization', 'industry', 'template_id', 'language', 'audience', 'title', 'objective')
    return dump({'reader_id': reader, **{key: requirements.get(key) for key in keys}})


def _learned_context(store, record):
    ids = _json(record['learned_edits'], None)
    if not isinstance(ids, list) or any(not isinstance(eid, str) or not eid for eid in ids) or len(ids) != len(set(ids)):
        return set(), [], None, 'invalid_learned_edits'
    views, scopes = [], set()
    from .revision_edits import _view
    for eid in ids:
        rows = store.rows('SELECT e.*,b.run_id AS bound_run_id FROM revision_edits e '
                          'JOIN feedback f ON f.id=e.feedback_id JOIN briefs b ON b.id=f.version_id WHERE e.id=?', (eid,))
        if not rows:
            return set(ids), [], None, 'missing_learned_edit'
        row = rows[0]
        data = _json(row['data'], {})
        runs = store.rows('SELECT requirements FROM runs WHERE id=?', (row['bound_run_id'],))
        requirements = _json(runs[0]['requirements'], None) if runs else None
        if not isinstance(data, dict) or data.get('run_id') != row['bound_run_id'] or not isinstance(requirements, dict):
            return set(ids), [], None, 'invalid_learned_binding'
        scope = _scope(requirements)
        profile = requirements.get('reader_profile') or {}
        reader = profile.get('id') or requirements.get('reader_id') if isinstance(profile, dict) else None
        if scope is None or record['reader_id'] and reader != record['reader_id']:
            return set(ids), [], None, 'learned_reader_mismatch'
        if row['status'] not in ('classified', 'answered', 'routed') or row['category'] not in ('taste', 'reader_specific'):
            return set(ids), [], None, 'unconfirmed_learned_edit'
        if any(not isinstance(data.get(key), str) for key in ('op', 'heading', 'before', 'after')):
            return set(ids), [], None, 'invalid_learned_edit'
        views.append(_view(row))
        scopes.add(scope)
    if len(scopes) != 1:
        return set(ids), [], None, 'ambiguous_learned_scope' if ids else None
    return set(ids), views, next(iter(scopes)), None


def learned_for_run(store, run_id):
    """Only expose edits from the unambiguous scope that this report uses."""
    run = store.one('runs', run_id)
    record = _record(store, run['skill_id']) if run['skill_id'] else None
    if record is None or _events(store, run['skill_id'], 'skill_verification_registration_conflict'):
        return []
    _, views, scope, error = _learned_context(store, record)
    requirements = _json(run['requirements'], {})
    if error or not isinstance(requirements, dict) or scope != _scope(requirements):
        return []
    return [{key: view[key] for key in ('id', 'heading', 'op', 'before', 'after', 'category')} for view in views]


def _positive_flags(store, feedback, learned):
    """Count explicit positive flags only from complete, certain edit triage.

    A complete edit segmentation is still partial report coverage. It can never
    supply a denominator or establish that unflagged learned edits were absent.
    """
    from .revision_edits import CATEGORIES, segment
    data = _json(feedback['data'], {})
    if not isinstance(data, dict) or not isinstance(data.get('before'), str) or data.get('after') != feedback['version_id']:
        return set(), 'invalid_revision_binding'
    versions = store.rows('SELECT * FROM briefs WHERE id IN (?,?)', (data.get('before'), feedback['version_id']))
    versions = {row['id']: row for row in versions}
    before, after = versions.get(data.get('before')), versions.get(feedback['version_id'])
    if not before or not after or before['run_id'] != after['run_id'] or after['parent_id'] != before['id']:
        return set(), 'invalid_revision_binding'
    requirements = _json(store.one('runs', after['run_id'])['requirements'], {})
    profile = requirements.get('reader_profile') or {}
    reader = profile.get('id') or requirements.get('reader_id') if isinstance(profile, dict) else None
    expected = {edit['edit_id']: edit for edit in segment(before['markdown'], after['markdown'])}
    rows = store.rows('SELECT * FROM revision_edits WHERE feedback_id=?', (feedback['id'],))
    if not expected or len(rows) != len(expected) or {row['edit_key'] for row in rows} != set(expected):
        return set(), 'missing_triage'
    seen = set()
    for row in rows:
        decision = _json(row['data'], {})
        if row['status'] not in ('classified', 'routed') or row['category'] not in CATEGORIES:
            return set(), 'unconfirmed_triage'
        if not isinstance(decision, dict) or decision.get('confident') is not True:
            return set(), 'uncertain_triage'
        if (decision.get('reader_id') != reader or decision.get('model_category') != row['category']
                or decision.get('run_id') != after['run_id'] or any(decision.get(key) != expected[row['edit_key']][key]
                                                                for key in ('op', 'before', 'after', 'heading'))):
            return set(), 'invalid_triage_binding'
        repeats = decision.get('repeats')
        if decision.get('repeats_provided') is not True or not isinstance(repeats, list):
            return set(), 'missing_repeats_assessment'
        if any(not isinstance(eid, str) or not eid for eid in repeats) or not set(repeats) <= learned:
            return set(), 'invalid_repeats_assessment'
        seen.update(repeats)
    return seen, None


def measure(store, skill_id):
    record = _record(store, skill_id)
    if record is None:
        return None
    learned, _, scope, context_error = _learned_context(store, record)
    conflicts = _events(store, skill_id, 'skill_verification_registration_conflict')
    collision = bool(conflicts)
    runs = store.rows("SELECT * FROM runs WHERE skill_id=? AND mode='normal' AND created>=? "
                      "AND EXISTS(SELECT 1 FROM briefs WHERE briefs.run_id=runs.id) ORDER BY created,id",
                      (skill_id, record['created']))
    reasons, flagged_reports, repeats, excluded = {}, 0, 0, 0
    for run in runs:
        requirements = _json(run['requirements'], None)
        reason = 'registration_conflict' if collision else context_error
        if not reason and (not isinstance(requirements, dict) or scope != _scope(requirements)):
            excluded += 1
            reasons['scope_mismatch'] = reasons.get('scope_mismatch', 0) + 1
            continue
        seen = set()
        if not reason:
            # One run is one report; duplicate feedback and revision versions do
            # not create additional recurrence samples for a learned edit.
            feedbacks = store.rows("SELECT f.* FROM feedback f JOIN briefs b ON b.id=f.version_id "
                                   "WHERE b.run_id=? AND f.kind='revision' ORDER BY f.created,f.id", (run['id'],))
            assessed_versions = set()
            for feedback in feedbacks:
                if feedback['version_id'] in assessed_versions:
                    continue
                assessed_versions.add(feedback['version_id'])
                flags, error = _positive_flags(store, feedback, learned)
                seen.update(flags)
                if error:
                    reasons[error] = reasons.get(error, 0) + 1
            # Deliveries and edits carry no complete independent observation.
            reason = 'missing_full_report_observation'
        reasons[reason] = reasons.get(reason, 0) + 1
        flagged_reports += bool(seen)
        repeats += len(seen)
    status = 'pending' if learned or collision or context_error else 'untracked'
    legacy = (record['status'] in ('adopted', 'not_improving')
              or any(event.get('original_status') in ('adopted', 'not_improving') for event in conflicts)
              or any(event.get('previous_status') in ('adopted', 'not_improving')
                     for event in _events(store, skill_id, 'skill_verification_status_corrected')))
    return {'skill_id': skill_id, 'reader_id': record['reader_id'], 'status': status, 'label': LABELS[status],
            'learned': len(learned), 'reports': len(runs), 'observed': 0, 'unknown': len(runs) - excluded,
            'excluded': excluded, 'repeats': repeats, 'flagged_reports': flagged_reports,
            'rate': None, 'baseline_rate': None, 'evidence_status': 'preliminary', 'calibration_required': True,
            'unknown_reasons': reasons, 'registration_conflict': collision,
            'legacy_status_corrected': legacy, 'note': CALIBRATION_NOTE, 'legacy_note': LEGACY_NOTE if legacy else None}


def refresh(store):
    """Correct old automatic verdicts, preserving the raw record in an event.

    Do not change skill contents/bindings or issue lack-of-improvement notices.
    """
    results = {}
    for row in store.rows('SELECT skill_id,status FROM skill_verifications'):
        value = measure(store, row['skill_id'])
        results[row['skill_id']] = value
        if value['status'] != row['status']:
            with store.tx() as connection:
                connection.execute('UPDATE skill_verifications SET status=?,updated=? WHERE skill_id=?',
                                   (value['status'], now(), row['skill_id']))
                connection.execute('INSERT INTO events(job_id,kind,data,created) VALUES(?,?,?,?)',
                                   (None, 'skill_verification_status_corrected',
                                    dump({'skill_id': row['skill_id'], 'previous_status': row['status'],
                                          'status': value['status'], 'reason': LEGACY_NOTE}), now()))
    return results
