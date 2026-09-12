import http.client
import json
import threading

from briefloop.server import make_server
from briefloop.sources import upload


def test_foreign_host_cannot_read_workspace_or_use_session_token(tmp_path):
    server = make_server(tmp_path / 'workspace', port=0, paused=True)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    authority = f'127.0.0.1:{server.server_port}'

    def request(path, *, host=authority, body=None, token=None):
        connection = http.client.HTTPConnection('127.0.0.1', server.server_port)
        headers = {'Host': host}
        if token:
            headers.update({'X-BriefLoop-Token': token, 'Origin': 'http://' + authority})
        connection.request('POST' if body is not None else 'GET', path,
                           body=json.dumps(body) if body is not None else None,
                           headers=headers)
        response = connection.getresponse()
        result = response.status, response.read()
        connection.close()
        return result

    try:
        source = upload(server.store, 'private-synthetic.txt', b'Synthetic private report source.')
        original = '/api/source-original?id=' + source['id']
        assert request(original) == (200, b'Synthetic private report source.')
        status, data = request('/api/session')
        assert status == 200
        token = json.loads(data)['token']
        for path in (original, '/api/state', '/api/session'):
            status, data = request(path, host=f'untrusted.example:{server.server_port}')
            assert status == 403, (path, status, data)
            assert b'Synthetic private report source.' not in data
            assert token.encode() not in data
        # Even possession of the token does not admit a foreign authority.
        assert request('/api/settings', host=f'untrusted.example:{server.server_port}',
                       body={'auto_learn': False}, token=token)[0] == 403
        assert request('/api/settings', body={'auto_learn': False}, token=token)[0] == 200
        assert request('/api/service-stop',body={'pid':0,'workspace_id':'wrong'},token=token)[0]!=200
        assert request('/api/state')[0]==200
        assert server.store.settings()['auto_learn'] is False
    finally:
        server.shutdown()
        thread.join()
        server.harness.close()
        server.opencode_harness.close()
        server.server_close()
        server.workspace_lock.close()
