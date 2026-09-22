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
from briefloop.document_workflows import ROLE_KEYS, workflow_context
from briefloop.evidence import bind_claim, blocks, create_claim, create_span
from briefloop.runtime import Worker
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


class _Quiet:
    """A host turn that finishes without submitting anything."""
    cancelled = threading.Event()

    def execute(self, job, prompt, folder, on_tick=lambda: None, *, resume_on_complete=False):
        return {}


def test_fact_checker_role_is_mapped_and_reaches_the_prompt(tmp_path):
    store, run, source = _world(tmp_path)
    stage = _admitted(store, run)
    brief, claim, _ = _claim_version(store, run)
    job = store.enqueue('fact_check', {'run_id': run['id'], 'version_id': brief['id']})
    folder = store.root / 'jobs' / job['id']
    folder.mkdir(parents=True)
    prompt = fact_check.fact_check_prompt(store, job, brief, folder)
    # The evaluation.md fact-checker paragraph has a role mapping and a consumer.
    assert ROLE_KEYS['fact-checker'] == 'evaluation'
    snapshot = json.loads(store.one('runs', run['id'])['requirements'])['workflow_snapshot']
    assert workflow_context(snapshot, 'fact-checker') in prompt
    assert '事实核查员' in prompt and 'fact-status' in prompt
    pack = json.loads((folder / 'input.json').read_text(encoding='utf-8-sig'))
    assert pack['stage_id'] == stage['stage_id']
    assert [item['claim_id'] for item in pack['claims']] == [claim['id']]  # bound claims only
    assert pack['budget']['stages']['fact_check']['search_requests'] == 0


def test_worker_runs_fact_check_job_and_closes_stage(tmp_path):
    store, run, source = _world(tmp_path)
    stage = _admitted(store, run)
    brief, claim, _ = _claim_version(store, run)
    span = create_span(store, {'source_id': source['id'],
                               'locator': {'kind': 'text', 'start_line': 1, 'end_line': 1}})['id']
    query = budget.reserve_search(store, run['id'], 3)['request_id']

    class Runtime:
        cancelled = threading.Event()

        def execute(self, job, prompt, folder, on_tick=lambda: None, *, resume_on_complete=False):
            payload = {'version_id': brief['id'], 'stage_id': stage['stage_id'],
                       'selection': {'claim_ids': [claim['id']], 'unselected': []},
                       'candidates': [{'claim_id': claim['id'], 'status': 'supported_for_scope',
                                       'reason': '公告披露同一金额', 'span_ids': [span],
                                       'query_ids': [query]}],
                       'execution': {'status': 'completed', 'summary': '1条主张核查完成'}}
            (folder / 'result.json').write_text(json.dumps(payload, ensure_ascii=False))
            fact_check.submit_result(store, run['id'], payload, job_id=job['id'])
            return {}

    queued = store.enqueue('fact_check', {'run_id': run['id'], 'version_id': brief['id']})
    outcome = Worker(store, Runtime()).run_fact_check(store.one('jobs', queued['id']))
    assert outcome['execution'] == 'completed' and outcome['candidates'] == 1
    assert research_plan.frozen(store, run['id'])['fact_check']['status'] == 'completed'
    record = fact_check.records_for(store, run['id'])[0]
    assert record['data']['snapshot']['model']  # recorded from the check job's frozen config
    assert record['data']['stage_id'] == stage['stage_id']


def test_turn_without_submission_fails_the_job_and_keeps_the_stage_retryable(tmp_path):
    store, run, source = _world(tmp_path)
    _admitted(store, run)
    brief, _, _ = _claim_version(store, run)
    job = store.enqueue('fact_check', {'run_id': run['id'], 'version_id': brief['id']})
    with pytest.raises(ValueError, match='未提交结果'):
        Worker(store, _Quiet()).run_fact_check(store.one('jobs', job['id']))
    # A turn with no admitted result is an honest job failure; the stage stays
    # active so the resumed job retries instead of an empty check standing.
    stage = research_plan.frozen(store, run['id'])['fact_check']
    assert stage['status'] == 'active' and stage['outcome'] is None
    retry = Worker(store, _Quiet())
    with pytest.raises(ValueError, match='未提交结果'):
        retry.run_fact_check(store.one('jobs', job['id']))


