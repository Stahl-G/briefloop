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
    bad = {'base_revision': first['revision'],
           'number_bindings': [dict(binding, report_quote='正文没有这句话')],
           'citations': [{'source_id': source['id'], 'excerpt': '原文不存在的引用'}]}
    with pytest.raises(ValueError, match=r'citations\[0\]') as rejected:
        writer.assemble_evidence(store, config, bad)
    assert 'number_bindings[0]: report_quote' in str(rejected.value)
    assert drafts._read(drafts._root(store, config)/'current.json')['revision'] == first['revision']
    assert drafts._candidate(store, config, {'revision': first['revision']})['draft'] == before
    saved = writer.assemble_evidence(store, config, {'base_revision': first['revision'], 'number_bindings': [binding]})
    now = drafts._candidate(store, config, {'revision': saved['revision']})['draft']
    assert now['editor_document'] == before['editor_document']
    assert writer.assemble_evidence(store, config, {
        'base_revision': first['revision'], 'number_bindings': [binding]}) == {**saved, 'replayed': True}
    with pytest.raises(ValueError):
        drafts.submit(store, config, {'revision': saved['revision']})
    for value, pattern in [(dict(binding, source_id='src_outside'),'事实材料'),
                           (dict(binding, report_quote='没有这句话'),'report_quote'),
                           (dict(binding, number_text='21%'),'number_text')]:
        with pytest.raises(ValueError, match=pattern):
            writer.assemble_evidence(store, config, {'base_revision': saved['revision'], 'number_bindings': [value]})
    assert drafts._read(drafts._root(store, config)/'current.json')['revision'] == saved['revision']


def test_invalid_optional_evidence_keeps_first_body_and_repairs_by_revision(tmp_path):
    store, run, source, config = setup_writer(tmp_path)
    binding = number(source['id'])
    bad = dict(binding, report_quote='不存在的正文')
    args = {'title': '报告', 'markdown': f'收入增长20%。[@{source["id"]}]',
            'number_bindings': [bad]}
    saved = writer.write_report(store, config, args)
    assert saved['status'] == 'saved'
    assert saved['evidence_status'] == 'not_saved'
    assert 'number_bindings[0]' in saved['evidence_error']
    assert saved['next_operation'] == 'assemble_evidence'
    candidate = drafts._candidate(store, config, {'revision': saved['revision']})['draft']
    assert '收入增长20%' in candidate['markdown']
    assert not candidate['number_bindings']
    assert writer.write_report(store, config, args)['revision'] == saved['revision']
    with pytest.raises(ValueError, match='draft_exists'):
        writer.write_report(store, config, {**args, 'markdown': '不允许覆盖'})
    repaired = writer.assemble_evidence(store, config, {
        'base_revision': saved['revision'], 'number_bindings': [binding]})
    assert repaired['revision'] != saved['revision']
    assert drafts.check(store, config, {'revision': repaired['revision']})['diagnostics']['numbers']['checked'] == 1
    drafts.submit(store, config, {'revision': repaired['revision']})


def test_missing_binding_is_setup_error_not_identity_creation(tmp_path):
    store, run, source, config = setup_writer(tmp_path)
    target = drafts._root(store, config).parent / 'unprepared' / 'report.json'
    with pytest.raises(ValueError, match='任务启动器'):
        drafts.file_config(store, run['id'], target)
    assert not (target.parent / 'conversation.json').exists()


def test_many_invalid_records_return_bounded_diagnostics_without_saving(tmp_path):
    store, run, source, config = setup_writer(tmp_path)
    saved = writer.write_report(store, config, {'title': '报告', 'markdown': '收入增长20%。'})
    request = {'base_revision': saved['revision'], 'citations': [
        {'source_id': source['id'], 'excerpt': f'不存在的摘录{i}'} for i in range(12)]}
    with pytest.raises(ValueError) as rejected:
        writer.assemble_evidence(store, config, request)
    message = str(rejected.value)
    assert message.count('citations[') == 8
    assert 'citations[7]' in message and 'citations[8]' not in message
    assert '本批未保存' in message
    assert drafts._read(drafts._root(store, config)/'current.json')['revision'] == saved['revision']


