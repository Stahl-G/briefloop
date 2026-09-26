"""Regressions for false assurance in version-scoped delivery checks."""
import pytest

from briefloop.delivery_checks import brief_checks, check_export, check_numbers, normalized
from briefloop.store import Store


def bound(store, quote, token, value=13.6, unit='billion USD', excerpt='Orders: $13.6 billion.'):
    source = store.add_source('disclosure', excerpt)
    return dict(label='订单', value=value, unit=unit, entity='Example', period='Q2',
                source_id=source['id'], locator='line 1', source_excerpt=excerpt,
                report_quote=quote, number_text=token)


def test_exact_tokens_currency_and_dimension(tmp_path):
    store = Store(tmp_path)
    cases = [
        ('订单136亿美元。', '136亿美元', 13.6, 'billion USD', '$13.6 billion', True),
        ('订单1360亿美元。', '1360亿美元', 13.6, 'billion USD', '$13.6 billion', False),
        ('利润110美元。', '110美元', 10, 'USD', '$10', False),
        ('变化-5%。', '-5%', 5, '%', '5%', False),
        ('变化−5%。', '−5%', -5, '%', '-5%', True),
        ('收入100万元人民币。', '100万元人民币', 1, 'million USD', '$1 million', False),
        ('订单136 亿美元。', '136 亿美元', 13.6, 'USD billion', '$13.6 billion', True),
        ('容量45,100 MW。', '45,100 MW', 45.1, 'GW', '45.1 GW', True),
        ('收入13.6亿元。', '13.6亿元', 13.6, '亿元', '13.6亿元', True),
        ('亏损0美元。', '0美元', 0, 'USD', '$0', True),
    ]
    for quote, token, value, unit, excerpt, found in cases:
        row = check_numbers(quote, [bound(store, quote, token, value, unit, excerpt)], store)[0]
        assert row['checked'], row
        assert row['found'] is found, (quote, row)
    for unit in ('USD/MWh', 'B USD', 'kilo EUR', '兆'):
        assert normalized(5, unit) is None
    for quote, token in [('收入110美元', '10美元'), ('价格5美元/MWh', '5美元/MWh'), ('电量5MWh/年', '5MWh/年')]:
        item = bound(store, quote, token, 5, 'USD', '$5')
        assert not check_numbers(quote, [item], store)[0]['checked']
    assert normalized(10, '万美元') == normalized(100000, 'USD')
    assert normalized(1, 'thousand USD') == normalized(1000, 'USD')


@pytest.mark.parametrize('token,value,unit,source', [
    ('10亿欧元', 1, 'billion EUR', '1 billion EUR'),
    ('120万英镑', '1.2', 'million GBP', '£1.2 million'),
    ('1,250,000欧元', '1.25e6', 'EUR', 'EUR 1.25e6'),
    ('100 million shares', 1, '亿股', '1亿股'),
    ('20,000 tonnes', 2, '万吨', '2万吨'),
    ('1,500 kg', '1.5', 'tonnes', '1.5 metric tons'),
    ('−1.5e3千克', '-1.5', 'tonnes', '-1.5 tonnes'),
    ('1000 MWh', 1, 'GWh', '1 GWh'),
    ('1000千瓦时', 1, '兆瓦时', '1兆瓦时'),
    ('10,000 vehicles', 1, '万辆', '1万辆'),
])
def test_registered_units_keep_source_and_exact_body_binding(tmp_path, token, value, unit, source):
    store = Store(tmp_path)
    quote = f'本期数值：{token}。'
    item = bound(store, quote, token, value, unit, source)
    row = check_numbers(quote, [item], store, {item['source_id']})[0]
    assert row['checked'] and row['found'], row
    # Adding units never bypasses identity, location or unique body binding.
    for changes in ({'locator': 'line 99'}, {'source_excerpt': source + ' not in source'},
                    {'source_id': 'src_missing'}, {'report_quote': quote + ' altered'}):
        rejected = check_numbers(quote, [dict(item, **changes)], store)[0]
        assert not rejected['checked'] and not rejected['found'], rejected
    assert not check_numbers(quote * 2, [item], store)[0]['checked']
    assert not check_numbers(quote, [item], store, set())[0]['checked']


