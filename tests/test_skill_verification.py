"""#861: incomplete recurrence coverage never becomes automatic acceptance."""
import json

import pytest

from briefloop import readers, revision_edits, skill_verification
from briefloop.native_roles import run_tool, triage_packet
from briefloop.store import Store, dump, now, uid

BEFORE = '# 周报\n\n## 市场\n\n需求保持稳定，值得持续关注后续变化，市场情绪整体偏谨慎。'
AFTER = '# 周报\n\n## 市场\n\n需求稳定。'


def report(store, source, skill_id, *, requirements=None, revision=True):
    run = store.create_run({'title': 'T', 'objective': 'O', **(requirements or {})}, [source['id']], skill_id=skill_id)
    brief = store.publish(run['id'], {'title': 'T', 'markdown': BEFORE})
    if not revision:
        return run, brief
    store.revise(brief['id'], markdown=AFTER)
    return run, store.rows("SELECT * FROM feedback WHERE kind='revision' ORDER BY rowid DESC LIMIT 1")[0]


def triaged(store, feedback, repeats=(), *, confident=True, provide_repeats=True):
    items = revision_edits.pending(store, [feedback['id']])
    result = {'edits': [{'feedback_id': feedback['id'], 'edit_id': e['edit_id'], 'category': 'taste',
                        'confident': confident, 'reason': '删去空话',
                        **({'repeats': list(repeats)} if provide_repeats else {})} for e in items[0]['edits']]}
    revision_edits.record(store, items, result, job_id='j')
    return items


@pytest.fixture
def workspace(tmp_path):
    store = Store(tmp_path / 'ws')
    source = store.add_source('Sheet', '需求稳定。')
    _, first = report(store, source, None)
    triaged(store, first)
    learned = [e['id'] for e in revision_edits.learnable(store, first['id'])]
    with store.tx() as c:
        c.execute('INSERT INTO skills VALUES(?,?,?,?,?,?)', ('skill_a', None, '# 不写空话', dump(['writing']), 'test', now()))
        skill_verification.register(c, 'skill_a', None, learned, 'job_learn')
    return store, source, learned


def test_two_unassessed_deliveries_remain_unknown(workspace):
    store, source, _ = workspace
    for i in range(2):
        _, brief = report(store, source, 'skill_a', revision=False)
        with store.tx() as c:
            c.execute('INSERT INTO releases VALUES(?,?,?,?,?,?,?,?,?,?,?,?)',
                      (uid('release'), brief['id'], None, f'f{i}', 'released', '{}', None, None, None, '', now(), now()))
    value = skill_verification.refresh(store)['skill_a']
    assert (value['status'], value['observed'], value['unknown'], value['rate']) == ('pending', 0, 2, None)
    assert value['baseline_rate'] is None and value['calibration_required']
    assert not store.rows("SELECT * FROM notifications WHERE event_key='skill-not-improving:skill_a'")


def test_explicit_positive_flags_are_partial_and_one_report_is_counted_once(workspace):
    store, source, learned = workspace
    run, feedback = report(store, source, 'skill_a')
    items = triaged(store, feedback, learned)
    assert [e['id'] for e in items[0]['learned_edits']] == learned
    # A later version and duplicated feedback do not create extra report samples.
    store.revise(feedback['version_id'], markdown=BEFORE)
    second = store.rows("SELECT * FROM feedback WHERE kind='revision' ORDER BY rowid DESC LIMIT 1")[0]
    triaged(store, second, learned)
    with store.tx() as c:
        c.execute('INSERT INTO feedback VALUES(?,?,?,?,?,?)',
                  (uid('feedback'), second['version_id'], 'revision', second['data'], None, now()))
    _, no_repeat = report(store, source, 'skill_a')
    triaged(store, no_repeat)
    value = skill_verification.measure(store, 'skill_a')
    assert (value['status'], value['observed'], value['unknown'], value['rate']) == ('pending', 0, 2, None)
    assert (value['flagged_reports'], value['repeats']) == (1, len(learned))
    assert skill_verification.learned_for_run(store, run['id'])


@pytest.mark.parametrize('case', ['missing_repeats', 'uncertain', 'skipped', 'incomplete'])
def test_unconfirmed_or_incomplete_triage_is_unknown(workspace, case):
    store, source, learned = workspace
    _, feedback = report(store, source, 'skill_a')
    triaged(store, feedback, learned, confident=case not in ('uncertain', 'skipped'),
            provide_repeats=case != 'missing_repeats')
    if case == 'skipped':
        edit = store.rows('SELECT id FROM revision_edits WHERE feedback_id=?', (feedback['id'],))[0]
        revision_edits.answer(store, edit['id'], 'skip')
    if case == 'incomplete':
        with store.tx() as c:
            c.execute('DELETE FROM revision_edits WHERE feedback_id=?', (feedback['id'],))
    value = skill_verification.measure(store, 'skill_a')
    assert (value['status'], value['observed'], value['unknown'], value['repeats'], value['rate']) == ('pending', 0, 1, 0, None)


