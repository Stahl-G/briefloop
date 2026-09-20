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


def test_writer_packet_is_identical_across_directories_and_confined(tmp_path):
    store, run, source, inputs = setup(tmp_path)
    a = analyst.packet(store, run['id'], store.root/'a', **inputs)
    b = analyst.packet(store, run['id'], store.root/'b', **inputs)
    assert a['fingerprint'] == b['fingerprint']
    config = {'native_role': 'analyst', 'run_id': run['id'], 'packet_root': str(a['root']),
              'result_file': str(a['root'].parent/'draft.json')}
    names = [t['name'] for t in runner_tool_specs('analyst', config=config)]
    assert names == ['render_pdf_pages', 'prepare_report_data', 'submit_draft']
    assert not run_tool(store, config, 'web_search', {'query': 'q'})['ok']
    outside = store.add_source('无关任务', 'Cannot cite me')
    refused = run_tool(store, config, 'submit_draft', {'draft': draft(outside['id'])})
    assert not refused['ok'] and '超出' in refused['error']
    assert not (a['root'].parent/'draft.json').exists()
    assert '主写稿' in system_prompt('analyst')['text']
    result = run_tool(store, config, 'submit_draft', {'draft': draft(source['id'])})
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
            cfg = {**staged['native_packet'], 'native_role': 'analyst', 'packet_root': str(folder/'packet')}
            assert run_tool(store, cfg, 'submit_draft', {'draft': draft(source['id'])})['ok']
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
           'result_file': str(tmp_path/'escape.json')}
    assert not run_tool(store, cfg, 'submit_draft', {'draft': draft(source['id'])})['ok']
    assert not (tmp_path/'escape.json').exists()
    (store.root/source['path']).write_text('已被篡改')
    cfg['result_file'] = str(p['root'].parent/'draft.json')
    result = run_tool(store, cfg, 'submit_draft', {'draft': draft(source['id'])})
    assert not result['ok']
