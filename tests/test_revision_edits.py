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


def revised(tmp_path, *, with_reader=False):
    store = Store(tmp_path / 'ws')
    source = store.add_source('Price sheet', '组件均价每瓦0.27美元。')
    requirements = {'title': '周报', 'objective': '跟踪市场', 'audience': '管理层'}
    if with_reader:
        from briefloop import readers
        requirements['reader_id'] = readers.save(store, name='管理层')['id']
    run = store.create_run(requirements, [source['id']])
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
    store, feedback = revised(tmp_path, with_reader=True)
    items = revision_edits.pending(store, [feedback['id']])
    revision_edits.record(store, items, verdicts(items, {
        'e2': {'category': 'taste', 'confident': False}, 'e3': {'category': 'taste', 'confident': False}}), job_id='j')
    asked = {e['edit_id']: e['id'] for e in revision_edits.snapshot(store)['questions']}
    # Small uncertain edits do not prompt, but also do not become writing skills.
    assert set(asked) == {'e3'}
    small = revision_edits.snapshot(store)['unconfirmed'][0]
    assert small['edit_id'] == 'e2' and small['category'] is None
    assert 'e2' not in [e['edit_id'] for e in revision_edits.learnable(store, feedback['id'])]
    confirmed = revision_edits.answer(store, small['id'], 'taste')
    assert confirmed['feedback_id'] and confirmed['status'] == 'answered'
    answered = revision_edits.answer(store, asked['e3'], 'reader_specific')
    assert answered['feedback_id'] and revision_edits.snapshot(store)['questions'] == []
    with pytest.raises(ValueError, match='已经处理过'):
        revision_edits.answer(store, asked['e3'], 'taste')
    job = {'payload': json.dumps({'feedback_ids': [answered['feedback_id']], 'reader_id': items[0]['reader_id']})}
    out, _ = _experience(store, job)
    # Reader-tagged feedback is preserved pending applicability/cross-reader
    # validation, and cannot be imported into the global writing study.
    assert out == []
    tagged = store.rows('SELECT data FROM feedback WHERE id=?', (answered['feedback_id'],))[0]
    assert json.loads(tagged['data'])['reader_id'] == items[0]['reader_id']
    assert revision_edits.edits_by_id(store, [answered['id']])[0]['category'] == 'reader_specific'
    assert 'e3' not in [e['edit_id'] for e in revision_edits.learnable(store, feedback['id'])]
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
    for repeats in ([{}], [[]], [None]):
        malformed = verdicts(items, {})
        malformed['edits'][0]['repeats'] = repeats
        with pytest.raises(ValueError, match='repeats'):
            revision_edits.record(store, items, malformed, job_id='j')
        refused = run_tool(store, config, 'submit_triage', malformed)
        assert refused['ok'] is False and 'repeats' in refused['error']
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
    budget = plan(store.settings())
    record = authorization(store.settings(), 'manual', confirmed=budget['fingerprint'])
    job = {**job, 'payload': json.dumps({**json.loads(job['payload']), 'authorization': record, 'budget': budget})}
    result = learn(store, runtime, job)
    assert runtime.calls[0][0] == 'triage' and len(runtime.calls) == 1
    assert result['study'] is None and store.meta('last_study') is None
    assert len(revision_edits.snapshot(store)['fact_corrections']) == 3
    # A resumed job does not classify the same revision twice.
    assert triage(store, runtime, job, tmp_path / 'again', 'codex') is None


def test_reader_specific_edit_without_reader_stays_unconfirmed_even_after_answer(tmp_path):
    store, feedback = revised(tmp_path)
    items = revision_edits.pending(store, [feedback['id']])
    revision_edits.record(store, items, verdicts(items, {
        'e1': {'category': 'fact_correction', 'confident': True},
        'e2': {'category': 'taste', 'confident': False},
        'e3': {'category': 'reader_specific', 'confident': True}}), job_id='j')
    assert revision_edits.snapshot(store)['questions'] == []
    unconfirmed = {e['edit_id']: e for e in revision_edits.snapshot(store)['unconfirmed']}
    assert set(unconfirmed) == {'e2', 'e3'}
    assert revision_edits.learnable(store, feedback['id']) == []
    assert store.rows("SELECT * FROM feedback WHERE kind='revision_edit'") == []
    # Older versions classified small uncertain taste edits directly. Reading
    # that saved state must also hold them outside learning.
    with store.tx() as c:
        c.execute("UPDATE revision_edits SET status='classified',category='taste',decided_by='evaluator' WHERE id=?",
                  (unconfirmed['e2']['id'],))
    assert revision_edits.learnable(store, feedback['id']) == []
    answered = revision_edits.answer(store, unconfirmed['e3']['id'], 'reader_specific')
    assert answered['status'] == 'unconfirmed' and answered['feedback_id'] is None
    assert revision_edits.learnable(store, feedback['id']) == []
    assert revision_edits.edits_by_id(store, [answered['id']]) == []
    # Existing unsafely classified rows from an older version cannot enter the
    # workspace or get picked up through revision_edit feedback either.
    with store.tx() as c:
        c.execute("UPDATE revision_edits SET status='answered' WHERE id=?", (answered['id'],))
    assert revision_edits.learnable(store, feedback['id']) == []
    assert revision_edits.edits_by_id(store, [answered['id']]) == []


def test_explicit_no_repeats_is_distinct_from_missing_observation(tmp_path):
    store, feedback = revised(tmp_path)
    items = revision_edits.pending(store, [feedback['id']])
    result = verdicts(items, {})
    result['edits'][0]['repeats'] = []
    revision_edits.record(store, items, result, job_id='j')
    views = revision_edits.learnable(store, feedback['id'])
    assert views[0]['repeats_provided'] is True and views[0]['repeats'] == []
    assert all(e['repeats_provided'] is False for e in views[1:])


def test_answered_taste_does_not_reintroduce_full_fact_corrected_draft(tmp_path):
    store, feedback = revised(tmp_path)
    items = revision_edits.pending(store,[feedback['id']])
    revision_edits.record(store,items,verdicts(items,{
        'e1':{'category':'fact_correction','confident':True},
        'e3':{'category':'taste','confident':False}}),job_id='j')
    answer = revision_edits.answer(store,revision_edits.snapshot(store)['questions'][0]['id'],'taste')
    out,_ = _experience(store,{'payload':json.dumps({'feedback_ids':[answer['feedback_id']]})})
    value=json.loads(out[0]['text'])
    assert 'brief' not in value and 'after' not in value and 'diff' not in value
    assert [e['category'] for e in value['edits']] == ['taste']
    assert '本周组件均价为每瓦0.27美元' not in json.dumps(value,ensure_ascii=False)


def test_user_confirmed_edit_belongs_only_to_its_new_feedback(tmp_path):
    store, feedback = revised(tmp_path)
    items=revision_edits.pending(store,[feedback['id']])
    result=verdicts(items,{e['edit_id']:{'category':'fact_correction','confident':True} for e in items[0]['edits']})
    result['edits'][-1].update(category='taste',confident=False)
    revision_edits.record(store,items,result,job_id='first')
    answer=revision_edits.answer(store,revision_edits.snapshot(store)['questions'][0]['id'],'taste')
    original,_=_experience(store,{'payload':json.dumps({'feedback_ids':[feedback['id']]})})
    fresh,_=_experience(store,{'payload':json.dumps({'feedback_ids':[answer['feedback_id']]})})
    assert original == [] and len(fresh) == 1
    assert len(json.loads(fresh[0]['text'])['edits']) == 1
