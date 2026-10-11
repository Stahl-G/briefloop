import json
from datetime import datetime
from zoneinfo import ZoneInfo
import pytest
from briefloop.report_time import freeze, check, instructions
from briefloop.store import Store


def test_clock_range_and_explicit_dates():
    clock = datetime(2026, 9, 14, 1, 30, tzinfo=ZoneInfo('UTC'))
    window = freeze({'period': '最新动态', 'report_timezone': 'Asia/Shanghai'}, clock)
    assert window['today'] == '2026-09-14'
    assert window['start'] == '2026-09-14T00:00:00+08:00'
    assert window['end_exclusive'] == '2026-09-14T09:30:00+08:00'
    historical = freeze({'period_start':'2025-02-01','period_end':'2025-02-28'}, clock)
    assert historical['end_exclusive'].startswith('2025-03-01')
    with pytest.raises(ValueError): freeze({'period':'某次里程碑之后'}, clock)
    with pytest.raises(ValueError): freeze({'period_start':'2026-09-15'}, clock)
    with pytest.raises(ValueError): freeze({'report_timezone':'bad/zone'}, clock)


def test_frozen_context_and_temporal_checks(tmp_path):
    store = Store(tmp_path)
    source = store.add_source('date fixture', 'Published 2025-02-24. Event 2025-02-24.')
    run = store.create_run({'title':'日报','objective':'核对日期','period':'2026-09-14',
                            'time_context':{'today':'2025-01-01'}}, [source['id']])
    window = json.loads(run['requirements'])['time_context']
    assert window['clock_source']=='system_clock' and window['today'] != '2025-01-01'
    stored = store.one('runs',run['id'])['requirements']
    result = check(window,[{'statement':'Old release','event_date':'2025-02-24','fetched_at':'2026-09-14'},
                           {'statement':'Unknown','published_at':'2026-09-14'},
                           {'statement':'Background','event_date':'2025-02-24','usage':'background'}])
    assert [c['temporal_status'] for c in result['items']] == ['out_of_range','unverified','background']
    assert store.one('runs',run['id'])['requirements']==stored
    from briefloop.runtime import generation_prompt
    folder=tmp_path/'prompt';folder.mkdir()
    text=generation_prompt(store,run,folder,backend='antigravity')
    assert window['start'] in text
    assert window['start'] in (folder/'scout-contract.md').read_text(encoding='utf-8')
    assert window['start'] in (folder/'analyst-writing.md').read_text(encoding='utf-8')
    assert json.loads((folder/'input.json').read_text(encoding='utf-8'))['requirements']['time_context']==window


def test_tavily_uses_frozen_dates(tmp_path,monkeypatch):
    from briefloop import tavily
    from io import BytesIO
    store=Store(tmp_path)
    store.set_meta('settings',{**store.settings(),'search_provider':'tavily'})
    run=store.create_run({'title':'日报','objective':'核对日期','allow_web':True,'period':'2026-09-14'},[])
    key=tmp_path/'key';tavily.save_key('test',key_file=key)
    seen=[]
    def respond(opener,request,**kwargs):
        seen.append(json.loads(request.data))
        return BytesIO(b'{"results":[]}')
    monkeypatch.setattr(tavily.urllib.request.OpenerDirector,'open',respond)
    tavily.search('news',store=store,run_id=run['id'],start_date='2025-01-01',key_file=key)
    assert seen[0]['start_date']=='2026-09-14' and seen[0]['end_date']=='2026-09-15'


def test_news_basis_keeps_event_date_and_does_not_promote_republication():
    window = freeze({'period': '2026年第39周'}, datetime(2026, 9, 28, tzinfo=ZoneInfo('UTC')))
    event = {'statement': '20日发生，25日首次披露', 'event_date': '2026-09-20',
             'published_at': '2026-09-25', 'fetched_at': '2026-09-26'}
    disclosure = {**event, 'news_basis': 'first_disclosure', 'news_date': '2026-09-25',
                  'news_note': '官方首次公开事件及处置过程', 'source_id': 'src_event', 'locator': 'line 2-4'}
    development = {**disclosure, 'news_basis': 'new_development',
                   'news_note': '25日官方公布新的处置进展'}
    no_source = {**disclosure, 'locator': ''}
    no_new_fact = {**disclosure, 'news_note': ''}
    result = check(window, [event, disclosure, development, no_source, no_new_fact])
    assert [item['temporal_status'] for item in result['items']] == [
        'out_of_range', 'in_range_unverified', 'in_range_unverified', 'unverified', 'unverified']
    assert result['items'][1]['event_date'] == '2026-09-20'
    assert result['items'][1]['basis_date'] == '2026-09-25'
    assert result['items'][3]['reason'] == 'missing_news_basis_evidence'
    assert check(window, [{**disclosure, 'news_date': ''}])['items'][0]['temporal_status'] == 'unverified'
    assert check(window, [{**disclosure, 'news_date': '2026-09-28'}])['out_of_range_count'] == 1
    assert result['status'] == 'needs_source_review'


