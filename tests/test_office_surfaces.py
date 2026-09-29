"""OfficeCLI surface visibility, the release chain, and audit bundles.

The release chain test is the pin for review finding #1: office checks are
composed at the server/review_status response layer only, so eligibility's
frozen input and the two equality checks inside generate_release can never
observe a refreshed office_checks row.
"""
import http.client
import json
import os
import threading
from io import BytesIO
from zipfile import ZipFile

import pytest

from briefloop import host_bins, office_cli
from briefloop.store import Store, dump
from test_office_cli import install_stub
from test_release import reviewed_report, complete_release


@pytest.fixture(autouse=True)
def _reset_office_caches():
    yield
    office_cli._clear_caches()


def _no_officecli(monkeypatch):
    monkeypatch.setenv('PATH', '/usr/bin:/bin')
    monkeypatch.setattr(host_bins, 'EXTRA_DIRS', ())
    office_cli._clear_caches()


def _start_server(workspace):
    from briefloop.server import make_server
    server = make_server(workspace, port=0, paused=True)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, thread


def _stop_server(server, thread):
    server.shutdown()
    thread.join()
    server.harness.close()
    server.opencode_harness.close()
    server.server_close()
    server.workspace_lock.close()


def _session(server):
    connection = http.client.HTTPConnection('127.0.0.1', server.server_port)
    authority = f'127.0.0.1:{server.server_port}'
    connection.request('GET', '/api/session', headers={'Host': authority})
    token = json.loads(connection.getresponse().read())['token']
    return connection, authority, token


def _get(connection, authority, path):
    connection.request('GET', path, headers={'Host': authority})
    response = connection.getresponse()
    return response.status, response.read()


def _post(connection, authority, token, path, body):
    connection.request('POST', path, body=json.dumps(body), headers={
        'Host': authority, 'X-BriefLoop-Token': token, 'Origin': 'http://' + authority})
    response = connection.getresponse()
    return response.status, json.loads(response.read())


def _completed_export(store, brief):
    from briefloop.export_jobs import enqueue_export, generate_word
    queued = enqueue_export(store, brief['id'])
    job = store.one('jobs', queued['id'])
    result = generate_word(store, job, threading.Event())
    store.update_job(job['id'], 'complete', result=result)
    return job, result


def test_http_surfaces_degrade_without_officecli(tmp_path, monkeypatch):
    _no_officecli(monkeypatch)
    server, thread = _start_server(tmp_path / 'workspace')
    try:
        connection, authority, token = _session(server)
        status, payload = _get(connection, authority, '/api/runtimes')
        capability = json.loads(payload)['capabilities']['officecli']
        assert capability['installed'] is False and capability['path'] is None
        assert capability['version'] is None and capability['available'] is False
        assert capability['diagnostic'] == '未检测到 OfficeCLI；' + host_bins.SEARCH_HINT
        status, payload = _get(connection, authority, '/api/state')
        assert json.loads(payload)['office'] == {'installed': False, 'enabled': False}
        for path, body in (('/api/office-check', {'job_id': 'job_none'}),
                           ('/api/office-preview', {'source_id': 'src_none'}),
                           ('/api/audit-bundle', {'release_id': 'rel_none'})):
            assert _post(connection, authority, token, path, body)[0] == 400, path
        status, _payload = _get(connection, authority, '/api/office-image?digest=zz&page=1')
        assert status == 400
        status, payload = _post(connection, authority, token, '/api/settings', {'officecli_enabled': True})
        assert status == 200 and payload['officecli_enabled'] is True
        status, payload = _get(connection, authority, '/api/state')
        assert json.loads(payload)['office'] == {'installed': False, 'enabled': True}
        connection.close()
    finally:
        _stop_server(server, thread)


@pytest.mark.skipif(os.name == 'nt', reason='stub uses a POSIX shebang')
def test_http_office_check_preview_and_image_with_stub(tmp_path, monkeypatch):
    install_stub(tmp_path, monkeypatch)
    server, thread = _start_server(tmp_path / 'workspace')
    try:
        store = server.store
        store.update_settings({'officecli_enabled': True})
        source = store.add_source('Synthetic', 'Revenue was USD 12 million.')
        run = store.create_run({'title': 'T', 'objective': 'o', 'allow_web': False}, [source['id']])
        brief = store.publish(run['id'], {'title': 'T', 'markdown': 'Revenue was USD 12 million.'})
        job, result = _completed_export(store, brief)
        connection, authority, token = _session(server)
        status, payload = _post(connection, authority, token, '/api/office-check', {'job_id': job['id']})
        assert status == 200
        assert payload['office']['validate']['status'] == 'ok'
        assert payload['office']['file_sha256'] == result['sha256']
        status, payload = _post(connection, authority, token, '/api/office-preview',
                                {'job_id': job['id'], 'pages': [1]})
        assert status == 200 and payload['target']['kind'] == 'export'
        assert payload['pages'][0]['url'].startswith('/api/office-image?digest=')
        status, body = _get(connection, authority, payload['pages'][0]['url'])
        assert status == 200 and body[:8] == b'\x89PNG\r\n\x1a\n'
        status, _body = _get(connection, authority, '/api/office-image?digest=' + 'f' * 64 + '&page=1')
        assert status == 400
        status, _body = _get(connection, authority, '/api/office-image?digest=' + 'f' * 64 + '&page=x')
        assert status == 400
        connection.close()
    finally:
        _stop_server(server, thread)


@pytest.mark.skipif(os.name == 'nt', reason='stub uses a POSIX shebang')
def test_version_checks_and_review_status_compose_the_office_key(tmp_path, monkeypatch):
    install_stub(tmp_path, monkeypatch)
    server, thread = _start_server(tmp_path / 'workspace')
    try:
        store = server.store
        source = store.add_source('Synthetic', 'Revenue was USD 12 million.')
        run = store.create_run({'title': 'T', 'objective': 'o', 'allow_web': False}, [source['id']])
        brief = store.publish(run['id'], {'title': 'T', 'markdown': 'Revenue was USD 12 million.'})
        from briefloop.delivery_checks import brief_checks
        from briefloop.review import review_status
        connection, authority, token = _session(server)
        # Without records both responses equal the baseline checks byte for key.
        status, payload = _get(connection, authority, '/api/version-checks?version=' + brief['id'])
        assert json.loads(payload) == brief_checks(store, brief['id'])
        status, payload = _get(connection, authority, '/api/review-status?version=' + brief['id'])
        assert json.loads(payload) == review_status(store, brief['id'])
        assert 'office' not in brief_checks(store, brief['id'])
        assert 'office_checks' not in review_status(store, brief['id'])
        # An export under the enabled switch writes rows; the server layer composes them.
        store.update_settings({'officecli_enabled': True})
        _completed_export(store, brief)
        status, payload = _get(connection, authority, '/api/version-checks?version=' + brief['id'])
        office = json.loads(payload)['office']
        assert office['tool'] == 'officecli' and office['job_kind'] == 'export_docx'
        assert office['validate']['status'] == 'ok'
        baseline = brief_checks(store, brief['id'])  # brief_checks itself stays officecli-free
        assert 'office' not in baseline
        status, payload = _get(connection, authority, '/api/review-status?version=' + brief['id'])
        assert json.loads(payload)['office_checks']['file_sha256'] == office['file_sha256']
        connection.close()
    finally:
        _stop_server(server, thread)
