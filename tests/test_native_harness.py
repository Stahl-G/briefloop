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

from briefloop.native_engine import NativeEngine
from briefloop.native_harness import NativeHarness
from briefloop.store import Store


def _wait_status(h, sid, mid, status, seconds=5):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        snap = h.snapshot(sid)
        if any(m['id'] == mid and m['status'] == status for m in snap['messages']):
            return snap
        time.sleep(.02)
    raise AssertionError(f'turn did not reach {status}: {[(m["id"], m["status"]) for m in h.snapshot(sid)["messages"]]}')


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


def test_quick_writer_has_no_runner_tools_and_does_not_require_json_or_submission(tmp_path):
    engine=EngineFixture();h=NativeHarness(Store(tmp_path),engine)
    sid=h.create_session('quick',{'model':'fake/m1','native_role':'quick_writer',
        'packet_root':str(tmp_path),'run_id':'synthetic'})['id']
    _wait_status(h,sid,h.send(sid,'Write only the report')['id'],'completed')
    create=next(params for name,params in engine.calls if name=='session_create')
    turn=next(params for name,params in engine.calls if name=='turn_start')
    assert create['runner_tools']==[]
    assert turn['expect_json'] is False and turn['require_submit'] is False


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
    native_dir = home / '.config/briefloop/native-engine'
    native_dir.mkdir(parents=True)
    (native_dir / 'providers.json').write_text(json.dumps({'fake/m1': {
        'provider': 'fake', 'model': 'm1', 'protocol': 'chat-completions',
        'base_url': f'http://127.0.0.1:{server.server_port}/v1', 'api_key': 'fixture-native-key',
    }}))
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


def test_report_feedback_reads_public_page_then_revokes_web_tools(tmp_path, monkeypatch):
    from briefloop import sources
    from briefloop.chat_dispatch import ChatDispatcher
    from briefloop.report_revision import bind_context
    from briefloop.research_plan import freeze, finish_round, frozen
    from briefloop.research_budget import snapshot

    class ReportEngine(EngineFixture):
        def call(self, method, params, timeout=None):
            if method == 'turn_start':
                self.calls.append((method, params))
                self.execution = params['execution_id']
                self.sinks[self.execution].put({'kind': 'tool_request', 'tool': 'add_url',
                    'args': {'url': 'https://example.com/python313'}, 'request_id': self.execution})
                return {'started': True}
            result = super().call(method, params, timeout)
            if method == 'tool_result':
                self.sinks[self.execution].put({'kind': 'end', 'status': 'completed'})
            return result

    store = Store(tmp_path)
    store.update_settings({'model': 'fake/m1', 'model_selection_required': False, 'fact_checker': False})
    run = store.create_run({'title': 'Python3.13', 'objective': '核对本期发布', 'allow_web': True,
        'completion_mode': 'direct', 'research_budget': {'search_requests': 2, 'candidate_urls': 4, 'source_pages': 2},
        'search_policy': {'primary_provider': 'tavily', 'coverage_mode': 'primary_only'}}, [], research_protocol='quality_v1')
    freeze(store, run['id'], structure={'breadth': 1, 'depth': 1, 'parallel': 1})
    finish_round(store, run['id'], gaps=[], summary='初稿留下可补查缺口')
    brief = store.publish(run['id'], {'title': 'Python3.13', 'markdown': '发布时间待补。'})
    reads = []
    def fetch(pending, url, *, allow_private=False):
        assert allow_private is False
        reads.append(url)
        return pending.add_source('公开发布页', 'Release date: Oct. 7, 2024. Free threading is not enabled by default.', url=url)
    monkeypatch.setattr(sources, 'fetch', fetch)
    engine = ReportEngine()
    h = NativeHarness(store, engine)
    ChatDispatcher({'briefloop-native': h})
    monkeypatch.setattr(h, '_schedule', lambda sid: None)
    sid = h.create_session('反馈', {'model': 'fake/m1'})['id']
    mid = h.send(sid, '读取公开发布页，补发布时间', allow_web=True)['id']
    bind_context(store, sid, mid, {'version_id': brief['id'], 'hash': brief['hash']})
    h._dispatch(sid, 1)
    receipt = next(params for name, params in engine.calls if name == 'tool_result')
    assert receipt['ok'] is True, receipt
    saved = json.loads(receipt['content'][0]['text'])
    assert store.source_text(saved['source_id']).startswith('Release date:')
    assert saved['source_id'] in store.source_ids(run['id'])
    assert snapshot(store, run['id'])['used']['source_pages'] == 1
    assert frozen(store, run['id'])['revision_research']['status'] == 'completed'
    assert store.one('briefs', brief['id'])['hash'] == brief['hash']

    # A new ordinary message cannot reuse a prior report message's tool authority.
    h.send(sid, '解释术语，不修改报告', allow_web=True)
    h._dispatch(sid, 2)
    receipts = [params for name, params in engine.calls if name == 'tool_result']
    assert receipts[-1]['ok'] is False
    assert reads == ['https://example.com/python313']
    assert snapshot(store, run['id'])['used']['source_pages'] == 1
