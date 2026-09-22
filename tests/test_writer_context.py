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
    assert [e['text'] for e in frozen['candidate_excerpts']] == [lines[266], lines[299]]
    assert (a['root']/f"sources/{source['id']}.txt").read_text() == text
    assert 'source-context.json' in a['files']


def test_navigation_discloses_truncation_and_no_match_is_not_clearance():
    text = '\n'.join(('Pending approval\n' + 'detail\n'*30) for _ in range(20))
    hints = navigation(text)
    assert len(hints['candidate_ranges']) == 16 and hints['omitted_ranges'] == 4
    assert navigation('No technical details provided.')['candidate_ranges'] == []
    assert '无匹配不代表没有条件' in navigation('No technical details provided.')['scope']
    quotes = navigation(('Pending ' + 'detail '*200 + '\n')*20, include_excerpts=True)
    assert sum(len(e['text']) for e in quotes['candidate_excerpts']) <= 3000
    assert quotes['omitted_excerpt_lines'] == 15
    assert all(e['truncated'] for e in quotes['candidate_excerpts'])


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


def test_exact_text_revision_preserves_rich_nodes_and_requires_new_complete_check(tmp_path):
    from test_analyst_draft_versions import writer, check
    store, _, source, config = writer(tmp_path)
    body = draft(source['id'], '需安装对应 PR 分支，不能直接安装主分支。')
    body['editor_document']['content'][0]['content'][0]['marks'] = [{'type': 'bold'}]
    def call(name, args):
        result = run_tool(store, config, name, args)
        assert result['ok'], result
        return json.loads(result['content'][0]['text'])
    receipt = call('save_draft_section', {'section_id': 'adoption',
        'content': body['editor_document']['content'], 'citations': body['citations']})
    first = saved_revision(store, config, {'title': '报告', 'section_ids': ['adoption'], 'gaps': ['待验证']})
    check(store, config, first)
    edited = call('save_draft_section', {'section_id': 'adoption', 'expected_hash': receipt['hash'],
        'text_replacements': [{'old_text': '对应 PR 分支，不能直接安装主分支', 'new_text': 'PR 分支'}]})
    assert edited['body_units'] < receipt['body_units']
    assert not run_tool(store, config, 'submit_draft', {'revision': first})['ok']
    view = call('read_draft', {'field': 'body', 'section_id': 'adoption'})
    assert view['section_hash'] == edited['hash']
    nodes = view['items'][0]['content']
    assert nodes[0]['text'] == '需安装PR 分支。'
    assert nodes[0]['marks'] == [{'type': 'bold'}]
    assert nodes[1] == {'type': 'citation', 'attrs': {'sourceId': source['id']}}
    second = saved_revision(store, config, {'base_revision': first, 'section_ids': ['adoption']})
    assert check(store, config, second)['diagnostics']['citations']['missing_locator'] == []
    assert run_tool(store, config, 'submit_draft', {'revision': second})['ok']


def test_failed_or_ambiguous_text_batch_leaves_saved_section_unchanged(tmp_path):
    from test_analyst_draft_versions import writer
    from briefloop.analyst import _sections_file
    store, _, source, config = writer(tmp_path)
    original = run_tool(store, config, 'save_draft_section', {'section_id': 'one',
        'content': draft(source['id'], '保留条件；重复，重复。')['editor_document']['content']})
    h = json.loads(original['content'][0]['text'])['hash']
    path = _sections_file(store, config)
    before = path.read_bytes()
    for expected, edits in [
        (h, [{'old_text': '保留条件', 'new_text': '新条件'}, {'old_text': '不存在', 'new_text': 'x'}]),
        (h, [{'old_text': '重复', 'new_text': ''}]),
        ('0'*64, [{'old_text': '保留条件', 'new_text': '新条件'}]),
    ]:
        assert not run_tool(store, config, 'save_draft_section', {'section_id': 'one',
            'expected_hash': expected, 'text_replacements': edits})['ok']
        assert path.read_bytes() == before