def test_iso_week_warning_preserves_custom_range_and_reaches_check():
    clock = datetime(2026, 9, 28, tzinfo=ZoneInfo('UTC'))
    req = {'title': 'AI 周报 2026年第39周', 'period_start': '2026-09-21', 'period_end': '2026-09-28'}
    window = freeze(req, clock)
    assert window['end_exclusive'] == '2026-09-29T00:00:00+00:00'
    assert window['warnings'][0]['code'] == 'iso_week_mismatch'
    assert '2026-09-27' in instructions(window)
    assert check(window, [])['warnings'] == window['warnings']
    assert check(window, [{'statement': '本期事件', 'event_date': '2026-09-28'}])['warnings'] == window['warnings']
    assert 'warnings' not in freeze({**req, 'period_end': '2026-09-27'}, clock)


def test_writer_retains_disclosure_basis_and_returns_date_diagnostics(tmp_path):
    from briefloop import analyst, analyst_drafts, writer_input
    from test_native_orchestrator import contract
    store = Store(tmp_path/'workspace')
    text = '事件发生于2026-09-20，2026-09-25首次披露处置过程。'
    source = store.add_source('测试事件公告', text)
    run = store.create_run({'title': '2026年第39周', 'objective': '区分事件和披露',
                            'period_start': '2026-09-21', 'period_end': '2026-09-28',
                            'allow_web': False}, [source['id']])
    store.set_meta('reader_contract:' + run['id'], contract(store, run['id']))
    pack = analyst.packet(store, run['id'], store.root/'writer',
                          plan={'draft_structure': ['动态']},
                          research={'sources': [{'source_id': source['id'], 'locator': 'line 1',
                                                 'excerpt': text, 'facts': ['此前事件本周首次披露'],
                                                 'coverage_status': 'complete'}], 'gaps': []})
    config = {'native_role': 'analyst', 'run_id': run['id'], 'packet_root': str(pack['root']),
              'result_file': str(pack['root'].parent/'draft.json'), 'attempt_id': 'time-test'}
    saved = writer_input.write_report(store, config, {
        'title': '2026年第39周', 'markdown': f'本周首次披露此前事件。[@{source["id"]}]',
        'temporal_claims': [{'statement': '本周首次披露此前事件', 'event_date': '2026-09-20',
                             'news_basis': 'first_disclosure', 'news_date': '2026-09-25',
                             'news_note': '首次披露此前事件的处置过程',
                             'source_id': source['id'], 'source_excerpt': text}]})
    diagnostics = analyst_drafts.check(store, config, {'revision': saved['revision']})['diagnostics']
    item = diagnostics['temporal']['items'][0]
    assert item['event_date'] == '2026-09-20'
    assert item['locator'] == 'line 1'
    assert item['news_basis'] == 'first_disclosure' and item['temporal_status'] == 'in_range_unverified'
    assert diagnostics['temporal']['warnings'][0]['code'] == 'iso_week_mismatch'
    assert any(note['code'] == 'iso_week_mismatch' for note in diagnostics['notes'])
    assert diagnostics['review_status'] == 'not_reviewed'
    assert analyst_drafts.submit(store, config, {'revision': saved['revision']})['status'] == 'saved'


def test_as_of_analysis_has_no_news_window_and_does_not_filter_search(tmp_path, monkeypatch):
    # A company/event analysis "as of now" once inherited a today-only window,
    # which limited every Tavily search to one day (2026-10 Manus run).
    clock = datetime(2026, 10, 10, 2, 20, tzinfo=ZoneInfo('UTC'))
    window = freeze({'period': '截至提交时刻', 'report_timezone': 'Asia/Shanghai'}, clock)
    assert window['start'] is None and window['mode'] == 'as_of'
    result = check(window, [{'statement': '此前融资', 'event_date': '2026-10-08'},
                            {'statement': '提交后的事', 'event_date': '2026-10-11'}])
    assert [c['temporal_status'] for c in result['items']] == ['as_of_unverified', 'out_of_range']
    from briefloop import tavily
    from io import BytesIO
    store = Store(tmp_path)
    store.set_meta('settings', {**store.settings(), 'search_provider': 'tavily'})
    run = store.create_run({'title': '估值分析', 'objective': '解释估值', 'allow_web': True, 'period': '截至提交时刻'}, [])
    key = tmp_path/'key';tavily.save_key('test', key_file=key)
    seen = []
    monkeypatch.setattr(tavily.urllib.request.OpenerDirector, 'open',
                        lambda opener, request, **kwargs: seen.append(json.loads(request.data)) or BytesIO(b'{"results":[]}'))
    tavily.search('funding', store=store, run_id=run['id'], key_file=key)
    assert seen[0].get('start_date') is None and seen[0]['end_date']
