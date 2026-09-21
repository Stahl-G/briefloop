import json
from pathlib import Path

import pytest

from briefloop import analyst
from briefloop.agent_prompts import system_prompt
from briefloop.native_roles import run_tool, runner_tool_specs
from briefloop.store import Store, Conflict


def setup(tmp_path):
    store = Store(tmp_path / 'ws')
    source = store.add_source('财报', '2025年收入1200万元，同比增长20%。')
    run = store.create_run({'title': '经营简报', 'objective': '中文解释收入增长', 'allow_web': False}, [source['id']])
    inputs = {'plan': {'draft_structure': ['业绩', '建议']},
              'research': {'sources': [{'source_id': source['id'], 'locator': 'line 1',
                                        'excerpt': '2025年收入1200万元，同比增长20%。', 'facts': ['收入增长20%'],
                                        'coverage_status': 'complete'}], 'gaps': []}}
    return store, run, source, inputs


def draft(sid, text='收入增长20%，下一步观察毛利能否同步改善。'):
    return {'title': '经营简报', 'editor_document': {'type': 'doc', 'content': [
        {'type': 'paragraph', 'content': [{'type': 'text', 'text': text},
         {'type': 'citation', 'attrs': {'sourceId': sid}}]}]},
        'citations': [{'source_id': sid, 'locator': 'line 1'}]}


def saved_revision(store, config, value):
    result = run_tool(store, config, 'save_draft', value)
    assert result['ok'], result
    return json.loads(result['content'][0]['text'])['revision']


def finish(store, config, value):
    result = run_tool(store, config, 'save_draft', value.get('draft', value))
    if not result['ok']: return result
    revision = json.loads(result['content'][0]['text'])['revision']
    checked = run_tool(store, config, 'check_draft', {'revision': revision})
    if not checked['ok']: return checked
    return run_tool(store, config, 'submit_draft', {'revision': revision})


def test_writer_packet_is_identical_across_directories_and_confined(tmp_path):
    store, run, source, inputs = setup(tmp_path)
    a = analyst.packet(store, run['id'], store.root/'a', **inputs)
    b = analyst.packet(store, run['id'], store.root/'b', **inputs)
    assert a['fingerprint'] == b['fingerprint']
    config = {'native_role': 'analyst', 'run_id': run['id'], 'packet_root': str(a['root']),
              'result_file': str(a['root'].parent/'draft.json'), 'attempt_id': 'a'}
    names = [t['name'] for t in runner_tool_specs('analyst', config=config)]
    assert names == ['render_pdf_pages', 'prepare_report_data', 'read_draft', 'save_draft_section', 'save_draft', 'check_draft', 'submit_draft']
    assert not run_tool(store, config, 'web_search', {'query': 'q'})['ok']
    outside = store.add_source('无关任务', 'Cannot cite me')
    refused = finish(store, config, {'draft': draft(outside['id'])})
    assert not refused['ok'] and '超出' in refused['error']
    assert not (a['root'].parent/'draft.json').exists()
    assert '主写稿' in system_prompt('analyst')['text']
    result = finish(store, config, {'draft': draft(source['id'])})
    assert result['ok'] and result['settle']
    assert json.loads((a['root'].parent/'draft.json').read_text())['editor_document']


def test_real_role_runner_saves_draft_and_revision_without_overwriting_user_edits(tmp_path):
    store, run, source, inputs = setup(tmp_path)
    job = {'id': 'job_write', 'kind': 'generate', 'payload': json.dumps(
        {'agent_backend': 'briefloop-native', 'runtime': {'model': 'fake/writer'}, 'run_id': run['id']})}

    class Runtime:
        calls = 0
        def execute(self, staged, prompt, folder):
            self.calls += 1
            assert staged['runtime_role'] == 'analyst' and staged['allow_web'] is False
            cfg = {**staged['native_packet'], 'native_role': 'analyst', 'packet_root': str(folder/'packet'), 'attempt_id': staged['id']}
            assert finish(store, cfg, {'draft': draft(source['id'])})['ok']
    runtime = Runtime()
    first = analyst.run(store, runtime, job, run['id'], store.root/'first', 'briefloop-native', **inputs)
    original = store.one('briefs', first['version_id'])
    revised = analyst.run(store, runtime, {**job, 'id': 'job_second'}, run['id'], store.root/'second',
                          'briefloop-native', base_version=original['id'], feedback=['保留已证实数据'], **inputs)
    assert store.one('briefs', revised['version_id'])['parent_id'] == original['id']
    store.revise(revised['version_id'], editor_document=draft(source['id'], '人工稿')['editor_document'])
    with pytest.raises(Conflict, match='用户已修改'):
        analyst.run(store, runtime, {**job, 'id': 'job_third'}, run['id'], store.root/'third',
                    'briefloop-native', base_version=revised['version_id'], feedback=['修订'], **inputs)
    assert (store.root/'third/draft.json').exists()  # failed admission leaves the suggestion
    with pytest.raises(ValueError, match='指纹变化'):
        analyst.run(store, runtime, job, run['id'], store.root/'mismatch', 'briefloop-native',
                    expected_fingerprint='different', **inputs)
    assert runtime.calls == 3


