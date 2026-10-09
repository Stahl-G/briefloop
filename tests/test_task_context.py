import json

import pytest

from briefloop import research_plan, research_budget, scout, analyst
from briefloop.runtime import generation_prompt
from briefloop.store import Store
from briefloop.task_context import project


def setup(tmp_path, **choices):
    store = Store(tmp_path)
    source = store.add_source('合成季度数据', '本期收入 120 万元；上一期 100 万元。')
    run = store.create_run({'title': '经营报告', 'objective': '解释收入增长能否持续',
        'audience': '经营团队', 'key_questions': ['增长来自哪里？'],
        'allow_web': False, 'research_strategy': 'goal_driven', **choices}, [source['id']], research_protocol='quality_v1')
    return store, run, source


def close_round(store, run_id, **choices):
    plan = research_plan.frozen(store, run_id)
    if plan.get('goal_contract'):
        info = plan['rounds'][plan['current_round_id']]
        path = store.root/'research'/run_id/'rounds'/str(info['index'])/'handoff.json'
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({'learnings': [], 'question_coverage': [
            {'question_id': q['id'], 'status': 'open', 'reason': '合成材料范围有限',
             'remaining_question': q['question']} for q in plan['goal_contract']['questions']]}))
    return research_plan.finish_round(store, run_id, **choices)


def test_goal_strategy_frozen_with_run_and_rounds_do_not_impose_work(tmp_path):
    store, run, _ = setup(tmp_path, research_tier='deep')
    plan = research_plan.freeze(store, run['id'])
    assert plan['research_strategy'] == 'goal_driven'
    assert len(plan['rounds']) == 1 and not next(iter(plan['rounds'].values()))['tasks']
    assert research_plan.freeze(store, run['id'])['plan_fingerprint'] == plan['plan_fingerprint']
    with pytest.raises(research_plan.AdmissionError, match='依据'):
        research_plan.finish_round(store, run['id'])
    close_round(store, run['id'], summary='现有资料可解释增长；缺少客户分项，因此不推断可持续性。')
    assert research_plan.require_writing_closeout(store, run['id'])['outcome']['summary']
    # Optional deepening retains the original maximum; no extra quota/rounds.
    for _ in range(2, plan['structure']['depth'] + 1):
        research_plan.begin_round(store, run['id'])
        close_round(store, run['id'], summary='没有新增证据，保留原有未知。')
    with pytest.raises(research_plan.AdmissionError, match='最大联网轮次'):
        research_plan.begin_round(store, run['id'])


def test_goal_stop_is_evidence_based_and_keeps_budget_admission(tmp_path):
    store, run, _ = setup(tmp_path, allow_web=True,
        research_budget={'search_requests': 12, 'candidate_urls': 20, 'source_pages': 2})
    plan = research_plan.freeze(store, run['id'])
    # Actual reservation is still capped. Simulate one counted, completed query
    # through the existing budget metadata without making an external request.
    store.set_meta('research_budget:' + run['id'], {'search_requests': 1, 'candidate_urls': [], 'source_pages': []})
    closed = close_round(store, run['id'], summary='已有原文足够回答必答问题，无需消耗剩余额度。')
    assert closed['index'] == 1
    with pytest.raises(research_plan.AdmissionError):
        research_budget.reserve_search(store, run['id'], 1)
    research_plan.begin_round(store, run['id'])
    store.set_meta('research_budget:' + run['id'], {'search_requests': 12, 'candidate_urls': [], 'source_pages': []})
    with pytest.raises(research_budget.BudgetExhausted):
        research_budget.reserve_search(store, run['id'], 1)
    assert research_plan.frozen(store, run['id'])['budget'] == plan['budget']


def test_role_projection_excludes_other_roles_and_does_not_summarize_sources(tmp_path):
    store, run, source = setup(tmp_path)
    research_plan.freeze(store, run['id'])
    task = scout.task(store, run['id'], {'slot_id': 'scout-1', 'assignment': '核对收入'})
    from briefloop.native_roles import scout_packet
    packet = scout_packet(store, task, store.root/'scout')
    assert json.loads((packet/'task.json').read_text())['task_context'] == task['task_context']
    assert 'task_context' in scout.native_prompt(task)
    writer = analyst.packet(store, run['id'], store.root/'writer', plan={}, research={'sources': [], 'gaps': []})
    context = json.loads((writer['root']/'input.json').read_text())['task_context']
    assert context['role'] == 'analyst' and context['purpose']['objective'] == '解释收入增长能否持续'
    assert 'research.json' in context['knowledge']['uncertainty']
    assert 'finish_research_round' not in json.dumps(context)
    assert '120' not in json.dumps(context)  # Navigation, not a second factual summary.
    evaluation = project(json.loads(run['requirements']), 'evaluator', evidence='sources', uncertainty='research_context')
    assert '不补搜' in evaluation['purpose']['responsibility']
    assert '研究取舍只是待核对' in evaluation['method']['guidance']


@pytest.mark.parametrize('backend', ['codex', 'opencode', 'briefloop-native', 'pi'])
def test_host_and_native_receive_same_strategy_without_fixed_waves(tmp_path, backend):
    store, run, _ = setup(tmp_path, allow_web=True)
    research_plan.freeze(store, run['id'])
    folder = store.root/'generation';folder.mkdir()
    prompt = generation_prompt(store, run, folder, backend=backend)
    data = json.loads((folder/'input.json').read_text())
    assert data['task_context']['method']['research_strategy'] == 'goal_driven'
    assert '按目标补证' in prompt and '第一轮侦察：整批' not in prompt
    assert '月报通常 6–8' not in prompt and '上限' in prompt
    assert '至少安排一个 Scout' not in prompt
    if backend == 'briefloop-native':
        from briefloop.native_orchestrator import prepare
        job = {'id': 'synthetic', 'kind': 'generate', 'allow_web': True,
               'payload': json.dumps({'run_id': run['id'], 'runtime': {'model': 'synthetic/no-call'}})}
        _, adapted = prepare(store, job, folder, prompt)
        actual = (folder/'packet'/'task.md').read_text()
        assert '按目标补证' in actual and 'task_context' in actual
        assert '保存交接并收轮' in actual


def test_empty_source_goal_run_does_not_force_a_scout(tmp_path):
    store = Store(tmp_path)
    for strategy in ('guided', 'goal_driven'):
        run = store.create_run({'title': '公开资料简报', 'objective': '核对一次公开发布',
            'allow_web': True, 'research_strategy': strategy}, [], research_protocol='quality_v1')
        research_plan.freeze(store, run['id'])
        folder = store.root/strategy;folder.mkdir()
        prompt = generation_prompt(store, run, folder, backend='codex')
        if strategy == 'goal_driven':
            assert '问题集中时主 Agent 可直接' in prompt and '至少安排一个 Scout' not in prompt
            assert 'scout_tasks=[]' in prompt and '已授权检索和来源工具' in prompt
        else:
            assert '至少安排一个 Scout' in prompt