@pytest.mark.parametrize('token,value,unit,source', [
    ('11亿欧元', 1, 'billion EUR', '1 billion EUR'),
    ('1200万英镑', '1.2', 'million GBP', '£1.2 million'),
    ('1 billion shares', 1, '亿股', '1亿股'),
    ('1 MWh', 1, 'MW', '1 MW'),
    ('1 MW', 1, 'MWh', '1 MWh'),
    ('1个百分点', 1, '%', '1%'),
    ('1 EUR', 1, 'USD', '$1'),
    ('1 GBP', 1, 'EUR', '€1'),
    ('1吨', 1, '股', '1股'),
])
def test_registered_units_never_hide_magnitude_currency_or_dimension_mismatch(tmp_path, token, value, unit, source):
    store = Store(tmp_path)
    quote = f'本期数值：{token}。'
    row = check_numbers(quote, [bound(store, quote, token, value, unit, source)], store)[0]
    assert row['checked'] and not row['found'], row


@pytest.mark.parametrize('text', [
    '5 MWhx', '5 EURfoo', '5 million EURfoo', '5 tonnesCO2',
    'XYZ5 EUR', 'b5 MWh', '$€5',
    '$5 EUR', 'EUR 5 USD', '5 MW·h', '5 MWh/年', '5万元/吨', '5万欧元每年',
    '1,23 EUR', '1.2.3 EUR', '1e+ EUR',
])
def test_partial_unknown_or_compound_units_are_not_accepted(text):
    from briefloop.delivery_checks import quantities
    assert list(quantities(text)) == []


def test_unknown_units_stay_unchecked_in_binding_denominator(tmp_path):
    store = Store(tmp_path)
    quote = '本期数值：1亿欧元。'
    supported = bound(store, quote, '1亿欧元', 100, 'million EUR', '100 million EUR')
    for unit in ('kEUR', 'ton', 'TWh', 'EUR per share', 'USD/MWh'):
        assert normalized(1, unit) is None
    assert normalized('1,23', 'EUR') is None
    unsupported = dict(supported, unit='EUR per share')
    run = store.create_run({'title': '单位核验', 'objective': '合成边界验收'}, [supported['source_id']])
    saved = store.publish(run['id'], {'title': '单位核验', 'markdown': quote,
                                     'number_bindings': [supported, unsupported]})
    numbers = brief_checks(store, saved['id'])['numbers']
    assert (numbers['total'], numbers['checked'], numbers['matched'], numbers['status']) == (2, 1, 1, 'partial')
    assert len(numbers['skipped']) == 1 and '不支持' in numbers['skipped'][0]['reason']
    assert not numbers['unmatched']


@pytest.mark.parametrize('supported,unsupported', [('MWh', 'mWh'), ('MW', 'mW'), ('t', 'T'), ('kg', 'KG')])
def test_unit_symbols_keep_case_and_never_turn_milli_into_mega(tmp_path, supported, unsupported):
    from briefloop.delivery_checks import quantities
    store = Store(tmp_path)
    quote = f'本期数值：1 {unsupported}。'
    source = f'1 {supported}'
    item = bound(store, quote, f'1 {unsupported}', 1, supported, source)
    row = check_numbers(quote, [item], store)[0]
    assert not row['checked'] and not row['found'], row
    assert normalized(1, unsupported) is None
    assert list(quantities(f'1 {unsupported}')) == []
    # A correctly cased report cannot borrow a quantity from the unsupported
    # spelling in its source either.
    reverse = bound(store, source, source, 1, supported, f'1 {unsupported}')
    assert not check_numbers(source, [reverse], store)[0]['checked']
    assert normalized(1, 'Million EUR') == normalized(1, 'million eur')
    assert normalized(1, 'TONNES') == normalized(1, 'tonnes')
    assert normalized(1, 'million T') is None


def test_binding_cannot_borrow_other_fact_or_unread_source(tmp_path):
    store = Store(tmp_path)
    quote = '甲公司订单1360亿美元。'
    item = bound(store, quote, '1360亿美元')
    body = quote + '乙公司收入136亿美元。'
    assert not check_numbers(body, [item], store)[0]['found']
    for edits in ({'source_id': 'src_missing'}, {'source_excerpt': 'fabricated'},
                  {'report_quote': ''}, {'locator': ''}, {'report_quote': 'changed'},
                  {'value': 999}):
        row = check_numbers(body, [dict(item, **edits)], store)[0]
        assert not row['checked'] and not row['found'], row
    assert not check_numbers(quote * 2, [item], store)[0]['checked']
    assert not check_numbers(body, [item], store, set())[0]['checked']


