import pytest
from briefloop import research_budget as budget
from briefloop import research_plan
from briefloop.store import Store


def quality_run(tmp_path, *, quality=True, values=None):
    store = Store(tmp_path)
    req = {'title': 'Report', 'objective': 'o', 'allow_web': True}
    if values:
        req['research_budget'] = values
    options = {'research_protocol': 'quality_v1'} if quality else {}
    return store, store.create_run(req, [], **options)


def test_quality_run_requires_freeze_before_any_controlled_request(tmp_path):
    store, run = quality_run(tmp_path)
    assert research_plan.is_quality(store, run['id'])
    with pytest.raises(research_plan.AdmissionError, match='尚未冻结'):
        budget.reserve_search(store, run['id'], 5)
    with pytest.raises(research_plan.AdmissionError, match='尚未冻结'):
        budget.reserve_pages(store, run['id'], ['https://example.test/a'])
    plan = research_plan.freeze(store, run['id'])
    assert plan['budget'] == {'search_requests': 30, 'candidate_urls': 150, 'source_pages': 60}
    reserved = budget.reserve_search(store, run['id'], 5)
    assert reserved['round_id'] == plan['current_round_id']
    entry = research_plan.pending_requests(store, run['id'])[reserved['request_id']]
    assert entry == {'operation': 'search', 'round_id': plan['current_round_id'], 'stage': 'research',
                     'status': 'reserved', 'created': entry['created']}


def test_freeze_is_idempotent_and_refuses_a_different_plan(tmp_path):
    store, run = quality_run(tmp_path)
    first = research_plan.freeze(store, run['id'])
    again = research_plan.freeze(store, run['id'])
    assert again['plan_fingerprint'] == first['plan_fingerprint']
    with pytest.raises(ValueError, match='不同的研究计划'):
        research_plan.freeze(store, run['id'], preset='quick')


def test_freeze_cannot_exceed_the_authorized_budget(tmp_path):
    store, run = quality_run(tmp_path, values={'search_requests': 4, 'candidate_urls': 20, 'source_pages': 6})
    plan = research_plan.freeze(store, run['id'], structure={'breadth': 6, 'depth': 2})
    assert plan['budget']['search_requests'] == 4
    assert plan['structure'] == {'breadth': 6, 'depth': 2, 'parallel': 2}
    for _ in range(4):
        budget.reserve_search(store, run['id'], 1)
    with pytest.raises(budget.BudgetExhausted):
        budget.reserve_search(store, run['id'], 1)


def test_deep_preset_freeze_expands_depth_rounds_and_breadth_slots(tmp_path):
    preset = research_plan.PRESETS['deep']
    store, run = quality_run(tmp_path, values={field: preset[field] for field in research_plan.BUDGET_FIELDS})
    plan = research_plan.freeze(store, run['id'], preset='deep')
    rounds = sorted(plan['rounds'].values(), key=lambda info: info['index'])
    assert [info['index'] for info in rounds] == list(range(1, preset['depth'] + 1))
    assert plan['rounds'][plan['current_round_id']]['index'] == 1
    assert rounds[0]['status'] == 'active'
    assert all(info['status'] == 'pending' for info in rounds[1:])
    for info in rounds:
        assert [task['slot_id'] for task in info['tasks']] == [f'scout-{n}' for n in range(1, preset['breadth'] + 1)]
        assert all(task['directory'] == str(store.root / 'research' / run['id'] / 'rounds' / str(info['index']) / task['slot_id'])
                   for task in info['tasks'])
    # Rounds advance by activating the pre-created plan, never by appending beyond it.
    research_plan.finish_round(store, run['id'], gaps=[{'description': '需补充对手口径'}])
    second = research_plan.begin_round(store, run['id'])
    assert second['index'] == 2 and not second['idempotent']
    assert len(second['tasks']) == preset['breadth']
    assert (store.root / 'research' / run['id'] / 'rounds' / '2' / 'manifest.json').exists()
    current = second
    while current['index'] < preset['depth']:
        research_plan.finish_round(store, run['id'])
        current = research_plan.begin_round(store, run['id'])
        assert len(research_plan.frozen(store, run['id'])['rounds']) == preset['depth']
    research_plan.finish_round(store, run['id'])
    with pytest.raises(research_plan.AdmissionError, match='最大联网轮次'):
        research_plan.begin_round(store, run['id'])
    final = research_plan.frozen(store, run['id'])
    assert len(final['rounds']) == preset['depth']
    # finish_round replay after closing picks the closed round, not a pending sibling.
    replay = research_plan.finish_round(store, run['id'])
    assert replay['idempotent'] and replay['index'] == preset['depth']


