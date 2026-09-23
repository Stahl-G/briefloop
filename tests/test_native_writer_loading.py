"""Real bundled SDK + real Python writer, with a local scripted model endpoint.

Exercises actual provider-visible schemas and saved artifacts. This is a
deterministic integration check, not a model quality or speed benchmark.
"""
import http.server
import json
import os
import queue
import shutil
import subprocess
import threading
import time
from importlib.resources import files
from pathlib import Path

import pytest

from briefloop import analyst
from briefloop.native_engine import NativeEngine
from briefloop.native_harness import NativeHarness
from briefloop.store import Store


def test_lazy_writer_tools_reach_provider_and_save_checked_revision(tmp_path, monkeypatch):
    node = shutil.which('node')
    if not node:
        pytest.skip('Node is required for the bundled SDK integration')
    version = subprocess.run([node, '--version'], capture_output=True, text=True).stdout.strip()
    if tuple(map(int, version.lstrip('v').split('.')[:2])) < (22, 19):
        pytest.skip('The embedded engine needs Node 22.19+')

    observed, failures = [], queue.Queue()
    flow = {'revision': None, 'checked_revision': None}
    source_id = None
    markdown = None

    def response(body):
        index = len(observed)
        tools = {t['function']['name']: t['function'] for t in body['tools']}
        observed.append(set(tools))
        if index == 0:
            assert set(tools) == {'packet_list', 'packet_read', 'packet_grep', 'write_report', 'load_tools'}
            assert set(tools['write_report']['parameters']['properties']) == {'title', 'markdown'}
            assert 'update_number_bindings' not in json.dumps(body['messages'][0])
            return 'write_report', {'title': '合成经营简报', 'markdown': markdown}
        last = next(m for m in reversed(body['messages']) if m['role'] == 'tool')
        value = json.loads(last['content'])
        if value.get('revision'):
            flow['revision'] = value['revision']
        if index == 1:
            assert 'assemble_evidence' not in tools
            return 'load_tools', {'groups': ['evidence']}
        if index == 2:
            assert {'assemble_evidence', 'check_draft', 'submit_draft'} <= set(tools)
            assert 'patch_report_text' not in tools
            return 'assemble_evidence', {
                'base_revision': flow['revision'],
                'citations': [{'source_id': source_id, 'excerpt': '2025年收入1200万元，同比增长20%。'}],
                'number_bindings': [{
                    'source_id': source_id, 'source_excerpt': '2025年收入1200万元，同比增长20%。',
                    'report_quote': '收入同比增长20%', 'number_text': '20%',
                    'value': 20, 'unit': '%', 'label': '收入同比增长率',
                    'entity': '合成公司', 'period': '2025',
                }],
            }
        if index == 3:
            return 'load_tools', {'groups': ['revise']}
        if index == 4:
            assert {'read_draft', 'patch_report_text', 'assemble_evidence'} <= set(tools)
            return 'read_draft', {'field': 'overview'}
        if index == 5:
            return 'patch_report_text', {'base_revision': flow['revision'], 'replacements': [
                {'old_text': '建议继续观察毛利率。', 'new_text': '建议下期继续观察毛利率。'}]}
        if index == 6:
            flow['checked_revision'] = flow['revision']
            return 'check_draft', {'revision': flow['revision']}
        if index == 7:
            assert flow['checked_revision'] == flow['revision']
            assert value['writer_action']['next_operation'] == 'submit_draft'
            return 'submit_draft', {'revision': flow['revision']}
        raise AssertionError('Accepted submission must stop without another model request')

    class Provider(http.server.BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers['content-length'])))
            try:
                name, args = response(body)
                delta = {'role': 'assistant', 'tool_calls': [{
                    'index': 0, 'id': f'call_{len(observed)}', 'type': 'function',
                    'function': {'name': name, 'arguments': json.dumps(args, ensure_ascii=False)},
                }]}
                finish = 'tool_calls'
            except Exception as exc:
                failures.put(repr(exc))
                delta, finish = {'role': 'assistant', 'content': 'Fixture failed'}, 'stop'
            self.send_response(200)
            self.send_header('content-type', 'text/event-stream')
            self.end_headers()
            for part, reason in [(delta, None), ({}, finish)]:
                chunk = {'id': 'fixture', 'object': 'chat.completion.chunk', 'created': 0,
                         'model': 'writer', 'choices': [{'index': 0, 'delta': part, 'finish_reason': reason}]}
                self.wfile.write(b'data: ' + json.dumps(chunk).encode() + b'\n\n')
            self.wfile.write(b'data: [DONE]\n\n')

    server = http.server.ThreadingHTTPServer(('127.0.0.1', 0), Provider)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    package = tmp_path / 'package'
    (package / 'static').mkdir(parents=True)
    bundle = os.environ.get('BRIEFLOOP_NATIVE_TEST_BUNDLE') or str(files('briefloop').joinpath('static/native-engine.mjs'))
    shutil.copy(bundle, package / 'static/native-engine.mjs')
    (package / 'static/native-engine-models.json').write_text(json.dumps({'providers': {'fixture': {
        'baseUrl': f'http://127.0.0.1:{server.server_port}/v1', 'api': 'openai-completions',
        'apiKey': 'FIXTURE_MODEL_KEY', 'models': [{'id': 'writer', 'name': 'Scripted writer',
        'api': 'openai-completions', 'provider': 'fixture', 'reasoning': False, 'input': ['text'],
        'cost': {'input': 0, 'output': 0, 'cacheRead': 0, 'cacheWrite': 0},
        'contextWindow': 100000, 'maxTokens': 2048}],
    }}}))
    home = tmp_path / 'home'
    home.mkdir()
    import briefloop.runtime_bridge as runtime_bridge
    monkeypatch.setattr(runtime_bridge, 'files', lambda name: package)
    monkeypatch.setenv('HOME', str(home))
    monkeypatch.setenv('USERPROFILE', str(home))
    monkeypatch.setenv('FIXTURE_MODEL_KEY', 'synthetic-only')
    monkeypatch.setenv('NO_PROXY', '*')
    store = Store(tmp_path / 'workspace')
    source = store.add_source('合成公司数据', '2025年收入1200万元，同比增长20%。')
    source_id = source['id']
    run = store.create_run({'title': '合成经营简报', 'objective': '解释合成数据，不描述真实公司',
                            'allow_web': False}, [source_id])
    folder = store.root / 'writer'
    packet = analyst.packet(store, run['id'], folder, plan={}, research={'sources': [], 'gaps': []},
                            writer_protocol='writer_input_v1')
    markdown = f'本期收入同比增长20%，建议继续观察毛利率。[@{source_id}]'
    harness = NativeHarness(store, NativeEngine())
    try:
        sid = harness.create_session('按需工具集成检查', {
            'native_role': 'analyst', 'model': 'fixture/writer', 'run_id': run['id'],
            'packet_root': str(packet['root']), 'result_file': str(folder / 'draft.json'),
        }, cwd=folder)['id']
        mid = harness.send(sid, '使用任务包写合成报告，并按需加载证据和修订工具。')['id']
        deadline = time.monotonic() + 45
        while time.monotonic() < deadline:
            snap = harness.snapshot(sid)
            state = next((m['status'] for m in snap['messages'] if m['id'] == mid), None)
            if state in ('completed', 'failed', 'cancelled'):
                break
            time.sleep(.03)
        assert failures.empty(), list(failures.queue)
        assert state == 'completed', [(m['role'], m['status']) for m in snap['messages']]
        saved = json.loads((folder / 'draft.json').read_text())
        assert '建议下期继续观察毛利率' in saved['markdown']
        assert saved['citations'][0]['source_id'] == source_id
        assert saved['number_bindings'][0]['value'] == 20
        assert len(observed) == 8
        assert all('bash' not in names and 'render_pdf_pages' not in names for names in observed)
        accepted = json.loads((folder / 'draft-accepted.json').read_text())
        assert accepted['revision'] == flow['checked_revision']
    finally:
        harness.close()
        server.shutdown()
        server.server_close()
