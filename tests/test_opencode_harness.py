"""Opencode transport mapping: v1 protocol shape and turn journaling."""
import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import pytest
from briefloop.backends.opencode_server import (OpencodeServerClient, model_ref, prompt_model,
                                               split_model)
from briefloop.opencode_harness import OpencodeHarness
from briefloop.store import Store


def test_split_model_requires_provider_prefix():
    assert split_model('opencode-go/gpt-5.6-luna') == ('opencode-go', 'gpt-5.6-luna')
    assert split_model('openrouter/a/b') == ('openrouter', 'a/b')
    for bad in ('gpt-5.6-luna', '', '/x', 'p/', None):
        with pytest.raises(ValueError):
            split_model(bad)
    assert model_ref('opencode-go/gpt-5.6-luna') == {'providerID': 'opencode-go', 'id': 'gpt-5.6-luna'}
    assert prompt_model('opencode-go/gpt-5.6-luna') == {'providerID': 'opencode-go', 'modelID': 'gpt-5.6-luna'}


class FakeOpencodeAPI(BaseHTTPRequestHandler):
    log_message = lambda *args: None

    def _send(self, value, status=200):
        body = json.dumps(value).encode() if value is not None else b''
        self.send_response(status)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        length = int(self.headers.get('Content-Length', '0'))
        body = json.loads(self.rfile.read(length) or b'{}')
        assert self.headers.get('Authorization', '').startswith('Basic ')
        if self.path.startswith('/session') and 'prompt_async' not in self.path and 'abort' not in self.path:
            assert 'directory=/tmp/ws' in self.path
            assert body['model'] == {'providerID': 'opencode-go', 'id': 'gpt-5.6-luna'}
            assert body['permission'][0] == {'permission': 'question', 'action': 'deny', 'pattern': '*'}
            self.server.created.append(body)
            self._send({'id': 'ses_fake'})
        elif self.path == '/session/ses_fake/prompt_async':
            if 'model' in body:
                assert body['model'] == {'providerID': 'opencode-go', 'modelID': 'gpt-5.6-luna'}
            assert body['parts'][0]['type'] == 'text'
            self.server.prompts.append(body)
            self._send(None, 204)
        elif self.path == '/session/ses_fake/abort':
            self.server.interrupts.append(True)
            self._send(True)
        else:
            self.send_error(404)

    def _send(self, value, status=200):
        body = json.dumps(value).encode() if value is not None else b''
        self.send_response(status)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path == '/session/ses_fake/message':
            assistant = {'info': {'id': 'msg_a1', 'sessionID': 'ses_fake', 'role': 'assistant',
                                  'time': {'created': 1001, 'completed': 1002}, 'finish': 'stop',
                                  'tokens': {'input': 10, 'output': 5, 'reasoning': 0,
                                             'cache': {'read': 0, 'write': 0}}, 'cost': 0.001},
                         'parts': [{'type': 'text', 'text': 'hello done'},
                                   {'type': 'tool', 'tool': 'bash', 'id': 'call_1',
                                    'state': {'status': 'completed', 'input': {'command': 'pwd'}}}]}
            self._send([{'info': {'id': 'msg_u1', 'role': 'user', 'time': {'created': 1000}},
                         'parts': []}, assistant])
        elif self.path == '/session/ses_fake/children':
            self._send([])
        elif self.path == '/config/providers':
            self._send({'providers': [{'id': 'b-prov', 'models': {'m1': {}}}]})
        else:
            self.send_error(404)


