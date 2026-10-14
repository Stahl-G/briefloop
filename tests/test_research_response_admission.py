"""Network responses retain their reservation identity through DB admission."""
import json
from pathlib import Path

import pytest

from briefloop import duckduckgo, research_budget as budget, research_plan as plan, sources, websearch
from briefloop.store import Store


LIMITS = {'search_requests': 2, 'candidate_urls': 3, 'source_pages': 3}


def world(tmp_path, provider='duckduckgo', *, quality=True, completion_mode='standard'):
    store = Store(tmp_path)
    store.set_meta('settings', {**store.settings(), 'search_provider': provider})
    run = store.create_run({'title': 'R', 'objective': 'test response admission', 'allow_web': True,
                            'fact_check': False, 'research_budget': LIMITS, 'completion_mode': completion_mode}, [],
                           **({'research_protocol': 'quality_v1'} if quality else {}))
    if quality:
        plan.freeze(store, run['id'])
        plan.finish_round(store, run['id'])
        plan.admit_fact_check(store, run['id'], {'kind': 'task_reserve', 'limits': LIMITS})
    return store, run['id']


def test_late_search_cannot_use_a_reopened_stage_or_erase_its_failure(tmp_path, monkeypatch):
    store, run = world(tmp_path)
    old_stage = plan.frozen(store, run)['fact_check']['stage_id']
    raw = b'{"retained":"old response"}'

    def late(*_, **__):
        plan.finish_fact_check(store, run, status='budget_exhausted')
        plan.add_fact_check_grant(store, run, {'search_requests': 1, 'candidate_urls': 2, 'source_pages': 1})
        return {'results': [{'title': 'Old', 'url': 'https://example.test/old'}]}, raw, 'old-provider-id', None

    monkeypatch.setattr(duckduckgo, 'call_search', late)
    result = websearch.search('old', store=store, run_id=run, max_results=1)
    assert result['status'] == 'response_rejected' and result['results'] == []
    assert Path(result['discovery_path']).read_bytes() == raw
    envelope = json.loads(Path(result['request_record_path']).read_text())
    assert envelope['outcome'] == 'response_rejected' and envelope['admitted_urls'] == []
    entry = plan.pending_requests(store, run)[result['local_request_id']]
    assert entry['round_id'] == old_stage and entry['status'] == 'response_rejected'
    assert budget.spent(store, run) == {'search_requests': 1, 'candidate_urls': 0, 'source_pages': 0}
    assert budget.snapshot(store, run)['remaining']['candidate_urls'] == 5

    monkeypatch.setattr(duckduckgo, 'call_search', lambda *_, **__: (
        {'results': [{'title': 'Current', 'url': 'https://example.test/current'}]}, b'{}', None, None))
    current = websearch.search('current', store=store, run_id=run, max_results=1)
    assert current['status'] == 'ok' and len(current['results']) == 1
    assert store.meta('research_budget:' + run)['candidate_urls'] == ['https://example.test/current']

    def fail_after_cancel(*_, **__):
        plan.finish_fact_check(store, run, status='cancelled')
        raise websearch.marked('synthetic network timeout', 'timeout')

    monkeypatch.setattr(duckduckgo, 'call_search', fail_after_cancel)
    with pytest.raises(websearch.SearchError) as failure:
        websearch.search('failure', store=store, run_id=run, max_results=1)
    failed = json.loads(Path(failure.value.request_record_path).read_text())
    entry = plan.pending_requests(store, run)[failed['local_request_id']]
    assert entry['status'] == 'response_rejected' and entry['response_status'] == 'failed'
    assert entry['failure_kind'] == failed['failure_kind'] == 'timeout'
    assert budget.spent(store, run)['search_requests'] == 3  # issued attempts are not refunded


def test_cancelled_direct_page_retains_original_without_registering_a_source(tmp_path, monkeypatch):
    store, run = world(tmp_path)
    raw = b'A response that arrived after cancellation.'
    calls = []

    def response(url, **_):
        calls.append(url)
        plan.finish_fact_check(store, run, status='cancelled')
        return raw, 'text/plain', 'utf-8'

    monkeypatch.setattr(sources, '_fetch_bytes', response)
    with pytest.raises(plan.AdmissionError) as late:
        sources.fetch_for_run(store, run, 'https://example.test/late')
    assert late.value.code == 'response_rejected'
    envelope = json.loads(Path(late.value.request_record_path).read_text())
    assert envelope['outcome'] == 'response_rejected' and envelope['admitted'] is False
    provenance = json.loads((store.root / envelope['provenance_path']).read_text())
    assert (store.root / provenance['original_path']).read_bytes() == raw
    assert store.source_ids(run) == [] and store.rows('SELECT * FROM sources') == []
    assert budget.spent(store, run)['source_pages'] == 1
    assert plan.pending_requests(store, run)[envelope['local_request_id']]['status'] == 'response_rejected'
    with pytest.raises(plan.AdmissionError):sources.fetch_for_run(store, run, 'https://example.test/late')
    assert len(calls) == 1


