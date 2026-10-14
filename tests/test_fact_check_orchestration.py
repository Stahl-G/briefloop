"""Worker-orchestrated fact-check execution (plan Phase B trigger, D3 grants).

The check is a real product path: the Worker dispatches the fact_check job,
builds the fact-checker's task pack (its role paragraph comes from the frozen
workflow snapshot), runs an independent session turn, and the stage closes only
through an admitted result. A user grant is spendable meter budget, not a label.
"""
import json
import threading

import pytest

from briefloop import fact_check, research_budget as budget, research_plan
from briefloop.evidence import bind_claim, blocks, create_claim
from briefloop.store import Store

SHARE = {'search_requests': 2, 'candidate_urls': 10, 'source_pages': 2}
BUDGET = {'search_requests': 8, 'candidate_urls': 40, 'source_pages': 8}


def _world(tmp_path, folder='w', with_budget=True, budget=None):
    store = Store(tmp_path / folder)
    source = store.add_source('公告', '公司2026财年营业收入12.0百万美元。')
    requirements = {'title': 'R', 'objective': 'o', 'allow_web': True, 'fact_check': True}
    if budget is not None:
        requirements['research_budget'] = dict(budget)
    elif with_budget:
        requirements['research_budget'] = dict(BUDGET)
    run = store.create_run(requirements, [source['id']], research_protocol='quality_v1')
    research_plan.freeze(store, run['id'])
    return store, run, source


def _legacy_without_budget(store, run):
    """Emulate a legacy run record that predates authorized budgets."""
    from briefloop.store import dump
    raw = json.loads(store.one('runs', run['id'])['requirements'])
    raw.pop('research_budget', None)
    with store.tx() as c:
        c.execute('UPDATE runs SET requirements=? WHERE id=?', (dump(raw), run['id']))


def _claim_version(store, run):
    brief = store.publish(run['id'], {'title': 'R', 'editor_document': {'type': 'doc', 'content': [
        {'type': 'paragraph', 'content': [{'type': 'text', 'text': '公司2026财年营业收入12.0百万美元。'}]}]}})
    block_ids = list(blocks(json.loads(brief['editor_document'])))
    claim = create_claim(store, run['id'], {'statement': '公司2026财年营业收入12.0百万美元。', 'kind': 'fact'})
    bind_claim(store, brief['id'], claim['id'], block_ids[0], '12.0百万美元')
    return brief, claim, block_ids[0]


def _admitted(store, run, source_kind='task_reserve'):
    research_plan.finish_round(store, run['id'])
    return research_plan.admit_fact_check(store, run['id'], {'kind': source_kind, 'limits': dict(SHARE)})


def test_revised_candidates_get_own_stage_without_resetting_budget_or_reusing_old_completion(tmp_path):
    from briefloop.runtime import Worker
    from briefloop.draft_completion import binding, require_fact_check_settled
    store, run, _ = _world(tmp_path)
    brief, claim, _ = _claim_version(store, run)
    research_plan.finish_round(store, run['id'])
    parent = store.enqueue('assess', {'run_id': run['id'], 'version_id': brief['id'],
        'runtime': {'model': 'test'}, 'continuation_of': 'job_original', 'checks_input': binding(store, brief['id'])})
    worker = Worker(store)
    first = worker._admit_fact_check(parent, brief)
    old_job = store.rows("SELECT * FROM jobs WHERE kind='fact_check'")[0]
    reserved = budget.reserve_search(store, run['id'], 1)
    fact_check.submit_result(store, run['id'], {'version_id': brief['id'], 'stage_id': first['stage_id'],
        'selection': {'claim_ids': [claim['id']]}, 'execution': {'status': 'completed', 'summary': 'Checked fixture'},
        'candidates': [{'claim_id': claim['id'], 'status': 'insufficient_evidence', 'reason': 'No adequate public source',
            'span_ids': [], 'query_ids': [reserved['request_id']]}]}, job_id=old_job['id'])
    store.update_job(old_job['id'], 'complete')
    assert worker._admit_fact_check(parent, brief)['stage_id'] == first['stage_id']
    assert len(store.rows("SELECT id FROM jobs WHERE kind='fact_check'")) == 1
    document = json.loads(brief['editor_document'])
    document['content'].append({'type': 'paragraph', 'content': [{'type': 'text', 'text': 'New public result is 18.'}]})
    revised = store.revise(brief['id'], editor_document=document)
    new_claim = create_claim(store, run['id'], {'statement': 'New public result is 18.', 'kind': 'fact'})
    new_block = list(blocks(json.loads(revised['editor_document'])))[-1]
    bind_claim(store, revised['id'], new_claim['id'], new_block, 'New public result is 18.')
    continuation = store.enqueue('assess', {'run_id': run['id'], 'version_id': revised['id'],
        'runtime': {'model': 'test'}, 'continuation_of': 'job_original', 'checks_input': binding(store, revised['id'])})
    with pytest.raises(ValueError, match='本版本候选核查尚未完成'):
        require_fact_check_settled(store, continuation)
    before = budget.snapshot(store, run['id'])
    second = worker._admit_fact_check(continuation, revised)
    assert second['status'] == 'active' and second['stage_id'] != first['stage_id']
    plan = research_plan.frozen(store, run['id'])
    assert plan['fact_check_history'][0]['stage_id'] == first['stage_id']
    assert plan['fact_check']['scope']['version_id'] == revised['id']
    assert new_claim['id'] in plan['fact_check']['scope']['claim_ids']
    after = budget.snapshot(store, run['id'])
    assert after['limits'] == before['limits'] and after['used'] == before['used']
    assert len(store.rows("SELECT id FROM jobs WHERE kind='fact_check'")) == 2
    with pytest.raises(ValueError, match='核查阶段或版本候选集合已变化'):
        worker.run_fact_check(old_job, runtime=_Quiet())
    with pytest.raises(research_plan.AdmissionError, match='旧任务不能关闭'):
        research_plan.finish_fact_check(store, run['id'], status='cancelled', stage_id=first['stage_id'])
    assert research_plan.frozen(store, run['id'])['fact_check']['status'] == 'active'


