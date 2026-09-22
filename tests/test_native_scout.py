"""The Scout on the native engine: a frozen task, metered web tools, checked evidence."""
import json
import queue
import threading
import time

import pytest

from briefloop import scout, sources, websearch
from briefloop.agent_prompts import system_prompt
from briefloop.models import ScoutResult
from briefloop.native_harness import NativeHarness
from briefloop.native_roles import run_tool, runner_tool_specs, scout_packet
from briefloop.scout_tools import evidence_errors
from briefloop.store import Store, content_hash
from briefloop.scout_evidence import begin, close

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
    config = {'model': 'fake/m1', 'attempt_id': name, 'native_role': 'scout', 'packet_root': str(folder / 'packet'), 'run_id': run_id,
            'result_file': str(folder / 'result.json'), 'allow_web': task['allow_web'],
            'search_channels': task['search_channels'], **extra}
    begin(store, config)
    return config


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
    assert '跨 80 行' in evidence_errors(store, run_id, ScoutResult.model_validate(
        {'sources': [_evidence(far_id, excerpt='Later note on cash.', locator='line 1-80')]}))[0]
    # Emphasis marks and quote styles are formatting, not content.
    styled = store.add_source('Styled', 'Each entered an amendment (the “**Amendment**”) on Sept. 1.\n')['id']
    store.attach_source(run_id, styled)
    assert evidence_errors(store, run_id, ScoutResult.model_validate({'sources': [_evidence(
        styled, locator='line 1', excerpt='Each entered an amendment (the "Amendment") on Sept. 1.')]})) == []
    assert '不可解析' in check(locator='second paragraph')[0]
    assert check(locator='', excerpt='') == []


def test_scout_tools_follow_the_run_web_permission_and_channels(tmp_path):
    store, run_id, _ = _run(tmp_path / 'offline')
    names = [t['name'] for t in runner_tool_specs('scout', config=_config(store, run_id, 'o'))]
    assert NativeHarness._sequential(_config(store, run_id, 'seq'), 'record_evidence')
    assert next(t for t in runner_tool_specs('scout', config=_config(store, run_id, 'seq')) if t['name']=='record_evidence')['sequential']
    assert names == ['source_read', 'source_grep', 'render_pdf_pages', 'record_evidence', 'submit_scout_result']

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
        'source_read', 'source_grep', 'render_pdf_pages', 'add_url', 'record_evidence', 'submit_scout_result']
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
    assert 'record_evidence' in prompt and str(tmp_path) not in prompt
    assert task['contract'] in prompt and '已内联部分无需再次' in prompt
    text = system_prompt('scout')['text']
    assert '研究检索（Scout）' in text and 'excerpt 逐字摘录' in text


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
    assert f"{added['source_id']} 2 (start_char=0): Orders reached 5 GW." in grep['content'][0]['text']
    outside = run_tool(store, config, 'source_read', {'source_id': 'src_other'})
    assert not outside['ok'] and 'add_url' in outside['error']


def item(sid, **extra):
    return {'id': 'revenue', 'source_id': sid, 'source_hash': content_hash(TEXT), 'locator': 'line 2-3',
            'quote': 'Revenue rose to USD 12 million', 'facts': ['Revenue grew 20%'], 'coverage_status': 'complete', **extra}


