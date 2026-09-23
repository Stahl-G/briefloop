"""Network responses retain their reservation identity through DB admission."""
import json
from pathlib import Path
import threading
from concurrent.futures import ThreadPoolExecutor

import pytest

from briefloop import duckduckgo, research_budget as budget, research_plan as plan, sources, tavily, websearch
from briefloop.store import Store


LIMITS = {'search_requests': 2, 'candidate_urls': 3, 'source_pages': 3}


def world(tmp_path, provider='duckduckgo', *, quality=True):
    store = Store(tmp_path)
    store.set_meta('settings', {**store.settings(), 'search_provider': provider})
    run = store.create_run({'title': 'R', 'objective': 'test response admission', 'allow_web': True,
                            'fact_check': False, 'research_budget': LIMITS}, [],
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


def test_tavily_batch_after_stage_completion_preserves_raw_but_no_partial_sources(tmp_path, monkeypatch):
    store, run = world(tmp_path, 'tavily')
    urls = ['https://example.test/a', 'https://example.test/b']
    response = {'results': [{'url': url, 'raw_content': 'decoded source'} for url in urls]}
    raw = json.dumps(response).encode()

    def completed(*_, **__):
        plan.finish_fact_check(store, run, status='completed')
        return response, raw

    monkeypatch.setattr(tavily, '_post', completed)
    result = tavily.extract(store, urls, run_id=run)
    assert result['status'] == 'response_rejected' and result['sources'] == []
    assert store.source_ids(run) == [] and store.rows('SELECT * FROM sources') == []
    assert budget.spent(store, run)['source_pages'] == 2
    envelope = json.loads(Path(result['request_record_path']).read_text())
    assert Path(envelope['raw_response_path']).read_bytes() == raw
    assert envelope['admitted_urls'] == [] and envelope['unadmitted_urls'] == urls
    assert len(envelope['retained_source_ids']) == 2


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


def test_cancel_serializes_with_candidate_commit_and_does_not_rewrite_admission(tmp_path, monkeypatch):
    store, run = world(tmp_path)
    monkeypatch.setattr(duckduckgo, 'call_search', lambda *_, **__: (
        {'results': [{'title': 'Found', 'url': 'https://example.test/found'}]}, b'{}', None, None))
    writing, closing = threading.Event(), threading.Event()
    save = budget._save

    def pause_candidate_save(connection, key, state):
        if state['candidate_urls']:
            writing.set()
            assert closing.wait(3)
        save(connection, key, state)

    def cancel():
        assert writing.wait(3)
        closing.set()
        return plan.finish_fact_check(store, run, status='cancelled')

    monkeypatch.setattr(budget, '_save', pause_candidate_save)
    with ThreadPoolExecutor(max_workers=1) as pool:
        cancellation = pool.submit(cancel)
        result = websearch.search('found', store=store, run_id=run, max_results=1)
        assert cancellation.result()['status'] == 'cancelled'
    assert result['status'] == 'ok' and len(result['results']) == 1
    entry = plan.pending_requests(store, run)[result['local_request_id']]
    assert entry['status'] == 'completed' and 'rejection_reason' not in entry
    assert entry['request_record_path'] == result['request_record_path']
    assert budget.spent(store, run)['candidate_urls'] == 1


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
