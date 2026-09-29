from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone, timedelta
import json

import pytest
from briefloop.store import Store
from briefloop import schedules as s


def at(value):return datetime.fromisoformat(value).replace(tzinfo=timezone.utc)

def setup(tmp_path, **config):
    store=Store(tmp_path)
    source=store.add_source('本期记录','本周完成 17 个订单。')
    body={'name':'周报','config':{'frequency':'custom','every':1,'unit':'minutes','start':'2026-09-14T10:00','timezone':'UTC','requirements':{'title':'周报','objective':'总结材料'},'source_ids':[source['id']],**config}}
    item=s.save(store,body,at('2026-09-14T09:59:00'))
    return store,item['id'],body


def test_calendar_and_custom_frequency():
    config={'start':'2026-01-31T09:00','timezone':'Asia/Shanghai','frequency':'monthly'}
    assert s.next_time(config,at('2026-02-01T00:00:00'))==at('2026-02-28T01:00:00')
    assert s.next_time(config,at('2026-03-01T00:00:00'))==at('2026-03-31T01:00:00')
    daily={'start':'2026-03-07T02:30','timezone':'America/New_York','frequency':'daily'}
    assert s.next_time(daily,at('2026-03-08T06:00:00'))==at('2026-03-08T07:30:00')
    assert s.next_time(daily,at('2026-03-08T08:00:00'))==at('2026-03-09T06:30:00')
    assert s.next_time({**daily,'frequency':'custom','every':2,'unit':'hours'},at('2026-03-08T06:00:00'))==at('2026-03-08T07:30:00')


def test_failed_admission_rolls_back_and_pause(tmp_path,monkeypatch):
    store,sid,_=setup(tmp_path)
    original=Store.enqueue
    def broken(self,*args,**kwargs):
        original(self,*args,**kwargs)
        raise RuntimeError('fixture failure')
    monkeypatch.setattr(Store,'enqueue',broken)
    result=s.fire(store,sid,at=at('2026-09-14T10:00:01'))
    assert result['status']=='failed' and result['error']
    assert not store.rows('SELECT * FROM runs') and not store.rows('SELECT * FROM jobs')
    monkeypatch.setattr(s,'clock',lambda:at('2026-09-14T10:01:00'))
    s.change(store,{'id':sid,'action':'toggle'})
    s.tick(store,at('2026-09-14T10:03:01'))
    assert not store.rows('SELECT * FROM jobs')
    s.change(store,{'id':sid,'action':'toggle'})
    assert s.listing(store)[0]['next_at']=='2026-09-14T10:02:00+00:00'


def test_custom_validation_and_company_gate(tmp_path):
    store,sid,body=setup(tmp_path)
    for value in (0,True,10001):
        with pytest.raises(ValueError):s.save(store,{**body,'config':{**body['config'],'every':value}})
    with pytest.raises(ValueError,match='企业背景'):
        s.save(store,{**body,'config':{**body['config'],'requirements':{**body['config']['requirements'],'writing_mode':'internal_report'}}})