def test_scope_mismatch_does_not_mix_reader_or_report_series(workspace):
    store, source, learned = workspace
    reader = readers.save(store, name='CEO')
    for requirements in ({'reader_id': reader['id']}, {'objective': 'other series'}):
        run, feedback = report(store, source, 'skill_a', requirements=requirements)
        assert skill_verification.learned_for_run(store, run['id']) == []
        # A stale classifier flag cannot override the frozen reader/series scope.
        items = revision_edits.pending(store, [feedback['id']])
        items[0]['learned_edits'] = [{'id': eid} for eid in learned]
        revision_edits.record(store, items, {'edits': [
            {'feedback_id': feedback['id'], 'edit_id': edit['edit_id'], 'category': 'taste',
             'confident': True, 'reason': 'x', 'repeats': learned} for edit in items[0]['edits']]}, job_id='j')
    value = skill_verification.measure(store, 'skill_a')
    assert (value['excluded'], value['unknown'], value['flagged_reports'], value['repeats']) == (2, 0, 0, 0)


def test_registration_collision_keeps_original_binding_and_excludes_samples(workspace):
    store, source, learned = workspace
    run, feedback = report(store, source, 'skill_a')
    triaged(store, feedback, learned)
    original = store.rows('SELECT * FROM skill_verifications')[0]
    with store.tx() as c:
        skill_verification.register(c, 'skill_a', 'other_reader', learned, 'other_job')
        skill_verification.register(c, 'skill_a', 'other_reader', learned, 'other_job')
    record = store.rows('SELECT * FROM skill_verifications')[0]
    assert all(record[key] == original[key] for key in ('reader_id', 'learned_edits', 'job_id', 'created'))
    assert len(store.rows("SELECT * FROM events WHERE kind='skill_verification_registration_conflict'")) == 1
    value = skill_verification.measure(store, 'skill_a')
    assert value['registration_conflict'] and (value['unknown'], value['repeats'], value['rate']) == (1, 0, None)
    assert skill_verification.learned_for_run(store, run['id']) == []


@pytest.mark.parametrize('old_status', ['adopted', 'not_improving'])
def test_legacy_automatic_status_is_corrected_without_skill_or_notification_changes(workspace, old_status):
    store, _, _ = workspace
    store.set_meta('active_skill', 'skill_a')
    original_skill = store.one('skills', 'skill_a')
    with store.tx() as c:
        c.execute("UPDATE skill_verifications SET status=? WHERE skill_id='skill_a'", (old_status,))
    first = skill_verification.refresh(store)['skill_a']
    second = skill_verification.refresh(store)['skill_a']
    assert first['status'] == second['status'] == 'pending'
    assert first['legacy_status_corrected'] and second['legacy_status_corrected']
    history = store.rows("SELECT data FROM events WHERE kind='skill_verification_status_corrected'")
    assert len(history) == 1 and json.loads(history[0]['data'])['previous_status'] == old_status
    assert store.meta('active_skill') == 'skill_a' and store.one('skills', 'skill_a') == original_skill
    assert not store.rows("SELECT * FROM notifications WHERE event_key='skill-not-improving:skill_a'")


def test_repeats_must_name_learned_edits_of_that_report(workspace, tmp_path):
    store, source, _ = workspace
    _, feedback = report(store, source, 'skill_a')
    items = revision_edits.pending(store, [feedback['id']])
    bad = {'edits': [{'feedback_id': feedback['id'], 'edit_id': e['edit_id'], 'category': 'taste', 'confident': True,
                      'reason': 'x', 'repeats': ['edit_unknown']} for e in items[0]['edits']]}
    assert 'repeats' in revision_edits.triage_errors(bad, items)
    packet = triage_packet(store, items, tmp_path / 'triage')
    result = run_tool(store, {'native_role': 'evaluator', 'evaluation_mode': 'triage', 'packet_root': str(packet)}, 'submit_triage', bad)
    assert result['ok'] is False and 'repeats' in result['error']


def test_skill_from_comments_only_is_untracked(tmp_path):
    store = Store(tmp_path / 'ws')
    with store.tx() as c:
        c.execute('INSERT INTO skills VALUES(?,?,?,?,?,?)', ('skill_c', None, '# x', dump(['writing']), 'test', now()))
        skill_verification.register(c, 'skill_c', None, [], 'job')
    value = skill_verification.measure(store, 'skill_c')
    assert value['status'] == 'untracked' and value['rate'] is None