def test_server_client_maps_v1_shapes():
    server = ThreadingHTTPServer(('127.0.0.1', 0), FakeOpencodeAPI)
    server.created = []
    server.prompts = []
    server.interrupts = []
    threading.Thread(target=server.serve_forever, daemon=True).start()
    client = OpencodeServerClient.__new__(OpencodeServerClient)
    client.port = server.server_port
    client.password = 'test'
    client.timeout = 10
    created = client.create_session('title', agent='build',
                                    model={'providerID': 'opencode-go', 'id': 'gpt-5.6-luna'},
                                    permission=[{'permission': 'question', 'action': 'deny', 'pattern': '*'}],
                                    directory='/tmp/ws')
    assert created == {'id': 'ses_fake'}
    client.prompt_async('ses_fake', 'hi', model='opencode-go/gpt-5.6-luna')
    assert server.prompts[0]['agent'] == 'build'
    client.prompt_async('ses_fake', 'see', files=[{'type': 'file', 'mime': 'image/png',
                                                  'filename': 'c.png', 'url': 'data:image/png;base64,AA=='}])
    file_parts = [p for p in server.prompts[1]['parts'] if p['type'] == 'file']
    assert len(file_parts) == 1 and file_parts[0]['mime'] == 'image/png'
    assert server.prompts[1]['parts'][0] == {'type': 'text', 'text': 'see'}
    assert client.messages('ses_fake')[1]['info']['id'] == 'msg_a1'
    assert client.children('ses_fake') == []
    assert client.abort('ses_fake') is True
    assert client.providers() == {'providers': [{'id': 'b-prov', 'models': {'m1': {}}}]}
    server.shutdown()


class FakeClient:
    def __init__(self, *args, **kwargs):
        self.prompts = []
        self.aborts = []
        self.created = []
        self.mode = 'complete'
        process = type('Proc', (), {'poll': lambda self: None, 'pid': 4242})()
        self.process = process

    def create_session(self, title, **kwargs):
        assert kwargs['directory']
        self.created.append(kwargs)
        return {'id': 'ses_fake'}

    def prompt_async(self, session_id, text, **kwargs):
        self.prompts.append((session_id, text, kwargs))

    def messages(self, session_id):
        import time as _time
        now = int(_time.time() * 1000)
        if self.mode == 'error':
            return [{'info': {'id': 'msg_u9', 'role': 'user', 'time': {'created': now}}, 'parts': []},
                    {'info': {'id': 'msg_a9', 'role': 'assistant', 'time': {'created': now, 'completed': now},
                              'finish': 'error', 'error': {'type': 'unknown', 'message': 'Provider request failed'}},
                     'parts': []}]
        info = {'id': 'msg_a1', 'role': 'assistant', 'time': {'created': now}, 'finish': 'stop',
                'tokens': {'input': 10, 'output': 5, 'reasoning': 0,
                           'cache': {'read': 0, 'write': 0}}, 'cost': 0.001}
        if self.mode != 'running':
            info['time']['completed'] = now
        return [{'info': {'id': 'msg_u1', 'role': 'user', 'time': {'created': now - 1}}, 'parts': []},
                {'info': info,
                 'parts': [{'type': 'text', 'text': 'hello done'},
                           {'type': 'tool', 'tool': 'bash', 'id': 'call_1',
                            'state': {'status': 'completed', 'input': {'command': 'pwd'}}}]}]

    def abort(self, session_id):
        self.aborts.append(session_id)
        return True

    def children(self, session_id):
        return []

    def providers(self):
        self.provider_calls = getattr(self, 'provider_calls', 0) + 1
        return {'providers': [{'id': 'b-prov', 'models': {'m2': {'name': 'M Two'}, 'm1': {}}},
                              {'id': 'a-prov', 'models': {'m0': {'name': 'M Zero'}}}]}

    def close(self):
        pass


def until(check):
    end = time.monotonic() + 10
    while time.monotonic() < end:
        if check():
            return
        time.sleep(.05)
    assert check()


def test_list_models_flattens_sorts_and_caches(tmp_path):
    manager = OpencodeHarness(Store(tmp_path), FakeClient)
    first = manager.list_models()
    assert [m['id'] for m in first] == ['a-prov/m0', 'b-prov/m1', 'b-prov/m2']
    assert first[0] == {'id': 'a-prov/m0', 'provider': 'a-prov', 'name': 'M Zero'}
    assert first[1]['name'] == 'm1'
    assert manager.client.provider_calls == 1
    manager.list_models()
    assert manager.client.provider_calls == 1
    manager.list_models(refresh=True)
    assert manager.client.provider_calls == 2
    manager.close()


