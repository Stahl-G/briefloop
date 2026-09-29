"""Live directory behavior against a local HTTP provider; no paid inference."""
from concurrent.futures import ThreadPoolExecutor
import http.server
import json
import threading
from urllib.parse import parse_qs, urlsplit

import pytest

from briefloop import native_providers
from briefloop.provider_catalog import read_provider_catalog


@pytest.fixture
def provider(tmp_path, monkeypatch):
    state = {'data': {'data': [{'id': 'api-new'}]}, 'status': 200, 'requests': []}

    class Handler(http.server.BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_GET(self):
            state['requests'].append((self.path, dict(self.headers)))
            if callback := state.get('callback'):
                status, data, headers = callback(self.path)
            else:
                status, data, headers = state['status'], state['data'], {}
            self.send_response(status)
            self.send_header('content-type', 'application/json')
            for key, value in headers.items():
                self.send_header(key, value)
            self.end_headers()
            self.wfile.write(json.dumps(data).encode())

    server = http.server.ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    monkeypatch.setattr(native_providers, 'config_path', lambda: tmp_path / 'providers.json')
    state['config'] = {'provider': 'fixture', 'model': 'local-only', 'protocol': 'chat-completions',
                       'base_url': f'http://127.0.0.1:{server.server_port}/v1', 'api_key': 'fixture-secret-key'}
    native_providers.save(state['config'])
    yield state
    server.shutdown()
    server.server_close()


def test_live_add_remove_refresh_and_failure_never_falls_back(provider):
    first = native_providers.model_catalog()
    assert [m['id'] for m in first['models']] == ['fixture/api-new']
    assert first['source'] == 'provider_api' and first['inference_tested'] is False
    assert first['providers'][0]['refreshed_at']
    provider['data'] = {'data': [{'id': 'replacement'}]}
    refreshed = native_providers.model_catalog(refresh=True)
    assert [m['id'] for m in refreshed['models']] == ['fixture/replacement']
    provider['status'] = 401
    provider['data'] = {'error': provider['config']['api_key']}
    failed = native_providers.model_catalog(refresh=True)
    assert failed['models'] == [] and failed['status'] == 'auth_failed'
    assert provider['config']['api_key'] not in json.dumps(failed)
    assert len(provider['requests']) == 3
    assert all(path == '/v1/models' for path, _ in provider['requests'])
    assert all(headers['Authorization'] == 'Bearer fixture-secret-key' for _, headers in provider['requests'])
    assert all(headers['User-Agent'].startswith('BriefLoop/') for _, headers in provider['requests'])


def test_redirect_never_forwards_credentials(provider):
    provider['callback'] = lambda path: (302, {}, {'Location': 'http://localhost:9/stolen'})
    result = native_providers.catalog({'provider': 'fixture'})
    assert result['status'] == 'redirect_blocked' and result['models'] == []
    assert len(provider['requests']) == 1
    assert 'fixture-secret-key' not in json.dumps(result)


def test_anthropic_and_google_paginate_the_same_saved_connection(provider):
    def anthropic(path):
        parsed = urlsplit(path)
        assert parsed.path == '/v1/models'
        if parse_qs(parsed.query).get('after_id') == ['a']:
            return 200, {'data': [{'id': 'b'}], 'has_more': False}, {}
        return 200, {'data': [{'id': 'a'}], 'has_more': True, 'last_id': 'a'}, {}

    provider['callback'] = anthropic
    result = read_provider_catalog({**provider['config'], 'protocol': 'anthropic-messages'})
    assert result['models'] == ['a', 'b']
    assert all(headers['X-Api-Key'] == 'fixture-secret-key' for _, headers in provider['requests'])
    assert all('Authorization' not in headers for _, headers in provider['requests'])
    provider['requests'].clear()

    def google(path):
        parsed = urlsplit(path)
        assert parsed.path == '/v1beta/models'
        assert 'key' not in parse_qs(parsed.query)
        if parse_qs(parsed.query).get('pageToken') == ['next']:
            return 200, {'models': [{'name': 'models/g2', 'supportedGenerationMethods': ['generateContent']}]}, {}
        return 200, {'models': [{'name': 'models/g1', 'supportedGenerationMethods': ['generateContent']},
                                {'name': 'models/embedding', 'supportedGenerationMethods': ['embedContent']}],
                     'nextPageToken': 'next'}, {}

    provider['callback'] = google
    config = {**provider['config'], 'protocol': 'google', 'base_url': provider['config']['base_url'].removesuffix('/v1')}
    assert read_provider_catalog(config)['models'] == ['g1', 'g2']
    assert all(headers['X-Goog-Api-Key'] == 'fixture-secret-key' for _, headers in provider['requests'])


def test_invalid_unknown_and_partial_catalogs_are_not_available(provider):
    for data in ({}, {'data': ['invalid']}, {'data': [{'id': 'fixture-secret-key'}]}):
        provider['data'] = data
        result = read_provider_catalog(provider['config'])
        assert result['status'] == 'invalid_catalog' and result['models'] == []
        assert 'fixture-secret-key' not in json.dumps(result)
    assert read_provider_catalog({**provider['config'], 'protocol': 'unknown'})['status'] == 'unsupported_protocol'
    assert len(provider['requests']) == 3
    provider['callback'] = lambda path: (200, {'data': [{'id': 'a'}], 'has_more': True, 'last_id': 'a'}, {})
    incomplete = read_provider_catalog({**provider['config'], 'protocol': 'anthropic-messages'})
    assert incomplete['status'] == 'invalid_catalog' and incomplete['models'] == []


def test_overlapping_refreshes_share_one_request_and_next_refresh_is_fresh(provider, monkeypatch):
    from briefloop import provider_catalog
    entered, release = threading.Event(), threading.Event()
    calls = []
    original = provider_catalog.read_provider_catalog

    def slow(config):
        calls.append(config['provider'])
        entered.set()
        assert release.wait(3)
        return original(config)

    monkeypatch.setattr(provider_catalog, 'read_provider_catalog', slow)
    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(native_providers.catalog, {'provider': 'fixture', 'refresh': True})
        assert entered.wait(3)
        # Wait until the second caller reaches the shared future, not a sleep.
        pending = next(iter(native_providers._CATALOG_PENDING.values()))
        original_result = pending.result
        joined = threading.Event()
        def tracked_result(*args, **kwargs):
            joined.set()
            return original_result(*args, **kwargs)
        monkeypatch.setattr(pending, 'result', tracked_result)
        second = pool.submit(native_providers.catalog, {'provider': 'fixture', 'refresh': True})
        assert joined.wait(3)
        release.set()
        assert first.result()['models'] == second.result()['models'] == ['api-new']
    assert len(calls) == 1
    native_providers.catalog({'provider': 'fixture', 'refresh': True})
    assert len(calls) == 2


def test_harness_passes_refresh_and_only_describes_live_ids(provider, tmp_path, monkeypatch):
    from briefloop.native_harness import NativeHarness
    from briefloop.store import Store
    calls = []
    class Engine:
        def call(self, method, params, timeout):
            calls.append((method, params))
            return {'models': [{'id': 'fixture/api-new', 'thinking_levels': ['off']}, {'id': 'static/phantom'}]}
    harness = NativeHarness(Store(tmp_path / 'ws'), Engine())
    result = harness.model_catalog(refresh=True)
    assert [m['id'] for m in result['models']] == ['fixture/api-new']
    assert calls == [('describe_models', {'models': ['fixture/api-new'], 'refresh': True})]


def test_deepseek_anthropic_directory_uses_same_origin_models_and_bearer(monkeypatch):
    from briefloop import provider_catalog
    requests = []
    class Response:
        def __enter__(self):
            return self
        def __exit__(self, *args):
            pass
        def read(self, limit):
            return b'{"data":[{"id":"provider-declared"}]}'
    class Opener:
        def open(self, request, timeout):
            requests.append(request)
            return Response()
    monkeypatch.setattr(provider_catalog.urllib.request, 'build_opener', lambda *args: Opener())
    for base in ('https://api.deepseek.com/anthropic', 'https://api.deepseek.com/anthropic/v1'):
        result = read_provider_catalog({'base_url': base, 'protocol': 'anthropic-messages', 'api_key': 'fixture-key'})
        assert result['models'] == ['provider-declared']
        request = requests[-1]
        assert request.full_url == 'https://api.deepseek.com/models'
        assert request.headers['Authorization'] == 'Bearer fixture-key'
        assert 'X-api-key' not in request.headers
