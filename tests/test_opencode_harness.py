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
                 'parts': [{'type': 'reasoning', 'text': 'weighing options'},
                           {'type': 'text', 'text': 'hello done'},
                           {'type': 'tool', 'tool': 'bash', 'id': 'call_1',
                            'state': {'status': 'completed', 'input': {'command': 'pwd'}}}]}]

    def abort(self, session_id):
        self.aborts.append(session_id)
        return True

    def children(self, session_id):
        return []

    def providers(self, directory=None):
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
    assert 'reasoning' not in snap['messages'][-1]
    assert manager.snapshot(sid, reasoning=True)['messages'][-1]['reasoning'] == 'weighing options'
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
    assert len(sent['files']) == 1
    part = sent['files'][0]
    assert part['type'] == 'file' and part['mime'] == 'image/png'
    import base64
    assert part['url'].startswith('data:image/png;base64,')
    assert base64.b64decode(part['url'].split(',', 1)[1]) == _test_png()
    assert image['id'] in manager.client.prompts[0][1]
    # A comparison task carries list[case], with saved brief records on both sides.
    # Use the production native serializer while substituting only its HTTP call.
    from io import BytesIO
    from PIL import Image
    from briefloop.figures import register_figure
    cases={};expected={}
    folder=store.root/'comparison';folder.mkdir()
    for side,color in (('baseline','red'),('candidate','green')):
        run=store.create_run({'title':side,'objective':'Compare the chart'},[text_src['id']])
        pixels=BytesIO();Image.new('RGB',(10,6),color).save(pixels,format='PNG')
        plot=store.root/(side+'.png');plot.write_bytes(pixels.getvalue())
        figure=register_figure(store,run['id'],plot,side,source_ids=[text_src['id']])
        brief=store.publish(run['id'],{'title':side,'markdown':figure['markdown'],'figures':[figure['figure_id']]})
        cases[side]=brief;expected['case-1_'+side+'_'+figure['figure_id']+'.png']=(store.root/figure['image_path']).read_bytes()
        plot.write_bytes(b'Producer working file changed after registration')
    (folder/'input.json').write_text(json.dumps([{'case_id':'case-test',**cases}]))
    requests=[];original=manager.client.prompt_async
    def capture(method,path,body):requests.append(body)
    manager.client._request=capture
    def serialize(session_id,text,**kwargs):
        OpencodeServerClient.prompt_async(manager.client,session_id,text,**kwargs)
        original(session_id,text,**kwargs)
    manager.client.prompt_async=serialize
    comparison=manager.create_session('Compare',{'model':'example/selected-vision'},folder)['id']
    manager.send(comparison,'Compare saved versions',message_id='paired-images')
    until(lambda:any(m['role']=='assistant' and m['status']=='completed' for m in manager.snapshot(comparison)['messages']))
    body=requests[0];assert body['model']=={'providerID':'example','modelID':'selected-vision'}
    files=[item for item in body['parts'] if item['type']=='file']
    assert {item['filename']:base64.b64decode(item['url'].split(',',1)[1]) for item in files}==expected
    context=body['parts'][0]['text']
    owners=[json.loads(line.split('：',1)[1]) for line in context.splitlines() if line.startswith('比较图表归属：')]
    assert [(owner['case_id'],owner['side'],owner['run_id'],owner['attachment']) for owner in owners]==[
        ('case-test',side,cases[side]['run_id'],name) for side in ('baseline','candidate') for name in expected if '_'+side+'_' in name]
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


def test_pending_tool_is_later_projected_as_complete(tmp_path):
    manager=OpencodeHarness(Store(tmp_path),FakeClient)
    session=manager.create_session('progress',{'model':'opencode-go/gpt-5.6-luna'})
    seen=set()
    part={'id':'tool-1','type':'tool','tool':'read','state':{'status':'pending'}}
    manager._project_tool(session['id'],'turn','assistant',part,seen)
    part['state']['status']='completed'
    manager._project_tool(session['id'],'turn','assistant',part,seen)
    manager._project_tool(session['id'],'turn','assistant',part,seen)
    kinds=[e['kind'] for e in manager.store.rows('SELECT kind FROM chat_events WHERE session_id=?',(session['id'],))]
    assert kinds.count('item/started')==1
    assert kinds.count('item/completed')==1


