"""#858/#860: frozen reader profiles and retained, inactive reader-specific learning."""
import json

import pytest

from briefloop import readers, revision_edits, skill_verification
from briefloop.learning import apply_accepted, enqueue_feedback
from briefloop.learning_budget import plan
from briefloop.store import Store, dump, now

BEFORE = '# 周报\n\n## 市场\n\n本周组件均价为每瓦0.31美元。'
AFTER = ('# 周报\n\n## 市场\n\n本周组件均价为每瓦0.31美元。\n\n'
         '对管理层而言，要点是在十二月初裁之前备齐全部溯源文件；若被认定规避，对美出货节奏需要重排。')


def workspace(tmp_path):
    store = Store(tmp_path / 'ws')
    source = store.add_source('Sheet', '组件均价每瓦0.31美元。')
    return store, source


def test_run_freezes_profile_and_uses_global_skill_without_losing_legacy_reader_binding(tmp_path):
    store, source = workspace(tmp_path)
    reader = readers.save(store, name='公司管理层', decisions='决定出货节奏', preferences='先给结论')
    with store.tx() as c:
        for sid in ('skill_general', 'skill_reader'):
            c.execute('INSERT INTO skills VALUES(?,?,?,?,?,?)', (sid, None, sid, dump(['writing']), 'test', now()))
    store.bind_skill('skill_general')
    store.set_meta('reader_skills', {reader['id']: 'skill_reader'})  # Stored before #860.
    plain = store.create_run({'title': 'T', 'objective': 'O'}, [source['id']])
    assert 'reader_id' not in json.loads(plain['requirements']) and plain['skill_id'] == 'skill_general'
    run = store.create_run({'title': 'T', 'objective': 'O', 'reader_id': reader['id']}, [source['id']])
    requirements = json.loads(run['requirements'])
    assert requirements['reader_profile']['decisions'] == '决定出货节奏' and requirements['audience'] == '公司管理层'
    assert run['skill_id'] == 'skill_general'
    assert store.meta('reader_skills') == {reader['id']: 'skill_reader'}
    assert readers.listing(store)[0]['skill_id'] == 'skill_reader'
    assert readers.listing(store)[0]['skill_enabled'] is False
    readers.save(store, reader_id=reader['id'], name='公司管理层', decisions='改了')
    assert json.loads(store.one('runs', run['id'])['requirements'])['reader_profile']['decisions'] == '决定出货节奏'
    from briefloop.deliverable_spec import resolve
    assert resolve(requirements)['reader_profile']['preferences'] == '先给结论'
    assert 'reader_profile' not in resolve(json.loads(plain['requirements']))
    with pytest.raises(ValueError, match='同名'):
        readers.save(store, name='公司管理层')


def test_reader_specific_edit_stays_waiting_for_applicability_and_cross_reader_acceptance(tmp_path):
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
    assert revision_edits.learnable(store, feedback['id']) == []
    scoped = store.rows("SELECT * FROM feedback WHERE kind='revision_edit'")
    assert json.loads(scoped[0]['data'])['reader_id'] == reader['id']
    store.update_settings({'model': 'test-model'})
    first = enqueue_feedback(store, confirmed_plan=plan(store.settings())['fingerprint'])
    assert 'reader_id' not in json.loads(first['payload'])
    store.update_job(first['id'], 'complete', result={})
    second = enqueue_feedback(store, confirmed_plan=plan(store.settings())['fingerprint'])
    assert second['status'] == 'awaiting_reader_scope'
    assert len(store.rows("SELECT id FROM jobs WHERE kind='learn'")) == 1
    assert store.rows('SELECT batch_id FROM feedback WHERE id=?', (scoped[0]['id'],))[0]['batch_id'] is None
    assert store.rows('SELECT category FROM revision_edits')[0]['category'] == 'reader_specific'


