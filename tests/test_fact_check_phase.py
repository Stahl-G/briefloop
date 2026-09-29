"""Phase B fact-check stage: explicit admission, staged budget, late-result rules."""
import json
import threading

import pytest

from briefloop import research_budget as budget
from briefloop import research_plan
from briefloop.store import Store, dump

SHARE = {'search_requests': 2, 'candidate_urls': 10, 'source_pages': 2}


def _run(store, **extra):
    requirements = {'title': 'R', 'objective': 'o', 'allow_web': True, 'fact_check': True}
    requirements.update(extra)
    return store.create_run(requirements, [], research_protocol='quality_v1')


def _closed_round(store, run):
    research_plan.freeze(store, run['id'])
    research_plan.finish_round(store, run['id'])
    return run


def test_admit_fact_check_is_idempotent_and_charges_the_stage(tmp_path):
    store = Store(tmp_path)
    run = _run(store, research_budget={'search_requests': 4, 'candidate_urls': 20, 'source_pages': 6})
    research_plan.freeze(store, run['id'])
    research = budget.reserve_search(store, run['id'], 1)  # one research search in round 1
    assert research['stage'] == 'research'
    research_plan.finish_round(store, run['id'])
    stage = research_plan.admit_fact_check(store, run['id'], {'kind': 'task_reserve', 'limits': SHARE})
    assert stage['status'] == 'active' and stage['idempotent'] is False
    assert stage['budget_source'] == {'kind': 'task_reserve', 'limits': SHARE}
    # A repeat request reuses the admitted stage; the first recorded source stays.
    again = research_plan.admit_fact_check(store, run['id'],
                                           {'kind': 'task_reserve', 'limits': {'search_requests': 1, 'candidate_urls': 1, 'source_pages': 1}})
    assert again['stage_id'] == stage['stage_id'] and again['idempotent'] is True
    plan = research_plan.frozen(store, run['id'])
    assert plan['fact_check']['stage_id'] == stage['stage_id']
    assert 'fact_check' not in plan['rounds'] and len(plan['rounds']) == 1  # never a disguised Scout round
    # Controlled requests inside the stage are charged to it, not to a round.
    checked = budget.reserve_search(store, run['id'], 1)
    assert checked['round_id'] == stage['stage_id'] and checked['stage'] == 'fact_check'
    entry = research_plan.pending_requests(store, run['id'])[checked['request_id']]
    assert entry['stage'] == 'fact_check'
    view = budget.snapshot(store, run['id'])
    assert budget.spent(store, run['id'])['search_requests'] == 2  # same KINDS meter as research
    assert view['stages'] == {'research': {'search_requests': 1, 'source_pages': 0},
                              'fact_check': {'search_requests': 1, 'source_pages': 0}}


def test_admit_fact_check_refuses_open_round_offline_and_missing_budget(tmp_path):
    store = Store(tmp_path)
    run = _run(store)
    research_plan.freeze(store, run['id'])
    with pytest.raises(research_plan.AdmissionError) as info:  # research rounds still open
        research_plan.admit_fact_check(store, run['id'], {'kind': 'task_reserve', 'limits': SHARE})
    assert info.value.code == 'fact_check_round_open'
    research_plan.finish_round(store, run['id'])

    def patch(**changes):
        raw = json.loads(store.one('runs', run['id'])['requirements'])
        raw.update(changes)
        if changes.get('research_budget') is None:
            raw.pop('research_budget', None)  # emulate a legacy run without authorized budget
        with store.tx() as c:
            c.execute('UPDATE runs SET requirements=? WHERE id=?', (dump(raw), run['id']))

    patch(allow_web=False)  # offline runs never gain a web-based stage
    with pytest.raises(research_plan.AdmissionError) as info:
        research_plan.admit_fact_check(store, run['id'], {'kind': 'task_reserve', 'limits': SHARE})
    assert info.value.code == 'fact_check_offline'

    patch(allow_web=True, research_budget=None)  # legacy run without an authorized budget
    with pytest.raises(research_plan.AdmissionError) as info:
        research_plan.admit_fact_check(store, run['id'], {'kind': 'task_reserve', 'limits': SHARE})
    assert info.value.code == 'fact_check_no_budget'

    patch(research_budget={'search_requests': 30, 'candidate_urls': 150, 'source_pages': 60})
    over = {'search_requests': 31, 'candidate_urls': 150, 'source_pages': 60}
    with pytest.raises(research_plan.AdmissionError) as info:  # reserve cannot exceed what is left
        research_plan.admit_fact_check(store, run['id'], {'kind': 'task_reserve', 'limits': over})
    assert info.value.code == 'fact_check_share_exceeds_budget'
    # An explicit user grant is a valid source with the same KINDS shape.
    granted = research_plan.admit_fact_check(store, run['id'], {'kind': 'user_grant', 'limits': SHARE})
    assert granted['budget_source']['kind'] == 'user_grant'


def test_budget_exhaustion_closes_fact_check_as_handoff_not_failure(tmp_path):
    store = Store(tmp_path)
    run = _run(store, research_budget={'search_requests': 2, 'candidate_urls': 10, 'source_pages': 2})
    _closed_round(store, run)
    stage = research_plan.admit_fact_check(store, run['id'], {'kind': 'task_reserve', 'limits': SHARE})
    budget.reserve_search(store, run['id'], 1)
    budget.reserve_search(store, run['id'], 1)
    with pytest.raises(budget.BudgetExhausted) as spent:  # the meter refuses more, it does not fail the stage
        budget.reserve_search(store, run['id'], 1)
    assert spent.value.result['status'] == 'budget_exhausted'
    closed = research_plan.finish_fact_check(store, run['id'], status='budget_exhausted', summary='预算耗尽，保留已核证据交接')
    assert closed['idempotent'] is False and closed['outcome']['status'] == 'budget_exhausted'
    assert closed['outcome']['usage'] == {'search': 2, 'pages': 0}
    replay = research_plan.finish_fact_check(store, run['id'], status='failed')  # replay never rewrites the outcome
    assert replay['idempotent'] is True and replay['outcome']['status'] == 'budget_exhausted'
    assert research_plan.frozen(store, run['id'])['fact_check']['status'] == 'budget_exhausted'
    with pytest.raises(research_plan.AdmissionError) as blocked:  # the closed stage admits nothing new
        budget.reserve_search(store, run['id'], 1)
    assert blocked.value.code == 'fact_check_closed'


def test_cancelled_stage_rejects_new_and_late_requests(tmp_path):
    store = Store(tmp_path)
    run = _run(store, research_budget={'search_requests': 3, 'candidate_urls': 10, 'source_pages': 2})
    _closed_round(store, run)
    stage = research_plan.admit_fact_check(store, run['id'], {'kind': 'task_reserve', 'limits': SHARE})
    reserved = budget.reserve_search(store, run['id'], 1)  # reserved, network answer not back yet
    research_plan.finish_fact_check(store, run['id'], status='cancelled', summary='任务已停止')
    assert research_plan.settle_request(store, run['id'], reserved['request_id'], 'success') is False
    entry = research_plan.pending_requests(store, run['id'])[reserved['request_id']]
    assert entry['status'] == 'response_rejected' and entry['response_status'] == 'success'
    with pytest.raises(research_plan.AdmissionError) as blocked:
        budget.reserve_search(store, run['id'], 1)
    assert blocked.value.code == 'fact_check_closed'