def test_child_history_keeps_only_current_turn_messages(tmp_path):
    manager=OpencodeHarness(Store(tmp_path),FakeClient)
    session=manager.create_session('History',{'model':'opencode-go/gpt-5.6-luna'})
    class Children:
        def children(self,owner):return [{'id':'child'}] if owner=='parent' else []
        def messages(self,owner):
            return [{'info':{'id':identity,'role':'assistant','time':{'created':created}},'parts':[{'type':'tool','id':identity+'-tool','tool':'read','state':{'status':'completed','input':{'filePath':'source'},'output':identity}}]} for identity,created in [('old',1000),('current',10000)]]
    manager._client=lambda:Children()
    manager._record_children(session['id'],'turn','parent',10000)
    records=manager.store.rows("SELECT data FROM chat_events WHERE kind='tool/record'")
    assert len(records)==1
    value=json.loads(records[0]['data'])['record']
    assert value['output']=='current' and value['native_message_id']=='current'


def test_live_child_activity_reaches_parent_progress_without_repeated_heartbeats(tmp_path):
    from briefloop.interactive_runtime import InteractiveRuntime
    from briefloop.progress import ProgressTracker
    store = Store(tmp_path)
    manager = OpencodeHarness(store, FakeClient)
    session = manager.create_session('Parent', {'model': 'opencode-go/gpt-5.6-luna'})
    job = store.enqueue('generate', {})
    folder = store.root / 'jobs' / job['id']; folder.mkdir(parents=True)
    log = folder / 'events.jsonl'
    tracker = ProgressTracker(store, job['id'], folder)

    class Children:
        revision = 0
        finished = False
        hidden = 'SECRET REASONING'
        def children(self, owner):
            return [{'id': 'child', 'title': 'Analyst draft', 'time': {'created': 10000}},
                    {'id': 'old', 'title': 'Old task', 'time': {'created': 1000}}] if owner == 'parent' else []
        def messages(self, owner):
            created = 1000 if owner == 'old' else 10000
            return [{'info': {'id': owner+'-assistant', 'role': 'assistant',
                'time': {'created': created, **({'completed': 12000} if self.finished else {})},
                'finish': 'stop' if self.finished else None}, 'parts': [
                {'type': 'reasoning', 'text': self.hidden},
                {'type': 'tool', 'id': 'write-'+str(self.revision), 'tool': 'write',
                 'state': {'status': 'completed' if self.finished else 'running',
                           'input': {'filePath': 'draft.json', 'content': 'PRIVATE DRAFT'},
                           'output': 'PRIVATE TOOL OUTPUT'}}]}]
    client = Children(); manager._client = lambda: client
    poll = {}; cursor = 0; seen = set()
    def project():
        nonlocal cursor
        cursor = InteractiveRuntime._project(manager.snapshot(session['id'], after=cursor), log, cursor, seen)
        tracker.update()
        rows = store.rows("SELECT data FROM events WHERE job_id=? AND kind='runtime_progress' ORDER BY seq", (job['id'],))
        return json.loads(rows[-1]['data']), len(rows)
    assert manager._poll_children(session['id'], 'turn', 'parent', 10000, poll, force=True)
    progress, count = project()
    assert len(progress['agents']) == 1
    assert progress['agents'][0]['id'] == 'child' and progress['agents'][0]['status'] == 'running'
    assert progress['agents'][0]['role'] == 'Analyst' and 'write' in progress['message']
    for _ in range(3):
        assert not manager._poll_children(session['id'], 'turn', 'parent', 10000, poll, force=True)
    assert project()[1] == count
    client.hidden += ' changed'
    assert not manager._poll_children(session['id'], 'turn', 'parent', 10000, poll, force=True)
    client.revision += 1
    assert manager._poll_children(session['id'], 'turn', 'parent', 10000, poll, force=True)
    assert project()[1] == count + 1
    client.finished = True
    assert manager._poll_children(session['id'], 'turn', 'parent', 10000, poll, force=True)
    progress, _ = project()
    assert progress['agents'][0]['status'] == 'completed'
    public = log.read_text() + json.dumps(progress)
    assert all(text not in public for text in ('SECRET REASONING', 'PRIVATE DRAFT', 'PRIVATE TOOL OUTPUT'))


