"""Explicit-unit occurrence hints locate work without claiming factual truth."""
import json
from pathlib import Path

from briefloop import analyst, analyst_drafts as drafts, writer_input as writer
from briefloop.delivery_checks import brief_checks, check_numbers, numeric_occurrence_review
from briefloop.document_model import markdown_document
from briefloop.draft_checks import inspect_draft
from briefloop.store import Store


def _binding(source):
    return {'label': '收入', 'entity': '示例企业', 'period': '2026年第二季度',
            'value': 1200, 'unit': '万元', 'source_id': source['id'],
            'locator': 'line 1', 'source_excerpt': source['text'],
            'report_quote': '本期收入1200万元', 'number_text': '1200万元'}


def test_pilot_like_repeated_occurrences_are_candidates_not_errors(tmp_path):
    store = Store(tmp_path / 'ws')
    source = store.add_source('合成来源', '本期收入1200万元，增长20%，毛利率25%，提高2个百分点。')
    source['text'] = store.source_text(source['id'])
    markdown = ('# 2026年示例报告：增长30%\n\n'
                '本期收入1200万元，增长20%，毛利率25%，提高2个百分点。\n\n'
                '另处收入1200万元，毛利率25%。\n\n'
                '| 指标 | 本期 |\n|---|---|\n| 收入 | 1200万元 |\n| 毛利率 | 25% |')
    binding = _binding(source)
    draft = {'title': '合成报告', 'markdown': markdown, 'number_bindings': [binding]}
    result = inspect_draft(draft, store=store, allowed_sources={source['id']})
    coverage = result['numbers']['occurrence_review']
    assert (coverage['candidate_count'], coverage['checked_occurrences'],
            coverage['review_candidate_count']) == (8, 1, 7)
    assert result['warnings'] == [] and result['status'] == 'checks_completed'
    note = next(n for n in result['notes'] if n['code'] == 'numeric_occurrences_to_review')
    assert note['kind'] == 'advisory' and note['count'] == 7
    assert [(s.get('kind'), s.get('paragraph'), s.get('row'), s.get('column'))
            for s in coverage['samples']] == [
                ('paragraph', 1, None, None), ('paragraph', 1, None, None),
                ('paragraph', 1, None, None), ('paragraph', 2, None, None),
                ('paragraph', 2, None, None), ('table_cell', None, 2, 2),
                ('table_cell', None, 3, 2)]
    assert all(s['text'] != '2026年' for s in coverage['samples'])

    run = store.create_run({'title': '合成报告', 'objective': '说明合成数据'}, [source['id']])
    saved = store.publish(run['id'], draft)
    live = brief_checks(store, saved['id'])['numbers']['occurrence_review']
    assert (live['candidate_count'], live['checked_occurrences'], live['review_candidate_count']) == (8, 1, 7)

    # A writer may still save this complete draft. The advisory does not
    # change the check receipt version or transform the original body.
    packet = analyst.packet(store, run['id'], store.root / 'writer', writer_protocol=writer.PROTOCOL,
                            plan={}, research={'sources': [], 'gaps': []})
    config = {'native_role': 'analyst', 'run_id': run['id'], 'packet_root': str(packet['root']),
              'result_file': str(packet['root'].parent / 'draft.json'), 'attempt_id': 'numeric-review'}
    first = writer.write_report(store, config, draft)
    checked = drafts.check(store, config, {'revision': first['revision']})
    assert checked['diagnostics']['numbers']['occurrence_review']['review_candidate_count'] == 7
    assert drafts.submit(store, config, {'revision': first['revision']})['status'] == 'saved'
    saved_body = json.loads(Path(config['result_file']).read_text())['markdown']
    assert '本期收入1200万元，增长20%，毛利率25%，提高2个百分点。' in saved_body
    assert '| 收入 | 1200万元 |' in saved_body


def test_full_direct_bindings_leave_no_explicit_unit_candidate(tmp_path):
    store = Store(tmp_path)
    source = store.add_source('合成来源', '本期收入1200万元，增长20%。')
    excerpt = store.source_text(source['id'])
    first = _binding({**source, 'text': excerpt})
    second = {**first, 'label': '增长', 'value': 20, 'unit': '%',
              'report_quote': '增长20%', 'number_text': '20%'}
    result = inspect_draft({'title': '合成', 'markdown': '本期收入1200万元，增长20%。',
                            'number_bindings': [first, second]}, store=store,
                           allowed_sources={source['id']})
    coverage = result['numbers']['occurrence_review']
    assert (coverage['candidate_count'], coverage['checked_occurrences'], coverage['review_candidate_count']) == (2, 2, 0)
    assert not any(n['code'] == 'numeric_occurrences_to_review' for n in result['notes'])