def test_harness_drives_turn_and_projects_events(tmp_path):
    manager = OpencodeHarness(Store(tmp_path), FakeClient)
    sid = manager.create_session('task', runtime={'model': 'opencode-go/gpt-5.6-luna'})['id']
    assert manager.client is None
    manager.send(sid, 'do work', message_id='m1')
    until(lambda: any(m['role'] == 'assistant' and m['status'] == 'completed'
                      for m in manager.snapshot(sid)['messages']))
    snap = manager.snapshot(sid)
    assert snap['messages'][-1]['text'] == 'hello done'
    kinds = [e['kind'] for e in snap['events']]
    assert 'message/delivered' in kinds
    assert 'item/completed' in kinds
    usage = snap['token_usage']
    assert usage['cost'] == 0.001 and usage['backend'] == 'opencode'
    assert manager.client.created[0]['model'] == {'providerID': 'opencode-go', 'id': 'gpt-5.6-luna'}
    assert manager.client.prompts[0][2]['model'] == {'providerID': 'opencode-go', 'modelID': 'gpt-5.6-luna'}
    with pytest.raises(ValueError, match='提问'):
        manager.answer(sid, 'q', {})
    manager.close()


def test_settings_side_model_variant_reaches_session(tmp_path):
    # P1-1: settings/chat carry `model_variant`; opencode takes `variant`.
    manager = OpencodeHarness(Store(tmp_path), FakeClient)
    session = manager.create_session('v', runtime={'model': 'opencode-go/x', 'model_variant': 'high',
                                                   'backend': 'opencode'})
    stored = manager.snapshot(session['id'])['session']['runtime']
    assert stored['variant'] == 'high' and 'model_variant' not in stored
    assert OpencodeHarness._config({'model': 'opencode-go/x', 'variant': 'max',
                                    'model_variant': 'high'})['variant'] == 'max'
    manager.send(session['id'], 'go', message_id='v1')
    until(lambda: any(m['role'] == 'assistant' and m['status'] == 'completed'
                      for m in manager.snapshot(session['id'])['messages']))
    assert manager.client.created[0]['model'] == {'providerID': 'opencode-go', 'id': 'x',
                                                  'variant': 'high'}
    manager.close()


def test_error_completion_is_failure_not_success(tmp_path):
    manager = OpencodeHarness(Store(tmp_path), FakeClient)
    sid = manager.create_session()['id']
    manager._client().mode = 'error'
    manager.send(sid, 'will fail', message_id='m3')
    until(lambda: manager.snapshot(sid)['session']['status'] == 'failed')
    snap = manager.snapshot(sid)
    assert snap['messages'][-1]['status'] == 'failed'
    assert any(e['kind'] == 'error' and 'Provider request failed' in str(e['data']) for e in snap['events'])
    manager.close()


def _test_png():
    import io
    from PIL import Image
    buffer = io.BytesIO()
    Image.new('RGB', (8, 4), (90, 140, 180)).save(buffer, format='PNG')
    return buffer.getvalue()


def test_image_source_becomes_direct_file_part(tmp_path):
    from briefloop import sources
    store = Store(tmp_path)
    record = sources.upload(store, 'chart.png', _test_png())
    assert record['status'] == 'ready'
    manager = OpencodeHarness(store, FakeClient)
    text, files = manager._input({'text': '看这张图', 'prompt': None, 'source_ids': [record['id']]})
    assert len(files) == 1
    part = files[0]
    assert part['type'] == 'file' and part['mime'] == 'image/png'
    assert part['url'].startswith('data:image/png;base64,')
    assert record['id'] in text and '图片附件' in text
    manager.close()


def test_oversize_image_skipped_with_note(tmp_path, monkeypatch):
    import briefloop.opencode_harness as harness_module
    from briefloop import sources
    monkeypatch.setattr(harness_module, 'ATTACH_IMAGE_MAX_BYTES', 10)
    store = Store(tmp_path)
    record = sources.upload(store, 'chart.png', _test_png())
    manager = OpencodeHarness(store, FakeClient)
    text, files = manager._input({'text': '看图', 'prompt': None, 'source_ids': [record['id']]})
    assert files == [] and record['id'] in text and '超过' in text
    manager.close()


def test_full_turn_sends_file_parts(tmp_path):
    from briefloop import sources
    store = Store(tmp_path)
    image = sources.upload(store, 'chart.png', _test_png())
    text_src = store.add_source('note', 'hello')
    manager = OpencodeHarness(store, FakeClient)
    sid = manager.create_session()['id']
    manager.send(sid, '看附件', source_ids=[image['id'], text_src['id']], message_id='img1')
    until(lambda: any(m['role'] == 'assistant' and m['status'] == 'completed'
                      for m in manager.snapshot(sid)['messages']))
    sent = manager.client.prompts[0][2]
    assert len(sent['files']) == 1 and sent['files'][0]['mime'] == 'image/png'
    assert image['id'] in manager.client.prompts[0][1]
    manager.close()


