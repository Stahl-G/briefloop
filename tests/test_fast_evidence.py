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


def test_enrichment_requires_a_body_anchor_and_keeps_evidence_only_version(tmp_path):
    store, source, run, job, runtime, worker = setup(tmp_path)
    result = worker.generate(job)
    store.update_job(job['id'], 'complete', result=result)
    brief = store.one('briefs', result['version_id'])
    check = store.one('jobs', result['checks_job_id'])
    store.update_job(check['id'], 'running')
    quote = '收入 120 万元，同比增长 20%。'
    raw = '收入 120 万元，同比增长 20%'
    def evidence(job, prompt, folder, *args, **kwargs):
        assert 'report_quote（正文连续原句）' in prompt
        assert 'USD million' in prompt
        value = {'source_id': 'S1', 'excerpt': raw}
        (folder / 'response.txt').write_text(dump({'citations': [
            {**value, 'report_quote': quote, 'source_context': '伪造上下文', 'source_title': '伪造标题'},
            value, {**value, 'report_quote': '不存在的正文句子'}], 'number_bindings': []}))
        return {'status': 'complete'}
    runtime.execute = evidence
    updated = enrich(worker, check, brief, worker.folder(check))
    details = json.loads(updated['detail'])
    item, = details['citations']
    assert item['report_quote'] == quote and item['excerpt'] == raw
    assert item['source_title'] == source['name'] and item['locator'] == 'line 2'
    assert '以上为本季度实际数。' in item['source_context']
    assert '伪造' not in dump(item)
    assert len(details['research_notes'][-1]['rejected']) == 2
    assert details['research_notes'][-1]['summary'] == '逐字定位已检查；支持关系仍需评价。'
    assert updated['parent_id'] == brief['id'] and updated['markdown'] == brief['markdown']
    assert updated['editor_document'] == brief['editor_document']


def test_material_packet_keeps_full_large_source(tmp_path):
    store = Store(tmp_path)
    text = '公开材料。\n' * 30_000 + '结尾限定：须先获批准。'
    source = store.add_source('Long disclosure', text)
    rows = material_packet(store, [source['id']])
    assert len(rows[0]['text']) > 100_000
    assert rows[0]['text'] == store.source_text(source['id'])
    assert rows[0]['text'].endswith('结尾限定：须先获批准。')


def test_web_source_over_old_limit_reaches_writing_unclipped(tmp_path, monkeypatch):
    from briefloop import sources
    from test_fast_research import setup as web_setup
    store, run, job, runtime, worker, searches, reads = web_setup(tmp_path, monkeypatch)
    runtime.selection = ['C1']
    body = '公开材料。' * 25_000 + '收入 120 万元，同比增长 20%。结尾限定：须先获批准。'
    monkeypatch.setattr(sources, '_fetch_bytes', lambda *a, **k: (
        ('<html><title>Long disclosure</title><p>' + body + '</p></html>').encode(),
        'text/html', 'utf-8'))
    execute = runtime.execute
    def observe(job, prompt, folder, *args, **kwargs):
        if folder.name == 'fast-writing':
            assert body in prompt
        return execute(job, prompt, folder, *args, **kwargs)
    runtime.execute = observe
    result = worker.generate(job)
    assert result['version_id']
    receipt = json.loads((worker.folder(job) / 'fast-web/research.json').read_text())
    assert len(receipt['source_ids']) == 1
    assert not any('10万' in gap or '超出' in gap for gap in receipt['gaps'])
    frozen = json.loads((worker.folder(job) / 'fast-materials.json').read_text())
    assert frozen[0]['text'] == store.source_text(receipt['source_ids'][0])
    assert body in frozen[0]['text']


def test_pdf_page_separator_keeps_the_located_excerpt_in_its_window():
    text = ('Page header\f' * 12) + '\n关键值42万元\n尾部'
    context = located_context(text, '关键值42万元')
    assert context['locator'] == 'line 2'
    assert '关键值42万元' in context['source_context']


def test_fast_web_with_existing_material_keeps_both_input_and_fetched_sources(tmp_path, monkeypatch):
    from test_fast_research import setup as web_setup
    store, run, job, runtime, worker, searches, reads = web_setup(tmp_path, monkeypatch)
    local = store.add_source('Uploaded synthetic material', 'LOCAL_PRIVATE_BODY')
    run = store.create_run(json.loads(run['requirements']), [local['id']], research_protocol='quality_v1')
    job = store.enqueue('generate', {'run_id': run['id'], 'agent_backend': 'codex'})
    store.update_job(job['id'], 'running')
    result = worker.generate(job)
    assert result['version_id']
    frozen = json.loads((worker.folder(job) / 'fast-materials.json').read_text())
    assert frozen[0]['source_id'] == local['id'] and len(frozen) == 2


def test_adjacent_table_rows_also_keep_their_header():
    text = '# Sales\nUnits: USD million\n| Period | Revenue |\n| --- | --- |\n| Q1 | 1 |\n| Q2 | 2 |\nRevenue grew.\n'
    context = located_context(text, 'Revenue grew.')
    assert '| Period | Revenue |' in context['source_context']
    assert 'Units: USD million' in context['source_context']
