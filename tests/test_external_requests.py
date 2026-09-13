from concurrent.futures import ThreadPoolExecutor
import json
import threading

import pytest

from briefloop.external_requests import dispatch
from briefloop.store import Store, Conflict


def workspace(tmp_path):
    store = Store(tmp_path)
    source = store.add_source('Synthetic source', 'Order count 17; delivery in two batches.')
    request = {'workspace_id': store.meta('workspace_id'), 'action': 'submit',
               'request_id': 'submit-1', 'requirements': {'title': 'Synthetic report', 'objective': 'Summarize the supplied record'},
               'source_ids': [source['id']]}
    return store, request


def test_atomic_submit_parallel_replay_conflict_and_rollback(tmp_path, monkeypatch):
    store, request = workspace(tmp_path)
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: dispatch(store, request), range(2)))
    assert results[0]['job_id'] == results[1]['job_id']
    assert sorted(r['replayed'] for r in results) == [False, True]
    assert len(store.rows('SELECT id FROM runs')) == len(store.rows('SELECT id FROM jobs')) == 1
    with pytest.raises(Conflict, match='不同内容'):
        dispatch(store, {**request, 'source_ids': []})
    original = Store.enqueue

    def fail_after_enqueue(self, *args, **kwargs):
        original(self, *args, **kwargs)
        raise RuntimeError('synthetic failure before response/commit')

    before = store.meta('requirements')
    monkeypatch.setattr(Store, 'enqueue', fail_after_enqueue)
    with pytest.raises(RuntimeError, match='synthetic'):
        dispatch(store, {**request, 'request_id': 'failed', 'requirements': {**request['requirements'], 'title': 'Must roll back'}})
    assert store.meta('requirements') == before
    assert len(store.rows('SELECT id FROM runs')) == len(store.rows('SELECT id FROM jobs')) == 1
    assert len(store.rows('SELECT request_id FROM external_requests')) == 1
    # A lost response can be replayed without consulting today's model config.
    store.set_meta('settings', {**store.settings(), 'model': 'changed-after-submit'})
    assert dispatch(store, request)['job_id'] == results[0]['job_id']
    store.update_job(results[0]['job_id'], 'complete', result={
        'scoring': {'status': 'incomplete', 'error': 'provider 503', 'private_log': 'do not expose'},
        'reasoning': 'do not expose'})
    state = dispatch(store, {'workspace_id': request['workspace_id'], 'action': 'query', 'job_id': results[0]['job_id']})
    assert state['terminal'] and state['scoring'] == {'status': 'incomplete', 'error': 'provider 503'}
    assert 'reasoning' not in state


def test_revision_replay_precedes_optimistic_lock_and_export_binds_version(tmp_path):
    from briefloop.export_jobs import generate_word, output_path
    store, request = workspace(tmp_path)
    submitted = dispatch(store, request)
    old = store.publish(submitted['run_id'], {'title': 'Saved example', 'markdown': 'Original text'}, author='example')
    revision = {'workspace_id': request['workspace_id'], 'action': 'revise', 'request_id': 'edit-1',
                'base_version': old['id'], 'editor_document': {'type': 'doc', 'content': [
                    {'type': 'paragraph', 'content': [{'type': 'text', 'text': 'Edited text'}]}]}}
    saved = dispatch(store, revision)
    assert dispatch(store, revision)['version_id'] == saved['version_id']
    with pytest.raises(Conflict, match='已有更新'):
        dispatch(store, {**revision, 'request_id': 'edit-2'})
    export = {'workspace_id': request['workspace_id'], 'action': 'export', 'request_id': 'export-1', 'version_id': saved['version_id']}
    accepted = dispatch(store, export)
    job = store.one('jobs', accepted['job_id'])
    result = generate_word(store, job, threading.Event())
    store.update_job(job['id'], 'complete', result=result)
    query = {'workspace_id': request['workspace_id'], 'action': 'query', 'job_id': job['id']}
    status = dispatch(store, query)
    assert status['artifact_available'] and status['version_id'] == saved['version_id']
    assert dispatch(store, export)['job_id'] == job['id']
    output_path(store, store.one('jobs', job['id'])).unlink()
    assert dispatch(store, export)['job_id'] == job['id']  # No hidden regeneration.
    assert dispatch(store, query)['artifact_available'] is False
    assert dispatch(store, {'workspace_id': request['workspace_id'], 'action': 'read', 'version_id': old['id']})['markdown'] == 'Original text'
    with pytest.raises(Conflict, match='工作区身份'):
        dispatch(store, {**export, 'workspace_id': 'wrong-workspace'})


