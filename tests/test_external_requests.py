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