def test_source_tampering_and_output_escape_are_rejected(tmp_path):
    store, run, source, inputs = setup(tmp_path)
    p = analyst.packet(store, run['id'], store.root/'writer', **inputs)
    cfg = {'native_role': 'analyst', 'run_id': run['id'], 'packet_root': str(p['root']),
           'result_file': str(tmp_path/'escape.json'), 'attempt_id': 'a'}
    assert not finish(store, cfg, {'draft': draft(source['id'])})['ok']
    assert not (tmp_path/'escape.json').exists()
    (store.root/source['path']).write_text('已被篡改')
    cfg['result_file'] = str(p['root'].parent/'draft.json')
    result = finish(store, cfg, {'draft': draft(source['id'])})
    assert not result['ok']


def test_root_submission_and_saved_sections_preserve_order_and_attempt_scope(tmp_path):
    store, run, source, inputs = setup(tmp_path)
    p = analyst.packet(store, run['id'], store.root/'writer', **inputs)
    cfg = {'native_role': 'analyst', 'run_id': run['id'], 'packet_root': str(p['root']),
           'result_file': str(p['root'].parent/'draft.json'), 'attempt_id': 'attempt-a'}
    spec = next(t for t in runner_tool_specs('analyst', config=cfg) if t['name'] == 'submit_draft')
    assert list(spec['parameters']['properties']) == ['revision']
    outside = store.add_source('另一任务', '不能引用')
    bad = draft(outside['id'])
    assert not run_tool(store, cfg, 'save_draft_section', {
        'section_id': 'bad', 'content': bad['editor_document']['content']})['ok']
    for sid, text in [('second', '第二章旧稿'), ('first', '第一章正文'), ('second', '第二章修订')]:
        d = draft(source['id'], text)
        r = run_tool(store, cfg, 'save_draft_section', {
            'section_id': sid, 'content': d['editor_document']['content'], 'citations': d['citations']})
        assert r['ok'] and 'settle' not in r and text not in json.dumps(r, ensure_ascii=False)
    value = {'title': '经营简报', 'section_ids': ['first', 'second']}
    assert not finish(store, {**cfg, 'attempt_id': 'attempt-b'}, value)['ok']
    assert not finish(store, cfg, {**value, 'section_ids': ['first', 'missing']})['ok']
    assert not finish(store, cfg, {**value, 'editor_document': draft(source['id'])['editor_document']})['ok']
    assert not Path(cfg['result_file']).exists()
    assert finish(store, cfg, value)['ok']
    saved = json.loads(Path(cfg['result_file']).read_text())
    assert saved['markdown'].index('第一章') < saved['markdown'].index('第二章修订')
    assert '旧稿' not in saved['markdown'] and len(saved['citations']) == 1
    assert finish(store, cfg, draft(source['id']))['ok']


def test_cancelled_writer_cannot_save_late_sections_or_finish(tmp_path):
    from briefloop.native_harness import NativeHarness
    from types import SimpleNamespace
    store, run, source, inputs = setup(tmp_path)
    p = analyst.packet(store, run['id'], store.root/'writer', **inputs)
    cfg = {'model': 'fake/writer', 'native_role': 'analyst', 'run_id': run['id'],
           'packet_root': str(p['root']), 'review_root': str(p['root']),
           'result_file': str(p['root'].parent/'draft.json'), 'attempt_id': 'cancelled'}
    replies = []
    harness = NativeHarness(store, SimpleNamespace(call=lambda method, params, **kw: replies.append(params)))
    sid = harness.create_session('writer', cfg)['id']
    harness.cancel(sid)
    for name, args in [('save_draft_section', {'section_id': 'one', 'content': draft(source['id'])['editor_document']['content']}),
                       ('submit_draft', draft(source['id']))]:
        harness._run_tool(sid, cfg, {'tool': name, 'args': args, 'request_id': name})
        assert replies[-1]['ok'] is False and '取消' in replies[-1]['error']
    assert not Path(cfg['result_file']).exists() and not list(p['root'].parent.glob('draft-sections-*'))