def test_submit_saves_a_checked_result_to_the_slot(tmp_path):
    store, run_id, sid = _run(tmp_path)
    config = _config(store, run_id, 'slot')
    response = run_tool(store, config, 'record_evidence', {'items': [item(sid), item(sid, id='bad', quote='Revenue grew to twenty')]})
    data = json.loads(response['content'][0]['text'])
    assert data['total'] == 1 and data['pending_ids'] == ['bad']
    assert not run_tool(store, config, 'submit_scout_result', {'gaps': []})['ok']
    assert not (store.root / 'jobs' / 'slot' / 'result.json').exists()
    # Only repair the rejected item; the first accepted item remains intact.
    response = run_tool(store, config, 'record_evidence', {'items': [item(sid, id='bad', locator='line 4', quote='Footnote: audited.')]})
    assert json.loads(response['content'][0]['text'])['total'] == 2
    ok = run_tool(store, config, 'submit_scout_result', {'gaps': ['No 2024 figure'], 'search_summary': 'offline'})
    assert ok['ok'] and json.loads(ok['settle']) == {'sources': 2, 'gaps': 1}
    saved = json.loads((store.root / 'jobs' / 'slot' / 'result.json').read_text())
    assert TEXT.splitlines()[1] + '\n' + TEXT.splitlines()[2] in [x['excerpt'] for x in saved['sources']]
    assert not evidence_errors(store, run_id, ScoutResult.model_validate(saved))


def test_incremental_identity_relocation_and_discard(tmp_path):
    store, run_id, sid = _run(tmp_path)
    config = _config(store, run_id, 'slot')
    def record(items, **extra):
        result = run_tool(store, config, 'record_evidence', {'items': items, **extra})
        assert result['ok'], result
        return json.loads(result['content'][0]['text'])
    moved = record([item(sid, locator='line 20')])
    assert moved['accepted'][0]['relocated'] and moved['accepted'][0]['locator'] == 'line 2'
    assert record([item(sid)])['total'] == 1  # same ID replaces, not appends
    unrelated = store.add_source('Other', TEXT)['id']
    assert record([item(unrelated, id='wrong-run')])['rejected']
    duplicate = store.add_source('Duplicated', TEXT + TEXT)['id']; store.attach_source(run_id, duplicate)
    assert record([item(duplicate, id='ambiguous', source_hash=content_hash(TEXT+TEXT), locator='line 30')])['rejected']
    assert record([item(sid, id='changed', source_hash='old')])['rejected']
    dropped = record([], discard=[{'id': x, 'reason': 'Cannot support claim'} for x in ['wrong-run', 'ambiguous', 'changed']])
    assert dropped['pending_ids'] == []
    ok = run_tool(store, config, 'submit_scout_result', {'gaps': []})
    assert ok['ok'] and json.loads(ok['settle'])['gaps'] == 3
    close(store, config)
    assert not run_tool(store, config, 'record_evidence', {'items': [item(sid)]})['ok']
    retry = {**config, 'attempt_id': 'next-turn'}; begin(store, retry)
    assert not run_tool(store, retry, 'submit_scout_result', {'gaps': []})['ok']


def test_read_bound_and_persisted_source_hash(tmp_path):
    store, run_id, sid = _run(tmp_path)
    config = _config(store, run_id, 'slot')
    long = store.add_source('Long', 'x'*70000)['id']; store.attach_source(run_id, long)
    result = run_tool(store, config, 'source_read', {'source_id': long})
    text = result['content'][0]['text']
    assert 'source_hash: ' + content_hash('x'*70000) in text
    assert '1: ' + 'x'*60000 in text and 'x'*60001 not in text
    # The same explicit read window as the host remains available, without grep.
    short = run_tool(store, config, 'source_read', {'source_id': long, 'max_chars': 1000})
    assert '1: ' + 'x'*1000 in short['content'][0]['text']
    assert 'x'*1001 not in short['content'][0]['text']


def test_harness_requires_a_run_and_slot_for_a_scout():
    with pytest.raises(ValueError, match='Scout 任务缺少'):
        NativeHarness._config({'model': 'fake/m1', 'native_role': 'scout', 'packet_root': '/p'})


