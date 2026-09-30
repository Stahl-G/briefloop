"""#858: saved readers — frozen into runs, their own skill, their own learning scope."""
import json

import pytest

from briefloop import readers, revision_edits
from briefloop.learning import _experience, apply_accepted, enqueue_feedback
from briefloop.learning_budget import plan
from briefloop.store import Store, dump, now

BEFORE = '# 周报\n\n## 市场\n\n本周组件均价为每瓦0.31美元。'
AFTER = ('# 周报\n\n## 市场\n\n本周组件均价为每瓦0.31美元。\n\n'
         '对管理层而言，要点是在十二月初裁之前备齐全部溯源文件；若被认定规避，对美出货节奏需要重排。')


def workspace(tmp_path):
    store = Store(tmp_path / 'ws')
    source = store.add_source('Sheet', '组件均价每瓦0.31美元。')
    return store, source


def test_run_freezes_profile_and_uses_reader_skill(tmp_path):
    store, source = workspace(tmp_path)
    reader = readers.save(store, name='公司管理层', decisions='决定出货节奏', preferences='先给结论')
    with store.tx() as c:
        for sid in ('skill_general', 'skill_reader'):
            c.execute('INSERT INTO skills VALUES(?,?,?,?,?,?)', (sid, None, sid, dump(['writing']), 'test', now()))
    store.bind_skill('skill_general')
    readers.bind_skill(store, reader['id'], 'skill_reader')
    plain = store.create_run({'title': 'T', 'objective': 'O'}, [source['id']])
    assert 'reader_id' not in json.loads(plain['requirements']) and plain['skill_id'] == 'skill_general'
    run = store.create_run({'title': 'T', 'objective': 'O', 'reader_id': reader['id']}, [source['id']])
    requirements = json.loads(run['requirements'])
    assert requirements['reader_profile']['decisions'] == '决定出货节奏' and requirements['audience'] == '公司管理层'
    assert run['skill_id'] == 'skill_reader'
    readers.save(store, reader_id=reader['id'], name='公司管理层', decisions='改了')
    assert json.loads(store.one('runs', run['id'])['requirements'])['reader_profile']['decisions'] == '决定出货节奏'
    from briefloop.deliverable_spec import resolve
    assert resolve(requirements)['reader_profile']['preferences'] == '先给结论'
    assert 'reader_profile' not in resolve(json.loads(plain['requirements']))
    with pytest.raises(ValueError, match='同名'):
        readers.save(store, name='公司管理层')


def test_reader_specific_edit_learns_in_its_reader_scope_only(tmp_path):
    store, source = workspace(tmp_path)
    reader = readers.save(store, name='公司管理层')
    run = store.create_run({'title': 'T', 'objective': 'O', 'reader_id': reader['id']}, [source['id']])
    brief = store.publish(run['id'], {'title': 'T', 'markdown': BEFORE})
    store.revise(brief['id'], markdown=AFTER)
    feedback = store.rows("SELECT * FROM feedback WHERE kind='revision'")[0]
    items = revision_edits.pending(store, [feedback['id']])
    assert items[0]['reader_id'] == reader['id']
    revision_edits.record(store, items, {'edits': [{'feedback_id': feedback['id'], 'edit_id': 'e1',
                          'category': 'reader_specific', 'confident': True, 'reason': '面向管理层'}]}, job_id='j')
    # The general batch no longer carries it; a reader-scoped feedback row does.
    assert revision_edits.learnable(store, feedback['id']) == []
    scoped = store.rows("SELECT * FROM feedback WHERE kind='revision_edit'")
    assert json.loads(scoped[0]['data'])['reader_id'] == reader['id']
    # Batches: the general revision first, then the reader's edits on their own.
    store.update_settings({'model': 'test-model'})
    first = enqueue_feedback(store, confirmed_plan=plan(store.settings())['fingerprint'])
    assert 'reader_id' not in json.loads(first['payload'])
    store.update_job(first['id'], 'complete', result={})
    second = enqueue_feedback(store, confirmed_plan=plan(store.settings())['fingerprint'])
    payload = json.loads(second['payload'])
    assert payload['reader_id'] == reader['id'] and payload['feedback_ids'] == [scoped[0]['id']]
    out, cases = _experience(store, {'payload': second['payload']})
    assert json.loads(out[0]['text'])['edits'][0]['category'] == 'reader_specific' and cases == [run['id']]


def test_reader_adoption_binds_reader_skill_not_workspace(tmp_path, monkeypatch):
    store, _ = workspace(tmp_path)
    reader = readers.save(store, name='投资者关系')
    study = tmp_path / 'study'
    (study / 'cand').mkdir(parents=True)
    (study / 'cand' / 'SKILL.md').write_text('# 给投资者关系的写法')
    state = {'history': [{'accepted': True, 'skill': {'file': 'cand/SKILL.md'}, 'reason': 'better'}]}
    job = store.enqueue('learn', {'feedback_ids': [], 'skill_id': None, 'targets': ['writing'], 'reader_id': reader['id'], 'runtime': {'model': 'test-model'}})
    apply_accepted(store, job, study, state)
    assert store.meta('active_skill') is None
    bound = store.meta('reader_skills')[reader['id']]
    assert readers.skill_for(store, reader['id']) == bound and readers.skill_for(store, None) is None
    assert readers.scope_meta('last_study', reader['id']) == 'last_study:' + reader['id']
    assert readers.wiki_path(store, reader['id']).name == f"reader-{reader['id']}.md"
