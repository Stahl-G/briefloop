"""The Scout on the native engine: a frozen task, metered web tools, checked evidence."""
import json
import queue
import time

import pytest

from briefloop import scout, sources, websearch
from briefloop.agent_prompts import system_prompt
from briefloop.models import ScoutResult
from briefloop.native_harness import NativeHarness
from briefloop.native_roles import run_tool, runner_tool_specs, scout_packet
from briefloop.scout_tools import evidence_errors
from briefloop.store import Store

TEXT = 'Header\nRevenue rose to USD 12 million in 2025,\nup from USD 10 million.\nFootnote: audited.\n'


def _run(tmp_path, *, allow_web=False, policy=None):
    store = Store(tmp_path / 'ws')
    if policy:
        store.set_meta('settings', {**store.settings(), 'search_policy': policy, 'search_provider': policy['primary_provider']})
    source = store.add_source('Synthetic', TEXT)
    run = store.create_run({'title': 'T', 'objective': 'Explain revenue', 'allow_web': allow_web,
                            'research_budget': {'search_requests': 2, 'candidate_urls': 10, 'source_pages': 3}},
                           [source['id']])
    return store, run['id'], source['id']


def _config(store, run_id, name, **extra):
    folder = store.root / 'jobs' / name  # slots live in the workspace
    task = scout.task(store, run_id, {'slot_id': 'scout-1', 'theme': 'revenue'})
    scout_packet(store, task, folder)
    return {'model': 'fake/m1', 'native_role': 'scout', 'packet_root': str(folder / 'packet'), 'run_id': run_id,
            'result_file': str(folder / 'result.json'), 'allow_web': task['allow_web'],
            'search_channels': task['search_channels'], **extra}


def _evidence(sid, **extra):
    return {'source_id': sid, 'locator': 'line 2-3', 'excerpt': 'Revenue rose to USD 12 million in 2025, up from USD 10 million.',
            'facts': ['Revenue grew 20% to USD 12 million'], 'coverage_status': 'complete', **extra}


def test_excerpts_must_be_verbatim_and_where_the_locator_points(tmp_path):
    store, run_id, sid = _run(tmp_path)
    check = lambda **item: evidence_errors(store, run_id, ScoutResult.model_validate({'sources': [_evidence(sid, **item)]}))
    assert check() == []
    # Line prefixes copied from the numbered view, extra spaces and "…" joins are fine.
    assert check(excerpt='2: Revenue rose to USD 12 million…Footnote: audited.', locator='line 2-4') == []
    assert check(excerpt='Revenue grew 20% to USD 12 million') == [
        'sources[0]（%s） 的 excerpt 不是原文逐字内容；逐字摘录原句，概括写进 facts' % sid]
    far = TEXT + '\n' * 20 + 'Later note on cash.\n'
    far_id = store.add_source('Long', far)['id']
    store.attach_source(run_id, far_id)
    wrong = evidence_errors(store, run_id, ScoutResult.model_validate(
        {'sources': [_evidence(far_id, excerpt='Later note on cash.', locator='line 1-2')]}))
    assert wrong and '不在 line 1-2 附近' in wrong[0]
    assert '不可解析' in check(locator='second paragraph')[0]
    assert check(locator='', excerpt='') == []


def test_scout_tools_follow_the_run_web_permission_and_channels(tmp_path):
    store, run_id, _ = _run(tmp_path / 'offline')
    names = [t['name'] for t in runner_tool_specs('scout', config=_config(store, run_id, 'o'))]
    assert names == ['source_read', 'source_grep', 'render_pdf_pages', 'submit_scout_result']

    store, run_id, _ = _run(tmp_path / 'ddg', allow_web=True, policy={'primary_provider': 'duckduckgo'})
    specs = runner_tool_specs('scout', config=_config(store, run_id, 'd'))
    search = next(t for t in specs if t['name'] == 'web_search')
    assert search['parameters']['properties']['provider']['enum'] == ['duckduckgo']
    assert 'add_url' in [t['name'] for t in specs] and 'extract_pages' not in [t['name'] for t in specs]

    store, run_id, _ = _run(tmp_path / 'tv', allow_web=True, policy={'primary_provider': 'tavily', 'native_search_enabled': True})
    names = [t['name'] for t in runner_tool_specs('scout', config=_config(store, run_id, 't'))]
    assert {'web_search', 'add_url', 'extract_pages'} <= set(names)

    # Host search only: the engine has none, so only saving given URLs is left.
    store, run_id, _ = _run(tmp_path / 'native', allow_web=True, policy={'primary_provider': 'native'})
    config = _config(store, run_id, 'n')
    assert [t['name'] for t in runner_tool_specs('scout', config=config)] == [
        'source_read', 'source_grep', 'render_pdf_pages', 'add_url', 'submit_scout_result']
    note = (store.root / 'jobs' / 'n' / 'packet' / 'search-policy.md').read_text(encoding='utf-8')
    assert '内置引擎没有这项能力' in note and 'web-search --run' not in note