def test_parent_follow_observes_child_before_parent_task_finishes(tmp_path, monkeypatch):
    import briefloop.opencode_harness as module
    from types import SimpleNamespace
    store = Store(tmp_path)
    manager = OpencodeHarness(store, FakeClient)
    sid = manager.create_session('Parent', {'model': 'opencode-go/gpt-5.6-luna'})['id']
    manager.chat.message(sid, 'Work', mid='turn', status='delivered', turn_id='turn')
    manager._epoch[sid] = 1
    manager._bound_session = lambda _: 'parent'
    clock = [0]
    def advance(seconds):clock[0] += max(seconds, 65)
    monkeypatch.setattr(module, 'time', SimpleNamespace(monotonic=lambda: clock[0], sleep=advance))
    class ActiveChild:
        polls = 0
        def children(self, owner):
            return [{'id': 'child', 'title': 'Analyst', 'time': {'created': 10000}}] if owner == 'parent' else []
        def messages(self, owner):
            if owner == 'parent':
                self.polls += 1
                assert self.polls <= 6, 'Parent never observed its running child'
                seen = store.rows("SELECT 1 FROM chat_events WHERE kind='child/item/updated'")
                return [{'info': {'id': 'parent-message', 'role': 'assistant',
                    'time': {'created': 10000, 'completed': 10001}, 'finish': 'stop' if seen and self.polls >= 4 else 'tool-calls'},
                    'parts': [{'type': 'tool', 'id': 'parent-task', 'tool': 'task',
                               'state': {'status': 'running', 'input': {'subagent_type': 'general'}}}]}]
            return [{'info': {'id': 'child-message', 'role': 'assistant', 'time': {'created': 10000}},
                     'parts': [{'type': 'tool', 'id': 'write', 'tool': 'write', 'state': {'status': 'running'}}]}]
    manager._client = lambda: ActiveChildClient
    ActiveChildClient = ActiveChild()
    manager._follow(sid, 1, 'turn', 10000)
    events = manager.snapshot(sid)['events']
    kinds = [e['kind'] for e in events]
    assert kinds.index('child/item/updated') < kinds.index('turn/completed')
    child = next(e['data']['item'] for e in events if e['kind'] == 'child/item/updated')
    assert child['status'] == 'running' and child['agentsStates']['child']['activity'] == '工具 write · running'


@pytest.mark.parametrize('read_failure', [False, True])
def test_plain_chat_child_wait_has_absolute_deadline(tmp_path, monkeypatch, read_failure):
    import briefloop.opencode_harness as module
    from types import SimpleNamespace
    from briefloop.backends.opencode_server import OpencodeError
    store = Store(tmp_path)
    store.set_meta('settings', {**store.settings(), 'timeout_minutes': 1})
    manager = OpencodeHarness(store, FakeClient)
    sid = manager.create_session()['id']
    manager.chat.message(sid, 'Work', mid='turn', status='delivered', turn_id='turn')
    manager._epoch[sid] = 1
    manager._bound_session = lambda _: 'parent'
    clock = [0]
    monkeypatch.setattr(module, 'time', SimpleNamespace(monotonic=lambda: clock[0],
        sleep=lambda seconds: clock.__setitem__(0, clock[0] + 5)))
    class Stuck:
        aborts = []
        def abort(self, owner):self.aborts.append(owner)
        def children(self, owner):
            return [{'id': 'child', 'time': {'created': 10000}}] if owner == 'parent' else []
        def messages(self, owner):
            if read_failure and clock[0] >= 10:
                raise OpencodeError('host unavailable')
            return [{'info': {'id': owner, 'role': 'assistant', 'time': {'created': 10000,
                **({'completed': 10001} if owner == 'parent' else {})},
                'finish': 'tool-calls' if owner == 'parent' else None}, 'parts': []}]
    client = Stuck(); manager._client = lambda: client
    with pytest.raises(TimeoutError, match='时限'):
        manager._follow(sid, 1, 'turn', 10000)
    assert clock[0] == 60
    assert client.aborts == ['parent']
    assert manager.snapshot(sid)['session']['status'] == 'failed'


