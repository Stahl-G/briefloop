import json
import pytest
from briefloop.store import Store
from briefloop import research_plan, research_goals
from briefloop.scout_tools import check_handoff, HandoffError


def setup(tmp_path):
    store = Store(tmp_path)
    source = store.add_source('季度原文', '本期收入120万元。')
    run = store.create_run({'title': '季度复盘', 'objective': '解释收入',
        'key_questions': ['收入多少？', '增长可持续吗？'], 'allow_web': False,
        'research_strategy': 'goal_driven'}, [source['id']], research_protocol='quality_v1')
    plan = research_plan.freeze(store, run['id'])
    return store, run, source, plan


def save_handoff(store, run, plan, coverage):
    index = plan['rounds'][plan['current_round_id']]['index']
    path = store.root/'research'/run['id']/'rounds'/str(index)/'handoff.json'
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({'learnings': [], 'question_coverage': coverage}))


def test_complete_coverage_can_preserve_unknown_without_more_search(tmp_path):
    store, run, source, plan = setup(tmp_path)
    with pytest.raises(ValueError, match='question_coverage'):
        research_plan.finish_round(store, run['id'], summary='已准备写作')
    assert research_plan.frozen(store, run['id'])['current_round_id']
    coverage = [{'question_id': 'q1', 'status': 'answered', 'reason': '收入有原文',
        'evidence': [{'source_id': source['id'], 'locator': 'line 1', 'excerpt': '本期收入120万元。'}]},
        {'question_id': 'q2', 'status': 'open', 'reason': '未提供客户结构，不推断持续性',
         'remaining_question': '增长来源和客户留存未知'}]
    save_handoff(store, run, plan, coverage)
    research_plan.finish_round(store, run['id'], summary='收入可核对；持续性未答，限定结论交稿。')
    progress = research_plan.status(Store(tmp_path), run['id'])['goal_progress']
    assert [q['status'] for q in progress['questions']] == ['answered', 'open']
    assert progress['questions'][0]['evidence'][0]['source_hash'] == source['hash']
    assert research_plan.require_writing_closeout(store, run['id'])
    # A late file change cannot rewrite the closed state through finish replay.
    coverage[1]['reason'] = '试图改写'
    save_handoff(store, run, plan, coverage)
    research_plan.finish_round(store, run['id'], summary='试图改写')
    assert research_plan.status(store, run['id'])['goal_progress'] == progress


def test_unbound_or_invented_evidence_cannot_close_question(tmp_path):
    store, run, source, plan = setup(tmp_path)
    foreign = store.add_source('别的材料', '本期收入120万元。')
    item = {'question_id': 'q1', 'status': 'answered', 'reason': '有依据',
            'evidence': [{'source_id': foreign['id'], 'locator': 'line 1', 'excerpt': '本期收入120万元。'}]}
    with pytest.raises(HandoffError, match='不属于本报告'):
        check_handoff(store, run['id'], {'learnings': [], 'question_coverage': [item]})
    item['evidence'][0]['source_id'] = source['id']
    item['evidence'][0]['excerpt'] = '本期收入999万元。'
    with pytest.raises(ValueError):research_goals.validate(store, run['id'], [item])
    assert research_plan.status(store, run['id'])['goal_progress']['questions'][0]['status'] == 'open'


def test_missing_and_duplicate_questions_are_not_silently_omitted(tmp_path):
    store, run, _, _ = setup(tmp_path)
    item = {'question_id': 'q1', 'status': 'open', 'reason': '材料不足', 'remaining_question': '收入未知'}
    with pytest.raises(ValueError, match='q2'):
        research_goals.validate(store, run['id'], [item], require_complete=True)
    with pytest.raises(ValueError, match='不得重复'):
        research_goals.validate(store, run['id'], [item, item])


