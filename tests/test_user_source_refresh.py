"""Single user-queued refreshes do not reopen closed research or its grants."""
import http.client
import json
import threading

import pytest

from briefloop import research_budget as budget, research_plan as plan, sources, source_updates
from briefloop.chat_tools import workspace_action
from briefloop.runtime import Worker
from briefloop.store import Store


class NoModel:
    cancelled = threading.Event()

    def execute(self, *args, **kwargs):
        raise AssertionError('Source refresh must not call a model')

    def cancel(self):
        pass


def report(store, *, completed_check=False, allow_web=True, pages=2):
    old = store.add_source('old', 'Original text.', url='https://example.test/source')
    run = store.create_run({'title': 'Saved report', 'objective': 'Refresh its source', 'allow_web': allow_web,
                            'fact_check': False, 'research_budget': {'search_requests': 2, 'candidate_urls': 4, 'source_pages': pages}},
                           [old['id']], research_protocol='quality_v1')
    brief = store.publish(run['id'], {'title': 'Saved report', 'markdown': 'Original report remains saved.'})
    plan.freeze(store, run['id']);plan.finish_round(store, run['id'])
    if completed_check:
        plan.admit_fact_check(store, run['id'], {'kind': 'task_reserve', 'limits': {'search_requests': 1, 'candidate_urls': 1, 'source_pages': 1}})
        plan.finish_fact_check(store, run['id'], status='completed')
    return run, old, brief


def queued(store, run, old, brief, **changes):
    return store.enqueue('source_refresh', {'run_id': run['id'], 'source_id': old['id'], 'source_url': old['url'],
                                           'version_id': brief['id'], 'information_cutoff': '2026-10-01',
                                           'requested_by': 'user', **changes})


def refresh(store, run, old, job):
    return source_updates.refresh(store, run['id'], old['id'], information_cutoff='2026-10-01', user_job_id=job['id'])


@pytest.mark.parametrize('completed_check', [False, True])
def test_http_queued_user_refresh_runs_after_report_closure(tmp_path, monkeypatch, completed_check):
    from briefloop.server import make_server

    server = make_server(tmp_path, port=0, paused=True)
    server.worker.runtime = NoModel()
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    connection = http.client.HTTPConnection('127.0.0.1', server.server_port)
    try:
        store = server.store
        run, old, brief = report(store, completed_check=completed_check)
        before = plan.frozen(store, run['id'])
        connection.request('GET', '/api/session')
        token = json.loads(connection.getresponse().read())['token']
        connection.request('POST', '/api/source-refresh', json.dumps({
            'version_id': brief['id'], 'source_id': old['id'], 'information_cutoff': '2026-10-01',
            'source_url': 'https://unselected.test/ignored'}), headers={'X-BriefLoop-Token': token})
        response = connection.getresponse()
        assert response.status == 200
        job = json.loads(response.read())
        assert json.loads(job['payload'])['source_url'] == old['url']  # server freezes its own source identity
        calls = []

        def fetched(url, **kwargs):
            calls.append((url, kwargs.get('allow_private')))
            return b'Updated response.', 'text/plain', 'utf-8'

        monkeypatch.setattr(sources, '_fetch_bytes', fetched)
        store.update_job(job['id'], 'running')
        server.worker._execute_main_job(job)
        saved = store.one('jobs', job['id'])
        assert saved['status'] == 'complete', saved['error']
        result = json.loads(saved['result'])
        assert result['outcome'] == 'changed_needs_review'
        assert calls == [(old['url'], True)]
        assert store.source_text(result['new_source_id']) == 'Updated response.'
        assert store.one('sources', old['id']) == old and store.one('briefs', brief['id']) == brief
        assert plan.frozen(store, run['id']) == before
        request = plan.pending_requests(store, run['id'])[result['data']['budget']['request_id']]
        assert request['stage'] == 'source_refresh' and request['status'] == 'completed'
        assert budget.snapshot(store, run['id'])['stages']['source_refresh']['source_pages'] == 1
        assert request['refresh'] == {'job_id': job['id'], 'attempt': 1, 'run_id': run['id'],
                                      'version_id': brief['id'], 'source_id': old['id'], 'url': old['url']}
    finally:
        connection.close();server.shutdown();thread.join()
        server.harness.close();server.opencode_harness.close();server.native_harness.close()
        server.runtime_bridge.close();server.native_engine.close()
        server.server_close();server.workspace_lock.close()