def test_permission_fallback_only_on_schema_rejection(tmp_path):
    from briefloop.backends.opencode_server import OpencodeError

    class StrictAPI(FakeOpencodeAPI):
        def do_POST(self):
            if self.path.startswith('/session'):
                length = int(self.headers.get('Content-Length', '0'))
                body = json.loads(self.rfile.read(length) or b'{}')
                if 'permission' in body:
                    self.send_error(400)
                    return
                assert body['model'] == {'providerID': 'opencode-go', 'id': 'gpt-5.6-luna'}
                self._send({'id': 'ses_fake'})
                return
            return super().do_POST()

    server = ThreadingHTTPServer(('127.0.0.1', 0), StrictAPI)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    client = OpencodeServerClient.__new__(OpencodeServerClient)
    client.port = server.server_port
    client.password = 'test'
    client.timeout = 10
    created = client.create_session('t', model={'providerID': 'opencode-go', 'id': 'gpt-5.6-luna'},
                                    permission=[{'permission': 'question', 'action': 'deny', 'pattern': '*'}],
                                    directory='/tmp/ws')
    assert created['_permission_dropped'] is True and created['id'] == 'ses_fake'

    class AuthFailAPI(FakeOpencodeAPI):
        def do_POST(self):
            self.send_error(401)

    server2 = ThreadingHTTPServer(('127.0.0.1', 0), AuthFailAPI)
    threading.Thread(target=server2.serve_forever, daemon=True).start()
    client2 = OpencodeServerClient.__new__(OpencodeServerClient)
    client2.port = server2.server_port
    client2.password = 'test'
    client2.timeout = 10
    try:
        client2.create_session('t', permission=[{'permission': 'question'}], directory='/tmp/ws')
        raise AssertionError('401 must surface, not fall back')
    except OpencodeError as exc:
        assert exc.status == 401
    server.shutdown()
    server2.shutdown()


def test_harness_cancel_aborts_and_rejects_backend_switch(tmp_path):
    manager = OpencodeHarness(Store(tmp_path), FakeClient)
    sid = manager.create_session()['id']
    manager._client().mode = 'running'
    manager.send(sid, 'long work', message_id='m2')
    until(lambda: manager.snapshot(sid)['session']['status'] == 'running')
    manager.cancel(sid)
    until(lambda: manager.snapshot(sid)['session']['status'] == 'cancelled')
    assert manager.client.aborts == ['ses_fake']
    with pytest.raises(ValueError, match='opencode'):
        manager.send(sid, 'x', runtime={'model': 'gpt-5.6-luna', 'backend': 'codex'})
    manager.close()


def test_pack_figures_attaches_cited_images_and_notes_missing(tmp_path, monkeypatch):
    import base64
    from briefloop import opencode_harness as harness_module
    folder = tmp_path / 'job'
    folder.mkdir()
    png = base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+a2ioAAAAASUVORK5CYII=')
    (folder / 'a.png').write_bytes(png)
    packet = {'figures': [{'figure_id': 'fig_ok', 'title': 'T', 'absolute_image_path': str(folder / 'a.png')},
                          {'figure_id': 'fig_gone', 'title': 'G', 'absolute_image_path': str(folder / 'nope.png')}]}
    (folder / 'input.json').write_text(json.dumps(packet), encoding='utf-8')
    out = OpencodeHarness._pack_figures(folder)
    assert len(out) == 2
    assert out[0][1]['url'].startswith('data:image/png;base64,') and 'fig_ok' in out[0][0]
    assert out[1][1] is None and 'fig_gone' in out[1][0]
    assert OpencodeHarness._pack_figures(None) == []
    assert OpencodeHarness._pack_figures(tmp_path / 'empty') == []
    monkeypatch.setattr(harness_module, 'ATTACH_IMAGE_MAX_BYTES', 10)
    capped = OpencodeHarness._pack_figures(folder)
    assert capped[0][1] is None and '过大' in capped[0][0]