def test_preflight_reports_short_sections_and_missing_provenance_without_finishing(tmp_path):
    store, run, source, inputs = setup(tmp_path)
    p = analyst.packet(store, run['id'], store.root/'writer', **inputs)
    cfg = {'native_role': 'analyst', 'run_id': run['id'], 'packet_root': str(p['root']),
           'result_file': str(p['root'].parent/'draft.json'), 'attempt_id': 'preflight'}
    task = json.loads((p['root']/'input.json').read_text())
    task['requirements'].update(target_words=1000, max_words=1500)
    (p['root']/'input.json').write_text(json.dumps(task))
    body = [{'type': 'heading', 'attrs': {'level': 2}, 'content': [{'type': 'text', 'text': '经营分析'}]},
            *draft(source['id'])['editor_document']['content']]
    saved = run_tool(store, cfg, 'save_draft_section', {'section_id': 'one', 'content': body})
    assert saved['ok']
    ledger = next(p['root'].parent.glob('draft-sections-*.json'))
    original = ledger.read_bytes()
    revision = saved_revision(store, cfg, {'title': '经营简报', 'section_ids': ['one']})
    result = run_tool(store, cfg, 'check_draft', {'revision': revision})
    assert result['ok'] and 'settle' not in result
    data = json.loads(result['content'][0]['text'])['diagnostics']
    assert data['review_status'] == 'not_reviewed' and data['length']['below_target']
    assert data['sections'][0]['title'] == '经营分析' and data['sections'][0]['body_units'] > 0
    assert data['citations']['missing_locator'] == [source['id']]
    assert data['numbers']['status'] == 'not_checked'
    assert {w['code'] for w in data['warnings']} == {'citation_location_missing', 'number_bindings_missing'}
    assert not Path(cfg['result_file']).exists() and ledger.read_bytes() == original
    # Diagnostic warnings must never hide the user's draft or prevent ordinary saving.
    assert run_tool(store, cfg, 'submit_draft', {'revision': revision})['ok']


def test_preflight_reuses_exact_number_binding_check_not_citation_presence(tmp_path):
    from briefloop.draft_checks import inspect_draft
    store, run, source, _ = setup(tmp_path)
    value = draft(source['id'], '收入增长30%，需解释差异。')
    value['number_bindings'] = [{'label': '收入增长', 'entity': '公司', 'period': '2025',
        'value': 20, 'unit': '%', 'source_id': source['id'], 'locator': 'line 1',
        'source_excerpt': '2025年收入1200万元，同比增长20%。',
        'report_quote': '收入增长30%，需解释差异。', 'number_text': '30%'}]
    result = inspect_draft(value, store=store, allowed_sources={source['id']})
    assert result['numbers']['checked'] == 1
    assert result['numbers']['results'][0]['found'] is False
    assert 'number_mismatch' in {w['code'] for w in result['warnings']}
    value['editor_document']['content'][0]['content'][0]['text'] = '收入增长20%，需解释差异。'
    value['number_bindings'][0].update(report_quote='收入增长20%，需解释差异。', number_text='20%')
    clean = inspect_draft(value, store=store, allowed_sources={source['id']})
    assert clean['numbers']['results'][0]['found'] is True
    assert clean['review_status'] == 'not_reviewed'  # Matching never becomes a fact verdict.


def test_prepared_reader_requirements_reach_shared_writer_packet(tmp_path):
    from briefloop.writing_guidance import ANALYST_GUIDE
    from briefloop.deliverable_spec import resolve, instructions
    store = Store(tmp_path)
    source = store.add_source('合成进度', 'A项目延期一周。')
    req = {'title': '项目周报', 'objective': '判断延期影响，融资留给我填写',
           'audience': '业务负责人', 'key_questions': ['延期影响哪一项交付？'],
           'manual_sections': ['融资'], 'writing_preferences': ['先结论，后依据'],
           'workflow_id': 'business_report', 'workflow_variant': 'work_progress',
           'period_start': '2026-09-01', 'period_end': '2026-09-07',
           'report_timezone': 'Asia/Shanghai', 'target_minutes': 10}
    run = store.create_run(req, [source['id']])
    saved = json.loads(run['requirements'])
    pack = analyst.packet(store, run['id'], store.root/'writer', plan={}, research={'sources': [], 'gaps': []})
    input_ = json.loads((pack['root']/'input.json').read_text())
    for key in req:
        assert input_['requirements'][key] == req[key]
    shared = instructions(resolve(saved), 'analyst')
    assert ANALYST_GUIDE in shared
    assert shared in (pack['root']/'writing.md').read_text()
    assert ANALYST_GUIDE not in instructions(resolve(saved), 'reviewer')