def test_cancelled_turn_closes_the_stage_cancelled(tmp_path):
    store, run, source = _world(tmp_path)
    _admitted(store, run)
    brief, _, _ = _claim_version(store, run)

    class Runtime:
        cancelled = threading.Event()

        def execute(self, job, prompt, folder, on_tick=lambda: None, *, resume_on_complete=False):
            raise InterruptedError('任务已停止')

    job = store.enqueue('fact_check', {'run_id': run['id'], 'version_id': brief['id']})
    with pytest.raises(InterruptedError):
        Worker(store, Runtime()).run_fact_check(store.one('jobs', job['id']))
    assert research_plan.frozen(store, run['id'])['fact_check']['status'] == 'cancelled'


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


def test_grant_paths_pending_active_reopened_refused(tmp_path):
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


def test_parallel_grants_survive_stale_admission_and_replayed_consumption(tmp_path):
    from concurrent.futures import ThreadPoolExecutor

    store, run, _ = _world(tmp_path)
    research_plan.add_fact_check_grant(store, run['id'], SHARE)
    stale = research_plan.pending_fact_check_grant(store, run['id'])
    # A persisted pre-ledger pending grant must also survive the upgrade.
    store.set_meta('fact_check_grant:' + run['id'], {'limits': stale['limits'], 'created': stale['created']})
    with ThreadPoolExecutor(max_workers=4) as pool:
        pending = list(pool.map(lambda _: research_plan.add_fact_check_grant(store, run['id'], SHARE), range(4)))
    assert all(item['status'] == 'pending' for item in pending)
    assert research_plan.pending_fact_check_grant(store, run['id'])['limits'] == {key: 5 * value for key, value in SHARE.items()}
    research_plan.finish_round(store, run['id'])
    barrier = threading.Barrier(2)

    def admit():
        barrier.wait()
        return research_plan.admit_fact_check(store, run['id'], {'kind': 'user_grant', 'limits': stale['limits']})

    def add():
        barrier.wait()
        return research_plan.add_fact_check_grant(store, run['id'], SHARE)

    with ThreadPoolExecutor(max_workers=2) as pool:
        admitted, addition = pool.submit(admit), pool.submit(add)
        stage_id = admitted.result()['stage_id']
        assert addition.result()['stage_id'] == stage_id
    assert research_plan.pending_fact_check_grant(store, run['id']) is None
    # The internal retry is idempotent; the public add API has no request_id,
    # so each call above intentionally contributes another authorized amount.
    before_retry = research_plan.frozen(store, run['id'])
    assert research_plan.admit_fact_check(store, run['id'], {'kind': 'user_grant', 'limits': stale['limits']})['idempotent']
    assert research_plan.consume_pending_fact_check_grant(store, run['id'], stage_id)
    assert research_plan.consume_pending_fact_check_grant(store, run['id'], stage_id)
    assert research_plan.frozen(store, run['id']) == before_retry
    with ThreadPoolExecutor(max_workers=4) as pool:
        active = list(pool.map(lambda _: research_plan.add_fact_check_grant(store, run['id'], SHARE), range(4)))
    assert all(item['status'] == 'active' for item in active)
    stage = research_plan.frozen(store, run['id'])['fact_check']
    records = stage['initial_grants'] + stage.get('grants', [])
    assert len(records) == 10
    assert len({item['id'] for item in records if 'id' in item}) == 9  # one legacy record
    assert budget.snapshot(store, run['id'])['limits'] == {key: BUDGET[key] + 10 * SHARE[key] for key in SHARE}
    assert budget.spent(store, run['id']) == {key: 0 for key in SHARE}


def test_legacy_research_usage_does_not_consume_new_explicit_grants(tmp_path):
    store, run, _ = _world(tmp_path, with_budget=False)
    _legacy_without_budget(store, run)
    for _ in range(3):budget.reserve_search(store, run['id'], 1)
    research_plan.finish_round(store, run['id'])
    for _ in range(2):
        previously_spent = budget.spent(store, run['id'])['search_requests']
        research_plan.add_fact_check_grant(store, run['id'], SHARE)
        assert budget.snapshot(store, run['id'])['limits']['search_requests'] == previously_spent + SHARE['search_requests']
        for _ in range(2):budget.reserve_search(store, run['id'], 1)
        with pytest.raises(budget.BudgetExhausted):budget.reserve_search(store, run['id'], 1)
        research_plan.finish_fact_check(store, run['id'], status='budget_exhausted')
    assert budget.spent(store, run['id'])['search_requests'] == 7
    assert 'research_budget' not in json.loads(store.one('runs', run['id'])['requirements'])


