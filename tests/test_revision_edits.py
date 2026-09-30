"""#858: a revision is split into edits; only taste/reader-specific edits become writing feedback."""
import json
from pathlib import Path

import pytest

from briefloop import revision_edits
from briefloop.learning import _experience, learn, triage
from briefloop.native_roles import run_tool, triage_packet
from briefloop.store import Store

BEFORE = '# 周报\n\n## 市场\n\n本周组件均价为每瓦0.31美元。\n\n需求保持稳定，值得关注。\n\n## 政策\n\n商务部发起调查。'
AFTER = '# 周报\n\n## 市场\n\n本周组件均价为每瓦0.27美元。\n\n需求稳定。\n\n## 政策\n\n商务部发起调查。\n\n对出口的影响取决于初裁结果：若被认定规避，电池对美出口可能面临反倾销和反补贴税，需要提前准备溯源材料。'


def revised(tmp_path):
    store = Store(tmp_path / 'ws')
    source = store.add_source('Price sheet', '组件均价每瓦0.27美元。')
    run = store.create_run({'title': '周报', 'objective': '跟踪市场', 'audience': '管理层'}, [source['id']])
    brief = store.publish(run['id'], {'title': '周报', 'markdown': BEFORE})
    store.revise(brief['id'], markdown=AFTER)
    feedback = store.rows("SELECT * FROM feedback WHERE kind='revision'")[0]
    return store, feedback


def verdicts(items, overrides):
    return {'edits': [{'feedback_id': item['feedback_id'], 'edit_id': edit['edit_id'],
                       **overrides.get(edit['edit_id'], {'category': 'taste', 'confident': True}), 'reason': '对照改前改后'}
                      for item in items for edit in item['edits']]}


def test_segment_pairs_changed_blocks_with_their_section():
    edits = revision_edits.segment(BEFORE, AFTER)
    assert [(e['op'], e['heading']) for e in edits] == [('replace', '市场'), ('replace', '市场'), ('insert', '政策')]
    assert edits[0]['numbers_changed'] and not edits[1]['numbers_changed']


def test_fact_corrections_and_open_questions_stay_out_of_writing_feedback(tmp_path):
    store, feedback = revised(tmp_path)
    items = revision_edits.pending(store, [feedback['id']])
    assert items[0]['reader']['audience'] == '管理层' and items[0]['source_ids']
    result = verdicts(items, {'e1': {'category': 'fact_correction', 'confident': True},
                              'e3': {'category': 'reader_specific', 'confident': False}})
    revision_edits.record(store, items, result, job_id='job_x')
    learned = revision_edits.learnable(store, feedback['id'])
    assert [e['edit_id'] for e in learned] == ['e2']
    snap = revision_edits.snapshot(store)
    assert [e['edit_id'] for e in snap['questions']] == ['e3']
    assert [e['edit_id'] for e in snap['fact_corrections']] == ['e1']
    job = {'id': 'job_x', 'payload': json.dumps({'feedback_ids': [feedback['id']]})}
    items_out, _ = _experience(store, job)
    text = json.loads(items_out[0]['text'])
    assert text['kind'] == 'user_revision_edits' and 'diff' not in text and 'after' not in text
    assert [e['after'] for e in text['edits']] == ['需求稳定。']
    assert '0.27' not in json.dumps(text['edits'], ensure_ascii=False)


def test_answer_routes_once_and_skip_keeps_edit_out(tmp_path):
    store, feedback = revised(tmp_path)
    items = revision_edits.pending(store, [feedback['id']])
    revision_edits.record(store, items, verdicts(items, {
        'e2': {'category': 'taste', 'confident': False}, 'e3': {'category': 'taste', 'confident': False}}), job_id='j')
    asked = {e['edit_id']: e['id'] for e in revision_edits.snapshot(store)['questions']}
    # e2 changed only 5 characters: below the question threshold, the Evaluator's guess stands.
    assert set(asked) == {'e3'}
    answered = revision_edits.answer(store, asked['e3'], 'reader_specific')
    assert answered['feedback_id'] and revision_edits.snapshot(store)['questions'] == []
    with pytest.raises(ValueError, match='已经处理过'):
        revision_edits.answer(store, asked['e3'], 'taste')
    job = {'payload': json.dumps({'feedback_ids': [answered['feedback_id']]})}
    out, _ = _experience(store, job)
    assert [e['category'] for e in json.loads(out[0]['text'])['edits']] == ['reader_specific']
    store2, feedback2 = revised(tmp_path / 'skip')
    items2 = revision_edits.pending(store2, [feedback2['id']])
    revision_edits.record(store2, items2, verdicts(items2, {'e3': {'category': 'taste', 'confident': False}}), job_id='j')
    edit = revision_edits.snapshot(store2)['questions'][0]
    assert revision_edits.answer(store2, edit['id'], 'skip')['feedback_id'] is None
    assert 'e3' not in [e['edit_id'] for e in revision_edits.learnable(store2, feedback2['id'])]


def test_invalid_triage_is_refused_and_native_submit_validates(tmp_path):
    store, feedback = revised(tmp_path)
    items = revision_edits.pending(store, [feedback['id']])
    incomplete = verdicts(items, {})
    incomplete['edits'].pop()
    with pytest.raises(ValueError, match='缺少'):
        revision_edits.record(store, items, incomplete, job_id='j')
    packet = triage_packet(store, items, tmp_path / 'triage')
    assert (packet / 'sources' / f"{items[0]['source_ids'][0]}.txt").is_file()
    config = {'native_role': 'evaluator', 'evaluation_mode': 'triage', 'packet_root': str(packet)}
    bad = run_tool(store, config, 'submit_triage', {'edits': incomplete['edits']})
    assert bad['ok'] is False and '缺少' in bad['error']
    good = run_tool(store, config, 'submit_triage', verdicts(items, {}))
    assert good['ok'] and json.loads((tmp_path / 'triage' / 'triage.json').read_text())['edits']


class FakeRuntime:
    def __init__(self, result_for):
        self.calls = []
        self.result_for = result_for

    def execute(self, job, prompt, folder, **_):
        self.calls.append((job.get('evaluation_mode'), prompt))
        items = json.loads((Path(folder) / 'input.json').read_text(encoding='utf-8'))
        (Path(folder) / 'triage.json').write_text(json.dumps(self.result_for(items)), encoding='utf-8')


def test_learning_batch_with_only_fact_corrections_starts_no_study(tmp_path):
    store, feedback = revised(tmp_path)
    runtime = FakeRuntime(lambda items: verdicts(items, {e['edit_id']: {'category': 'fact_correction', 'confident': True}
                                                         for e in items[0]['edits']}))
    job = store.enqueue('learn', {'feedback_ids': [feedback['id']], 'k': 1, 'targets': ['writing'], 'skill_id': None,
                                  'agent_backend': 'codex', 'runtime': {'model': 'test-model'}, 'role_models': {}})
    from briefloop.learning_budget import authorization, plan
    record = authorization(store.settings(), 'manual', confirmed=plan(store.settings())['fingerprint'])
    job = {**job, 'payload': json.dumps({**json.loads(job['payload']), 'authorization': record})}
    result = learn(store, runtime, job)
    assert runtime.calls[0][0] == 'triage' and len(runtime.calls) == 1
    assert result['study'] is None and store.meta('last_study') is None
    assert len(revision_edits.snapshot(store)['fact_corrections']) == 3
    # A resumed job does not classify the same revision twice.
    assert triage(store, runtime, job, tmp_path / 'again', 'codex') is None