def test_legacy_run_keeps_unmetered_behavior_without_a_plan(tmp_path):
    store, run = quality_run(tmp_path, quality=False)
    assert not research_plan.is_quality(store, run['id'])
    reserved = budget.reserve_search(store, run['id'], 5)
    assert reserved['round_id'] is None
    assert research_plan.pending_requests(store, run['id']) == {}
    page = budget.reserve_pages(store, run['id'], ['https://example.test/b'])
    assert page['round_id'] is None


def test_closed_round_blocks_new_requests(tmp_path):
    store, run = quality_run(tmp_path)
    plan = research_plan.freeze(store, run['id'])
    stored = research_plan.frozen(store, run['id'])
    stored['rounds'][plan['current_round_id']]['status'] = 'closed'
    store.set_meta('research_plan:' + run['id'], stored)
    with pytest.raises(research_plan.AdmissionError, match='没有可用的联网轮次'):
        budget.reserve_search(store, run['id'], 5)


def test_per_round_breadth_is_soft_advisory(tmp_path):
    store, run = quality_run(tmp_path)
    plan = research_plan.freeze(store, run['id'])  # standard: breadth 8
    for _ in range(10):
        budget.reserve_search(store, run['id'], 1)
    # Per-round breadth is an advisory ceiling, not a hard stop; the global budget still applies.
    assert research_plan.round_usage(store, run['id'], plan['current_round_id'])['search'] == 10


def test_round_lifecycle_creates_real_gaps_and_files(tmp_path):
    store, run = quality_run(tmp_path)
    source = store.add_source('Disclosure', 'Revenue 12 million USD.')
    store.attach_source(run['id'], source['id'])
    plan = research_plan.freeze(store, run['id'])
    first = plan['current_round_id']
    again = research_plan.begin_round(store, run['id'])
    assert again == {'round_id': first, 'index': 1, 'tasks': [], 'idempotent': True}
    closed = research_plan.finish_round(store, run['id'], summary='first pass',
                                        gaps=[{'description': '需要核对第二季度口径', 'source_ids': [source['id']]}])
    gap_id = closed['gaps'][0]['id']
    assert gap_id.startswith('gap_') and closed['gaps'][0]['round_index'] == 1
    assert (store.root / 'research' / run['id'] / 'rounds' / '1' / 'outcome.json').exists()
    second = research_plan.begin_round(store, run['id'], target_gap_ids=[gap_id])
    assert second['index'] == 2
    assert research_plan.round_usage(store, run['id'], second['round_id'])['breadth'] == 8
    research_plan.finish_round(store, run['id'])
    third = research_plan.begin_round(store, run['id'])
    assert third['index'] == 3
    research_plan.finish_round(store, run['id'])
    with pytest.raises(research_plan.AdmissionError, match='最大联网轮次'):
        research_plan.begin_round(store, run['id'])


def test_begin_round_rejects_unknown_gap_and_open_round(tmp_path):
    store, run = quality_run(tmp_path)
    research_plan.freeze(store, run['id'])
    with pytest.raises(research_plan.AdmissionError, match='上一轮尚未结束'):
        research_plan.begin_round(store, run['id'], target_gap_ids=['gap_missing'])
    research_plan.finish_round(store, run['id'], gaps=[{'description': 'g'}])
    with pytest.raises(ValueError, match='不存在的缺口'):
        research_plan.begin_round(store, run['id'], target_gap_ids=['gap_missing'])