class _Quiet:
    """A host turn that finishes without submitting anything."""
    cancelled = threading.Event()

    def execute(self, job, prompt, folder, on_tick=lambda: None, *, resume_on_complete=False):
        return {}


def test_user_grant_is_spendable_budget_not_a_label(tmp_path):
    store, run, source = _world(tmp_path, budget={'search_requests': 2, 'candidate_urls': 10, 'source_pages': 2})
    budget.reserve_search(store, run['id'], 1)
    budget.reserve_search(store, run['id'], 1)  # research spends the whole task budget
    with pytest.raises(budget.BudgetExhausted):  # the task meter is exhausted while the round is open
        budget.reserve_search(store, run['id'], 1)
    research_plan.finish_round(store, run['id'])
    research_plan.admit_fact_check(store, run['id'], {'kind': 'user_grant', 'limits': SHARE})
    view = budget.snapshot(store, run['id'])
    assert view['limits']['search_requests'] == 4  # task 2 + granted 2 while the stage runs
    checked = budget.reserve_search(store, run['id'], 1)  # the granted amount is really spendable
    assert checked['stage'] == 'fact_check'
    research_plan.finish_fact_check(store, run['id'], status='budget_exhausted', summary='核查额度用完')
    assert budget.snapshot(store, run['id'])['limits']['search_requests'] == 2  # additions lapse with the stage


def test_grant_is_the_only_ceiling_for_runs_without_authorized_budget(tmp_path):
    store, run, source = _world(tmp_path, with_budget=False)
    _legacy_without_budget(store, run)
    research_plan.finish_round(store, run['id'])
    with pytest.raises(research_plan.AdmissionError) as info:
        research_plan.admit_fact_check(store, run['id'], {'kind': 'task_reserve', 'limits': SHARE})
    assert info.value.code == 'fact_check_no_budget'
    research_plan.admit_fact_check(store, run['id'], {'kind': 'user_grant', 'limits': SHARE})
    assert budget.snapshot(store, run['id'])['limits'] == dict(SHARE)
    budget.reserve_search(store, run['id'], 1)
    budget.reserve_search(store, run['id'], 1)
    with pytest.raises(budget.BudgetExhausted):
        budget.reserve_search(store, run['id'], 1)  # the grant itself is the hard ceiling


