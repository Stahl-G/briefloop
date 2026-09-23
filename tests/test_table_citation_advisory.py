"""Cell-local citation reminders stay read-only and never gate submission."""
from copy import deepcopy
from io import BytesIO
import json
from pathlib import Path

from docx import Document

from briefloop import analyst, analyst_drafts as drafts, writer_input as writer
from briefloop.document_model import document_markdown
from briefloop.draft_checks import inspect_draft
from briefloop.exports import docx_bytes
from briefloop.store import Store


CODE = 'table_numeric_cell_citation_missing'


def advisory(result):
    return next((n for n in result['notes'] if n['code'] == CODE), None)


def test_paragraph_citation_does_not_cover_cells_but_reminder_allows_submit(tmp_path):
    store = Store(tmp_path / 'ws')
    source = store.add_source('合成来源', '2026年第二季度收入1200万元，同比增长20%，毛利率25%，提高2个百分点。')
    run = store.create_run({'title': '合成报告', 'objective': '依据合成来源说明数据', 'allow_web': False}, [source['id']])
    pack = analyst.packet(store, run['id'], store.root / 'writer', writer_protocol=writer.PROTOCOL,
                          plan={}, research={'sources': [], 'gaps': []})
    config = {'native_role': 'analyst', 'run_id': run['id'], 'packet_root': str(pack['root']),
              'result_file': str(pack['root'].parent / 'draft.json'), 'attempt_id': 'table-test'}
    body = (f'正文收入1200万元，同比增长20%。[@{source["id"]}]\n\n'
            '| 指标 | 本期 | 同比 |\n|---|---|---|\n'
            '| 收入 | 1200万元 | 20% |\n| 毛利率 | 25% | 2个百分点 |')
    first = writer.write_report(store, config, {'title': '合成报告', 'markdown': body,
        'citations': [{'source_id': source['id'], 'locator': 'line 1', 'excerpt': store.source_text(source['id'])}],
        'number_bindings': [{'label': '收入', 'entity': '示例企业', 'period': '2026年第二季度',
            'value': 1200, 'unit': '万元', 'source_id': source['id'], 'locator': 'line 1',
            'source_excerpt': store.source_text(source['id']), 'report_quote': '正文收入1200万元',
            'number_text': '1200万元'}]})
    before = deepcopy(drafts._candidate(store, config, {'revision': first['revision']})['draft'])
    checked = drafts.check(store, config, {'revision': first['revision']})['diagnostics']
    note = advisory(checked)
    assert checked['warnings'] == [] and checked['status'] == 'checks_completed'
    assert note['kind'] == 'advisory' and note['count'] == 4 and not note['truncated']
    assert [(s['row'], s['column']) for s in note['samples']] == [(2, 2), (2, 3), (3, 2), (3, 3)]
    assert '单位只在表头时可能漏检' in note['scope']
    assert drafts.submit(store, config, {'revision': first['revision']})['status'] == 'saved'
    assert json.loads(Path(config['result_file']).read_text()) == before

    # Explicitly add cell citations to a new revision; the checker adds none.
    repaired = deepcopy(before['editor_document'])
    table = next(n for n in repaired['content'] if n['type'] == 'table')
    for row in table['content'][1:]:
        for cell in row['content'][1:]:
            cell['content'][0]['content'].append({'type': 'citation', 'attrs': {'sourceId': source['id']}})
    latest = drafts.save(store, config, {'base_revision': first['revision'], 'editor_document': repaired})
    assert advisory(drafts.check(store, config, {'revision': latest['revision']})['diagnostics']) is None
    drafts.submit(store, config, {'revision': latest['revision']})
    saved = json.loads(Path(config['result_file']).read_text())
    markdown = document_markdown(saved['editor_document'])
    assert f'1200万元[@{source["id"]}]' in markdown and f'2个百分点[@{source["id"]}]' in markdown
    roundtrip = writer.compile_markdown(markdown)
    assert advisory(inspect_draft({**saved, 'editor_document': roundtrip})) is None
    word = Document(BytesIO(docx_bytes(document=saved['editor_document'], source_records={source['id']: source})))
    assert len(word.element.xpath('//w:tc//w:hyperlink')) == 4


def test_headers_years_ordinals_code_and_source_links_are_skipped_merged_cell_counted_once():
    def paragraph(text, marks=None):
        return {'type': 'paragraph', 'content': [{'type': 'text', 'text': text, **({'marks': marks} if marks else {})}]}
    def cell(text='', **attrs):
        return {'type': 'tableCell', 'content': [paragraph(text)], **({'attrs': attrs} if attrs else {})}
    header = {'type': 'tableHeader', 'attrs': {'colspan': 2}, 'content': [paragraph('2026年 / 20% / 金额（万元）')]}
    code = {'type': 'tableCell', 'content': [{'type': 'codeBlock', 'content': [{'type': 'text', 'text': '1200万元'}]}]}
    inline_code = {'type': 'tableCell', 'content': [paragraph('30%', [{'type': 'code'}])]}
    linked = {'type': 'tableCell', 'content': [paragraph('40%', [{'type': 'link', 'attrs': {'href': '#source-src_fixture'}}])]}
    cited = cell('20%'); cited['content'][0]['content'].append({'type': 'citation', 'attrs': {'sourceId': 'src_fixture'}})
    rows = [[header], [cell('25%', rowspan=2, blockId='merged-value'), cell('2026年')], [cell('1')],
            [code, inline_code], [cell('   '), linked], [cell('1200'), cited]]
    value = {'title': '边界', 'editor_document': {'type': 'doc', 'content': [
        {'type': 'table', 'content': [{'type': 'tableRow', 'content': row} for row in rows]}]}}
    before = deepcopy(value)
    note = advisory(inspect_draft(value))
    assert note['count'] == 1
    assert note['samples'] == [{'table': 1, 'row': 2, 'column': 1, 'rowspan': 2, 'colspan': 1,
                                'text': '25%', 'block_id': 'merged-value'}]
    assert value == before


def test_legacy_markdown_is_read_only_and_advisory_samples_are_bounded():
    rows = [f'| 项目{i} | {i + 1}万元' + '说明' * 90 + ' |' for i in range(20)]
    text = '| 项目 | 金额 |\n|---|---|\n' + '\n'.join(rows)
    value = {'title': '旧 Markdown', 'markdown': text}
    before = deepcopy(value)
    note = advisory(inspect_draft(value))
    assert note['count'] == 20 and note['truncated'] is True
    assert len(note['samples']) == note['sample_limit'] == 12
    assert note['samples'][0]['row'] == 2 and note['samples'][-1]['row'] == 13
    assert all(len(s['text']) <= 120 for s in note['samples'])
    assert value == before
    bare = '| 序号 | 2026年金额（万元） |\n|---|---|\n| 1 | 1200 |'
    assert advisory(inspect_draft({'title': '单位在表头', 'markdown': bare})) is None