def test_packet_prompt_and_system_prompt_name_only_native_tools(tmp_path):
    store, run_id, sid = _run(tmp_path, allow_web=True, policy={'primary_provider': 'duckduckgo'})
    task = scout.task(store, run_id, {'slot_id': 'scout-2', 'theme': 'revenue'})
    packet = scout_packet(store, task, tmp_path / 'slot')
    for name in ('task.json', 'scout-contract.md', 'reader-contract.json', 'search-policy.md', 'source-index.json', 'scout.schema.json'):
        assert (packet / name).is_file(), name
    assert json.loads((packet / 'source-index.json').read_text(encoding='utf-8'))[0]['source_id'] == sid
    assert '逐字摘录' in (packet / 'scout-contract.md').read_text(encoding='utf-8')
    prompt = scout.native_prompt(task)
    assert 'submit_scout_result' in prompt and 'source_read' in prompt and 'scout-2' in prompt
    assert 'briefloop' not in prompt.lower().replace('本报告', '') and str(tmp_path) not in prompt
    text = system_prompt('scout')['text']
    assert '研究检索（Scout）' in text and 'excerpt 是原文逐字摘录' in text


def test_web_search_and_add_url_spend_the_run_budget(tmp_path, monkeypatch):
    store, run_id, _ = _run(tmp_path, allow_web=True, policy={'primary_provider': 'duckduckgo'})
    config = _config(store, run_id, 'slot')

    class Provider:
        validate_search = staticmethod(lambda q, opts: opts)
        call_search = staticmethod(lambda q, params, **kw: ({}, b'{}', 'request', None))
        rows = staticmethod(lambda parsed: [{'url': 'https://example.test/report', 'title': 'Report', 'content': 'snippet'}])
        search_note = staticmethod(lambda: 'discovery only')
    monkeypatch.setattr(websearch, 'provider_module', lambda provider: Provider)
    monkeypatch.setattr(sources, '_fetch_bytes', lambda url, **_: (b'Fetched page\nOrders reached 5 GW.\n', 'text/plain', 'utf-8'))

    refused = run_tool(store, config, 'web_search', {'provider': 'tavily', 'query': 'q', 'purpose': 'primary', 'reason': 'r'})
    assert not refused['ok'] and '允许的受控渠道' in refused['error']
    found = run_tool(store, config, 'web_search', {'provider': 'duckduckgo', 'query': 'orders', 'purpose': 'primary', 'reason': 'find orders'})
    body = json.loads(found['content'][0]['text'])
    assert found['ok'] and body['results'][0]['url'] == 'https://example.test/report' and body['remaining']['search_requests'] == 1
    added = json.loads(run_tool(store, config, 'add_url', {'url': 'https://example.test/report'})['content'][0]['text'])
    assert added['status'] == 'ready' and added['lines'] == 2 and added['remaining']['source_pages'] == 2
    assert added['source_id'] in store.source_ids(run_id)
    read = run_tool(store, config, 'source_read', {'source_id': added['source_id']})
    assert '2: Orders reached 5 GW.' in read['content'][0]['text']
    grep = run_tool(store, config, 'source_grep', {'pattern': 'GW'})
    assert f"{added['source_id']} 2: Orders reached 5 GW." in grep['content'][0]['text']
    outside = run_tool(store, config, 'source_read', {'source_id': 'src_other'})
    assert not outside['ok'] and 'add_url' in outside['error']