def test_number_source_excerpt_is_checked_only_at_its_locator(tmp_path):
    store = Store(tmp_path)
    source = store.add_source('Two periods', 'Revenue $1.2 million.\nRevenue $12 million.\nRevenue $12 million.')
    quote = 'Revenue $12 million.'
    item = dict(label='Revenue', value=12, unit='million USD', source_id=source['id'],
                locator='line 1', source_excerpt=quote, report_quote=quote, number_text='$12 million')
    run = store.create_run({'title': 'Report', 'objective': 'Explain'}, [source['id']])
    brief = store.publish(run['id'], {'title': 'Report', 'markdown': quote, 'number_bindings': [item]})
    checked = brief_checks(store, brief['id'])['numbers']
    assert checked['status'] == 'not_checked' and checked['matched'] == 0
    assert len(checked['skipped']) == 1 and checked['unmatched'] == []
    # An excerpt repeated elsewhere is valid when the explicit location contains it.
    for locator in ('line 2', 'L3', 'lines 2-3', '第2行', '{"kind":"text","start_line":2,"end_line":2}'):
        result = check_numbers(quote, [{**item, 'locator': locator}], store)[0]
        assert result['checked'] and result['found'], (locator, result)
    for locator in ('line 99', 'revenue section', '{invalid json}'):
        result = check_numbers(quote, [{**item, 'locator': locator}], store)[0]
        assert not result['checked'] and not result['found'], (locator, result)


def test_located_magnitude_mismatch_blocks_even_when_reviewer_supports_claim(tmp_path):
    from briefloop.evidence import bind_claim, blocks, create_claim, create_span
    from briefloop.document_model import brief_document
    from briefloop.review import _snapshot
    from briefloop.release import decision

    store = Store(tmp_path)
    quote = 'Revenue $12 million.'
    item = bound(store, quote, '$12 million', 1.2, 'million USD', 'Revenue $1.2 million.')
    run = store.create_run({'title': 'Report', 'objective': 'Explain'}, [item['source_id']])
    brief = store.publish(run['id'], {'title': 'Report', 'markdown': quote, 'number_bindings': [item]})
    span = create_span(store, {'source_id': item['source_id'],
                             'locator': {'kind': 'text', 'start_line': 1, 'end_line': 1}})
    claim = create_claim(store, run['id'], {'statement': quote, 'kind': 'fact',
        'supports': [{'span_id': span['id'], 'supports_quote': quote}]})
    bind_claim(store, brief['id'], claim['id'], next(iter(blocks(brief_document(brief)))), quote)
    snapshot = _snapshot(store, brief['id'])
    snapshot['deterministic'] = brief_checks(store, brief['id'])
    result = decision(snapshot, {'status': 'complete', 'coverage_scan_complete': True,
        'claim_checks': [{'claim_id': claim['id'], 'status': 'supported_for_scope'}],
        'requirement_checks': [{'requirement_id': item['requirement_id'], 'status': 'covered'}
                               for item in snapshot['requirements']['requirement_items']]}, [])
    assert not result['eligible']
    assert [item['code'] for item in result['blockers']] == ['number_mismatch']


def test_version_checks_distinguish_absent_partial_conflict_and_stale(tmp_path):
    store = Store(tmp_path)
    quote = '订单136亿美元。'
    item = bound(store, quote, '136亿美元')
    run = store.create_run({'title': 'demo', 'objective': 'demo'}, [item['source_id']])
    def publish(bindings):
        return store.publish(run['id'], {'title': 'demo', 'markdown': quote + '[@src_missing]',
                                        'number_bindings': bindings})
    b = publish([])
    checks = brief_checks(store, b['id'])
    assert checks['broken_refs'] == ['src_missing']
    assert checks['numbers']['status'] == 'not_checked'
    b = publish([item, dict(item, unit='USD/MWh')])
    checks = brief_checks(store, b['id'])['numbers']
    assert (checks['total'], checks['checked'], checks['matched'], checks['status']) == (2, 1, 1, 'partial')
    edited = store.revise(b['id'], '订单1360亿美元。[@src_missing]')
    checks = brief_checks(store, edited['id'])['numbers']
    assert checks['checked'] == 0 and checks['matched'] == 0
    assert checks['status'] == 'not_checked'
    assert check_export(r'a \*\*b\*\*')['escaped_bold']
    assert check_export('![图](briefloop-figure:fig_01)')['figure_markers'] == ['fig_01']