def test_response_identity_and_replay_are_checked_in_the_candidate_transaction(tmp_path):
    store, run = world(tmp_path)
    reservation = budget.reserve_search(store, run, 1)
    with pytest.raises(plan.AdmissionError) as mismatch:
        budget.record_candidates(store, run, ['https://example.test/wrong'],
                                 reservation={**reservation, 'round_id': 'another-stage'})
    assert mismatch.value.code == 'response_request_mismatch'
    assert budget.spent(store, run)['candidate_urls'] == 0
    assert plan.pending_requests(store, run)[reservation['request_id']]['status'] == 'reserved'
    assert budget.record_candidates(store, run, ['https://example.test/once'], reservation=reservation)['accepted']
    assert not budget.record_candidates(store, run, ['https://example.test/twice'], reservation=reservation)['accepted']
    assert store.meta('research_budget:' + run)['candidate_urls'] == ['https://example.test/once']
    page = budget.reserve_pages(store, run, ['https://example.test/page'], request_id='stable-page')
    with pytest.raises(plan.AdmissionError):
        budget.reserve_pages(store, run, ['https://example.test/other-page'], request_id=page['request_id'])
    assert budget.spent(store, run)['source_pages'] == 1


def test_legacy_search_and_page_admission_still_work_without_quality_records(tmp_path, monkeypatch):
    store, run = world(tmp_path, quality=False)
    monkeypatch.setattr(duckduckgo, 'call_search', lambda *_, **__: (
        {'results': [{'title': 'Found', 'url': 'https://example.test/found'}]}, b'{}', None, None))
    assert websearch.search('legacy', store=store, run_id=run)['status'] == 'ok'
    monkeypatch.setattr(sources, '_fetch_bytes', lambda *_, **__: (b'original', 'text/plain', 'utf-8'))
    source = sources.fetch_for_run(store, run, 'https://example.test/found')
    assert source['status'] == 'ready' and store.source_text(source['id']) == 'original'
    assert store.source_ids(run) == [source['id']]
    assert plan.pending_requests(store, run) == {}


def test_human_revision_reopens_closed_research_without_resetting_budget(tmp_path, monkeypatch):
    from briefloop import revision_research
    from briefloop.chat_store import ChatStore

    store, run = world(tmp_path, completion_mode='direct')
    budget.reserve_search(store, run, 1)
    plan.finish_fact_check(store, run, status='completed')
    # An issued request remains charged across revisions.
    brief = store.publish(run, {'title': 'R', 'markdown': 'Original report'}, author='user')
    chat = ChatStore(store)
    session = chat.create('Report revision', {}, tmp_path)['id']
    message = chat.message(session, 'Please add missing news', status='sending', allow_web=True)['id']
    context = {'message_id': message, 'base_version_id': brief['id']}
    with pytest.raises(plan.AdmissionError) as unbound:
        revision_research.begin(store, run, base_version_id=brief['id'], session_id=session, message_id=message)
    assert unbound.value.code == 'revision_context_missing'
    chat.event(session, revision_research.CONTEXT_EVENT, context)
    original_rounds = plan.frozen(store, run)['rounds']
    stage = revision_research.begin(store, run, base_version_id=brief['id'], session_id=session, message_id=message)
    restored = Store(tmp_path)
    recovered = revision_research.begin(restored, run, base_version_id=brief['id'], session_id=session, message_id=message)
    assert recovered['idempotent'] and recovered['stage_id'] == stage['stage_id']
    reservation = budget.reserve_search(restored, run, 1)
    assert reservation['stage'] == 'revision_research'
    assert budget.spent(restored, run)['search_requests'] == 2
    with pytest.raises(budget.BudgetExhausted):
        budget.reserve_search(restored, run, 1)
    revision_research.finish_for_message(restored, message, status='cancelled')
    assert not budget.record_candidates(restored, run, ['https://example.test/late'], reservation=reservation)['accepted']
    assert plan.frozen(restored, run)['rounds'] == original_rounds
    assert revision_research.finish(restored, run, stage['stage_id'], status='completed')['status'] == 'cancelled'
    with pytest.raises(plan.AdmissionError):
        budget.reserve_pages(restored, run, ['https://example.test/closed'])

    # Restart after the terminal message commit must finish its saved revision.
    from briefloop import draft_completion, report_revision
    from briefloop.store import now
    job = restored.enqueue('generate', {'run_id': run, 'runtime': {'model': 'test-model'}})
    restored.update_job(job['id'], 'complete')
    folder = restored.root / 'jobs' / job['id']
    folder.mkdir(parents=True, exist_ok=True)
    (folder / 'generated-source-snapshots.json').write_text(json.dumps({brief['id']: {}}))
    followup = chat.message(session, 'Add the final missing item', status='sending', allow_web=True)
    chat.patch_message(followup['id'], turn_id='revision-followup')
    chat.update(session, turn_id='revision-followup')
    report_revision.bind_context(restored, session, followup['id'], {'version_id': brief['id'], 'hash': brief['hash']})
    revision_research.begin(restored, run, base_version_id=brief['id'], session_id=session, message_id=followup['id'])
    from briefloop.version_execution import chat_publication
    revised = restored.revise(brief['id'], 'Revised report', allow_markdown_conversion=True, author='agent',
                              writer=chat_publication(restored, session, followup['id']))
    with restored.tx() as connection:
        connection.execute("UPDATE chat_messages SET status='completed',updated=? WHERE id=?", (now(), followup['id']))
    ChatStore(restored).recover_stale()
    assert plan.frozen(restored, run)['revision_research']['status'] == 'completed'
    checks = restored.rows("SELECT * FROM jobs WHERE kind='assess'")
    assert len(checks) == 1 and json.loads(checks[0]['payload'])['version_id'] == revised['id']
    restored.update_job(checks[0]['id'], 'cancelled')
    # Already-settled messages neither reopen originals nor resume cancelled checks.
    monkeypatch.setattr(draft_completion, 'enqueue_revision', lambda *a, **kw: pytest.fail('duplicate closeout'))
    ChatStore(restored).recover_stale()
    assert restored.one('jobs', checks[0]['id'])['status'] == 'cancelled'
