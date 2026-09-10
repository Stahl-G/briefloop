import queue
import time
import pytest
from briefloop.store import Store
from briefloop.bridge_harness import BridgeHarness


class BridgeFixture:
    def __init__(self):self.starts=[];self.sinks={}
    def subscribe(self,eid):self.sinks[eid]=queue.Queue();return self.sinks[eid]
    def unsubscribe(self,eid):self.sinks.pop(eid,None)
    def call(self,method,params,timeout=None):
        if method=='start':
            self.starts.append(params)
            for event in [{'kind':'session','session_id':'native-session'},
                          {'kind':'text','text':'visible answer'},
                          {'kind':'tool','id':'native-item','name':'read','status':'completed','input':{'path':'sample.txt'},'output':'data'},
                          {'kind':'end','status':'completed'}]:self.sinks[params['execution_id']].put(event)
            return {'execution_id':params['execution_id']}
        return {}


def test_bridge_turn_is_durable_and_same_message_is_not_redispatched(tmp_path):
    bridge=BridgeFixture();h=BridgeHarness(Store(tmp_path),bridge,'claude')
    s=h.create_session('test',{'model':'host-model'})
    message=h.send(s['id'],'read the fixture',message_id='fixed-admission')
    deadline=time.monotonic()+3
    while time.monotonic()<deadline:
        snap=h.snapshot(s['id'])
        if snap['messages'][0]['status']=='completed':break
        time.sleep(.01)
    assert snap['messages'][0]['status']=='completed'
    assert snap['session']['thread_id']=='native-session'
    assert snap['messages'][1]['text']=='visible answer'
    assert bridge.starts[0]['execution_id']=='fixed-admission'
    assert bridge.starts[0]['allow_web'] is None
    h.send(s['id'],'read the fixture',message_id='fixed-admission')
    assert len(bridge.starts)==1
    assert any(e['kind']=='item/completed' and e['data']['item']['id']=='native-item' for e in snap['events'])


def test_restricted_reviewer_cannot_enter_unverified_bridge(tmp_path):
    bridge=BridgeFixture();h=BridgeHarness(Store(tmp_path),bridge,'kimi')
    with pytest.raises(ValueError,match='隔离'):
        h.create_session('review',{'model':'default','permission':'read-only','review_root':str(tmp_path)})
    assert not bridge.starts
