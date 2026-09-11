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
    for unit in ('USD/MWh', 'B USD', 'million EUR', '兆', '百分点'):
        assert normalized(5, unit) is None
    for quote, token in [('收入110美元', '10美元'), ('价格5美元/MWh', '5美元/MWh'), ('电量5MWh', '5MWh')]:
        item = bound(store, quote, token, 5, 'USD', '$5')
        assert not check_numbers(quote, [item], store)[0]['checked']
    assert normalized(10, '万美元') == normalized(100000, 'USD')
    assert normalized(1, 'thousand USD') == normalized(1000, 'USD')


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