def test_discovery_of_missing_or_plain_directory_has_no_writes(tmp_path):
    from briefloop.external_client import discover
    missing = tmp_path / 'must-not-be-created'
    assert discover(missing)['status'] == 'not_workspace'
    assert not missing.exists()
    plain = tmp_path / 'ordinary-folder'
    plain.mkdir()
    assert discover(plain)['status'] == 'not_workspace'
    assert list(plain.iterdir()) == []
    store, _ = workspace(tmp_path / 'existing')
    assert discover(store.root)['status'] == 'service_unavailable'
    assert not (store.root / 'server.json').exists()
    for body in ({'action': []}, {'action': 'read', 'version_id': {}}):
        with pytest.raises(ValueError):
            dispatch(store, {**body, 'workspace_id': store.meta('workspace_id')})


def test_local_client_auth_identity_and_saved_report_http_roundtrip(tmp_path):
    import http.client
    import os
    from briefloop.external_client import Client, discover
    from briefloop.server import make_server
    store, request = workspace(tmp_path)
    run = store.create_run(request['requirements'], request['source_ids'])
    brief = store.publish(run['id'], {'title': 'HTTP example', 'markdown': 'Existing report text'}, author='example')
    server = make_server(tmp_path, port=0, paused=True)
    server.worker.start()
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    url = f'http://127.0.0.1:{server.server_port}'
    (tmp_path / 'server.json').write_text(json.dumps({'pid': os.getpid(), 'url': url, 'workspace_id': request['workspace_id']}))
    try:
        assert discover(tmp_path)['ready']
        client = Client(tmp_path)
        index = client.request({'action': 'inspect'})
        assert index['reports'][0]['version_id'] == brief['id']
        assert client.request({'action': 'source', 'source_id': request['source_ids'][0]})['text'] == 'Order count 17; delivery in two batches.'
        assert client.request({'action': 'read', 'version_id': brief['id']})['markdown'] == 'Existing report text'
        conn = http.client.HTTPConnection('127.0.0.1', server.server_port)
        conn.request('POST', '/api/external/action', json.dumps({'action': 'inspect', 'workspace_id': request['workspace_id']}))
        response = conn.getresponse()
        assert response.status == 403
        response.read(); conn.close()
        with pytest.raises(ValueError, match='不属于'):
            client.request({'action': 'inspect', 'workspace_id': 'different'})
        # This uses the real file worker and real authenticated HTTP client.
        import time
        exported = client.request({'action': 'export', 'request_id': 'http-export', 'version_id': brief['id']})
        for _ in range(100):
            state = client.request({'action': 'query', 'job_id': exported['job_id']})
            if state['terminal']: break
            time.sleep(.1)
        assert state['status'] == 'complete', state
        target = tmp_path / 'download.docx'
        downloaded = client.download(exported['job_id'], target)
        assert target.read_bytes().startswith(b'PK') and downloaded['version_id'] == brief['id']
        assert client.download(exported['job_id'], target)['reused']
        server.draining = True
        with pytest.raises(ValueError, match='503'):
            client.request({'action': 'inspect'})
    finally:
        server.draining = False
        server.shutdown(); thread.join()
        server.worker.close()
        server.harness.close(); server.opencode_harness.close(); server.runtime_bridge.close()
        server.server_close(); server.workspace_lock.close()
