import json
from datetime import datetime
from zoneinfo import ZoneInfo
import pytest
from briefloop.report_time import freeze, check
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
    assert window['start'] in (folder/'scout-contract.md').read_text()
    assert window['start'] in (folder/'analyst-writing.md').read_text()
    assert json.loads((folder/'input.json').read_text())['requirements']['time_context']==window


def test_tavily_uses_frozen_dates(tmp_path,monkeypatch):
    from briefloop import tavily
    from io import BytesIO
    store=Store(tmp_path)
    store.set_meta('settings',{**store.settings(),'search_provider':'tavily'})
    run=store.create_run({'title':'日报','objective':'核对日期','allow_web':True,'period':'2026-09-14'},[])
    key=tmp_path/'key';tavily.save_key('test',key_file=key)
    seen=[]
    def respond(request,**kwargs):
        seen.append(json.loads(request.data))
        return BytesIO(b'{"results":[]}')
    monkeypatch.setattr(tavily.urllib.request,'urlopen',respond)
    tavily.search('news',store=store,run_id=run['id'],start_date='2025-01-01',key_file=key)
    assert seen[0]['start_date']=='2026-09-14' and seen[0]['end_date']=='2026-09-15'