def test_delivery_gaps_need_related_and_impact_and_are_counted(tmp_path):
    from briefloop.models import GapRecord
    with pytest.raises(ValueError):
        GapRecord(related='利润问题', impact='')
    store = Store(tmp_path)
    source = store.add_source('local', '正文')
    run = store.create_run({'title': 'Report', 'objective': 'Explain'}, [source['id']])
    brief = store.publish(run['id'], {'title': 'Report', 'markdown': '正文', 'gap_records': [
        {'related': '利润是否改善', 'impact': '现有材料不足以支持利润改善结论', 'action': '补查单位成本', 'status': 'open'},
        {'related': '旧的次要缺口', 'impact': '已补查', 'status': 'resolved'}]})
    checked = brief_checks(store, brief['id'])['gaps']
    assert checked['total'] == 2 and checked['open'] == 1
    assert checked['open_records'][0]['related'] == '利润是否改善'


def test_legacy_free_text_gaps_stay_visible(tmp_path):
    store = Store(tmp_path)
    source = store.add_source('local', '正文')
    run = store.create_run({'title': 'Report', 'objective': 'Explain'}, [source['id']])
    brief = store.publish(run['id'], {'title': 'Report', 'markdown': '正文', 'gaps': ['单位成本未取得']})
    checked = brief_checks(store, brief['id'])['gaps']
    assert checked['legacy'] and checked['open'] == 1
    assert checked['open_records'][0]['impact'] == '单位成本未取得'


def test_layout_findings_are_reported_but_never_blocking(tmp_path):
    from briefloop.delivery_checks import check_layout
    headings = lambda pairs: {'type': 'doc', 'content': [
        {'type': 'heading', 'attrs': {'level': level}, 'content': [{'type': 'text', 'text': text}]}
        for level, text in pairs]}
    broken = headings([(1, '一、概览'), (3, '1.1.1 跳级'), (2, '')])
    result = check_layout(broken)
    assert result['status'] == 'issues'
    assert result['heading_jumps'] == [{'after': 1, 'level': 3, 'text': '1.1.1 跳级'}]
    assert result['empty_headings'] == 1
    clean = headings([(1, '一、概览'), (2, '1.1 明细')])
    assert check_layout(clean)['status'] == 'ok'

    store = Store(tmp_path)
    source = store.add_source('local', '正文')
    run = store.create_run({'title': 'Report', 'objective': 'Explain'}, [source['id']])
    table = {'type': 'table', 'content': [
        {'type': 'tableRow', 'content': [
            {'type': 'tableCell', 'content': [{'type': 'paragraph', 'content': [{'type': 'text', 'text': '数值'}]}]},
            {'type': 'tableCell', 'content': [{'type': 'paragraph', 'content': [{'type': 'text', 'text': '12 GW'}]}]}]},
        {'type': 'tableRow', 'content': [
            {'type': 'tableCell', 'content': [{'type': 'paragraph', 'content': [{'type': 'text', 'text': '容量'}]}]},
            {'type': 'tableCell', 'content': [{'type': 'paragraph', 'content': [{'type': 'text', 'text': '45 GW'}]}]}]}]}
    brief = store.publish(run['id'], {'title': 'Report', 'editor_document': {
        'type': 'doc', 'content': [
            {'type': 'heading', 'attrs': {'level': 1}, 'content': [{'type': 'text', 'text': '一、数据'}]},
            {'type': 'heading', 'attrs': {'level': 3}, 'content': [{'type': 'text', 'text': '1.1.1 明细'}]},
            table]}})
    layout = brief_checks(store, brief['id'])['layout']
    assert layout['status'] == 'issues' and len(layout['heading_jumps']) == 1 and layout['tables_without_header'] == ['数值']
    assert layout['empty_headings'] == 0


