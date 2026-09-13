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


def test_concurrent_admission_skip_running_and_offline(tmp_path):
    store,sid,_=setup(tmp_path)
    with ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(lambda _:s.tick(store,at('2026-09-14T10:00:01')),range(4)))
    assert len(store.rows('SELECT * FROM jobs'))==1
    s.tick(store,at('2026-09-14T10:01:01'))
    assert len(store.rows('SELECT * FROM jobs'))==1
    assert s.listing(store)[0]['history'][0]['status']=='skipped'
    s.skip_offline(store,at('2026-09-14T11:00:00'))
    s.tick(store,at('2026-09-14T11:00:01'))
    assert len(store.rows('SELECT * FROM jobs'))==1
    assert s.listing(store)[0]['next_at']=='2026-09-14T11:01:00+00:00'
    s.change(store,{'id':sid,'action':'delete'})
    assert not s.listing(store)
    assert len(store.rows('SELECT * FROM jobs'))==1


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


def test_each_run_freezes_current_skill_and_rolls_date(tmp_path):
    store,sid,_=setup(tmp_path)
    # Skill ID is resolved by the same Store path as a normal report.
    store.set_meta('active_skill','skill_first')
    first=s.fire(store,sid,at=at('2026-09-14T10:00:01'))
    assert first['status']=='accepted'
    store.update_job(first['job_id'],'complete')
    store.set_meta('active_skill','skill_second')
    second=s.fire(store,sid,at=at('2026-09-15T10:00:01'))
    assert store.one('runs',first['run_id'])['skill_id']=='skill_first'
    run=store.one('runs',second['run_id'])
    assert run['skill_id']=='skill_second'
    req=json.loads(run['requirements'])
    assert req['report_date']=='2026-09-15' and '2026-09-15' in req['period']


def test_custom_validation_and_company_gate(tmp_path):
    store,sid,body=setup(tmp_path)
    for value in (0,True,10001):
        with pytest.raises(ValueError):s.save(store,{**body,'config':{**body['config'],'every':value}})
    with pytest.raises(ValueError,match='企业背景'):
        s.save(store,{**body,'config':{**body['config'],'requirements':{**body['config']['requirements'],'writing_mode':'internal_report'}}})


def test_manual_retry_does_not_duplicate_and_preserves_setup(tmp_path):
    store,sid,_=setup(tmp_path)
    store.set_meta('requirements',{'title':'正在编辑的其他报告'})
    first=s.fire(store,sid,manual=True,request_id='stable-click')
    assert first['status']=='accepted'
    store.update_job(first['job_id'],'complete')
    assert s.fire(store,sid,manual=True,request_id='stable-click')['job_id']==first['job_id']
    assert len(store.rows('SELECT * FROM jobs'))==1
    assert store.meta('requirements')=={'title':'正在编辑的其他报告'}


def test_retry_reason_is_bound_to_own_running_job(tmp_path):
    from briefloop.chat_store import ChatStore
    store,sid,_=setup(tmp_path)
    result=s.fire(store,sid,manual=True,request_id='retry-status')
    store.update_job(result['job_id'],'running')
    chat=ChatStore(store)
    session=chat.create('报告',{},str(tmp_path))
    chat.event(session['id'],'job/attached',{'jobId':result['job_id']})
    chat.event(session['id'],'runtime/status',{'status':'retry','message':'429 weekly usage limit reached'})
    assert '429' in s.listing(store)[0]['history'][0]['runtime_message']
    other=chat.create('其他报告',{},str(tmp_path))
    chat.event(other['id'],'runtime/status',{'status':'retry','message':'OTHER'})
    assert 'OTHER' not in s.listing(store)[0]['history'][0]['runtime_message']
    chat.event(session['id'],'runtime/status',{'status':'resumed'})
    assert s.listing(store)[0]['history'][0]['runtime_message'] is None


@pytest.mark.parametrize('explicit', [None, False])
def test_schedule_freezes_check_choice_and_deep_tier(tmp_path, explicit):
    requirements={'title':'周报','objective':'总结材料','allow_web':True,
                  'research_tier':'deep','fact_check':explicit}
    store,sid,_=setup(tmp_path,requirements=requirements)
    store.set_meta('settings',{**store.settings(),'fact_checker':True})
    s.tick(store,at('2026-09-14T10:00:01'))
    run=store.rows('SELECT * FROM runs')[0]
    frozen=json.loads(run['requirements'])
    assert frozen['fact_check'] is False
    assert frozen['research_tier']=='deep'