def test_historical_goal_plan_remains_replayable_without_new_contract(tmp_path):
    store, run, _, plan = setup(tmp_path)
    plan.pop('goal_contract')
    keys = ['research_protocol','run_id','owner_generate_job_id','authorized_budget','structure',
            'budget','preset_id','frozen_runtime','research_strategy']
    plan['plan_fingerprint'] = research_plan._fingerprint({key: plan[key] for key in keys})
    store.set_meta('research_plan:' + run['id'], plan)
    assert research_plan.freeze(store, run['id']) == plan
    research_plan.finish_round(store, run['id'], summary='旧任务仍按原契约收轮')
    assert research_plan.status(store, run['id'])['goal_progress'] is None


def test_task_card_projection_keeps_source_navigation_without_raw_model_data(tmp_path):
    from briefloop.task_progress import summary
    store, run, source, plan = setup(tmp_path)
    job = store.enqueue('generate', {'run_id': run['id'], 'runtime': {'model': 'synthetic-no-call'}})
    from briefloop.scout_coverage import declare
    declare(store, run['id'], [])
    coverage = [{'question_id': 'q1', 'status': 'answered', 'reason': '收入有原文',
        'evidence': [{'source_id': source['id'], 'locator': 'line 1', 'excerpt': '本期收入120万元。'}]},
        {'question_id': 'q2', 'status': 'open', 'reason': '数据有限', 'remaining_question': '增长未知'}]
    save_handoff(store, run, plan, coverage)
    research_plan.finish_round(store, run['id'], summary='保留未知')
    store.event(job['id'], 'runtime_progress', {'thinking': 'PRIVATE', 'stage': '准备写稿'})
    card = summary(store, job['id'])
    assert card['goal_progress']['questions'][0]['evidence'][0]['source_name'] == source['name']
    assert card['goal_progress']['questions'][1]['remaining_question'] == '增长未知'
    assert 'PRIVATE' not in json.dumps(card)
    with store.tx() as connection:
        connection.execute('UPDATE sources SET hash=? WHERE id=?', ('changed-after-closeout', source['id']))
    changed = summary(store, job['id'])['goal_progress']['questions'][0]['evidence'][0]
    assert changed['source_changed'] and changed['source_hash'] == source['hash']
    assert changed['excerpt'] == '本期收入120万元。'


def test_native_direct_research_handoff_reaches_writer_input_without_fake_scout(tmp_path):
    from briefloop.native_orchestrator import _refresh_research
    from briefloop.scout_coverage import declare
    store, run, _, plan = setup(tmp_path)
    declare(store, run['id'], [])
    coverage = [{'question_id': q['id'], 'status': 'open', 'reason': '现有材料不足',
                 'remaining_question': q['question']} for q in plan['goal_contract']['questions']]
    save_handoff(store, run, plan, coverage)
    research_plan.finish_round(store, run['id'], summary='直接研究完成；保留未答问题')
    packet = store.root/'native'/'packet';packet.mkdir(parents=True)
    config = {'run_id': run['id'], 'packet_root': str(packet)}
    _refresh_research(store, config)
    research = json.loads((packet/'research.json').read_text())
    assert research['sources'] == []  # No invented Scout quotations.
    notes = [n for n in research['retrieval_notes'] if n.get('kind') == 'research_round_closeout']
    assert notes[0]['question_coverage'][1]['status'] == 'open'
    assert (packet.parent/'research.json').read_bytes() == (packet/'research.json').read_bytes()


def test_fast_web_retained_goal_selection_can_finish_its_own_search_pass(tmp_path):
    store = Store(tmp_path)
    run = store.create_run({'title': '快速公开检索', 'objective': '查最新公告',
        'completion_mode': 'fast_web', 'research_strategy': 'goal_driven',
        'fact_check': False,
        'search_policy': {'primary_provider': 'duckduckgo', 'native_search_enabled': False}},
        [], research_protocol='quality_v1')
    # This is the actual fast_research.collect plan/closeout boundary. Its
    # plain planning turns do not create a Scout/Orchestrator handoff file.
    research_plan.freeze(store, run['id'], preset='quick',
                         structure={'breadth': 3, 'depth': 1, 'parallel': 3})
    research_plan.finish_round(store, run['id'], summary='快速原文读取结束，不声明覆盖完整')
    restored = Store(tmp_path)
    assert research_plan.status(restored, run['id'])['goal_progress'] is None
    assert research_plan.require_writing_closeout(restored, run['id'])
