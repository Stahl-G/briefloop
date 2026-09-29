import json
import threading
import time
from queue import Queue
from briefloop.store import Store
from briefloop.runtime import Worker
from briefloop.learning import _baseline_for_attempt
from briefloop.harness import HarnessManager
import pytest


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
