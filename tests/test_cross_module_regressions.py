import json
import threading
import time
from queue import Queue
from briefloop.store import Store
from briefloop.runtime import Worker
from briefloop.learning import _baseline_for_attempt
from briefloop.harness import HarnessManager
import pytest


def test_baseline_is_bound_to_completed_attempt(tmp_path):
    s=Store(tmp_path);source=s.add_source('source','Evidence')
    run=s.create_run({'title':'Test','objective':'Test'},[source['id']])
    old=s.enqueue('generate',{'run_id':run['id'],'runtime':{'model':'old-model'}})
    s.publish(run['id'],{'title':'Old','markdown':'Old'},version_id='brief_'+old['id'][4:])
    s.update_job(old['id'],'failed')
    job=s.enqueue('generate',{'run_id':run['id']})
    brief=s.publish(run['id'],{'title':'Current','markdown':'Current'},version_id='brief_'+job['id'][4:])
    from briefloop.review_learning import source_snapshot
    s.update_job(job['id'],'complete',result={'version_id':brief['id'],'source_snapshot':source_snapshot(s,run['id'])})
    payload=json.loads(job['payload']);payload['skill_id']=None
    assert _baseline_for_attempt(s,run,payload)['id']==brief['id']
    payload['runtime']={'model':'different'}
    assert _baseline_for_attempt(s,run,payload) is None


def test_cancel_between_select_and_claim_never_executes(tmp_path):
    s=Store(tmp_path);s.set_meta('settings',{**s.settings(),'auto_learn':False})
    job=s.enqueue('generate',{})
    selected=threading.Event();released=threading.Event();checked=threading.Event()
    rows=s.rows
    def delayed(query,args=()):
        value=rows(query,args)
        if "SELECT * FROM jobs" in query and "status='queued'" in query and "kind!='review'" in query:
            if value and not selected.is_set():selected.set();assert released.wait(3)
            elif selected.is_set():checked.set()
        return value
    s.rows=delayed
    class Runtime:
        cancelled=threading.Event()
        def cancel(self):self.cancelled.set()
    w=Worker(s,Runtime());executed=[];w.generate=lambda j:executed.append(j['id'])
    w.start()
    try:
        assert selected.wait(3);w.stop_job(job['id']);released.set();assert checked.wait(3)
        assert s.one('jobs',job['id'])['status']=='cancelled' and not executed
    finally:released.set();w.close()


class RPC:
    def __init__(self,*args):self.notifications=Queue();self.server_requests=Queue();self.calls=[]
    def request(self,method,params):
        self.calls.append((method,params))
        if method=='thread/start':return {'thread':{'id':'parent'}}
        if method=='turn/start':return {'turn':{'id':'parent-turn'}}
        return {}
    def interrupt(self,*args):pass
    def answer(self,*args):pass
    def close(self):pass


def test_child_completion_expires_only_its_request_and_steer_cannot_change_network(tmp_path):
    s=Store(tmp_path);h=HarnessManager(s,RPC);sid=h.create_session()['id']
    h.send(sid,'Start',allow_web=False)
    deadline=time.monotonic()+3
    while not h.snapshot(sid)['session']['turn_id'] and time.monotonic()<deadline:time.sleep(.01)
    try:
        with pytest.raises(ValueError,match='联网'):h.send(sid,'Enable web',mode='steer',allow_web=True)
        queued=h.send(sid,'Next turn web',allow_web=True)
        assert queued['status']=='queued' and queued['allow_web']
        h.handle_notification({'method':'item/completed','params':{'threadId':'parent','turnId':'parent-turn','item':{'id':'spawn','type':'collabAgentToolCall','receiverThreadIds':['child']}}})
        child=h.chat.add_request(sid,1,{'turnId':'child-turn','threadId':'child','questions':[]})
        parent=h.chat.add_request(sid,2,{'turnId':'parent-turn','threadId':'parent','questions':[]})
        h.handle_notification({'method':'turn/completed','params':{'threadId':'child','turn':{'id':'child-turn','status':'interrupted'}}})
        statuses={r['id']:r['status'] for r in h.snapshot(sid)['requests']}
        assert statuses[child]=='expired' and statuses[parent]=='pending'
        h.cancel(sid)
        h.handle_notification({'method':'turn/completed','params':{'threadId':'parent','turn':{'id':'parent-turn','status':'interrupted'}}})
        assert not h.snapshot(sid)['session']['busy']
        h.archive(sid)
    finally:h.close()
