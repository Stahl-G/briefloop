"""Shared assembly moves exact text/structure work out of model generation."""
import json

import pytest

from briefloop import analyst_drafts as drafts, writer_input as writer
from briefloop.writer_assembly import locate_excerpt
from test_analyst_draft_versions import writer as setup_writer


def number(sid):
    return {'label': '收入同比', 'value': 20, 'unit': '%', 'entity': '示例公司', 'period': '2025年',
            'source_id': sid, 'source_excerpt': '2025年收入1200万元，同比增长20%。',
            'report_quote': '收入增长20%', 'number_text': '20%'}


def test_unique_multiline_and_ambiguous_excerpt():
    assert locate_excerpt('标题\n前句\n后句\n结束', '前句\n后句', 'line 90') == 'line 2-3'
    with pytest.raises(ValueError, match='命中 2 处'):
        locate_excerpt('收入20%\n收入20%', '收入20%')
    assert locate_excerpt('收入20%\n收入20%', '收入20%', 'line 2') == 'line 2'
    with pytest.raises(ValueError, match='找不到'):
        locate_excerpt('收入20%', '收入 20%')
    with pytest.raises(ValueError, match='命中 2 处'):
        locate_excerpt('20%和20%', '20%', 'line 1')


def test_write_and_assemble_same_draft_with_real_number_checks(tmp_path):
    store, run, source, config = setup_writer(tmp_path)
    sid = source['id']; body = f'收入增长20%。[@{sid}]'
    request = {'title': '报告', 'markdown': body, 'number_bindings': [number(sid)],
               'temporal_claims': [{'statement': '2025年收入增长', 'event_date': '2025',
                  'source_id': sid, 'source_excerpt': '2025年收入1200万元，同比增长20%。'}]}
    first = writer.write_report(store, config, request)
    assert writer.write_report(store, config, request)['revision'] == first['revision']
    data = drafts._candidate(store, config, {'revision': first['revision']})['draft']
    assert data['number_bindings'][0]['locator'] == 'line 1'
    assert len(data['citations']) == 1  # number/time use the same excerpt
    assert data['temporal_claims'][0]['locator'] == 'line 1'
    assert 'source_excerpt' not in data['temporal_claims'][0]
    result = drafts.check(store, config, {'revision': first['revision']})
    assert result['diagnostics']['numbers']['checked'] == 1
    drafts.submit(store, config, {'revision': first['revision']})


def test_batch_failure_is_atomic_and_preserves_body(tmp_path):
    store, run, source, config = setup_writer(tmp_path)
    first = writer.write_report(store, config, {'title': '报告', 'markdown': f'**收入**增长20%。[@{source["id"]}]'})
    before = drafts._candidate(store, config, {'revision': first['revision']})['draft']
    binding = number(source['id']);binding['report_quote'] = '增长20%'
    bad = {'base_revision': first['revision'], 'number_bindings': [binding],
           'citations': [{'source_id': source['id'], 'excerpt': '原文不存在的引用'}]}
    with pytest.raises(ValueError, match=r'citations\[0\]'):
        writer.assemble_evidence(store, config, bad)
    assert drafts._read(drafts._root(store, config)/'current.json')['revision'] == first['revision']
    saved = writer.assemble_evidence(store, config, {'base_revision': first['revision'], 'number_bindings': [binding]})
    now = drafts._candidate(store, config, {'revision': saved['revision']})['draft']
    assert now['editor_document'] == before['editor_document']
    with pytest.raises(ValueError):
        writer.assemble_evidence(store, config, {'base_revision': first['revision'], 'number_bindings': [binding]})
    with pytest.raises(ValueError):
        drafts.submit(store, config, {'revision': saved['revision']})
    for value, pattern in [(dict(binding, source_id='src_outside'),'事实材料'),
                           (dict(binding, report_quote='没有这句话'),'report_quote'),
                           (dict(binding, number_text='21%'),'number_text')]:
        with pytest.raises(ValueError, match=pattern):
            writer.assemble_evidence(store, config, {'base_revision': saved['revision'], 'number_bindings': [value]})
    assert drafts._read(drafts._root(store, config)/'current.json')['revision'] == saved['revision']
