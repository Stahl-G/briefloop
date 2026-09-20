"""BriefLoop's embedded engine: review-only routing and session lifecycle."""
import http.server
import json
import queue
import re
import shutil
import subprocess
import threading
import time
from importlib.resources import files
from pathlib import Path

import pytest
from pydantic import ValidationError

from briefloop.models import Settings
from briefloop.native_engine import NativeEngine
from briefloop.native_harness import NativeHarness
from briefloop.review_capability import restricted_review, summary
from briefloop.store import Store


def _wait_status(h, sid, mid, status, seconds=5):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        snap = h.snapshot(sid)
        if any(m['id'] == mid and m['status'] == status for m in snap['messages']):
            return snap
        time.sleep(.02)
    raise AssertionError(f'turn did not reach {status}: {[(m["id"], m["status"]) for m in h.snapshot(sid)["messages"]]}')


@pytest.mark.real_review_capabilities
def test_the_embedded_engine_runs_pinned_reviews_but_never_the_main_chain(tmp_path):
    store = Store(tmp_path)
    source = store.add_source('Synthetic', 'Revenue was USD 12 million.')
    store.set_meta('settings', {**store.settings(), 'agent_backend': 'opencode',
                                'model': 'synthetic/model', 'model_selection_required': False})
    task = {'title': 'T', 'objective': 'o', 'allow_web': True}
    with pytest.raises(ValueError, match='只执行受限独立审阅'):
        store.create_run(task, [source['id']], agent_backend='briefloop-native')
    run = store.create_run(task, [source['id']])
    brief = store.publish(run['id'], {'title': 'T', 'markdown': 'Revenue was USD 12 million.'})
    native = {'agent_backend': 'briefloop-native', 'runtime': {'model': 'deepseek/deepseek-v4-flash'}}
    for kind, payload in (('generate', {'run_id': run['id']}), ('assess', {'version_id': brief['id']}),
                          ('learn', {})):
        with pytest.raises(ValueError, match='只执行受限独立审阅'):
            store.enqueue(kind, {**payload, **native})
    assert store.rows('SELECT * FROM jobs') == []
    assert store.enqueue('review', {'version_id': brief['id'], **native})
    with pytest.raises(ValidationError):
        Settings.model_validate({'agent_backend': 'briefloop-native'})
    assert restricted_review('briefloop-native') is True
    # Never offered as the “执行后端” to switch to: the page cannot select it.
    # It is offered only as a separately chosen Reviewer, marked experimental.
    assert summary() == {'restricted_review': [{'id': 'opencode', 'label': 'Opencode CLI'}],
                         'review_choices': [{'id': 'opencode', 'label': 'Opencode CLI', 'experimental': False},
                                            {'id': 'briefloop-native', 'label': 'BriefLoop 内置引擎', 'experimental': True}]}


class EngineFixture:
    """Scripted engine; replacing `process` models the bridge retiring an idle child."""
    def __init__(self):
        self.process = object()
        self.calls = []
        self.sinks = {}

    def subscribe(self, eid):
        self.sinks[eid] = queue.Queue()
        return self.sinks[eid]

    def unsubscribe(self, eid):
        self.sinks.pop(eid, None)

    def call(self, method, params, timeout=None):
        self.calls.append((method, params))
        if method == 'session_create':
            count = sum(1 for name, _ in self.calls if name == 'session_create')
            return {'session_id': params['session_id'], 'model': params['model'],
                    'session_file': params.get('session_file') or f'/sessions/s{count}.jsonl',
                    'resumed': 'session_file' in params, 'tools': ['packet_list', 'packet_read']}
        if method == 'turn_start':
            self.sinks[params['execution_id']].put({'kind': 'text', 'delta': '{"ok":true}'})
            self.sinks[params['execution_id']].put({'kind': 'end', 'status': 'completed', 'final_text': '{"ok":true}'})
            return {'started': True}
        return {}

    def close(self):
        pass


def test_refuses_sessions_that_are_not_restricted_reviews(tmp_path):
    h = NativeHarness(Store(tmp_path), EngineFixture())
    with pytest.raises(ValueError, match='只读核查包'):
        h.create_session('t', {'model': 'fake/m1'})
    with pytest.raises(ValueError, match='不支持的角色'):
        h.create_session('t', {'model': 'fake/m1', 'review_root': str(tmp_path), 'native_role': 'orchestrator'})
    with pytest.raises(ValueError, match='只读核查包'):
        h.create_session('t', {'model': 'fake/m1', 'review_root': str(tmp_path), 'permission': 'workspace-write'})


def test_a_retired_engine_process_resumes_the_session_from_its_transcript(tmp_path):
    engine = EngineFixture()
    h = NativeHarness(Store(tmp_path), engine)
    sid = h.create_session('review', {'model': 'fake/m1', 'review_root': str(tmp_path)})['id']
    creates = lambda: [p for name, p in engine.calls if name == 'session_create']

    _wait_status(h, sid, h.send(sid, 'first')['id'], 'completed')
    _wait_status(h, sid, h.send(sid, 'second')['id'], 'completed')
    assert len(creates()) == 1, 'the same engine process keeps its session'

    engine.process = object()
    snap = _wait_status(h, sid, h.send(sid, 'after idle retirement')['id'], 'completed')
    assert len(creates()) == 2
    assert creates()[1]['session_file'] == '/sessions/s1.jsonl'
    bound = [e['data'] for e in snap['events'] if e['kind'] == 'session/bound']
    assert [b['resumed'] for b in bound] == [False, True]
    assert [p['session_id'] for name, p in engine.calls if name == 'turn_start'] == [sid, sid, sid]


