"""Idle release must preserve work and native conversation identity."""
import time

from briefloop.harness import HarnessManager
from briefloop.store import Store
from test_harness import RPC, until


class IdleRPC(RPC):
    def __init__(self, *args):
        super().__init__(*args)
        self.closed = False

    def close(self):
        self.closed = True


def test_config_lease_survives_idle_deadline_then_releases(tmp_path):
    manager = HarnessManager(Store(tmp_path), IdleRPC)
    manager.IDLE_SECONDS = .03
    try:
        with manager.client_use() as client:
            time.sleep(.3)
            assert not client.closed
            assert manager.client is client
        until(lambda: manager.client is None)
        until(lambda: client.closed)
        assert manager.list_sessions() == []
    finally:
        manager.close()


def test_active_turn_and_child_block_idle_then_resume_saved_thread(tmp_path):
    manager = HarnessManager(Store(tmp_path), IdleRPC)
    manager.IDLE_SECONDS = .03
    sid = manager.create_session()['id']
    try:
        manager.send(sid, 'first')
        until(lambda: manager.chat.session(sid)['turn_id'] == 'turn1')
        original = manager.client
        time.sleep(.3)
        assert not original.closed
        manager.handle_notification({'method':'item/completed','params':{
            'threadId':'t1','turnId':'turn1','item':{'id':'spawn','type':'collabAgentToolCall',
            'tool':'spawnAgent','receiverThreadIds':['child'],'agentsStates':{'child':{'status':'running'}}}}})
        manager.handle_notification({'method':'turn/completed','params':{
            'threadId':'t1','turn':{'id':'turn1','status':'completed'}}})
        time.sleep(.3)
        assert not original.closed
        manager.handle_notification({'method':'turn/completed','params':{
            'threadId':'child','turn':{'id':'child1','status':'completed'}}})
        until(lambda: manager.client is None)
        assert manager.chat.session(sid)['thread_id'] == 't1'
        assert manager.chat.session(sid)['status'] == 'idle'
        manager.send(sid, 'second')
        until(lambda: manager.chat.session(sid)['turn_id'] is not None)
        replacement = manager.client
        assert replacement is not original
        assert any(method=='thread/resume' and params['threadId']=='t1'
                   for method, params in replacement.calls)
        assert not any(e['kind']=='error' for e in manager.snapshot(sid)['events'])
    finally:
        manager.close()
