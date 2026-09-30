"""#858: an adopted skill is pending until later reports show fewer repeats of its learned edits."""
import json

import pytest

from briefloop import revision_edits, skill_verification
from briefloop.native_roles import run_tool, triage_packet
from briefloop.store import Store, dump, now

BEFORE = '# 周报\n\n## 市场\n\n需求保持稳定，值得持续关注后续变化，市场情绪整体偏谨慎。'
AFTER = '# 周报\n\n## 市场\n\n需求稳定。'


def report(store, source, skill_id):
    run = store.create_run({'title': 'T', 'objective': 'O'}, [source['id']], skill_id=skill_id)
    brief = store.publish(run['id'], {'title': 'T', 'markdown': BEFORE})
    store.revise(brief['id'], markdown=AFTER)
    return run, store.rows("SELECT * FROM feedback WHERE kind='revision' ORDER BY rowid DESC LIMIT 1")[0]


def triaged(store, feedback, repeats):
    items = revision_edits.pending(store, [feedback['id']])
    revision_edits.record(store, items, {'edits': [{'feedback_id': feedback['id'], 'edit_id': e['edit_id'], 'category': 'taste',
                          'confident': True, 'reason': '删去空话', 'repeats': repeats} for e in items[0]['edits']]}, job_id='j')
    return items


@pytest.fixture
def workspace(tmp_path):
    store = Store(tmp_path / 'ws')
    source = store.add_source('Sheet', '需求稳定。')
    _, first = report(store, source, None)
    triaged(store, first, [])
    learned = [e['id'] for e in revision_edits.learnable(store, first['id'])]
    with store.tx() as c:
        c.execute('INSERT INTO skills VALUES(?,?,?,?,?,?)', ('skill_a', None, '# 不写空话', dump(['writing']), 'test', now()))
        skill_verification.register(c, 'skill_a', None, learned, 'job_learn')
    return store, source, learned


def test_pending_until_enough_reports_then_adopted_or_not_improving(workspace):
    store, source, learned = workspace
    assert skill_verification.measure(store, 'skill_a')['status'] == 'pending'
    run, feedback = report(store, source, 'skill_a')
    items = triaged(store, feedback, [])
    # The triage input names what the skill was learned from.
    assert [e['id'] for e in items[0]['learned_edits']] == learned
    value = skill_verification.measure(store, 'skill_a')
    assert (value['status'], value['observed']) == ('pending', 1)
    _, feedback = report(store, source, 'skill_a')
    triaged(store, feedback, learned)
    value = skill_verification.measure(store, 'skill_a')
    assert (value['status'], value['observed'], value['rate']) == ('adopted', 2, 0.5)
    _, feedback = report(store, source, 'skill_a')
    triaged(store, feedback, learned)
    assert skill_verification.refresh(store)['skill_a']['status'] == 'not_improving'
    notes = store.rows("SELECT * FROM notifications WHERE event_key='skill-not-improving:skill_a'")
    assert len(notes) == 1
    skill_verification.refresh(store)
    assert len(store.rows("SELECT * FROM notifications WHERE event_key='skill-not-improving:skill_a'")) == 1


def test_repeats_must_name_learned_edits_of_that_report(workspace, tmp_path):
    store, source, learned = workspace
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
    assert skill_verification.measure(store, 'skill_c')['status'] == 'untracked'