def _node_supports_engine():
    node = shutil.which('node')
    if not node:
        return False
    found = re.match(r'v(\d+)\.(\d+)', subprocess.run([node, '--version'], capture_output=True, text=True).stdout)
    return bool(found) and (int(found[1]), int(found[2])) >= (22, 19)


@pytest.mark.skipif(not _node_supports_engine(), reason='the embedded engine needs Node 22.19+')
def test_real_engine_session_survives_bridge_idle_retirement(tmp_path, monkeypatch):
    """End to end on the shipped bundle: a scripted provider, the real bridge
    retiring the idle engine, and the next turn continuing the same conversation."""
    requests = []

    class Provider(http.server.BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers['content-length'])))
            requests.append(body)
            self.send_response(200)
            self.send_header('content-type', 'text/event-stream')
            self.end_headers()
            # A fresh user turn is answered by submit_review; the tool result by a short reply.
            if body['messages'][-1]['role'] == 'tool':
                steps = (({'role': 'assistant', 'content': '已提交'}, None), ({}, 'stop'))
            else:
                call = {'index': 0, 'id': f'call_{len(requests)}', 'type': 'function',
                        'function': {'name': 'submit_review', 'arguments': json.dumps({'review': {'turn': len(requests)}})}}
                steps = (({'role': 'assistant', 'tool_calls': [call]}, None), ({}, 'tool_calls'))
            for delta, finish in steps:
                chunk = {'id': 'c', 'object': 'chat.completion.chunk', 'created': 0, 'model': 'm1',
                         'choices': [{'index': 0, 'delta': delta, 'finish_reason': finish}]}
                self.wfile.write(b'data: ' + json.dumps(chunk).encode() + b'\n\n')
            self.wfile.write(b'data: [DONE]\n\n')

    server = http.server.ThreadingHTTPServer(('127.0.0.1', 0), Provider)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    package = tmp_path / 'package'
    (package / 'static').mkdir(parents=True)
    shutil.copy(Path(str(files('briefloop').joinpath('static/native-engine.mjs'))), package / 'static/native-engine.mjs')
    (package / 'static/native-engine-models.json').write_text(json.dumps({'providers': {'fake': {
        'baseUrl': f'http://127.0.0.1:{server.server_port}/v1', 'api': 'openai-completions', 'apiKey': 'FAKE_PROVIDER_KEY',
        'models': [{'id': 'm1', 'name': 'M1', 'api': 'openai-completions', 'provider': 'fake', 'reasoning': False,
                    'input': ['text'], 'cost': {'input': 0, 'output': 0, 'cacheRead': 0, 'cacheWrite': 0},
                    'contextWindow': 100000, 'maxTokens': 1000}]}}}))
    packet = tmp_path / 'packet'
    packet.mkdir()
    (packet / 'target.json').write_text('{}')
    (packet / 'output.schema.json').write_text('{"type": "object"}')
    home = tmp_path / 'home'
    home.mkdir()
    import briefloop.runtime_bridge as runtime_bridge
    monkeypatch.setattr(runtime_bridge, 'files', lambda name: package)
    for name, value in (('HOME', str(home)), ('USERPROFILE', str(home)), ('FAKE_PROVIDER_KEY', 'k'), ('NO_PROXY', '*')):
        monkeypatch.setenv(name, value)
    monkeypatch.setattr(NativeEngine, 'IDLE_SECONDS', .5)

    engine = NativeEngine()
    h = NativeHarness(Store(tmp_path / 'ws'), engine)
    try:
        sid = h.create_session('review', {'model': 'fake/m1', 'review_root': str(packet)})['id']
        _wait_status(h, sid, h.send(sid, 'first')['id'], 'completed', seconds=30)
        deadline = time.monotonic() + 10
        while engine.process is not None and time.monotonic() < deadline:
            time.sleep(.05)
        assert engine.process is None, 'the bridge retires the idle engine'
        snap = _wait_status(h, sid, h.send(sid, 'second')['id'], 'completed', seconds=30)
        assert not [e for e in snap['events'] if e['kind'] == 'error']
        assert h.snapshot(sid)['messages'][1]['text'] == '{"turn":1}', 'the admitted submission is the turn result'
        # An admitted submission ends the turn without another model request.
        assert [[m['role'] for m in r['messages']] for r in requests] == [
            ['system', 'user'], ['system', 'user', 'assistant', 'tool', 'user']]
        second = requests[1]
        assert second['messages'][2]['tool_calls'][0]['function']['name'] == 'submit_review', \
            'the resumed session keeps the first submission'
        system = second['messages'][0]
        assert system['role'] in ('system', 'developer') and '独立只读 Reviewer' in str(system['content'])
    finally:
        h.close()
        server.shutdown()
