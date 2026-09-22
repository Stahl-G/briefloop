"""BriefLoop's embedded engine: review-only routing and session lifecycle."""
import http.server
import json
import os
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
def test_native_main_chain_keeps_independent_reviewer_capability(tmp_path):
    store = Store(tmp_path)
    source = store.add_source('Synthetic', 'Revenue was USD 12 million.')
    settings = Settings.model_validate({**store.settings(), 'agent_backend':'briefloop-native',
        'model':'synthetic/model', 'model_selection_required':False})
    store.set_meta('settings', settings.model_dump())
    run = store.create_run({'title':'T','objective':'o','allow_web':False}, [source['id']])
    job = store.enqueue('generate', {'run_id':run['id']})
    assert json.loads(job['payload'])['agent_backend'] == 'briefloop-native'
    assert restricted_review('briefloop-native')
    assert any(r['id']=='briefloop-native' for r in summary()['restricted_review'])


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


def test_chat_is_available_but_packet_roles_keep_readonly_confinement(tmp_path):
    h = NativeHarness(Store(tmp_path), EngineFixture())
    session = h.create_session('t', {'model':'fake/m1'})
    assert session['runtime']['native_role'] == 'chat'
    with pytest.raises(ValueError, match='不支持的角色'):
        h.create_session('t', {'model':'fake/m1','review_root':str(tmp_path),'native_role':'invented'})
    with pytest.raises(ValueError, match='只读核查包'):
        h.create_session('t', {'model':'fake/m1','review_root':str(tmp_path),'permission':'workspace-write'})


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


def test_provider_revisions_rebind_only_between_turns_and_keep_the_transcript(tmp_path):
    class ProviderEngine(EngineFixture):
        revision = 1
        create_revision = None

        def call(self, method, params, timeout=None):
            result = super().call(method, params, timeout)
            if method == 'ping':
                result['configuration_revision'] = self.revision
            if method == 'session_create':
                # A provider save can race between the metadata probe and create.
                if self.create_revision is not None:
                    self.revision = self.create_revision
                    self.create_revision = None
                result['configuration_revision'] = self.revision
            return result

    engine = ProviderEngine()
    h = NativeHarness(Store(tmp_path), engine)
    sid = h.create_session('provider edits', {'model': 'fake/m1', 'review_root': str(tmp_path)})['id']
    creates = lambda: [p for name, p in engine.calls if name == 'session_create']
    for text in ('first', 'unchanged'):
        _wait_status(h, sid, h.send(sid, text)['id'], 'completed')
    assert len(creates()) == 1
    engine.revision = 2
    engine.create_revision = 3
    _wait_status(h, sid, h.send(sid, 'updated provider')['id'], 'completed')
    assert len(creates()) == 2
    assert creates()[1]['session_file'] == creates()[0].get('session_file', '/sessions/s1.jsonl')
    assert [p for name, p in engine.calls if name == 'session_close'] == [{'session_id': sid}]
    _wait_status(h, sid, h.send(sid, 'same actual revision')['id'], 'completed')
    assert len(creates()) == 2, 'bind the actual created revision, not the earlier probe'
    bound = [e['data'] for e in h.snapshot(sid)['events'] if e['kind'] == 'session/bound']
    assert [event['configuration_revision'] for event in bound] == [1, 3]
    assert [event['resumed'] for event in bound] == [False, True]


@pytest.mark.parametrize('blocked_method', ['ping', 'session_create'])
def test_cancellation_during_provider_refresh_never_starts_the_cancelled_turn(tmp_path, blocked_method):
    class RefreshEngine(EngineFixture):
        revision = 1
        block_next = None

        def __init__(self):
            super().__init__()
            self.entered = threading.Event()
            self.release = threading.Event()

        def call(self, method, params, timeout=None):
            result = super().call(method, params, timeout)
            if method in ('ping', 'session_create'):
                result['configuration_revision'] = self.revision
            if method == self.block_next:
                self.block_next = None
                self.entered.set()
                assert self.release.wait(5), 'test must release the blocked configuration operation'
            return result

    engine = RefreshEngine()
    h = NativeHarness(Store(tmp_path), engine)
    sid = h.create_session('cancel provider refresh', {'model': 'fake/m1', 'review_root': str(tmp_path)})['id']
    try:
        _wait_status(h, sid, h.send(sid, 'first')['id'], 'completed')
        transcript = h.chat.session(sid)['thread_id']
        engine.revision = 2
        engine.block_next = blocked_method
        mid = h.send(sid, 'cancel before model starts')['id']
        assert engine.entered.wait(5)
        h.cancel(sid)
        engine.release.set()
        _wait_status(h, sid, mid, 'cancelled')
        with h._lock:
            starts = [params for method, params in engine.calls if method == 'turn_start']
            assert len(starts) == 1, 'cancelling metadata/recreation must not issue a second model request'
            assert not engine.sinks and sid not in h._busy
            assert h.chat.session(sid)['thread_id'] == transcript
            assert h._engine_sessions[sid]['session_file'] == transcript
        _wait_status(h, sid, h.send(sid, 'explicitly continue')['id'], 'completed')
        assert len([params for method, params in engine.calls if method == 'turn_start']) == 2
        creates = [params for method, params in engine.calls if method == 'session_create']
        assert len(creates) == 2 and creates[-1]['session_file'] == transcript
    finally:
        engine.release.set()
        h.close()


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
    bundle = os.environ.get('BRIEFLOOP_NATIVE_TEST_BUNDLE') or str(files('briefloop').joinpath('static/native-engine.mjs'))
    shutil.copy(Path(bundle), package / 'static/native-engine.mjs')
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


