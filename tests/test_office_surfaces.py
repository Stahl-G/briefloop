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


@pytest.mark.skipif(os.name == 'nt', reason='stub uses a POSIX shebang')
def test_release_chain_stays_frozen_with_office_checks_running(tmp_path, monkeypatch):
    install_stub(tmp_path, monkeypatch)
    store, source, brief, review = reviewed_report(tmp_path)
    store.update_settings({'officecli_enabled': True})
    release, job = complete_release(store, brief)
    assert release['status'] == 'released'
    assert store.one('jobs', job['id'])['status'] == 'complete'
    # The hook ran inside generate_release (between the two eligibility calls).
    assert office_cli.version_office_view(store, brief['id'])['file_sha256'] == release['result']['sha256']
    # The frozen release input carries no officecli output anywhere.
    serialized = dump(release['data'])
    assert 'officecli' not in serialized and 'office_checks' not in serialized and '"office"' not in serialized
    # A manual refresh after release cannot disturb the frozen identity.
    office_cli.run_check_for_job(store, job['id'])
    from briefloop.release import eligibility, enqueue_release
    checked = eligibility(store, brief['id'])
    assert checked['eligible']
    expected = {key: value for key, value in release['data'].items()
                if key not in ('notices', 'previous_id', 'change_type', 'change_reason')}
    assert checked['input'] == expected
    again = enqueue_release(store, brief['id'])
    assert again['release']['id'] == release['id'] and again['job']['id'] == job['id']


@pytest.mark.skipif(os.name == 'nt', reason='stub uses a POSIX shebang')
def test_audit_bundle_office_render_branches(tmp_path, monkeypatch):
    install_stub(tmp_path, monkeypatch)
    store, source, brief, review = reviewed_report(tmp_path)
    store.update_settings({'officecli_enabled': True})
    release, job = complete_release(store, brief)
    from briefloop.audit_bundle import (_bundle_identity, enqueue_bundle, generate_bundle,
                                        verify_bundle)
    from briefloop.release import sha

    def build(include, permissions=None):
        queued = enqueue_bundle(store, release['id'], permissions or {}, include_office_render=include)
        result = generate_bundle(store, queued, threading.Event())
        store.update_job(queued['id'], 'complete', result=result)
        blob = (store.root / result['path']).read_bytes()
        with ZipFile(BytesIO(blob)) as archive:
            return queued, result, json.loads(archive.read('manifest.json')), blob

    # Included and available: the PNG is hashed into the manifest and verified.
    queued, result, manifest, blob = build(True)
    assert manifest['office_render']['status'] == 'ok'
    assert manifest['office_render']['source_sha256'] == release['result']['sha256']
    assert manifest['office_render']['page'] == 1 and manifest['office_render']['tool'] == 'officecli'
    assert manifest['office_render']['tool_version'] == '9.9.9-stub'
    assert 'office-render/report-page-1.png' in manifest['files']
    assert verify_bundle(blob)['valid'] is True
    payload = json.loads(queued['payload'])
    assert payload['office_render'] is True
    # Both fingerprint sides come from the same identity construction.
    assert sha(dump(_bundle_identity(release['id'], release['result']['manifest_hash'],
                                     payload['permissions'], True)).encode()) == payload['fingerprint']

    # Requested but unavailable: no PNG, the bundle still builds and verifies.
    # (Distinct permissions give a distinct fingerprint; the same request would
    # idempotently reuse the completed bundle above.)
    store.update_settings({'officecli_enabled': False})
    queued_off, result_off, manifest_off, blob_off = build(True, {source['id']: 'original'})
    assert queued_off['id'] != queued['id']
    assert manifest_off['office_render']['status'] == 'unavailable'
    assert 'office-render/report-page-1.png' not in manifest_off['files']
    assert verify_bundle(blob_off)['valid'] is True

    # Default request: identical to the legacy path, no office_render key at all.
    queued_plain, result_plain, manifest_plain, blob_plain = build(False)
    assert 'office_render' not in manifest_plain
    assert json.loads(queued_plain['payload']).get('office_render') is False
    assert queued_plain['id'] != queued['id']
    assert queued_plain['id'] != queued_off['id']
    assert verify_bundle(blob_plain)['valid'] is True


@pytest.mark.skipif(os.name == 'nt', reason='stub uses a POSIX shebang')
def test_audit_bundle_identity_changes_only_with_the_option(tmp_path, monkeypatch):
    install_stub(tmp_path, monkeypatch)
    store, source, brief, review = reviewed_report(tmp_path)
    store.update_settings({'officecli_enabled': True})
    release, job = complete_release(store, brief)
    from briefloop.audit_bundle import _bundle_identity, enqueue_bundle, permissions_for
    from briefloop.release import sha
    permissions = permissions_for([s['id'] for s in release['data']['snapshot']['sources']], {})
    with_identity = _bundle_identity(release['id'], release['result']['manifest_hash'],
                                     permissions, True)
    without_identity = _bundle_identity(release['id'], release['result']['manifest_hash'],
                                        permissions, False)
    assert with_identity['office_render'] is True and without_identity['office_render'] is False
    assert sha(dump(without_identity).encode()) != sha(dump(with_identity).encode())
    plain = enqueue_bundle(store, release['id'], {})
    assert json.loads(plain['payload'])['fingerprint'] == sha(dump(_bundle_identity(
        release['id'], release['result']['manifest_hash'],
        json.loads(plain['payload'])['permissions'], False)).encode())
