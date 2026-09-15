"""Report deletion keeps audit history and never silently drops in-flight work."""
import http.client
import json
import threading

import pytest

from briefloop.server import make_server
from briefloop.store import Store


def published(store, title='待删除报告'):
    source = store.add_source('Synthetic', '本周两项交付。')
    run = store.create_run({'title': title, 'objective': '合成测试', 'allow_web': False}, [source['id']])
    brief = store.publish(run['id'], {'title': title, 'markdown': '# ' + title + '\n\n本周两项交付。'})
    return run, brief


def test_delete_removes_the_report_from_browsing_but_keeps_its_history(tmp_path):
    store = Store(tmp_path)
    run, brief = published(store)
    kept_run, kept_brief = published(store, '保留的报告')

    assert store.delete_report(brief['id']) == {'run_id': run['id'], 'deleted': True}
    snapshot = store.snapshot()
    assert run['id'] not in {r['id'] for r in snapshot['runs']}
    assert brief['id'] not in {b['id'] for b in snapshot['briefs']}
    assert kept_run['id'] in {r['id'] for r in snapshot['runs']}
    # The rows survive for audit exports and version-bound downloads.
    assert store.one('briefs', brief['id'])['run_id'] == run['id']
    assert [row['key'] for row in store.rows("SELECT key FROM meta WHERE key LIKE 'deleted_report:%'")] == ['deleted_report:' + run['id']]


@pytest.mark.parametrize('job', [('export_docx', 'version'), ('generate', 'run')])
def test_delete_refuses_while_a_job_still_references_the_report(tmp_path, job):
    kind, reference = job
    store = Store(tmp_path)
    run, brief = published(store)
    payload = {'version_id': brief['id']} if reference == 'version' else {'run_id': run['id']}
    queued = store.enqueue(kind, payload)

    with pytest.raises(ValueError, match='仍有任务'):
        store.delete_report(brief['id'])
    assert store.one('runs', run['id'])['id'] == run['id']

    with store.tx() as c:
        c.execute("UPDATE jobs SET status='complete' WHERE id=?", (queued['id'],))
    assert store.delete_report(brief['id'])['deleted'] is True


def test_delete_rejects_an_unknown_report(tmp_path):
    store = Store(tmp_path)
    with pytest.raises(ValueError, match='报告不存在'):
        store.delete_report('version_missing')


def test_application_route_deletes_only_that_report(tmp_path):
    server = make_server(tmp_path / 'application', port=0, paused=True)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    def request(path, body=None, headers=None):
        connection = http.client.HTTPConnection('127.0.0.1', server.server_port)
        connection.request('POST' if body is not None else 'GET', path,
                           json.dumps(body) if body is not None else None, headers or {})
        response = connection.getresponse()
        status, result = response.status, json.loads(response.read())
        connection.close()
        return status, result

    try:
        header = {'X-BriefLoop-Token': request('/api/session')[1]['token']}
        run, brief = published(server.store)
        other_run, other_brief = published(server.store, '另一个报告')
        status, result = request('/api/reports/delete', {'version_id': brief['id']}, header)
        assert status == 200, result
        assert result == {'run_id': run['id'], 'deleted': True}
        state = request('/api/state', None, header)[1]
        assert run['id'] not in {r['id'] for r in state['runs']}
        assert other_run['id'] in {r['id'] for r in state['runs']}
        assert other_brief['id'] in {b['id'] for b in state['briefs']}
    finally:
        server.shutdown()
        server.server_close()