def test_old_reader_adoption_does_not_start_a_separate_skill_chain(tmp_path):
    store, _ = workspace(tmp_path)
    reader = readers.save(store, name='投资者关系')
    with store.tx() as c:
        for sid in ('skill_general', 'skill_reader'):
            c.execute('INSERT INTO skills VALUES(?,?,?,?,?,?)', (sid, None, sid, dump(['writing']), 'test', now()))
    store.bind_skill('skill_general')
    store.set_meta('reader_skills', {reader['id']: 'skill_reader'})
    study = tmp_path / 'study'
    (study / 'cand').mkdir(parents=True)
    (study / 'cand' / 'SKILL.md').write_text('# 给投资者关系的写法')
    state = {'history': [{'accepted': True, 'skill': {'file': 'cand/SKILL.md'}, 'reason': 'better'}]}
    job = store.enqueue('learn', {'feedback_ids': [], 'skill_id': 'skill_general', 'targets': ['writing'],
                                 'reader_id': reader['id'], 'runtime': {'model': 'test-model'}})
    apply_accepted(store, job, study, state)
    assert store.meta('active_skill') == 'skill_general'
    assert store.meta('reader_skills') == {reader['id']: 'skill_reader'}
    assert {row['id'] for row in store.rows('SELECT id FROM skills')} == {'skill_general', 'skill_reader'}
    assert readers.skill_for(store, reader['id']) == readers.skill_for(store, None) == 'skill_general'
    assert store.rows('SELECT * FROM skill_verifications') == []


def test_global_binding_rejects_legacy_reader_skills_and_descendants(tmp_path):
    store, source = workspace(tmp_path)
    reader = readers.save(store, name='公司管理层')
    with store.tx() as c:
        for sid, parent in [('skill_general', None), ('skill_bound', None), ('skill_verified', None), ('skill_child', 'skill_verified')]:
            c.execute('INSERT INTO skills VALUES(?,?,?,?,?,?)', (sid, parent, sid, dump(['writing']), 'test', now()))
        skill_verification.register(c, 'skill_verified', reader['id'], [], 'legacy_job')
    store.set_meta('reader_skills', {reader['id']: 'skill_bound'})
    store.bind_skill('skill_general')
    for sid in ('skill_bound', 'skill_verified', 'skill_child'):
        assert readers.reader_scope_ids(store, sid) == (reader['id'],)
        with pytest.raises(ValueError, match='跨读者验证'):
            readers.validate_global_skill(store, sid)
        with pytest.raises(ValueError, match='跨读者验证'):
            store.bind_skill(sid)
        assert store.meta('active_skill') == 'skill_general'
    # A workspace that had already activated an old reader skill keeps that
    # history, but new reports cannot inherit it for another reader.
    store.set_meta('active_skill', 'skill_child')
    assert readers.skill_for(store, None) is None
    run = store.create_run({'title': 'T', 'objective': 'O'}, [source['id']])
    assert run['skill_id'] is None and store.meta('active_skill') == 'skill_child'


def test_explicit_legacy_unbind_preserves_scope_and_new_reader_bindings_are_rejected(tmp_path):
    store, _ = workspace(tmp_path)
    reader = readers.save(store, name='管理层')
    with store.tx() as c:
        c.execute('INSERT INTO skills VALUES(?,?,?,?,?,?)', ('skill_old', None, '# old', dump(['writing']), 'old', now()))
    store.set_meta('reader_skills', {reader['id']: 'skill_old'})
    with pytest.raises(ValueError, match='暂未启用'):
        readers.bind_skill(store, reader['id'], 'skill_old')
    assert store.meta('reader_skills') == {reader['id']: 'skill_old'}
    readers.bind_skill(store, reader['id'], None)
    assert store.meta('reader_skills') == {}
    assert readers.reader_scope_ids(store, 'skill_old') == (reader['id'],)
    assert store.rows('SELECT status FROM skill_verifications')[0]['status'] == 'untracked'
    with pytest.raises(ValueError, match='跨读者验证'):
        readers.validate_global_skill(store, 'skill_old')
