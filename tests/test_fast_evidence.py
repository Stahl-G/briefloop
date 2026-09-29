"""Exact claim anchors and compact source context without a material-size gate."""
import json

import pytest

from briefloop.evidence_context import located_context
from briefloop.fast_reports import enrich, material_packet
from briefloop.models import BriefDraft, Citation
from briefloop.store import Store, dump
from briefloop.writer_assembly import CitationInput
from test_fast_reports import setup


def test_old_citation_shape_and_writer_input_remain_compatible():
    old = {'source_id': 'src_1', 'locator': 'line 1', 'excerpt': '原文'}
    assert Citation.model_validate(old).model_dump() == old
    assert CitationInput.model_validate(old).model_dump() == old
    draft = BriefDraft(title='标题', markdown='正文', citations=[old])
    assert draft.model_dump()['citations'] == [old]
    linked = {**old, 'report_quote': '正文', 'source_title': '来源标题',
              'source_context': '原文', 'context_locator': 'line 1'}
    assert Citation.model_validate(linked).model_dump() == linked
    assert CitationInput.model_validate(linked).model_dump() == linked


def test_context_keeps_distant_table_header_and_qualifier_without_full_source():
    lines = ['# 示例披露', '## 经营业绩', '单位：USD million；2026年第一季度',
             '以下为计划值，并非已经实现。', '| 部门 | 收入 |', '| --- | ---: |']
    lines += [f'| 部门{i} | {i} |' for i in range(12)]
    lines += ['仅在许可获批后执行。', '下一章', '不相关尾部']
    text = '\n'.join(lines)
    excerpt = '| 部门10 | 10 |'
    result = located_context(text, excerpt)
    assert result['locator'] == 'line 17'
    assert result['context_locator'] == 'line 1-6; line 15-19'
    for required in ('# 示例披露', '## 经营业绩', '单位：USD million',
                     '以下为计划值', '| 部门 | 收入 |', excerpt, '仅在许可获批后执行。'):
        assert required in result['source_context']
    assert '| 部门0 | 0 |' not in result['source_context']
    assert '不相关尾部' not in result['source_context']
    assert text == '\n'.join(lines)
    with pytest.raises(ValueError, match='命中 2 处'):
        located_context('重复摘录\n中间\n重复摘录', '重复摘录')
    assert located_context('重复摘录\n中间\n重复摘录', '重复摘录', 'line 3')['locator'] == 'line 3'


def test_material_packet_keeps_full_large_source(tmp_path):
    store = Store(tmp_path)
    text = '公开材料。\n' * 30_000 + '结尾限定：须先获批准。'
    source = store.add_source('Long disclosure', text)
    rows = material_packet(store, [source['id']])
    assert len(rows[0]['text']) > 100_000
    assert rows[0]['text'] == store.source_text(source['id'])
    assert rows[0]['text'].endswith('结尾限定：须先获批准。')


def test_pdf_page_separator_keeps_the_located_excerpt_in_its_window():
    text = ('Page header\f' * 12) + '\n关键值42万元\n尾部'
    context = located_context(text, '关键值42万元')
    assert context['locator'] == 'line 2'
    assert '关键值42万元' in context['source_context']


def test_adjacent_table_rows_also_keep_their_header():
    text = '# Sales\nUnits: USD million\n| Period | Revenue |\n| --- | --- |\n| Q1 | 1 |\n| Q2 | 2 |\nRevenue grew.\n'
    context = located_context(text, 'Revenue grew.')
    assert '| Period | Revenue |' in context['source_context']
    assert 'Units: USD million' in context['source_context']