def test_concurrent_begin_round_replays_instead_of_losing_a_round(tmp_path, monkeypatch):
    import threading
    store, run = quality_run(tmp_path)
    research_plan.freeze(store, run['id'])
    research_plan.finish_round(store, run['id'], summary='first')
    original = research_plan.frozen
    reads = {}
    both_read = threading.Barrier(2)

    def synchronized_read(target_store, run_id):
        plan = original(target_store, run_id)
        ident = threading.get_ident()
        reads[ident] = reads.get(ident, 0) + 1
        if reads[ident] == 1:  # _save_plan's base read: hold it until both writers saw the same plan
            both_read.wait(timeout=10)
        return plan

    monkeypatch.setattr(research_plan, 'frozen', synchronized_read)
    results, errors = [], []

    def starter():
        try:
            results.append(research_plan.begin_round(store, run['id']))
        except Exception as exc:
            errors.append(exc)

    threads = [threading.Thread(target=starter) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=10)
    assert errors == []
    # Both writers read round 1 closed before either committed. One commits its
    # round; the other must replay on the fresh plan and converge on the same
    # round instead of blind-overwriting it with a second, orphaned round.
    assert len(results) == 2
    assert results[0]['round_id'] == results[1]['round_id']
    assert sorted(result['idempotent'] for result in results) == [False, True]
    plan = original(store, run['id'])
    assert len(plan['rounds']) == 2
    assert plan['current_round_id'] == results[0]['round_id']
    # Serialization now comes from the store's BEGIN IMMEDIATE write transaction
    # (no plan-level rev counter); the convergence assertions above carry the guarantee.


def test_join_scouts_enforces_run_scope_and_source_statements(tmp_path):
    import json
    from briefloop.evidence import create_span, create_claim
    from briefloop.scout_tools import join_scouts
    store, run = quality_run(tmp_path)
    source = store.add_source('Disclosure', 'Revenue 12 million USD in H1.')
    store.attach_source(run['id'], source['id'])
    span = create_span(store, {'source_id': source['id'], 'locator': {'kind': 'text', 'start_line': 1, 'end_line': 1}})
    statement = create_claim(store, run['id'], {'statement': 'Revenue 12 million USD in H1.', 'kind': 'fact',
        'claim_role': 'source_statement', 'attribution': 'Company', 'supports': [{'span_id': span['id'], 'supports_quote': '12 million USD'}]})
    path = store.root / 'scout-1.json'
    path.write_text(json.dumps({'sources': [{'source_id': source['id'], 'coverage_status': 'ok', 'claim_ids': [statement['id']]}],
                                'gaps': ['g1'], 'search_summary': 'found one', 'retrieval_notes': [{'query': 'q'}]}))
    merged = join_scouts(store, [str(path)], run_id=run['id'], slots=[str(path)])
    assert merged['search_summary'] == 'found one' and merged['retrieval_notes'] == [{'query': 'q'}]
    with pytest.raises(ValueError, match='槽位'):
        join_scouts(store, [str(path)], run_id=run['id'], slots=[str(store.root / 'other.json')])
    bad = create_claim(store, run['id'], {'statement': 'Revenue 12 million USD in H1.', 'kind': 'fact',
        'supports': [{'span_id': span['id'], 'supports_quote': '12 million USD'}]})
    path.write_text(json.dumps({'sources': [{'source_id': source['id'], 'coverage_status': 'ok', 'claim_ids': [bad['id']]}]}))
    with pytest.raises(ValueError, match='只能引用来源陈述'):
        join_scouts(store, [str(path)], run_id=run['id'])


@pytest.mark.parametrize('search_limit', [0, 4])
def test_chat_generation_freezes_small_budget_and_resumes_into_bound_review(tmp_path, search_limit):
    import json
    import threading
    from briefloop.chat_tools import workspace_action
    from briefloop.runtime import Worker
    from briefloop.review import _snapshot, review_status
    store = Store(tmp_path)
    source = store.add_source('Synthetic', 'Revenue was USD 12 million.')
    result = workspace_action(store, {'action': 'generate', 'requirements': {
        'title': 'Local report', 'objective': 'Summarize supplied material', 'allow_web': False,
        'research_budget': {'search_requests': search_limit, 'candidate_urls': 0, 'source_pages': 0}},
        'source_ids': [source['id']]})
    run_id = result['run_id']
    assert research_plan.is_quality(store, run_id)
    class LocalRuntime:
        cancelled = threading.Event()
        attempts = 0
        def execute(self, job, prompt, folder, on_tick):
            self.attempts += 1
            assert job['allow_web'] is False
            plan = json.loads((folder/'input.json').read_text())['research_plan']
            assert plan['budget']['search_requests'] == search_limit
            closed = workspace_action(store, {'action': 'finish_research_round', 'run_id': run_id,
                'gaps': [{'description': 'Unverified context', 'id': 'spoofed', 'round_id': 'wrong', 'round_index': 99}]})
            gap = closed['gaps'][0]
            assert gap['id'].startswith('gap_') and gap['round_index'] == 1
            if self.attempts == 1:
                self.gap_id = gap['id']
                raise RuntimeError('interrupted after round close')
            assert closed['idempotent'] and gap['id'] == self.gap_id
            record = workspace_action(store, {'action': 'reconciliation_save', 'run_id': run_id,
                'reconciliation': {'status': 'partial', 'examined_claim_ids': [], 'unexamined_claim_ids': [],
                    'open_questions': [{'question': 'No independent context supplied'}]}})
            (folder/'draft.json').write_text(json.dumps({'title': 'Local report', 'markdown': 'Revenue was USD 12 million.',
                'reconciliation_id': record['id']}))
            on_tick()
            return {}
    worker = Worker(store, LocalRuntime())
    job = store.one('jobs', result['job_id'])
    with pytest.raises(RuntimeError, match='interrupted'):
        worker.generate(job, score=False)
    generated = worker.generate(job, score=False)
    assert len(research_plan.frozen(store, run_id)['rounds']) == 1
    assert research_plan.pending_requests(store, run_id) == {}
    target = _snapshot(store, generated['version_id'])
    assert target['reconciliation']['open_questions'][0]['question'] == 'No independent context supplied'
    assert review_status(store, generated['version_id'])['reconciliation']['id'] == target['reconciliation']['id']