@pytest.mark.skipif(not _node_supports_engine(), reason='the embedded engine needs Node 22.19+')
def test_saved_provider_updates_reach_existing_and_new_conversation_requests(tmp_path, monkeypatch):
    from briefloop import native_providers
    import briefloop.runtime_bridge as runtime_bridge

    requests = []

    class Provider(http.server.BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers['content-length'])))
            requests.append({'path': self.path, 'key': self.headers.get('Authorization'),
                             'output': body.get('max_completion_tokens', body.get('max_tokens')),
                             'messages': body['messages']})
            self.send_response(200)
            self.send_header('content-type', 'text/event-stream')
            self.end_headers()
            for delta, finish in (({'role': 'assistant', 'content': 'saved conversation'}, None), ({}, 'stop')):
                chunk = {'id': 'c', 'object': 'chat.completion.chunk', 'created': 0, 'model': 'model',
                         'choices': [{'index': 0, 'delta': delta, 'finish_reason': finish}]}
                self.wfile.write(b'data: ' + json.dumps(chunk).encode() + b'\n\n')
            self.wfile.write(b'data: [DONE]\n\n')

    server = http.server.ThreadingHTTPServer(('127.0.0.1', 0), Provider)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    home = tmp_path / 'home'
    home.mkdir()
    package = tmp_path / 'package'
    (package / 'static').mkdir(parents=True)
    bundle = os.environ.get('BRIEFLOOP_NATIVE_TEST_BUNDLE') or str(files('briefloop').joinpath('static/native-engine.mjs'))
    shutil.copy(bundle, package / 'static/native-engine.mjs')
    (package / 'static/native-engine-models.json').write_text('{"providers":{}}')
    monkeypatch.setattr(runtime_bridge, 'files', lambda name: package)
    for name, value in (('HOME', str(home)), ('USERPROFILE', str(home)), ('NO_PROXY', '*')):
        monkeypatch.setenv(name, value)
    engine = NativeEngine()
    h = NativeHarness(Store(tmp_path / 'workspace'), engine)

    def save(path, key, context, output):
        native_providers.save({'provider': 'fixture', 'model': 'model', 'protocol': 'chat-completions',
                               'base_url': f'http://127.0.0.1:{server.server_port}/{path}/v1', 'api_key': key,
                               'context_limit': context, 'output_limit': output})

    def send(sid, text):
        return _wait_status(h, sid, h.send(sid, text)['id'], 'completed', seconds=15)

    try:
        save('old', 'fixture-old-key', 65536, 1024)
        sid = h.create_session('provider changes', {'model': 'fixture/model'})['id']
        send(sid, 'first')
        process = engine.process
        save('new', 'fixture-new-key', 32768, 256)
        h.list_models(refresh=True)
        send(sid, 'after update')
        save('new', 'fixture-new-key', None, None)
        # The next turn detects a save even when no catalog refresh took place.
        continued = send(sid, 'after clearing limits')
        new_sid = h.create_session('fresh', {'model': 'fixture/model'})['id']
        send(new_sid, 'fresh conversation')
        assert engine.process is process, 'apply saved configuration without killing the shared engine'
        assert [(r['path'], r['key'], r['output']) for r in requests] == [
            ('/old/v1/chat/completions', 'Bearer fixture-old-key', 1024),
            ('/new/v1/chat/completions', 'Bearer fixture-new-key', 256),
            ('/new/v1/chat/completions', 'Bearer fixture-new-key', 8192),
            ('/new/v1/chat/completions', 'Bearer fixture-new-key', 8192),
        ]
        assert 'first' in json.dumps(requests[1]['messages'])
        assert 'after update' in json.dumps(requests[2]['messages'])
        bindings = [e['data'] for e in continued['events'] if e['kind'] == 'session/bound']
        assert [b['runtime_policy']['context_window'] for b in bindings] == [65536, 32768, 1_000_000]
        assert [b['resumed'] for b in bindings] == [False, True, True]
    finally:
        h.close()
        server.shutdown()
        server.server_close()


def test_native_reasoning_catalog_and_workspace_choice_survive_shared_controls(tmp_path):
    from briefloop.runtime_reasoning import options
    from briefloop.native_harness import _thinking
    from briefloop.models import runtime_fields

    class NativeCatalog:
        def list_models(self):
            return [{'id': 'fake/m1', 'thinking_levels': ['off', 'low', 'high']}]

    class NoBridge:
        def call(self, *args, **kwargs):
            raise AssertionError('Native metadata must not launch another host')

    result = options('briefloop-native', 'fake/m1', tmp_path, NoBridge(), NativeCatalog())
    assert [o['id'] for o in result['options']] == ['off', 'low', 'high']
    with pytest.raises(ValueError, match='尚未登记'):
        options('briefloop-native', 'fake/missing', tmp_path, NoBridge(), NativeCatalog())
    store = Store(tmp_path)
    selected = store.confirm_runtime_choice('briefloop-native', {'model': 'fake/m1', 'model_variant': 'high'})
    assert runtime_fields(selected, 'briefloop-native') == {'model': 'fake/m1', 'model_variant': 'high'}
    assert _thinking({'variant': 'off'}) == 'off'
    with pytest.raises(ValueError, match='不支持'):
        _thinking({'variant': 'invented'})