def test_quantities_separates_numeric_from_prose_only_bodies():
    """Regression seam for the silent-inactive defect: the detector that
    separates "body has numbers" from "body has none" must see bare years
    and percentages — the OfficeQA shapes that escaped the old trigger.
    Assertions land on DIMENSIONS, not just counts: a bare year must come
    out as a year, a multiplier as a ratio."""
    from briefloop.delivery_checks import quantities
    numeric = '编号 42，峰值出现在 1990 年，占比 34.2%，约 2.414 倍。'
    prose = '本段不包含可检出的数值表述。'
    found = list(quantities(numeric))
    dims = {dim for _, _, (_, dim) in found}
    assert 'year' in dims and 'ratio' in dims and 'percent' in dims and 'scalar' in dims
    assert len(found) >= 4
    assert len(list(quantities(prose))) == 0


def test_dimensionless_binding_requires_semantic_specificity(tmp_path):
    """A scalar binding without label/entity matches any equal bare number
    in an excerpt (page numbers, unrelated years) — it must be skipped as
    under-specified, not silently matched."""
    store = Store(tmp_path)
    source = store.add_source('disclosure', 'Fiscal year 1990 total: 1990' + chr(10) + 'Page 1990 footer.')
    bare = dict(label='', value='1990', unit='', entity='', period='',
                source_id=source['id'], locator='line 1', source_excerpt='Fiscal year 1990 total: 1990',
                report_quote='总数是 1990。', number_text='1990')
    rows = check_numbers('总数是 1990。', [bare], store)
    assert rows[0]['checked'] is False and '特异性不足' in rows[0]['reason']
    specified = dict(bare, label='财年', entity='Treasury', period='FY1990')
    rows2 = check_numbers('总数是 1990。', [specified], store)
    assert rows2[0]['checked'] is True and rows2[0]['found'] is True
    assert rows2[0].get('dimensionless') is True and '无量纲' in rows2[0]['reason']


def test_release_flags_numbers_unbound_and_numbers_absent():
    """Zero-binding bodies must stop being invisible in the release record:
    numbers-present/not-checked is a notice (report mode), absent is its own
    quieter notice — neither reads as a completed check."""
    from briefloop.release import decision
    base = {'deterministic': {'numbers': {'total': 0, 'checked': 0, 'matched': 0,
                                          'status': 'not_checked', 'unmatched': [], 'skipped': []}},
            'coverage': {'complete': True}, 'premises': {},
            'requirements': {'satisfied': True, 'requirement_items': []},
            'evidence': {'bindings': []}}
    review = {'status': 'complete', 'overall': 'pass', 'coverage': True, 'premises': True,
              'requirement_checks': [], 'coverage_scan_complete': True}
    with_numbers = decision({**base, 'deterministic': {**base['deterministic'], 'numbers': {
        **base['deterministic']['numbers'], 'body_quantity_count': 7}}}, review, [])
    codes = {n['code'] for n in with_numbers['notices']}
    assert 'numbers_unbound' in codes
    absent = decision(base, review, [])
    codes = {n['code'] for n in absent['notices']}
    assert 'numbers_absent' in codes and 'numbers_unbound' not in codes


def test_percentage_point_and_count_units_do_not_collide():
    """Review findings A/B: '个' must not swallow '个百分点' compounds (a
    check that reads matched while its dimension is wrong is the exact
    false-assurance this file exists to prevent), and 百分点 must normalize
    (it was regex-known but normalized-rejected, silently dropped)."""
    from briefloop.delivery_checks import quantities, normalized
    from decimal import Decimal
    pp = [(v, d) for _, _, (v, d) in quantities('上升 1.2个百分点')]
    assert pp == [(Decimal('1.2'), 'percentage_point')]
    assert normalized(1.2, '百分点') == (Decimal('1.2'), 'percentage_point')
    months = [(v, d) for _, _, (v, d) in quantities('近 3个月 环比增长 12%')]
    assert ('percentage_point',) not in [(d,) for _, d in months]
    assert (Decimal('12'), 'percent') in months
    assert (Decimal('3'), 'count') not in months   # 3个月 is a period, not a count
    assert (Decimal('50000'), 'count') not in [v for _, _, v in quantities('5万个月')]
    counts = [(v, d) for _, _, (v, d) in quantities('新增 5 家机构')]
    assert counts == [(Decimal('5'), 'count')]