def test_grant_paths_pending_active_reopened_refused(tmp_path, monkeypatch):
    store, run, source = _world(tmp_path)
    pending = research_plan.add_fact_check_grant(store, run['id'], SHARE)  # research round still open
    assert pending['status'] == 'pending'
    assert research_plan.pending_fact_check_grant(store, run['id'])['limits'] == dict(SHARE)
    research_plan.finish_round(store, run['id'])
    admitted = research_plan.add_fact_check_grant(store, run['id'], {'search_requests': 1, 'candidate_urls': 5, 'source_pages': 1})
    assert admitted['status'] == 'admitted'
    plan = research_plan.frozen(store, run['id'])
    assert plan['fact_check']['budget_source'] == {'kind': 'user_grant',
                                                  'limits': {'search_requests': 3, 'candidate_urls': 15, 'source_pages': 3}}
    assert research_plan.pending_fact_check_grant(store, run['id']) is None
    active = research_plan.add_fact_check_grant(store, run['id'], SHARE)  # top up a running stage
    assert active['status'] == 'active'
    plan = research_plan.frozen(store, run['id'])
    assert [grant['limits'] for grant in plan['fact_check']['grants']] == [dict(SHARE)]
    assert budget.snapshot(store, run['id'])['limits']['search_requests'] == BUDGET['search_requests'] + 2 + 1 + 2
    exhausted = plan['fact_check']['stage_id']
    research_plan.finish_fact_check(store, run['id'], status='budget_exhausted', summary='额度用完')
    reopened = research_plan.add_fact_check_grant(store, run['id'], SHARE)
    assert reopened['status'] == 'reopened'
    plan = research_plan.frozen(store, run['id'])
    assert plan['fact_check']['status'] == 'active'
    assert plan['fact_check_history'][0]['stage_id'] == exhausted  # the old stage stays auditable
    assert budget.snapshot(store, run['id'])['limits']['search_requests'] == BUDGET['search_requests'] + 2
    research_plan.finish_fact_check(store, run['id'], status='completed', summary='done')
    with pytest.raises(research_plan.AdmissionError) as info:  # completed stages never reopen via money
        research_plan.add_fact_check_grant(store, run['id'], SHARE)
    assert info.value.code == 'fact_check_closed'
    # The new automatic candidate lane rechecks public scope at both boundaries.
    from briefloop import sources
    from briefloop.runtime import Worker
    monkeypatch.setattr(sources,'_fetch_bytes',lambda *a,**k: (b'Public revenue: 12.', 'text/plain', 'utf-8'))
    monkeypatch.setattr(sources,'_checked_addresses',lambda *a: ['1.1.1.1'])
    store=Store(tmp_path/'automatic')
    source=sources.fetch(store,'https://www.python.org/release/')
    run=store.create_run({'title':'R','objective':'o','allow_web':True,'fact_check':True,
                         'completion_mode':'direct','research_budget':BUDGET},[source['id']],research_protocol='quality_v1')
    research_plan.freeze(store,run['id'])
    brief,claim,_=_claim_version(store,run)
    store.event(None,'fact_check_candidates',{'version_id':brief['id'],'registered':[claim['id']]})
    research_plan.finish_round(store,run['id'])
    parent=store.enqueue('assess',{'version_id':brief['id'],'runtime':{'model':'test'}})
    worker=Worker(store)
    assert worker._admit_fact_check(parent,brief)['status']=='active'
    child=store.rows("SELECT * FROM jobs WHERE kind='fact_check'")[0]
    assert json.loads(child['payload'])['automatic_candidates_version_id']==brief['id']
    private=store.add_source('Internal input','Unpublished revenue 99.')
    store.attach_source(run['id'],private['id'])
    # _Quiet would raise for an unsettled turn if any model transport were called.
    result=worker.run_fact_check(child,runtime=_Quiet())
    assert result['execution']=='failed'
    assert research_plan.frozen(store,run['id'])['fact_check']['status']=='failed'
    assert fact_check.view(store,brief['id'])['not_checked_reason']
    assert worker._admit_fact_check(parent,brief) is None
    assert len(store.rows("SELECT id FROM jobs WHERE kind='fact_check'"))==1
    changed=store.publish(run['id'],{'title':'R','editor_document':{'type':'doc','content':[
        {'type':'paragraph','content':[{'type':'text','text':'Entirely different report.'}]}]}},parent_id=brief['id'])
    assert worker._admit_fact_check(parent,changed) is None
    assert not fact_check.eligible_bindings(store,changed['id'])
    from briefloop.draft_completion import require_fact_check_settled
    continuation=store.enqueue('assess',{'run_id':run['id'],'version_id':changed['id'],
                                        'continuation_of':parent['id'],'runtime':{'model':'test'}})
    require_fact_check_settled(store,continuation)  # Old run claims do not block this version.
    valid=store.enqueue('assess',{'run_id':run['id'],'version_id':brief['id'],
                                 'continuation_of':parent['id'],'runtime':{'model':'test'}})
    with pytest.raises(ValueError):
        require_fact_check_settled(store,valid)  # Private-scope rejection is not a completed check.