def test_submit_saves_a_checked_result_to_the_slot(tmp_path):
    store, run_id, sid = _run(tmp_path)
    config = _config(store, run_id, 'slot')
    paraphrase = run_tool(store, config, 'submit_scout_result', {'sources': [_evidence(sid, excerpt='Revenue grew 20%')], 'gaps': []})
    assert not paraphrase['ok'] and '逐字' in paraphrase['error'] and not (store.root / 'jobs' / 'slot' / 'result.json').exists()
    other = store.add_source('Unregistered', 'Revenue rose to USD 12 million in 2025,')['id']
    stray = run_tool(store, config, 'submit_scout_result', {'sources': [_evidence(other, locator='line 1', excerpt='Revenue rose')], 'gaps': []})
    assert not stray['ok'] and not (store.root / 'jobs' / 'slot' / 'result.json').exists()
    ok = run_tool(store, config, 'submit_scout_result', {'sources': [_evidence(sid)], 'gaps': ['No 2024 figure'], 'search_summary': 'offline'})
    assert ok['ok'] and json.loads(ok['settle']) == {'sources': 1, 'gaps': 1}
    saved = json.loads((store.root / 'jobs' / 'slot' / 'result.json').read_text(encoding='utf-8'))
    assert saved['sources'][0]['source_id'] == sid and saved['gaps'] == ['No 2024 figure']


def test_harness_requires_a_run_and_slot_for_a_scout():
    with pytest.raises(ValueError, match='Scout 任务缺少'):
        NativeHarness._config({'model': 'fake/m1', 'native_role': 'scout', 'packet_root': '/p'})


class Engine:
    def __init__(self, sid):
        self.sid, self.calls, self.sinks = sid, [], {}
        self.process = object()

    def subscribe(self, execution_id):
        self.sinks[execution_id] = queue.Queue()
        return self.sinks[execution_id]

    def unsubscribe(self, execution_id):
        self.sinks.pop(execution_id, None)

    def call(self, method, params, timeout=None):
        self.calls.append((method, params))
        if method == 'session_create':
            return {'session_id': params['session_id'], 'session_file': '/s.jsonl', 'model': params['model']}
        if method == 'turn_start':
            sink = self.sinks[params['execution_id']]
            sink.put({'kind': 'tool_request', 'request_id': 'read-1', 'tool': 'source_read', 'args': {'source_id': self.sid}})
            sink.put({'kind': 'tool_request', 'request_id': 'submit-1', 'tool': 'submit_scout_result',
                      'args': {'sources': [_evidence(self.sid)], 'gaps': []}})
            sink.put({'kind': 'end', 'status': 'completed', 'final_text': 'done'})
        return {}

    def close(self):
        pass


def test_the_runtime_runs_one_scout_slot_on_the_native_engine(tmp_path):
    from briefloop.interactive_runtime import InteractiveRuntime
    store, run_id, sid = _run(tmp_path)
    engine = Engine(sid)
    harness = NativeHarness(store, engine)
    runtime = InteractiveRuntime(store, backends={'briefloop-native': harness})
    # Written directly: the main-chain gate still refuses native generate jobs.
    from briefloop.store import uid
    jid = uid('job')
    with store.tx() as c:
        c.execute("INSERT INTO jobs(id,kind,payload,status,created,updated) VALUES(?,?,?,?,datetime('now'),datetime('now'))",
                  (jid, 'generate', json.dumps({'run_id': run_id, 'agent_backend': 'briefloop-native',
                                                'runtime': {'model': 'fake/m1'}}), 'running'))
    job = store.one('jobs', jid)
    slot = store.root / 'jobs' / job['id'] / 'scout-1'
    joined = scout.run(store, runtime, job, run_id, {'slot_id': 'scout-1', 'theme': 'revenue'}, slot, 'briefloop-native')
    assert joined['sources'][0]['source_id'] == sid
    create = next(p for name, p in engine.calls if name == 'session_create')
    assert create['role'] == 'scout' and create['packet_root'] == str(slot / 'packet')
    assert [t['name'] for t in create['runner_tools']][-1] == 'submit_scout_result'
    deadline = time.monotonic() + 5
    while len([p for name, p in engine.calls if name == 'tool_result']) < 2:
        assert time.monotonic() < deadline
        time.sleep(.02)
    results = {p['request_id']: p for name, p in engine.calls if name == 'tool_result'}
    assert results['read-1']['ok'] and results['submit-1']['ok']
    harness.close()
