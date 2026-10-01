"""Monthly defaults, an explained early research stop and the engine behind a chat-submitted model."""
import json

import pytest

from briefloop import research_plan
from briefloop.models import Requirements, RESEARCH_BUDGET_PRESETS, INDUSTRY_MONTHLY_LENGTH, INDUSTRY_LENGTH
from briefloop.store import Store


MONTH = {'title': 'AI 行业月报', 'objective': '梳理本月动态', 'report_profile': 'industry_periodic',
         'period_start': '2026-09-01', 'period_end': '2026-09-30'}


def test_monthly_period_gets_monthly_length_budget_and_scouts():
    spec = Requirements.model_validate(MONTH)
    assert (spec.target_words, spec.max_words) == INDUSTRY_MONTHLY_LENGTH['zh']
    assert spec.research_budget.model_dump() == RESEARCH_BUDGET_PRESETS['monthly']
    assert spec.scout_limit == 8
    week = Requirements.model_validate({**MONTH, 'title': 'AI 行业周报', 'period_end': '2026-09-07'})
    assert (week.target_words, week.max_words) == INDUSTRY_LENGTH['zh'] and week.scout_limit is None
    chosen = Requirements.model_validate({**MONTH, 'research_budget': {'search_requests': 12}, 'scout_limit': 14,
                                          'target_words': 6000})
    assert chosen.research_budget.search_requests == 12 and chosen.scout_limit == 14 and chosen.target_words == 6000
    with pytest.raises(ValueError):
        Requirements.model_validate({**MONTH, 'scout_limit': 17})


def _run_with_searches(tmp_path, used):
    store = Store(tmp_path)
    run = store.create_run({'title': '周报', 'objective': '本周动态', 'allow_web': True, 'fact_check': False,
                            'report_date': '2026-09-08'}, [], research_protocol='quality_v1')
    research_plan.freeze(store, run['id'])
    state = store.meta('research_budget:' + run['id']) or {'search_requests': 0, 'candidate_urls': [], 'source_pages': []}
    store.set_meta('research_budget:' + run['id'], {**state, 'search_requests': used})
    return store, run


def test_first_round_stop_with_most_budget_left_needs_a_reason(tmp_path):
    store, run = _run_with_searches(tmp_path, used=8)
    with pytest.raises(research_plan.AdmissionError) as refused:
        research_plan.finish_round(store, run['id'])
    assert refused.value.code == 'research_stopped_early' and '8/30' in str(refused.value)
    closed = research_plan.finish_round(store, run['id'], early_stop_reason='本周只有两件可证实的发布，均已取得原文')
    plan = research_plan.frozen(store, run['id'])
    assert plan['rounds'][closed['round_id']]['outcome']['early_stop_reason'].startswith('本周只有两件')


def test_continuing_or_unsearched_rounds_are_not_held(tmp_path):
    store, run = _run_with_searches(tmp_path, used=8)
    assert research_plan.finish_round(store, run['id'], continue_research=True)['index'] == 1
    store2, run2 = _run_with_searches(tmp_path / 'offline', used=0)
    assert research_plan.finish_round(store2, run2['id'])['index'] == 1


def test_chat_model_runs_on_the_chat_engine_not_the_workspace_default(tmp_path):
    from briefloop.chat_tools import workspace_action
    store = Store(tmp_path)
    store.update_settings({'agent_backend': 'antigravity', 'model': 'gemini-3.8-flash'})
    from briefloop.chat_store import ChatStore
    chat = ChatStore(store).create('报告对话', {'backend': 'claude', 'model': 'opus', 'effort': 'medium'}, store.root)
    source = store.add_source('公开材料', '本期公开材料。')
    result = workspace_action(store, {'action': 'generate', 'session_id': chat['id'],
                                      'requirements': {'title': '月报', 'objective': '梳理动态', 'allow_web': False, 'fact_check': False},
                                      'source_ids': [source['id']], 'runtime': {'model': 'opus', 'reasoning_effort': 'medium'}})
    payload = json.loads(store.one('jobs', result['job_id'])['payload'])
    assert payload['agent_backend'] == 'claude' and payload['runtime']['model'] == 'opus'