def test_deep_tier_from_task_creation_persists_through_resume_and_shows_rounds(tmp_path):
    """The tier chosen at task creation reaches the frozen plan and survives an interrupted run."""
    import json
    import threading
    from briefloop.chat_tools import workspace_action
    from briefloop.progress import ProgressTracker
    from briefloop.runtime import Worker
    store = Store(tmp_path)
    source = store.add_source('Synthetic', 'Revenue was USD 12 million.')
    result = workspace_action(store, {'action': 'generate', 'requirements': {
        'title': 'Deep report', 'objective': 'Summarize supplied material', 'allow_web': False,
        'research_tier': 'deep',
        'research_budget': {field: research_plan.PRESETS['deep'][field] for field in research_plan.BUDGET_FIELDS}},
        'source_ids': [source['id']]})
    run_id = result['run_id']
    assert json.loads(store.one('runs', run_id)['requirements'])['research_tier'] == 'deep'
    frozen_fingerprints = []

    class LocalRuntime:
        cancelled = threading.Event()
        attempts = 0

        def execute(self, job, prompt, folder, on_tick):
            self.attempts += 1
            plan = json.loads((folder / 'input.json').read_text(encoding='utf-8-sig'))['research_plan']
            assert plan['preset_id'] == 'deep' and plan['structure']['depth'] == 4
            assert len(plan['rounds']) == 4
            if self.attempts == 1:
                frozen_fingerprints.append(plan['plan_fingerprint'])
                raise RuntimeError('interrupted before drafting')
            (folder / 'draft.json').write_text(json.dumps({'title': 'Deep report',
                                                           'markdown': 'Revenue was USD 12 million.'}))
            return {}

    worker = Worker(store, LocalRuntime())
    job = store.one('jobs', result['job_id'])
    with pytest.raises(RuntimeError, match='interrupted'):
        worker.generate(job, score=False)
    worker.generate(job, score=False)
    plan = research_plan.frozen(store, run_id)
    assert frozen_fingerprints and plan['plan_fingerprint'] == frozen_fingerprints[0]
    assert json.loads(store.one('runs', run_id)['requirements'])['research_tier'] == 'deep'
    research_plan.finish_round(store, run_id)
    research_plan.begin_round(store, run_id)
    tracker = ProgressTracker(store, job['id'], store.root / 'jobs' / job['id'],
                              context={'payload': json.dumps({'run_id': run_id})})
    tracker.update()
    rows = store.rows("SELECT data FROM events WHERE job_id=? AND kind='runtime_progress'", (job['id'],))
    stages = json.loads(rows[-1]['data'])['stages']
    assert [stage['label'] for stage in stages if stage['id'] == 'research'] == ['深度研究 第 2/4 轮']