def test_upgrade_restores_unspent_old_active_grant_before_another_addition(tmp_path):
    unit = {key: 1 for key in SHARE}
    store, run, _ = _world(tmp_path, budget=unit)
    original_requirements = store.one('runs', run['id'])['requirements']

    def spend(label):
        reserved = budget.reserve_search(store, run['id'], 1)
        budget.record_candidates(store, run['id'], [f'https://example.test/{label}'], reservation=reserved)
        budget.reserve_pages(store, run['id'], [f'https://example.test/{label}'])
        return reserved

    spend('research')
    research_plan.finish_round(store, run['id'])
    research_plan.add_fact_check_grant(store, run['id'], unit)
    spend('first-check')
    research_plan.finish_fact_check(store, run['id'], status='budget_exhausted')
    research_plan.add_fact_check_grant(store, run['id'], unit)
    plan = research_plan.frozen(store, run['id'])
    for stage in plan['fact_check_history'] + [plan['fact_check']]:
        stage.pop('budget_offset', None)
        stage.pop('initial_grants', None)
    store.set_meta('research_plan:' + run['id'], plan)  # persisted pre-upgrade stage
    assert budget.snapshot(store, run['id'])['limits'] == {key: 3 for key in unit}
    assert research_plan.frozen(store, run['id']) == plan  # reads never migrate SQLite
    research_plan.add_fact_check_grant(store, run['id'], unit)
    assert budget.snapshot(store, run['id'])['limits'] == {key: 4 for key in unit}
    assert budget.spent(store, run['id']) == {key: 2 for key in unit}
    spend('after-upgrade-1')
    reserved = spend('after-upgrade-2')
    with pytest.raises(budget.BudgetExhausted):budget.reserve_search(store, run['id'], 1)
    assert budget.record_candidates(store, run['id'], ['https://example.test/extra'], reservation=reserved)['allowed_urls'] == []
    with pytest.raises(budget.BudgetExhausted):budget.reserve_pages(store, run['id'], ['https://example.test/extra'])
    assert budget.snapshot(store, run['id'])['limits'] == {key: 4 for key in unit}
    assert store.one('runs', run['id'])['requirements'] == original_requirements


def test_upgrade_excludes_current_stage_spending_and_freezes_recovered_offset(tmp_path):
    store, run, _ = _world(tmp_path, budget={'search_requests': 1, 'candidate_urls': 20, 'source_pages': 10})
    grant = {'search_requests': 3, 'candidate_urls': 0, 'source_pages': 0}
    budget.reserve_search(store, run['id'], 1)
    research_plan.finish_round(store, run['id'])
    research_plan.add_fact_check_grant(store, run['id'], grant)
    budget.reserve_search(store, run['id'], 1)  # only 1 of the old grant's 3 was used
    research_plan.finish_fact_check(store, run['id'], status='budget_exhausted')
    research_plan.add_fact_check_grant(store, run['id'], grant)
    for _ in range(2):budget.reserve_search(store, run['id'], 1)  # valid under the old ceiling of 4
    plan = research_plan.frozen(store, run['id'])
    for stage in plan['fact_check_history'] + [plan['fact_check']]:
        stage.pop('budget_offset', None)
    store.set_meta('research_plan:' + run['id'], plan)
    # Counting current used-base as historical credit would grant 3 instead of 1.
    assert budget.snapshot(store, run['id'])['limits']['search_requests'] == 5
    budget.reserve_search(store, run['id'], 1)  # the first writer freezes the recovered 1
    research_plan.add_fact_check_grant(store, run['id'], {**grant, 'search_requests': 1})
    assert budget.snapshot(store, run['id'])['limits']['search_requests'] == 6
    budget.reserve_search(store, run['id'], 1)
    with pytest.raises(budget.BudgetExhausted):budget.reserve_search(store, run['id'], 1)
    assert budget.spent(store, run['id'])['search_requests'] == 6
    assert budget.snapshot(store, run['id'])['limits']['search_requests'] == 6