def test_child_cache_and_queries_remain_bounded_across_disjoint_discoveries(tmp_path):
    manager = OpencodeHarness(Store(tmp_path), FakeClient)
    sid = manager.create_session()['id']
    class Changing:
        generation = 0
        queried = []
        def children(self, owner):
            return [{'id': f'{self.generation}-{i}', 'time': {'created': 10000}}
                    for i in range(150)] if owner == 'parent' else []
        def messages(self, owner):
            self.queried.append(owner)
            return [{'info': {'id': owner, 'role': 'assistant', 'time': {'created': 10000}}, 'parts': []}]
    client = Changing(); manager._client = lambda: client
    poll = {}
    for generation in range(3):
        client.generation = generation
        client.queried = []
        manager._poll_children(sid, 'turn', 'parent', 10000, poll, force=True)
        assert len(poll['children']) == len(client.queried) == 127
        assert all(cid.startswith(f'{generation}-') for cid in client.queried)
    events = manager.snapshot(sid)['events']
    states = [e['data']['item']['status'] for e in events if e['kind'] == 'child/item/updated']
    assert 'unknown' in states and 'completed' not in states
    notices = [e for e in events if e['kind'] == 'child/observation']
    assert len(notices) == 1 and notices[0]['data']['status'] == 'limited'


def test_child_read_failure_removes_running_exemption_without_heartbeat(tmp_path):
    from briefloop.backends.opencode_server import OpencodeError
    manager = OpencodeHarness(Store(tmp_path), FakeClient)
    sid = manager.create_session()['id']
    class Child:
        unavailable = False
        def children(self, owner):
            return [{'id': 'child', 'time': {'created': 10000}}] if owner == 'parent' else []
        def messages(self, owner):
            if self.unavailable:raise OpencodeError('unavailable')
            return [{'info': {'id': owner, 'role': 'assistant', 'time': {'created': 10000}}, 'parts': []}]
    client = Child(); manager._client = lambda: client
    poll = {}
    assert manager._poll_children(sid, 'turn', 'parent', 10000, poll, force=True)
    assert 'observed_running_at' in poll['children']['child']
    client.unavailable = True
    assert manager._poll_children(sid, 'turn', 'parent', 10000, poll, force=True)
    assert poll['children']['child']['status'] == 'unknown'
    assert 'observed_running_at' not in poll['children']['child']
    assert not manager._poll_children(sid, 'turn', 'parent', 10000, poll, force=True)
    client.unavailable = False
    assert manager._poll_children(sid, 'turn', 'parent', 10000, poll, force=True)
    assert poll['children']['child']['status'] == 'running'


@pytest.mark.parametrize('activity', ['none', 'reasoning', 'child'])
def test_empty_assistant_fails_startup_only_without_real_parts(tmp_path, monkeypatch, activity):
    import briefloop.opencode_harness as module
    from types import SimpleNamespace
    manager = OpencodeHarness(Store(tmp_path), FakeClient)
    sid = manager.create_session()['id']
    manager.chat.message(sid, 'Work', mid='turn', status='delivered', turn_id='turn')
    manager._epoch[sid] = 1
    manager._bound_session = lambda _: 'parent'
    clock = [0]
    monkeypatch.setattr(module, 'time', SimpleNamespace(monotonic=lambda: clock[0],
        sleep=lambda seconds: clock.__setitem__(0, clock[0] + 30)))
    class EmptyShell:
        def __init__(self):self.aborts = []
        def abort(self, owner):self.aborts.append(owner)
        def children(self, owner):
            return [{'id': 'child', 'time': {'created': 10000}}] if activity == 'child' and owner == 'parent' else []
        def messages(self, owner):
            assert clock[0] <= 120, 'Empty assistant shell did not stop'
            active = activity == 'reasoning' or (activity == 'child' and owner == 'child')
            done = clock[0] == 120
            return [{'info': {'id': owner, 'role': 'assistant',
                     'time': {'created': 10000, **({'completed': 11000} if done else {})},
                     **({'finish': 'stop'} if done else {})},
                     'parts': [{'type': 'reasoning', 'text': 'PRIVATE REAL REASONING'}] if active else []}]
    client = EmptyShell(); manager._client = lambda: client
    if activity == 'none':
        with pytest.raises(RuntimeError, match='未开始执行'):
            manager._follow(sid, 1, 'turn', 10000)
        assert clock[0] == 90 and client.aborts == ['parent']
        assert manager.snapshot(sid)['session']['status'] == 'failed'
        assert '可用额度' in json.dumps(manager.snapshot(sid), ensure_ascii=False)
    else:
        manager._follow(sid, 1, 'turn', 10000)
        assert clock[0] == 120 and not client.aborts
        assert manager.snapshot(sid)['session']['status'] == 'idle'
    assert 'PRIVATE REAL REASONING' not in json.dumps(manager.snapshot(sid), ensure_ascii=False)