def test_external_submit_exposes_research_tier_with_standard_default(tmp_path):
    from briefloop.external_requests import dispatch
    store = Store(tmp_path)
    source = store.add_source('Synthetic', 'Order count 17.')
    workspace_id = store.meta('workspace_id')
    submitted = dispatch(store, {'workspace_id': workspace_id, 'action': 'submit', 'request_id': 'tier-deep',
                                 'requirements': {'title': 'Deep', 'objective': 'o', 'allow_web': True,
                                                  'research_tier': 'deep',
                                                  'research_budget': {field: research_plan.PRESETS['deep'][field]
                                                                      for field in research_plan.BUDGET_FIELDS}},
                                 'source_ids': [source['id']]})
    plan = research_plan.freeze(store, submitted['run_id'])  # what generate does before any controlled call
    assert plan['preset_id'] == 'deep' and len(plan['rounds']) == research_plan.PRESETS['deep']['depth']
    plain = dispatch(store, {'workspace_id': workspace_id, 'action': 'submit', 'request_id': 'tier-default',
                             'requirements': {'title': 'Plain', 'objective': 'o', 'allow_web': True},
                             'source_ids': [source['id']]})
    assert research_plan.freeze(store, plain['run_id'])['preset_id'] == 'standard'


def test_join_scouts_cli_checks_run_and_round_scope(tmp_path, monkeypatch, capsys):
    import json
    import sys
    from briefloop.cli import main
    store, run = quality_run(tmp_path)
    foreign = store.add_source('Other report', 'Other evidence')
    path = store.root/'scout.json'
    path.write_text(json.dumps({'sources': [{'source_id': foreign['id'], 'coverage_status': 'ok'}]}))
    argv = ['briefloop', 'tool', '--workspace', str(store.root), 'join-scouts', '--run', run['id'], '--files', str(path)]
    monkeypatch.setattr(sys, 'argv', argv)
    with pytest.raises(ValueError, match='未登记到本轮'):
        main()
    store.attach_source(run['id'], foreign['id'])
    main()
    assert json.loads(capsys.readouterr().out)['sources'][0]['source_id'] == foreign['id']
    monkeypatch.setattr(sys, 'argv', [*argv, '--round', 'round_missing'])
    with pytest.raises(ValueError, match='轮次不属于本任务'):
        main()


@pytest.mark.parametrize('operation', ['freeze', 'finish', 'begin'])
def test_concurrent_round_mutations_share_committed_identities(tmp_path, monkeypatch, operation):
    """Two tool calls reaching admission together cannot overwrite each other's IDs."""
    from concurrent.futures import ThreadPoolExecutor
    from contextlib import contextmanager
    from threading import Barrier
    import json

    store, run = quality_run(tmp_path)
    run_id = run['id']
    target = []
    if operation != 'freeze':
        research_plan.freeze(store, run_id)
    if operation == 'begin':
        closed = research_plan.finish_round(store, run_id, gaps=[{'description': 'Check reporting period'}])
        target = [closed['gaps'][0]['id']]
    barrier = Barrier(2)
    original_tx = store.tx

    @contextmanager
    def concurrent_tx():
        # Exercise contention at the database boundary, including stale reads
        # made before it. Never wait while holding the SQLite write lock.
        barrier.wait(timeout=10)
        with original_tx() as connection:
            yield connection

    monkeypatch.setattr(store, 'tx', concurrent_tx)
    def call():
        if operation == 'freeze':
            return research_plan.freeze(store, run_id)
        if operation == 'finish':
            return research_plan.finish_round(store, run_id, gaps=[{'description': 'Check reporting period'}])
        return research_plan.begin_round(store, run_id, target_gap_ids=target, tasks=[{'slot_id': 'company'}])

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(call) for _ in range(2)]
        results = [future.result(timeout=15) for future in futures]
    monkeypatch.setattr(store, 'tx', original_tx)
    saved = research_plan.frozen(store, run_id)
    if operation == 'freeze':
        assert results[0] == results[1] == saved
    else:
        assert results[0]['round_id'] == results[1]['round_id']
        assert sorted(result['idempotent'] for result in results) == [False, True]
        info = saved['rounds'][results[0]['round_id']]
        field = 'gaps' if operation == 'finish' else 'tasks'
        assert results[0][field] == results[1][field] == info[field]
        filename = 'outcome.json' if operation == 'finish' else 'manifest.json'
        recorded = json.loads((store.root/'research'/run_id/'rounds'/str(info['index'])/filename).read_text())
        assert recorded['round_id'] == results[0]['round_id']
        assert recorded[field] == info[field]
        assert len(saved['rounds']) == (2 if operation == 'begin' else 1)