def test_grant_entry_reopens_exhausted_stage_and_reschedules_the_check(tmp_path):
    store, run, source = _world(tmp_path)
    _admitted(store, run)
    brief, _, _ = _claim_version(store, run)
    research_plan.finish_fact_check(store, run['id'], status='budget_exhausted', summary='额度用完')
    outcome = fact_check.grant(store, brief['id'], SHARE)
    assert outcome['status'] == 'reopened' and outcome['job_id']
    job = store.one('jobs', outcome['job_id'])
    payload = json.loads(job['payload'])
    assert job['kind'] == 'fact_check' and job['status'] == 'queued'
    assert payload['run_id'] == run['id'] and payload['version_id'] == brief['id']
    assert payload['runtime']['model']  # the continued check runs on a frozen model config
    assert research_plan.frozen(store, run['id'])['fact_check']['status'] == 'active'


def test_worker_prefers_a_pending_user_grant_when_admitting(tmp_path):
    store, run, source = _world(tmp_path, folder='grant')
    research_plan.add_fact_check_grant(store, run['id'], SHARE)  # granted mid-research → pending

    class LocalRuntime:
        cancelled = threading.Event()

        def execute(self, job, prompt, folder, on_tick):
            research_plan.finish_round(store, run['id'])
            create_claim(store, run['id'], {'statement': 'Revenue was USD 12 million.', 'kind': 'fact'})
            (folder / 'draft.json').write_text(json.dumps(
                {'title': 'R', 'markdown': 'Revenue was USD 12 million.'}))
            return {}

    worker = Worker(store, LocalRuntime())
    job = store.one('jobs', store.enqueue('generate', {'run_id': run['id']})['id'])
    worker.generate(job, score=False)
    plan = research_plan.frozen(store, run['id'])
    assert plan['fact_check']['budget_source'] == {'kind': 'user_grant', 'limits': dict(SHARE)}
    assert research_plan.pending_fact_check_grant(store, run['id']) is None  # consumed by admission
    assert store.rows("SELECT id FROM jobs WHERE kind='fact_check' AND status='queued'")
    assert budget.snapshot(store, run['id'])['limits']['search_requests'] == BUDGET['search_requests'] + 2


def test_grant_is_reachable_through_the_product_http_api(tmp_path):
    import http.client
    from briefloop.server import make_server
    server = make_server(tmp_path / 'ws', port=0, paused=True)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        store = server.store
        source = store.add_source('公告', '公司2026财年营业收入12.0百万美元。')
        run = store.create_run({'title': 'R', 'objective': 'o', 'allow_web': True, 'fact_check': True,
                                'research_budget': dict(BUDGET)}, [source['id']], research_protocol='quality_v1')
        research_plan.freeze(store, run['id'])
        research_plan.finish_round(store, run['id'])
        research_plan.admit_fact_check(store, run['id'], {'kind': 'task_reserve', 'limits': dict(SHARE)})
        brief = store.publish(run['id'], {'title': 'R', 'editor_document': {'type': 'doc', 'content': [
            {'type': 'paragraph', 'content': [{'type': 'text', 'text': '占位'}]}]}})
        research_plan.finish_fact_check(store, run['id'], status='budget_exhausted', summary='额度用完')

        connection = http.client.HTTPConnection('127.0.0.1', server.server_port)
        connection.request('GET', '/api/session')
        token = json.loads(connection.getresponse().read())['token']

        def post(path, body):
            connection.request('POST', path, body=json.dumps(body), headers={
                'X-BriefLoop-Token': token, 'Origin': f'http://127.0.0.1:{server.server_port}'})
            response = connection.getresponse()
            return response.status, json.loads(response.read())

        status, outcome = post('/api/fact-check-grant', {'version_id': brief['id'], 'limits': SHARE})
        assert status == 200 and outcome['status'] == 'reopened'
        job = store.one('jobs', outcome['job_id'])
        assert job['kind'] == 'fact_check' and json.loads(job['payload'])['version_id'] == brief['id']
        assert research_plan.frozen(store, run['id'])['fact_check']['status'] == 'active'
        # The panel view carries the stage state and the grant record for the UI.
        view = fact_check.view(store, brief['id'])
        assert view['enabled'] is True and view['stage']['status'] == 'active'
        assert view['stage']['budget_source']['kind'] == 'user_grant'
        # Malformed limits are a structured refusal, not a crash.
        status, refused = post('/api/fact-check-grant', {'version_id': brief['id'], 'limits': {'search_requests': -1}})
        assert status == 400 and refused.get('code') == 'fact_check_budget_source'
        connection.close()
    finally:
        server.shutdown(); thread.join()
        server.harness.close(); server.opencode_harness.close()
        server.server_close(); server.workspace_lock.close()

