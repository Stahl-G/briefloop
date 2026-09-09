"""Delivery checks around this round's actual failures, nothing more."""
from briefloop.delivery_checks import (body_refs, brief_checks, check_export,
                                       check_numbers, check_refs, equivalent_forms)
from briefloop.store import Store


def _bind(**kw):
    base = {'label': 't', 'value': 1, 'unit': 'USD'}
    base.update(kw)
    return base


def test_broken_refs_are_listed_not_blocked(tmp_path):
    store = Store(tmp_path)
    good = store.add_source('ok', 'text')
    assert check_refs(store, '见[@%s]与[@src_missing]' % good['id']) == ['src_missing']
    assert body_refs('无引用') == []
    brief = store.publish(store.create_run(
        {'title': 't', 'objective': 'o'}, [good['id']])['id'],
        {'title': 't', 'markdown': '见[@%s]与[@src_missing]' % good['id']})
    checks = brief_checks(store, brief['id'])
    assert checks['broken_refs'] == ['src_missing']
    assert checks['assessment_overall'] is None


def test_tenfold_amount_is_unmatched_but_correct_form_matches():
    forms = equivalent_forms(13.6, 'billion USD')
    assert '136亿美元' in forms and '1360亿美元' not in forms
    bad = check_numbers('在手订单45.1吉瓦约1360亿美元。',
                        [_bind(label='FSLR订单', value=13.6, unit='billion USD')])
    assert len(bad) == 1 and bad[0]['checked'] and not bad[0]['found']
    assert bad[0]['expected'] == '136亿美元'
    good = check_numbers('在手订单45.1吉瓦约136亿美元。',
                         [_bind(label='FSLR订单', value=13.6, unit='billion USD'),
                          _bind(label='在手规模', value=45.1, unit='GW')])
    assert all(r['found'] for r in good)


def test_percent_capacity_and_skipped_units():
    assert check_numbers('开盘缺口-21.33%。', [_bind(label='缺口', value=21.33, unit='%')])[0]['found']
    assert check_numbers('同比增长5 percent', [_bind(label='g', value=5, unit='percent')])[0]['found']
    assert check_numbers('新增45100兆瓦', [_bind(label='c', value=45.1, unit='GW')])[0]['found']
    skipped = check_numbers('whatever', [_bind(label='s', value=1, unit='兆'),
                                         _bind(label='v', value='n/a', unit='USD'),
                                         'not-a-dict'])
    assert [r['checked'] for r in skipped] == [False, False]
    assert check_numbers('x', [_bind(label='e', value=1, unit='')]) == [
        {'label': 'e', 'checked': False, 'found': False, 'expected': ''}]


def test_export_flags_escaped_bold_and_lists_figures():
    assert check_export('a \\*\\*b\\*\\* c') == {'escaped_bold': True, 'figure_markers': []}
    assert check_export('见 ![图](briefloop-figure:fig_01)') == {
        'escaped_bold': False, 'figure_markers': ['fig_01']}