class Engine:
    def __init__(self, sid):
        self.sid, self.calls, self.sinks = sid, [], {}
        self.process = object()
        self._lock = threading.Lock()
        self._execution_id = None
        self._completed_requests = set()
        self._end_emitted = False

    def subscribe(self, execution_id):
        self.sinks[execution_id] = queue.Queue()
        return self.sinks[execution_id]

    def unsubscribe(self, execution_id):
        self.sinks.pop(execution_id, None)

    def call(self, method, params, timeout=None):
        with self._lock:
            self.calls.append((method, params))
        if method == 'session_create':
            return {'session_id': params['session_id'], 'session_file': '/s.jsonl', 'model': params['model']}
        if method == 'turn_start':
            with self._lock:
                self._execution_id = params['execution_id']
                self._completed_requests.clear()
                self._end_emitted = False
                sink = self.sinks[self._execution_id]
            sink.put({'kind': 'tool_request', 'request_id': 'read-1', 'tool': 'source_read', 'args': {'source_id': self.sid}})
            sink.put({'kind': 'tool_request', 'request_id': 'record-1', 'tool': 'record_evidence', 'args': {'items': [item(self.sid)]}})
            sink.put({'kind': 'tool_request', 'request_id': 'submit-1', 'tool': 'submit_scout_result',
                      'args': {'gaps': []}})
        elif method == 'tool_result':
            with self._lock:
                if params['request_id'] in {'read-1', 'record-1', 'submit-1'}:
                    self._completed_requests.add(params['request_id'])
                if not self._end_emitted and self._completed_requests == {'read-1', 'record-1', 'submit-1'}:
                    self._end_emitted = True
                    self.sinks[self._execution_id].put({'kind': 'end', 'status': 'completed', 'final_text': 'done'})
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
    while len([p for name, p in engine.calls if name == 'tool_result']) < 3:
        assert time.monotonic() < deadline
        time.sleep(.02)
    results = {p['request_id']: p for name, p in engine.calls if name == 'tool_result'}
    assert results['read-1']['ok'] and results['submit-1']['ok']
    harness.close()


def test_concurrent_records_survive_and_changed_source_cannot_publish(tmp_path):
    from concurrent.futures import ThreadPoolExecutor
    store, run_id, sid = _run(tmp_path)
    config = _config(store, run_id, 'slot')
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda i: run_tool(store, config, 'record_evidence', {'items': [item(sid, id=f'e{i}')]}), range(2)))
    assert all(r['ok'] for r in results)
    from briefloop.scout_evidence import collect
    assert len(collect(store, config)[0]) == 2
    original = store.one('sources', sid)
    (store.root / original['path']).write_text('Changed source', encoding='utf-8')
    result = run_tool(store, config, 'submit_scout_result', {'gaps': []})
    assert not result['ok'] and not (store.root/'jobs'/'slot'/'result.json').exists()


def test_long_single_line_can_be_found_read_and_recorded_without_copying_the_whole_source(tmp_path):
    store, run_id, _ = _run(tmp_path)
    phrase = 'Revenue rose to USD 12 million; excludes discontinued operations.'
    line = 'x'*30000 + phrase + 'z'*30000
    sid = store.add_source('Long single line', line)['id']; store.attach_source(run_id, sid)
    config = _config(store, run_id, 'long')
    grep = run_tool(store, config, 'source_grep', {'source_id': sid, 'pattern': 'Revenue'})
    assert 'start_char=29900' in grep['content'][0]['text'] and 'Revenue rose' in grep['content'][0]['text']
    read = run_tool(store, config, 'source_read', {'source_id': sid, 'start_line': 1, 'start_char': 30000, 'max_chars': 24000})
    assert 'Revenue rose' in read['content'][0]['text'] and 'start_char=54000' in read['content'][0]['text']
    value = item(sid, source_hash=content_hash(line), locator='line 1', start_char=30000, end_char=30000+len(phrase))
    response = run_tool(store, config, 'record_evidence', {'items': [value]})
    accepted = json.loads(response['content'][0]['text'])['accepted']
    assert accepted[0]['excerpt'] == phrase and 'excludes discontinued operations.' in accepted[0]['excerpt']