@pytest.mark.parametrize('check_fails', [False, True])
def test_generation_waits_for_check_before_review_and_revision(tmp_path, monkeypatch, check_fails):
    """Exercise both real queue lanes: a late check must not miss the first review."""
    import time
    store, run, source = _world(tmp_path)
    order = []

    class Runtime:
        def __init__(self):self.cancelled = threading.Event()
        def cancel(self):self.cancelled.set()
        def execute(self, job, prompt, folder, on_tick=lambda: None, **kwargs):
            if job['kind'] == 'fact_check':
                order.append('check')
                if check_fails:raise RuntimeError('synthetic provider failure')
                pack = json.loads((folder / 'input.json').read_text())
                claim = pack['claims'][0]['claim_id']
                span = create_span(store, {'source_id': source['id'],
                    'locator': {'kind': 'text', 'start_line': 1, 'end_line': 1}})['id']
                query = budget.reserve_search(store, run['id'], 1)['request_id']
                fact_check.submit_result(store, run['id'], {
                    'version_id': pack['brief']['version_id'], 'stage_id': pack['stage_id'],
                    'selection': {'claim_ids': [claim], 'unselected': []},
                    'candidates': [{'claim_id': claim, 'status': 'supported_for_scope',
                        'reason': '同一公告同一期间', 'span_ids': [span], 'query_ids': [query]}],
                    'execution': {'status': 'completed', 'summary': 'done'}}, job_id=job['id'])
            else:
                order.append('draft')
                (folder / 'draft.json').write_text(json.dumps({'title': 'R', 'markdown': '公司2026财年营业收入12.0百万美元。'}))
                on_tick()
                brief = store.rows('SELECT * FROM briefs WHERE run_id=?', (run['id'],))[0]
                claim = create_claim(store, run['id'], {'statement': '公司2026财年营业收入12.0百万美元。', 'kind': 'fact'})
                block = next(iter(blocks(json.loads(brief['editor_document']))))
                bind_claim(store, brief['id'], claim['id'], block, '12.0百万美元')
                research_plan.finish_round(store, run['id'])
            return {}

    worker = Worker(store, Runtime(), report_runtime_factory=Runtime)
    worker._review_runtime_factory = Runtime
    def assess(job, brief, folder, backend):
        order.append('review')
        child = store.rows("SELECT * FROM jobs WHERE kind='fact_check'")[0]
        assert child['status'] == ('failed' if check_fails else 'complete')
        assert bool(fact_check.records_for(store, run['id'])) is not check_fails
        return {}
    def revise(job, brief, folder):
        order.append('revision')
        return {}
    monkeypatch.setattr(worker, 'assess_version', assess)
    monkeypatch.setattr(worker, 'auto_revise', revise)
    job = store.enqueue('generate', {'run_id': run['id'], 'auto_revision': True})
    worker.start()
    try:
        until = time.monotonic() + 10
        while time.monotonic() < until:
            saved = store.one('jobs', job['id'])
            if saved['status'] not in ('queued', 'running'):break
            time.sleep(.05)
        assert saved['status'] == 'complete', saved['error']
        assert order == ['draft', 'check', 'review', 'revision']
    finally:
        worker.close()


def test_check_uses_remaining_pages_after_research(tmp_path):
    store, run, source = _world(tmp_path, budget={'search_requests': 10, 'candidate_urls': 40, 'source_pages': 10})
    reservation = budget.reserve_pages(store, run['id'], ['https://example.com/'+str(i) for i in range(4)])
    research_plan.settle_request(store, run['id'], reservation['request_id'], status='completed')
    research_plan.finish_round(store, run['id'])
    brief, _, _ = _claim_version(store, run)
    parent = store.enqueue('generate', {'run_id': run['id']})
    Worker(store, _Quiet())._admit_fact_check(parent, brief)
    stage = research_plan.frozen(store, run['id'])['fact_check']
    assert stage['status'] == 'active'
    assert stage['budget_source']['limits']['source_pages'] == 6
    assert len(store.rows("SELECT id FROM jobs WHERE kind='fact_check' AND status='queued'")) == 1
