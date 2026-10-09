import io
import json
from urllib.error import HTTPError
import pytest
from briefloop import software_release_notes as notes
from briefloop import __version__


def test_packaged_current_changelog_is_version_bound():
    current = notes.bundled_notes(__version__)
    assert current['version'] == __version__ and current['notes'].strip()
    assert notes.bundled_notes('999.0.0') is None


def test_exact_public_tag_text_and_success_cache(monkeypatch):
    monkeypatch.setattr(notes, '_cache', {})
    calls = []
    def fetch(request, timeout):
        calls.append(request.full_url)
        return io.BytesIO(json.dumps({'tag_name': 'v0.30.0', 'draft': False, 'prerelease': False,
                                     'body': '<script>plain text</script>'}).encode())
    monkeypatch.setattr(notes, 'urlopen', fetch)
    result = notes.release_notes('0.30.0')
    assert result['version'] == '0.30.0' and result['notes'] == '<script>plain text</script>'
    assert result['state'] == 'loaded'
    assert notes.release_notes('0.30.0') == result
    assert calls == ['https://api.github.com/repos/Stahl-G/briefloop/releases/tags/v0.30.0']
    with pytest.raises(ValueError):
        notes.release_notes('../latest')


@pytest.mark.parametrize('patch', [{'tag_name': 'v0.31.0'}, {'draft': True}, {'prerelease': True}])
def test_other_version_or_unpublished_notes_never_substitute(monkeypatch, patch):
    monkeypatch.setattr(notes, '_cache', {})
    value = {'tag_name': 'v0.30.0', 'draft': False, 'prerelease': False, 'body': 'Wrong release', **patch}
    monkeypatch.setattr(notes, 'urlopen', lambda *a, **k: io.BytesIO(json.dumps(value).encode()))
    result = notes.release_notes('0.30.0')
    assert result['state'] == 'error' and result['notes'] == ''


def test_unavailable_offline_and_empty_are_distinct_and_retryable(monkeypatch):
    monkeypatch.setattr(notes, '_cache', {})
    def absent(*args, **kwargs):
        raise HTTPError('https://api.github.com', 404, 'Not found', {}, None)
    monkeypatch.setattr(notes, 'urlopen', absent)
    assert notes.release_notes('0.30.0')['state'] == 'unavailable'
    def offline(*args, **kwargs):
        raise OSError('Offline')
    monkeypatch.setattr(notes, 'urlopen', offline)
    assert notes.release_notes('0.30.0')['state'] == 'error'
    monkeypatch.setattr(notes, 'urlopen', lambda *a, **k: io.BytesIO(json.dumps(
        {'tag_name': 'v0.30.0', 'draft': False, 'prerelease': False, 'body': ''}).encode()))
    assert notes.release_notes('0.30.0')['state'] == 'empty'


def test_release_notes_route_uses_exact_requested_version(tmp_path, monkeypatch):
    import http.client
    import threading
    from briefloop.server import make_server
    monkeypatch.setattr(notes, 'release_notes', lambda version: {'version': version, 'state': 'loaded', 'notes': 'Synthetic notes'})
    server = make_server(tmp_path / 'synthetic-workspace', port=0, paused=True)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    authority = f'127.0.0.1:{server.server_port}'
    connection = http.client.HTTPConnection('127.0.0.1', server.server_port)
    try:
        connection.request('GET', '/api/session')
        token = json.loads(connection.getresponse().read())['token']
        connection.request('POST', '/api/software-release-notes', body=json.dumps({'version': '0.30.0'}),
                           headers={'X-BriefLoop-Token': token, 'Origin': 'http://' + authority})
        response = connection.getresponse()
        assert response.status == 200
        assert json.loads(response.read()) == {'version': '0.30.0', 'state': 'loaded', 'notes': 'Synthetic notes'}
    finally:
        connection.close()
        from briefloop.server import _close_service
        server.shutdown()
        thread.join()
        _close_service(server)