def test_local_updates_resolve_same_excerpts_and_preserve_other_records(tmp_path):
    store, run, source, config = setup_writer(tmp_path)
    first = writer.write_report(store, config, {'title': '报告', 'markdown': f'收入增长20%。[@{source["id"]}]'})
    time_record = {'source_id': source['id'], 'source_excerpt': store.source_text(source['id']),
                   'statement': '2025年收入增长', 'event_date': '2025'}
    batch = writer.assemble_evidence(store, config, {'base_revision': first['revision'],
        'temporal_claims': [time_record]})
    prior = drafts._candidate(store, config, {'revision': batch['revision']})['draft']
    update = writer.operations()['update_temporal_claims'][1]
    args = {'base_revision': batch['revision'], 'records': [
        {**time_record, 'statement': '2025年收入增长20%', 'record_key': batch['record_keys']['temporal_claims'][0]}]}
    saved = update(store, config, args)
    assert update(store, config, args) == {**saved, 'replayed': True}
    value = drafts._candidate(store, config, {'revision': saved['revision']})['draft']
    assert value['temporal_claims'][0]['locator'] == prior['temporal_claims'][0]['locator'] == 'line 1'
    assert 'source_excerpt' not in value['temporal_claims'][0]
    assert value['citations'] == prior['citations']
    assert value['editor_document'] == prior['editor_document']
    with pytest.raises(ValueError, match='最新稿件版本'):
        update(store, config, {**args, 'records': [{**time_record, 'statement': '旧版不得更新'}]})
    before = drafts._read(drafts._root(store, config)/'current.json')
    with pytest.raises(ValueError, match=r'records\[1\]'):
        update(store, config, {'base_revision': saved['revision'], 'records': [
            {**time_record, 'statement': '本条有效也不得部分保存'},
            {**time_record, 'source_excerpt': '原文没有'},
            {**time_record, 'source_id': 'src_outside'}]})
    assert drafts._read(drafts._root(store, config)/'current.json') == before
    assert drafts._candidate(store, config, {'revision': saved['revision']})['draft'] == value


def test_local_number_update_rejects_ambiguous_body_without_saving(tmp_path):
    store, run, source, config = setup_writer(tmp_path)
    first = writer.write_report(store, config, {'title': '报告', 'markdown': '收入增长20%，再次提到收入增长20%。'})
    update = writer.operations()['update_number_bindings'][1]
    with pytest.raises(ValueError, match='report_quote'):
        update(store, config, {'base_revision': first['revision'], 'records': [number(source['id'])]})
    assert drafts._read(drafts._root(store, config)/'current.json')['revision'] == first['revision']


def test_failed_batch_repairs_only_bad_record_and_replays_atomically(tmp_path):
    store, run, source, config = setup_writer(tmp_path)
    sid = source['id']
    first = writer.write_report(store, config, {'title': '报告', 'markdown': f'收入增长20%。[@{sid}]'})
    valid = number(sid)
    request = {'base_revision': first['revision'], 'number_bindings': [valid],
               'citations': [{'source_id': sid, 'excerpt': '不存在的摘录'}]}
    with pytest.raises(ValueError) as error:
        writer.assemble_evidence(store, config, request)
    rejected = json.loads(str(error.value))
    assert rejected['code'] == 'evidence_batch_rejected'
    repair = {'base_revision': first['revision'], 'repair_batch_id': rejected['repair_batch_id'],
              'corrections': [{'field': 'citations', 'index': 0,
                              'record': {'source_id': sid, 'excerpt': valid['source_excerpt']}}]}
    saved = writer.assemble_evidence(store, config, repair)
    assert writer.assemble_evidence(store, config, repair) == {**saved, 'replayed': True}
    candidate = drafts._candidate(store, config, {'revision': saved['revision']})['draft']
    assert candidate['number_bindings'][0]['value'] == 20  # not resent by the author
    assert candidate['citations'][0]['excerpt'] == valid['source_excerpt']
    assert drafts.check(store, config, {'revision': saved['revision']})['diagnostics']['numbers']['checked'] == 1
    drafts.submit(store, config, {'revision': saved['revision']})