@pytest.mark.parametrize('native_error', [{'error': {'message': 'Quota exceeded'}}, {'finish': 'error'}])
def test_native_error_without_completed_time_fails_immediately(tmp_path, monkeypatch, native_error):
    import briefloop.opencode_harness as module
    from types import SimpleNamespace
    manager = OpencodeHarness(Store(tmp_path), FakeClient)
    sid = manager.create_session()['id']
    manager.chat.message(sid, 'Work', mid='turn', status='delivered', turn_id='turn')
    manager._epoch[sid] = 1
    manager._bound_session = lambda _: 'parent'
    def no_sleep(seconds):raise AssertionError('Native failure must not be polled again')
    monkeypatch.setattr(module, 'time', SimpleNamespace(monotonic=lambda: 0, sleep=no_sleep))
    class Failed:
        def __init__(self):self.aborts = []
        def abort(self, owner):self.aborts.append(owner)
        def children(self, owner):raise AssertionError('Native failure must precede child discovery')
        def messages(self, owner):
            return [{'info': {'id': 'empty', 'role': 'assistant', 'time': {'created': 10000}, **native_error}, 'parts': []}]
    client = Failed(); manager._client = lambda: client
    with pytest.raises(RuntimeError, match='执行失败'):
        manager._follow(sid, 1, 'turn', 10000)
    assert client.aborts == ['parent']
    assert manager.snapshot(sid)['session']['status'] == 'failed'


def test_nested_native_error_exposes_only_sanitized_message(tmp_path, monkeypatch):
    import briefloop.opencode_harness as module
    from types import SimpleNamespace
    manager = OpencodeHarness(Store(tmp_path), FakeClient)
    sid = manager.create_session()['id']
    manager.chat.message(sid, 'Work', mid='turn', status='delivered', turn_id='turn')
    manager._epoch[sid] = 1
    manager._bound_session = lambda _: 'parent'
    monkeypatch.setattr(module, 'time', SimpleNamespace(monotonic=lambda: 0))
    error = {'name': 'APIError', 'data': {
        'message': 'Quota exceeded; https://user:URL_SECRET@provider.invalid/v1?token=QUERY_SECRET\nAuthorization: Basic MESSAGE_SECRET\nTry again after reset.',
        'statusCode': 429,
        'responseHeaders': {'Authorization': 'Bearer HEADER_SECRET', 'x-private': 'PRIVATE_HEADER'},
        'responseBody': 'PRIVATE RESPONSE BODY'}}
    class Failed:
        def abort(self, owner):pass
        def messages(self, owner):
            return [{'info': {'id': 'native', 'role': 'assistant', 'time': {'created': 10000}, 'error': error}, 'parts': []}]
    manager._client = lambda: Failed()
    with pytest.raises(RuntimeError, match='执行失败'):
        manager._follow(sid, 1, 'turn', 10000)
    # Check persisted public events, not just the helper's return value.
    public = json.dumps(manager.snapshot(sid), ensure_ascii=False)
    assert 'Quota exceeded' in public and 'Try again after reset.' in public
    assert all(secret not in public for secret in ('URL_SECRET', 'QUERY_SECRET', 'MESSAGE_SECRET',
        'HEADER_SECRET', 'PRIVATE_HEADER', 'PRIVATE RESPONSE BODY', 'responseHeaders', 'responseBody'))
    assert module._public_native_error({**error, 'message': 'Top-level message'}) == 'Top-level message'
    error['data']['message'] = {'private': 'PRIVATE NONSTRING MESSAGE'}
    assert module._public_native_error(error) == 'APIError'
    assert module._public_native_error({'name': 'Authorization: Basic NAME_SECRET', 'data': error['data']}) == '执行失败'
