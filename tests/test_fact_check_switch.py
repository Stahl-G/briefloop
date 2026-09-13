"""Phase A fact-check switch: default off, persistence, offline linkage."""
import http.client
import json
import threading

import pytest

from briefloop.store import Store


def _requirements(**extra):
    base = {'title': 'T', 'objective': 'o', 'allow_web': True}
    base.update(extra)
    return base


def test_fact_check_defaults_off_and_follows_workspace_default(tmp_path):
    store = Store(tmp_path)
    source = store.add_source('Synthetic', 'Revenue was USD 12 million.')
    assert store.settings()['fact_checker'] is False
    assert Store(tmp_path / 'second').settings()['fact_checker'] is False
    run = store.create_run(_requirements(), [source['id']])
    assert json.loads(run['requirements'])['fact_check'] is False
    store.set_meta('settings', {**store.settings(), 'fact_checker': True})
    run = store.create_run(_requirements(), [source['id']])
    assert json.loads(run['requirements'])['fact_check'] is True  # workspace default
    run = store.create_run(_requirements(fact_check=False), [source['id']])
    assert json.loads(run['requirements'])['fact_check'] is False  # task overrides


def test_enabled_fact_check_persists_through_interrupted_run(tmp_path):
    from briefloop.chat_tools import workspace_action
    from briefloop.runtime import Worker
    store = Store(tmp_path)
    source = store.add_source('Synthetic', 'Order count 17.')
    result = workspace_action(store, {'action': 'generate', 'requirements': {
        'title': 'Checked report', 'objective': 'Summarize supplied material',
        'allow_web': True, 'fact_check': True}, 'source_ids': [source['id']]})
    run_id = result['run_id']
    assert json.loads(store.one('runs', run_id)['requirements'])['fact_check'] is True

    class LocalRuntime:
        cancelled = threading.Event()
        attempts = 0

        def execute(self, job, prompt, folder, on_tick):
            self.attempts += 1
            if self.attempts == 1:
                raise RuntimeError('interrupted before drafting')
            (folder / 'draft.json').write_text(json.dumps(
                {'title': 'Checked report', 'markdown': 'Order count 17.'}))
            return {}

    worker = Worker(store, LocalRuntime())
    job = store.one('jobs', result['job_id'])
    with pytest.raises(RuntimeError, match='interrupted'):
        worker.generate(job, score=False)
    generated = worker.generate(job, score=False)  # resume keeps the stored switch
    assert store.one('briefs', generated['version_id'])['run_id'] == run_id
    assert json.loads(store.one('runs', run_id)['requirements'])['fact_check'] is True


def test_offline_fact_check_rejected_at_both_creation_entries(tmp_path):
    from briefloop.external_requests import dispatch
    from briefloop.server import make_server
    from briefloop.sources import upload
    server = make_server(tmp_path / 'workspace', port=0, paused=True)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    authority = f'127.0.0.1:{server.server_port}'
    try:
        source = upload(server.store, 'offline-synthetic.txt', b'Synthetic offline material.')
        connection = http.client.HTTPConnection('127.0.0.1', server.server_port)
        connection.request('GET', '/api/session', headers={'Host': authority})
        token = json.loads(connection.getresponse().read())['token']

        def post(body):
            connection.request('POST', '/api/generate', body=json.dumps(body), headers={
                'Host': authority, 'X-BriefLoop-Token': token, 'Origin': 'http://' + authority})
            response = connection.getresponse()
            return response.status, json.loads(response.read())

        # The web entry rejects the conflicting combination with a structured code.
        status, payload = post({'requirements': _requirements(allow_web=False, fact_check=True),
                                'source_ids': [source['id']]})
        assert status == 400 and payload['code'] == 'fact_check_requires_web'
        assert '离线' in payload['error']
        assert server.store.rows('SELECT * FROM runs') == []  # nothing was created
        # Plain offline tasks keep working; only the web-based switch is refused.
        status, _payload = post({'requirements': _requirements(allow_web=False),
                                 'source_ids': [source['id']]})
        assert status == 200
        runs = server.store.rows('SELECT * FROM runs')
        assert len(runs) == 1 and json.loads(runs[0]['requirements'])['fact_check'] is False
        connection.close()
        # The external entry raises the same structured error.
        with pytest.raises(ValueError) as excinfo:
            dispatch(server.store, {'workspace_id': server.store.meta('workspace_id'),
                                    'action': 'submit', 'request_id': 'offline-check',
                                    'requirements': _requirements(allow_web=False, fact_check=True),
                                    'source_ids': [source['id']]})
        assert getattr(excinfo.value, 'code', None) == 'fact_check_requires_web'
    finally:
        server.shutdown()
        thread.join()
        server.harness.close()
        server.opencode_harness.close()
        server.server_close()
        server.workspace_lock.close()
