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
    assert entry == {'operation': 'search', 'round_id': plan['current_round_id'], 'status': 'reserved', 'created': entry['created']}


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
