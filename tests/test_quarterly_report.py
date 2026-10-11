"""Quarterly industry reports: their own tier, and a state table read from both ends of the period."""
from datetime import date

import pytest

from briefloop.industry_data import prepare_report_data
from briefloop.models import Requirements, RESEARCH_BUDGET_PRESETS, INDUSTRY_QUARTERLY_LENGTH, QUARTERLY_SCOUTS
from briefloop.document_workflows import freeze_workflow, resolve_workflow, workflow_context


QUARTER = {'title': 'AI 行业季报', 'objective': '本季格局变化', 'workflow_id': 'business_report',
           'workflow_variant': 'industry_quarterly', 'period_start': '2026-07-01', 'period_end': '2026-09-30'}


def test_quarterly_variant_gets_quarterly_tier_and_industry_tools():
    spec = Requirements.model_validate(QUARTER)
    assert spec.report_profile == 'industry_periodic'
    assert (spec.target_words, spec.max_words) == INDUSTRY_QUARTERLY_LENGTH['zh']
    assert spec.research_budget.model_dump() == RESEARCH_BUDGET_PRESETS['quarterly']
    assert spec.scout_limit == QUARTERLY_SCOUTS
    # A three-month window alone is a quarter; a month stays monthly; explicit choices are kept.
    window = Requirements.model_validate({'title': '动态', 'objective': 'x', 'report_profile': 'industry_periodic',
                                          'period_start': '2026-07-01', 'period_end': '2026-09-30'})
    assert window.research_budget.model_dump() == RESEARCH_BUDGET_PRESETS['quarterly']
    month = Requirements.model_validate({**QUARTER, 'workflow_variant': 'industry_periodic', 'title': 'AI 月报',
                                         'period_end': '2026-07-31'})
    assert month.research_budget.model_dump() == RESEARCH_BUDGET_PRESETS['monthly'] and month.scout_limit == 8
    chosen = Requirements.model_validate({**QUARTER, 'scout_limit': 4, 'research_budget': RESEARCH_BUDGET_PRESETS['monthly']})
    assert chosen.scout_limit == 4 and chosen.research_budget.model_dump() == RESEARCH_BUDGET_PRESETS['monthly']


def test_quarterly_method_reaches_writer_and_review_cards_reach_only_review():
    snapshot = freeze_workflow(resolve_workflow(QUARTER))
    assert '全景扫描' in workflow_context(snapshot, 'orchestrator')
    assert '期初基线' in workflow_context(snapshot, 'analyst') and '核查增量' not in workflow_context(snapshot, 'analyst')
    review = workflow_context(snapshot, 'evaluator')
    assert '核查增量' in review and '期初基线' not in review


WINDOW = (date(2026, 7, 1), date(2026, 10, 1))


def _change(**overrides):
    return {'subject': '甲公司', 'dimension': '价格', 'change_type': 'reversed', 'start_state': '领先',
            'start_date': '2026-06-28', 'start_source_id': 'src_a', 'end_state': '落后',
            'end_date': '2026-09-20', 'end_source_id': 'src_b', **overrides}


@pytest.mark.parametrize('overrides,gap', [
    ({}, ''),
    ({'change_type': 'emerged', 'start_state': '', 'start_date': None, 'start_source_id': None}, ''),
    ({'start_state': '', 'start_source_id': None}, '缺少期初状态'),
    ({'start_date': '2026-09-01'}, '前三分之一'),
    ({'end_date': '2026-10-05'}, '不在本期'),
])
def test_state_change_needs_evidence_at_both_ends_of_the_period(overrides, gap):
    result = prepare_report_data({'state_changes': [_change(**overrides)]}, WINDOW)
    assert gap in result['state_checks'][0]['gap'] if gap else not result['gaps']
    assert '期初状态（日期）' in result['markdown'] and '[@src_b]' in result['markdown']
