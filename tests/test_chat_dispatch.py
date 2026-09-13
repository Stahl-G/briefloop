import threading

import pytest

from briefloop.bridge_harness import BridgeHarness
from briefloop.chat_dispatch import ChatDispatcher
from briefloop.harness import HarnessManager
from briefloop.store import Store
from test_bridge_harness import BridgeFixture
from test_harness import RPC, until


def test_actual_driver_queue_switch_back_and_cancel_owner(tmp_path):
    store = Store(tmp_path)
    codex = HarnessManager(store, RPC)
    bridge = BridgeFixture()
    buddy = BridgeHarness(store, bridge, 'codebuddy')
    dispatch = ChatDispatcher({'codex': codex, 'codebuddy': buddy})
    sid = codex.create_session(runtime={'model': 'a', 'permission': 'read-only'})['id']
    codex.send(sid, '记住 17 件', message_id='a1')
    until(lambda: codex.chat.session(sid)['turn_id'] == 'turn1')
    # New host defaults must not inherit Codex's unsupported read-only permission.
    buddy.send(sid, '接着处理', runtime={'backend': 'codebuddy', 'model': 'b'}, message_id='b1')
    codex.send(sid, '回切，结合 B 的回答', runtime={'backend': 'codex', 'model': 'a'}, message_id='a2')
    assert dispatch.select(sid=sid) is codex
    assert not bridge.starts
    with pytest.raises(ValueError, match='追加'):
        buddy.send(sid, '不能中途切', mode='steer', runtime={'backend': 'codebuddy'})
    codex.handle_notification({'method': 'item/completed', 'params': {'threadId': 't1', 'turnId': 'turn1',
                              'item': {'type': 'agentMessage', 'id': 'answer-a', 'text': '订单 17 件'}}})
    codex.handle_notification({'method': 'turn/completed', 'params': {'threadId': 't1', 'turn': {'id': 'turn1', 'status': 'completed'}}})
    until(lambda: codex.chat.session(sid)['turn_id'] == 'turn2')
    assert len(bridge.starts) == 1
    assert 'session_id' not in bridge.starts[0]
    assert '订单 17 件' in bridge.starts[0]['prompt']
    turns = [p for method, p in codex.client.calls if method == 'turn/start']
    assert turns[-1]['threadId'] == 't2'
    assert 'visible answer' in turns[-1]['input'][0]['text']
    assert 'weighing options' not in turns[-1]['input'][0]['text']
    # Late old Codex completion cannot terminate the newly active A segment.
    codex.handle_notification({'method': 'turn/completed', 'params': {'threadId': 't1', 'turn': {'id': 'turn1', 'status': 'completed'}}})
    assert codex.chat.session(sid)['turn_id'] == 'turn2'
    codex.handle_notification({'method': 'turn/completed', 'params': {'threadId': 't2', 'turn': {'id': 'old-turn', 'status': 'completed'}}})
    assert codex.chat.session(sid)['turn_id'] == 'turn2'
    codex.handle_notification({'method': 'item/completed', 'params': {'threadId': 't2', 'turnId': 'turn2',
                              'item': {'type': 'agentMessage', 'id': 'answer-a', 'text': 'New answer'}}})
    answers = [m['text'] for m in codex.snapshot(sid)['messages'] if m['role'] == 'assistant']
    assert '订单 17 件' in answers and 'New answer' in answers
    buddy.send(sid, '排队 B', runtime={'backend': 'codebuddy'}, message_id='b2')
    dispatch.select(sid=sid).cancel(sid)
    assert codex.client.calls[-1] == ('turn/interrupt', {'threadId': 't2', 'turnId': 'turn2'})
    assert next(m for m in codex.snapshot(sid)['messages'] if m['id'] == 'b2')['status'] == 'cancelled'
    assert len(bridge.starts) == 1
    codex.close()


def test_cancel_before_native_dispatch_and_restart_do_not_replay(tmp_path):
    codex = HarnessManager(Store(tmp_path), RPC)
    dispatch = ChatDispatcher({'codex': codex})
    ready, release = threading.Event(), threading.Event()
    original = codex._dispatch

    def delayed(sid):
        ready.set()
        assert release.wait(3)
        original(sid)

    codex._dispatch = delayed
    sid = codex.create_session()['id']
    codex.send(sid, 'must not reach native host', message_id='cancel-before-start')
    assert ready.wait(3)
    codex.cancel(sid)
    release.set()
    until(lambda: codex.chat.session(sid)['status'] == 'interrupted')
    assert codex.client is None
    # Emulate a process loss immediately after durable admission.
    message = codex.chat.message(sid, 'pending admission', mid='crash', runtime={'backend': 'codex', 'model': 'a'})
    assert dispatch.execution.admit(sid, message['id'])
    codex.chat.recover_stale()
    dispatch.recover()
    assert next(m for m in codex.snapshot(sid)['messages'] if m['id'] == 'crash')['status'] == 'interrupted'
    assert not codex.chat.session(sid)['busy']