def test_reopened_grant_retains_consumed_credit_without_carrying_unused_budget(tmp_path):
    base = {'search_requests': 2, 'candidate_urls': 4, 'source_pages': 2}
    grant = {'search_requests': 2, 'candidate_urls': 3, 'source_pages': 2}
    store, run, _ = _world(tmp_path, budget=base)
    original_requirements = store.one('runs', run['id'])['requirements']
    urls = lambda prefix, count: [f'https://example.test/{prefix}/{index}' for index in range(count)]
    for _ in range(2):reserved = budget.reserve_search(store, run['id'], 1)
    budget.record_candidates(store, run['id'], urls('research', 4), reservation=reserved)
    budget.reserve_pages(store, run['id'], urls('research', 2))
    research_plan.finish_round(store, run['id'])
    research_plan.add_fact_check_grant(store, run['id'], grant)
    for _ in range(2):reserved = budget.reserve_search(store, run['id'], 1)
    budget.record_candidates(store, run['id'], urls('first-check', 1), reservation=reserved)
    budget.reserve_pages(store, run['id'], urls('first-check', 1))
    research_plan.finish_fact_check(store, run['id'], status='budget_exhausted')
    used = budget.spent(store, run['id'])
    assert used == {'search_requests': 4, 'candidate_urls': 5, 'source_pages': 3}

    # Closing the check does not donate its unused 2 candidates / 1 page to
    # later research. Original requirements and cumulative charges stay fixed.
    research_plan.begin_round(store, run['id'])
    with pytest.raises(budget.BudgetExhausted):budget.reserve_search(store, run['id'], 1)
    with pytest.raises(research_plan.AdmissionError):budget.record_candidates(store, run['id'], urls('later-research', 1))
    with pytest.raises(budget.BudgetExhausted):budget.reserve_pages(store, run['id'], urls('later-research', 1))
    research_plan.finish_round(store, run['id'])
    research_plan.add_fact_check_grant(store, run['id'], grant)
    view = budget.snapshot(store, run['id'])
    assert view['limits'] == {key: used[key] + grant[key] for key in base}
    assert budget.spent(store, run['id']) == used
    for _ in range(2):reserved = budget.reserve_search(store, run['id'], 1)
    candidates = budget.record_candidates(store, run['id'], urls('second-check', 4), reservation=reserved)
    assert len(candidates['allowed_urls']) == 3 and len(candidates['unadmitted_urls']) == 1
    budget.reserve_pages(store, run['id'], urls('second-check', 2))
    with pytest.raises(budget.BudgetExhausted):budget.reserve_search(store, run['id'], 1)
    with pytest.raises(budget.BudgetExhausted):budget.reserve_pages(store, run['id'], urls('second-check-extra', 1))
    assert budget.spent(store, run['id']) == {key: used[key] + grant[key] for key in base}
    research_plan.finish_fact_check(store, run['id'], status='budget_exhausted')
    research_plan.add_fact_check_grant(store, run['id'], grant)
    assert budget.snapshot(store, run['id'])['limits'] == {key: used[key] + 2 * grant[key] for key in base}
    assert store.one('runs', run['id'])['requirements'] == original_requirements
    plan = research_plan.frozen(store, run['id'])
    assert len(plan['fact_check_history']) == 2 and plan['budget'] == base


def test_pending_grant_receipt_does_not_add_again_after_the_stage_consumes_it(tmp_path):
    store, run, _ = _world(tmp_path)
    brief, _, _ = _claim_version(store, run)
    request = {'request_id': 'pending-click'}
    first = fact_check.grant(store, brief['id'], SHARE, **request)
    assert first['status'] == 'pending' and 'job_id' not in first
    research_plan.finish_round(store, run['id'])
    research_plan.admit_fact_check(store, run['id'], {'kind': 'user_grant', 'limits': SHARE})
    before = research_plan.frozen(store, run['id'])
    assert fact_check.grant(store, brief['id'], SHARE, **request) == {**first, 'replayed': True}
    assert research_plan.frozen(store, run['id']) == before
    assert len(before['fact_check']['initial_grants']) == 1
    assert research_plan.pending_fact_check_grant(store, run['id']) is None
    assert store.rows("SELECT id FROM jobs WHERE kind='fact_check'") == []
