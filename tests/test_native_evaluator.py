"""The Evaluator on the native engine: a frozen packet, runner tools, one backend."""
import json
import queue
import time

import pytest

from briefloop.native_harness import NativeHarness
from briefloop.native_roles import run_tool, runner_tool_specs
from briefloop.runtime import assessment_prompt
from briefloop.store import Store


def _brief(tmp_path):
    store = Store(tmp_path)
    source = store.add_source('Synthetic', 'Revenue was USD 12 million in 2025.')
    run = store.create_run({'title': 'T', 'objective': 'Explain revenue'}, [source['id']])
    brief = store.publish(run['id'], {'title': 'T', 'markdown': f'Revenue was USD 12 million.[@{source["id"]}]'})
    return store, source, brief


def _assessment(brief, **extra):
    return {'brief_hash': brief['hash'], 'summary': 's', 'overall': '达到要求',
            'evidence': 3, 'coverage': 3, 'analysis': 3, 'expression': 3, **extra}


def test_native_evaluator_reads_a_frozen_packet_and_is_told_its_real_tools(tmp_path):
    store, source, brief = _brief(tmp_path)
    folder = tmp_path / 'job'
    folder.mkdir()
    prompt = assessment_prompt(store, brief, folder, 'briefloop-native')
    packet = folder / 'packet'
    for name in ('input.json', 'source-index.json', 'assessment.schema.json', f'sources/{source["id"]}.txt'):
        assert (packet / name).is_file(), name
    pack = json.loads((packet / 'input.json').read_text(encoding='utf-8'))
    assert pack['source_index_path'] == 'source-index.json'
    assert 'USD 12 million' in (packet / f'sources/{source["id"]}.txt').read_text(encoding='utf-8')
    assert 'submit_assessment' in prompt and 'render_pdf_pages' in prompt and 'packet_read' in prompt
    # No host commands or absolute paths the native session cannot use.
    assert 'read-source' not in prompt and 'render-source' not in prompt and str(folder) not in prompt
    assert brief['hash'] in prompt
    # Hosts keep their own wording.
    (tmp_path / 'codex').mkdir()
    codex = assessment_prompt(store, brief, tmp_path / 'codex', 'codex')
    assert 'read-source' in codex and 'submit_assessment' not in codex and 'assessment.json' in codex


def test_submit_assessment_is_admitted_by_the_store_rules_and_saved_by_the_runner(tmp_path):
    store, source, brief = _brief(tmp_path)
    folder = tmp_path / 'job'
    folder.mkdir()
    assessment_prompt(store, brief, folder, 'briefloop-native')
    config = {'native_role': 'evaluator', 'packet_root': str(folder / 'packet'), 'version_id': brief['id']}
    wrong = run_tool(store, config, 'submit_assessment', {'assessment': _assessment(brief, brief_hash='stale')})
    assert not wrong['ok'] and '不一致' in wrong['error']
    incomplete = run_tool(store, config, 'submit_assessment', {'assessment': _assessment(brief, evidence=None)})
    assert not incomplete['ok'] and 'four grades' in incomplete['error']
    assert not (folder / 'assessment.json').exists()
    ok = run_tool(store, config, 'submit_assessment', {'assessment': _assessment(brief)})
    assert ok['ok'] and json.loads(ok['settle'])['brief_hash'] == brief['hash']
    assert json.loads((folder / 'assessment.json').read_text(encoding='utf-8'))['overall'] == '达到要求'
    # Only sources of this task, and only what the tool validates.
    outside = run_tool(store, config, 'render_pdf_pages', {'source_id': 'src_other', 'pages': [1]})
    assert not outside['ok'] and 'source-index.json' in outside['error']
    not_pdf = run_tool(store, config, 'render_pdf_pages', {'source_id': source['id'], 'pages': [1]})
    assert not not_pdf['ok']
    assert run_tool(store, config, 'packet_read', {})['ok'] is False
    assert [t['name'] for t in runner_tool_specs('evaluator')] == ['render_pdf_pages', 'submit_assessment']
    assert runner_tool_specs('reviewer') == []


class Engine:
    def __init__(self, store, brief):
        self.process, self.calls, self.sinks = object(), [], {}
        self.store, self.brief = store, brief

    def subscribe(self, eid):
        self.sinks[eid] = queue.Queue()
        return self.sinks[eid]

    def unsubscribe(self, eid):
        self.sinks.pop(eid, None)

    def call(self, method, params, timeout=None):
        self.calls.append((method, params))
        if method == 'session_create':
            return {'session_id': params['session_id'], 'session_file': '/s.jsonl', 'model': params['model']}
        if method == 'turn_start':
            sink = self.sinks[params['execution_id']]
            sink.put({'kind': 'tool_request', 'request_id': 'tool-1', 'tool': 'submit_assessment',
                      'args': {'assessment': _assessment(self.brief, brief_hash='stale')}})
            sink.put({'kind': 'tool_request', 'request_id': 'tool-2', 'tool': 'submit_assessment',
                      'args': {'assessment': _assessment(self.brief)}})
            sink.put({'kind': 'end', 'status': 'completed', 'final_text': json.dumps(_assessment(self.brief))})
        return {}

    def close(self):
        pass


def test_the_harness_opens_an_evaluator_session_and_answers_its_tool_calls(tmp_path):
    store, source, brief = _brief(tmp_path)
    folder = tmp_path / 'job'
    folder.mkdir()
    assessment_prompt(store, brief, folder, 'briefloop-native')
    engine = Engine(store, brief)
    h = NativeHarness(store, engine)
    config = {'model': 'fake/m1', 'native_role': 'evaluator', 'packet_root': str(folder / 'packet'), 'version_id': brief['id']}
    sid = h.create_session('Evaluator · 评分', config, folder)['id']
    mid = h.send(sid, 'score it')['id']
    deadline = time.monotonic() + 5
    while not any(m['id'] == mid and m['status'] == 'completed' for m in h.snapshot(sid)['messages']):
        assert time.monotonic() < deadline
        time.sleep(.02)
    create = next(p for name, p in engine.calls if name == 'session_create')
    assert create['role'] == 'evaluator' and create['packet_root'] == str(folder / 'packet')
    assert [t['name'] for t in create['runner_tools']] == ['render_pdf_pages', 'submit_assessment']
    assert '独立 Evaluator' in create['system_prompt'] and 'Reviewer' not in create['system_prompt'].split('## 当前角色')[1][:40]
    results = [p for name, p in engine.calls if name == 'tool_result']
    assert [r['request_id'] for r in results] == ['tool-1', 'tool-2']
    assert results[0]['ok'] is False and results[1]['ok'] is True and 'settle' in results[1]
    assert (folder / 'assessment.json').is_file()