def test_evidence_repair_rejects_stale_scope_and_invalid_indexes(tmp_path):
    store, run, source, config = setup_writer(tmp_path)
    first = writer.write_report(store, config, {'title': '报告', 'markdown': '收入增长20%。'})
    with pytest.raises(ValueError) as error:
        writer.assemble_evidence(store, config, {'base_revision': first['revision'],
            'citations': [{'source_id': source['id'], 'excerpt': '不存在'}]})
    receipt = json.loads(str(error.value))
    patch = {'field': 'citations', 'index': 0, 'record': {'source_id': source['id'],
                                                      'excerpt': number(source['id'])['source_excerpt']}}
    args = {'base_revision': first['revision'], 'repair_batch_id': receipt['repair_batch_id'], 'corrections': [patch]}
    for edits in ({'corrections': [dict(patch, index=1)]}, {'corrections': [patch, patch]},
                  {'repair_batch_id': '0'*64}, {'corrections': []}, {'citations': []}):
        with pytest.raises(ValueError):
            writer.assemble_evidence(store, config, {**args, **edits})
    before = drafts._read(drafts._root(store, config)/'current.json')
    assert before['revision'] == first['revision']
    with pytest.raises(ValueError):
        writer.assemble_evidence(store, {**config, 'attempt_id': 'another'}, args)
    updated = writer.patch_report_text(store, config, {'base_revision': first['revision'],
                    'replacements': [{'old_text': '收入', 'new_text': '当年收入'}]})
    with pytest.raises(ValueError, match='最新稿件版本'):
        writer.assemble_evidence(store, config, args)
    with pytest.raises(ValueError, match='stale_evidence_batch'):
        writer.assemble_evidence(store, config, {**args, 'base_revision': updated['revision']})


def test_evidence_repair_rechecks_all_sources_and_keeps_failed_input(tmp_path):
    store, run, source, config = setup_writer(tmp_path)
    first = writer.write_report(store, config, {'title': '报告', 'markdown': '收入增长20%。'})
    with pytest.raises(ValueError) as error:
        writer.assemble_evidence(store, config, {'base_revision': first['revision'],
            'citations': [{'source_id': source['id'], 'excerpt': '不存在'}]})
    receipt = json.loads(str(error.value))
    pending = drafts._root(store, config)/'rejected-evidence'/(receipt['repair_batch_id']+'.json')
    original = pending.read_bytes()
    (store.root/source['path']).write_text('来源更新了')
    with pytest.raises(ValueError):
        writer.assemble_evidence(store, config, {'base_revision': first['revision'],
            'repair_batch_id': receipt['repair_batch_id'], 'corrections': [{'field': 'citations', 'index': 0,
                'record': {'source_id': source['id'], 'excerpt': number(source['id'])['source_excerpt']}}]})
    assert pending.read_bytes() == original
    assert drafts._read(drafts._root(store, config)/'current.json')['revision'] == first['revision']


def test_bare_number_location_expands_only_equal_body_quantity(tmp_path):
    store, run, source, config = setup_writer(tmp_path)
    binding = dict(number(source['id']), number_text='20')
    saved = writer.write_report(store, config, {'title': '报告', 'markdown': '收入增长20%。',
                                               'number_bindings': [binding]})
    record = drafts._candidate(store, config, {'revision': saved['revision']})['draft']['number_bindings'][0]
    assert record['number_text'] == '20%'
    assert record['value'] == binding['value'] and record['unit'] == binding['unit']
    assert drafts.check(store, config, {'revision': saved['revision']})['diagnostics']['numbers']['checked'] == 1
    from briefloop.delivery_checks import complete_number_token
    assert complete_number_token('收入120%', '20', 20, '%') is None
    assert complete_number_token('收入120%', '20', 120, '%') is None
    assert complete_number_token('新增20家公司', '20', 20, '人') is None
    assert complete_number_token('新增20家公司', '20', 20, '家') == '20家'
    assert complete_number_token('价格20美元/件', '20', 20, '美元/件') is None
    assert complete_number_token('20%与20%', '20', 20, '%') is None
    assert complete_number_token('收入20元', '20', 20, '%') is None


def test_null_evidence_field_has_no_unusable_repair_batch(tmp_path):
    store, run, source, config = setup_writer(tmp_path)
    saved = writer.write_report(store, config, {'title': '报告', 'markdown': '收入增长20%。'})
    args = {'base_revision': saved['revision'], 'citations': None}
    with pytest.raises(ValueError) as error:
        writer.assemble_evidence(store, config, args)
    diagnostic = json.loads(str(error.value))
    assert diagnostic['code'] == 'null_evidence_field' and 'repair_batch_id' not in diagnostic
    assert not (drafts._root(store, config)/'rejected-evidence').exists()
    writer.assemble_evidence(store, config, {**args, 'citations': []})