def test_checked_binding_locates_bold_and_linked_visible_numbers(tmp_path):
    store = Store(tmp_path)
    source = store.add_source('合成来源', '本期收入1200万元。')
    run = store.create_run({'title': '合成', 'objective': '说明合成数据'}, [source['id']])
    binding = _binding({**source, 'text': store.source_text(source['id'])})
    for quote in ('本期**收入1200万元**', '[本期收入1200万元](https://example.test/disclosure)'):
        body = quote + '。'
        row = {**binding, 'report_quote': quote}
        direct = check_numbers(body, [row], store, {source['id']})
        assert direct[0]['checked'] and direct[0]['found'], (quote, direct)
        result = inspect_draft({'title': '合成', 'markdown': body, 'number_bindings': [row]},
                               store=store, allowed_sources={source['id']})
        coverage = result['numbers']['occurrence_review']
        assert (coverage['candidate_count'], coverage['checked_occurrences'],
                coverage['review_candidate_count']) == (1, 1, 0), (quote, coverage)
        assert not any(n['code'] == 'numeric_occurrences_to_review' for n in result['notes'])
        saved = store.publish(run['id'], {'title': '合成', 'markdown': body, 'number_bindings': [row]})
        live = brief_checks(store, saved['id'])['numbers']['occurrence_review']
        assert (live['candidate_count'], live['checked_occurrences'], live['review_candidate_count']) == (1, 1, 0)


def test_ambiguous_visible_projection_remains_a_review_candidate(tmp_path):
    store = Store(tmp_path)
    source = store.add_source('合成来源', '本期收入1200万元。')
    quote = '**本期收入1200万元**'
    body = quote + '，本期收入1200万元。'
    row = {**_binding({**source, 'text': store.source_text(source['id'])}), 'report_quote': quote}
    assert check_numbers(body, [row], store, {source['id']})[0]['found']
    coverage = inspect_draft({'title': '合成', 'markdown': body, 'number_bindings': [row]},
                             store=store, allowed_sources={source['id']})['numbers']['occurrence_review']
    assert coverage['candidate_count'] == coverage['review_candidate_count'] == 2
    assert coverage['checked_occurrences'] == 0


def test_binding_in_skipped_heading_header_or_code_cannot_cover_body(tmp_path):
    store = Store(tmp_path)
    source = store.add_source('合成来源', '收入1200万元。')
    run = store.create_run({'title': '合成', 'objective': '说明合成数据'}, [source['id']])
    quote = '**收入1200万元**'
    row = {**_binding({**source, 'text': store.source_text(source['id'])}), 'report_quote': quote}
    bodies = [
        '# **收入1200万元**\n\n收入1200万元。',
        '| **收入1200万元** | 数值 |\n|---|---|\n| 指标 | 收入1200万元 |',
        '```text\n**收入1200万元**\n```\n\n收入1200万元。',
        '`**收入1200万元**`，收入1200万元。',
    ]
    for body in bodies:
        # The original binding is genuinely accepted by the existing checker.
        # Its scope is the skipped text, so it must not clear another occurrence.
        direct = check_numbers(body, [row], store, {source['id']})
        assert direct[0]['checked'] and direct[0]['found'], (body, direct)
        result = inspect_draft({'title': '合成', 'markdown': body, 'number_bindings': [row]},
                               store=store, allowed_sources={source['id']})
        coverage = result['numbers']['occurrence_review']
        assert (coverage['candidate_count'], coverage['checked_occurrences'],
                coverage['review_candidate_count']) == (1, 0, 1), (body, coverage)
        saved = store.publish(run['id'], {'title': '合成', 'markdown': body, 'number_bindings': [row]})
        live = brief_checks(store, saved['id'])['numbers']['occurrence_review']
        assert (live['candidate_count'], live['checked_occurrences'], live['review_candidate_count']) == (1, 0, 1)


def test_headers_code_urls_ordinals_and_bare_numbers_are_excluded():
    markdown = ('# 2026年收入增长20%\n\n'
                '第1次、第 2 项；第3条，2026年，序号7。参见 https://example.test/45% 。\n\n'
                '这里有 `98%`，另有30%。\n\n'
                '```\n75%\n```\n\n'
                '| 项目 | 本期（万元） |\n|---|---|\n| 收入 | 1200 |\n| 增长 | 25% |')
    review = numeric_occurrence_review(markdown_document(markdown), [], [])
    assert review['candidate_count'] == 2
    assert [s['text'] for s in review['samples']] == ['30%', '25%']
    assert review['samples'][1]['row'] == 3 and review['samples'][1]['column'] == 2


def test_occurrence_samples_are_bounded_and_repeat_display_is_not_deduplicated():
    markdown = '。'.join(['增长20%'] * 20)
    review = numeric_occurrence_review(markdown_document(markdown), [], [])
    assert review['candidate_count'] == review['review_candidate_count'] == 20
    assert len(review['samples']) == review['sample_limit'] == 12
    assert review['truncated'] is True
