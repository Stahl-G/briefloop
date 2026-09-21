"""Condition navigation and incremental writing feedback, not semantic scoring."""
import json

from briefloop import analyst
from briefloop.document_model import document_markdown, normalize_document, source_ids
from briefloop.native_roles import run_tool
from briefloop.scout_tools import read_source
from briefloop.source_context import navigation
from test_native_analyst import setup, draft, saved_revision


def test_navigation_includes_restriction_and_later_removal_without_rewriting_source(tmp_path):
    store, run, _, inputs = setup(tmp_path)
    lines = ['Ordinary product description.'] * 320
    lines[266] = 'The pull requests are pending. Install from the PR branch before adopting.'
    lines[299] = 'Correction: PRs are now merged. Branch installation is no longer required.'
    text = '\n'.join(lines)
    source = store.add_source('更新说明', text)
    store.attach_source(run['id'], source['id'])
    hints = navigation(text)
    assert all(any(r['start_line'] <= line <= r['end_line'] for r in hints['candidate_ranges']) for line in (267, 300))
    assert 'status' not in hints and 'facts' not in hints
    partial = read_source(store, source['id'], start_line=160, end_line=200, max_chars=60000)
    assert '264-273' in partial and '297-306' in partial
    assert '已截取部分正文' in partial and 'PRs are now merged' not in partial
    assert read_source(store, source['id']) == text
    a = analyst.packet(store, run['id'], store.root/'a', **inputs)
    b = analyst.packet(store, run['id'], store.root/'b', **inputs)
    assert a['fingerprint'] == b['fingerprint']
    frozen = json.loads((a['root']/'source-context.json').read_text())['sources'][source['id']]
    assert frozen['candidate_ranges'] == hints['candidate_ranges']
    assert (a['root']/f"sources/{source['id']}.txt").read_text() == text
    assert 'source-context.json' in a['files']


def test_navigation_discloses_truncation_and_no_match_is_not_clearance():
    text = '\n'.join(('Pending approval\n' + 'detail\n'*30) for _ in range(20))
    hints = navigation(text)
    assert len(hints['candidate_ranges']) == 16 and hints['omitted_ranges'] == 4
    assert navigation('No technical details provided.')['candidate_ranges'] == []
    assert '无匹配不代表没有条件' in navigation('No technical details provided.')['scope']


def test_packet_table_example_validates_and_keeps_reader_citation(tmp_path):
    store, run, source, inputs = setup(tmp_path)
    pack = analyst.packet(store, run['id'], store.root/'writer', **inputs)
    example = json.loads((pack['root']/'document-guide.json').read_text())['table_example']
    example = json.loads(json.dumps(example, ensure_ascii=False).replace('替换为真实src_ID', source['id']))
    doc = normalize_document({'type': 'doc', 'content': [example]})
    assert source['id'] in source_ids(doc)
    assert '[@' + source['id'] + ']' in document_markdown(doc)


def test_section_receipt_counts_latest_content_and_partial_reassembly_keeps_metadata(tmp_path):
    store, _, source, inputs = setup(tmp_path)
    run = store.create_run({'title': '简报', 'objective': '分析', 'allow_web': False,
                            'target_words': 100, 'max_words': 150}, [source['id']])
    pack = analyst.packet(store, run['id'], store.root/'writer', **inputs)
    config = {'native_role': 'analyst', 'run_id': run['id'], 'packet_root': str(pack['root']),
              'result_file': str(pack['root'].parent/'draft.json'), 'attempt_id': 'writer-a'}
    def save(sid, size):
        result = run_tool(store, config, 'save_draft_section', {'section_id': sid,
            'content': draft(source['id'], '文' * size)['editor_document']['content']})
        assert result['ok'], result
        return json.loads(result['content'][0]['text'])
    save('a', 80)
    receipt = save('b', 80)
    assert receipt['saved_sections_length']['count'] == 160
    assert receipt['saved_sections_length']['over_by'] == 10
    first = saved_revision(store, config, {'title': '简报', 'section_ids': ['a', 'b'], 'gaps': ['仍缺验收数据']})
    receipt = save('a', 40)
    assert receipt['saved_sections_length']['count'] == 120
    assert receipt['saved_sections_length']['remaining_to_max'] == 30
    assert receipt['section_lengths'] == {'a': 40, 'b': 80}
    second = saved_revision(store, config, {'base_revision': first, 'section_ids': ['a', 'b']})
    from briefloop.analyst_drafts import _candidate
    value = _candidate(store, config, {'revision': second})['draft']
    assert value['gaps'] == ['仍缺验收数据']
    checked = run_tool(store, config, 'check_draft', {'revision': second})
    assert json.loads(checked['content'][0]['text'])['diagnostics']['length']['count'] == 120