def test_manual_label_and_wrong_queue_identity_cannot_bypass_closed_research(tmp_path, monkeypatch):
    store = Store(tmp_path)
    run, old, brief = report(store)
    job = queued(store, run, old, brief)
    calls = []
    monkeypatch.setattr(sources, '_fetch_bytes', lambda *args, **kwargs: calls.append(args))
    with pytest.raises(plan.AdmissionError) as forged:
        workspace_action(store, {'action': 'refresh_source', 'run_id': run['id'], 'source_id': old['id'],
                                 'trigger': 'manual', 'user_job_id': job['id'], 'information_cutoff': '2026-10-01'})
    assert forged.value.code == 'round_closed'
    with pytest.raises(plan.AdmissionError):refresh(store, run, old, job)  # queued is not running
    store.update_job(job['id'], 'running')
    other_run, other_source, _ = report(store)
    with pytest.raises(plan.AdmissionError):refresh(store, other_run, other_source, job)
    wrong_url = queued(store, run, old, brief, source_url='https://unselected.test/source')
    store.update_job(wrong_url['id'], 'running')
    with pytest.raises(plan.AdmissionError):refresh(store, run, old, wrong_url)
    agent = queued(store, run, old, brief, requested_by='agent')
    store.update_job(agent['id'], 'running')
    with pytest.raises(plan.AdmissionError):refresh(store, run, old, agent)
    assert calls == [] and budget.spent(store, run['id'])['source_pages'] == 0


@pytest.mark.parametrize('allow_web', [False, True])
def test_user_refresh_preserves_offline_permission_and_cannot_spend_fact_check_grants(tmp_path, monkeypatch, allow_web):
    store = Store(tmp_path)
    run, old, brief = report(store, allow_web=allow_web, pages=0)
    if allow_web:
        plan.admit_fact_check(store, run['id'], {'kind': 'user_grant', 'limits': {'search_requests': 0, 'candidate_urls': 0, 'source_pages': 1}})
    job = queued(store, run, old, brief)
    store.update_job(job['id'], 'running')
    monkeypatch.setattr(sources, '_fetch_bytes', lambda *args, **kwargs: pytest.fail('No authorized page budget'))
    outcome = refresh(store, run, old, job)
    assert outcome['outcome'] == ('budget_exhausted' if allow_web else 'not_authorized')
    assert outcome['new_source_id'] is None and budget.spent(store, run['id'])['source_pages'] == 0


def test_cancelled_user_refresh_retains_late_bytes_and_only_explicit_resume_can_fetch_again(tmp_path, monkeypatch):
    store = Store(tmp_path)
    run, old, brief = report(store, completed_check=True)
    before = plan.frozen(store, run['id'])
    job = queued(store, run, old, brief)
    store.update_job(job['id'], 'running')
    worker = Worker(store, NoModel())

    def late(url, **kwargs):
        worker.stop_job(job['id'])
        worker.resume(job['id'])  # the user's explicit retry is a different attempt
        return b'Late response from attempt 1.', 'text/plain', 'utf-8'

    monkeypatch.setattr(sources, '_fetch_bytes', late)
    rejected = refresh(store, run, old, job)
    assert rejected['outcome'] == 'response_rejected' and rejected['new_source_id'] is None
    assert store.source_ids(run['id']) == [old['id']]
    response = rejected['data']['rejected_response']
    provenance = json.loads((store.root / response['provenance_path']).read_text())
    assert (store.root / provenance['original_path']).read_bytes() == b'Late response from attempt 1.'
    request = plan.pending_requests(store, run['id'])[response['local_request_id']]
    assert request['refresh']['attempt'] == 1 and request['status'] == 'response_rejected'
    assert request['rejection_reason'] == 'refresh_job_changed_or_stopped'
    assert budget.spent(store, run['id'])['source_pages'] == 1
    monkeypatch.setattr(sources, '_fetch_bytes', lambda *args, **kwargs: (b'Attempt 2 response.', 'text/plain', 'utf-8'))
    store.update_job(job['id'], 'running')
    accepted = refresh(store, run, old, job)
    assert store.source_text(accepted['new_source_id']) == 'Attempt 2 response.'
    with pytest.raises(plan.AdmissionError) as duplicate:refresh(store, run, old, job)
    assert duplicate.value.code == 'request_already_reserved'
    assert len(plan.pending_requests(store, run['id'])) == 2
    assert budget.spent(store, run['id'])['source_pages'] == 1  # the existing unique-URL meter is unchanged
    assert plan.frozen(store, run['id']) == before
