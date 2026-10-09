import json
import pytest
from briefloop.store import Store, Conflict
from briefloop.source_lifecycle import annotate, change, report_source_ids
from briefloop.source_library_search import search


def test_archive_keeps_two_reports_sources_and_allows_explicit_reuse(tmp_path):
    store = Store(tmp_path)
    source = store.add_source('Shared source', 'needle: 12 deliveries.')
    runs = [store.create_run({'title': title, 'objective': 'Check deliveries', 'allow_web': False}, [source['id']])
            for title in ('Previous period', 'Current period')]
    briefs = [store.publish(r['id'], {'title': 'Report', 'markdown': '12 deliveries.'}) for r in runs]
    before = [store.one('runs', r['id']) for r in runs]
    change(store, [source['id']], True)
    first = annotate(store, [source])[0]['archived_at']
    change(store, [source['id']], True)
    assert annotate(store, [source])[0]['archived_at'] == first
    assert store.one('sources', source['id']) == source
    assert store.source_text(source['id']) == 'needle: 12 deliveries.'
    assert [store.one('runs', r['id']) for r in runs] == before
    assert [store.one('briefs', b['id']) for b in briefs] == briefs
    assert all(report_source_ids(store, r['id']) == [source['id']] for r in runs)
    assert search(store, 'needle', scope='active')['items'] == []
    assert search(store, 'needle', scope='archived', run_id=runs[1]['id'])['items'][0]['source_id'] == source['id']
    explicit = store.create_run({'title': 'Explicit reuse', 'objective': 'Check', 'allow_web': False}, [source['id']])
    assert store.source_ids(explicit['id']) == [source['id']]
    change(store, [source['id']], False)
    assert annotate(store, [source])[0]['archived_at'] is None
    assert search(store, 'needle', scope='active')['items']


def test_archive_atomic_validation_and_search_cursor_scope(tmp_path):
    store = Store(tmp_path)
    sources = [store.add_source(f'{i}.txt', 'needle') for i in range(3)]
    with pytest.raises(ValueError):change(store, [sources[0]['id'], 'missing'], True)
    assert not annotate(store, sources)[0]['archived_at']
    with pytest.raises(ValueError):change(store, [sources[0]['id']], 'false')
    first = search(store, 'needle', scope='active', order='newest', limit=1)
    assert first['items'][0]['source_id'] == sources[-1]['id']
    change(store, [sources[1]['id']], True)
    with pytest.raises(Conflict):search(store, 'needle', scope='active', order='newest', cursor=first['next_cursor'])


def test_archive_endpoint_requires_token_and_source_remains_readable(tmp_path):
    import http.client
    import threading
    from briefloop.server import make_server, _close_service
    server = make_server(tmp_path, port=0, paused=True)
    thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
    try:
        source = server.store.add_source('HTTP source', 'readable')
        def request(method, path, body=None, token=None):
            conn = http.client.HTTPConnection('127.0.0.1', server.server_port)
            headers = {'Content-Type': 'application/json'}
            if token: headers['X-BriefLoop-Token'] = token
            conn.request(method, path, body=json.dumps(body) if body else None, headers=headers)
            res = conn.getresponse(); value = res.status, json.loads(res.read()); conn.close(); return value
        body = {'source_ids': [source['id']], 'archived': True}
        assert request('POST', '/api/source-archive', body)[0] == 403
        token = request('GET', '/api/session')[1]['token']
        assert request('POST', '/api/source-archive', body, token)[0] == 200
        state = request('GET', '/api/state')[1]
        assert next(s for s in state['sources'] if s['id'] == source['id'])['archived_at']
        assert request('GET', '/api/source?id=' + source['id'])[0] == 200
        assert request('POST', '/api/source-archive', {**body, 'archived': False}, token)[0] == 200
    finally:
        server.shutdown(); thread.join(); _close_service(server)
