"""Bounded fault injection: real pipe backpressure and blocked host admission."""
import json
import sys
import threading
import time
from pathlib import Path
import pytest
from briefloop.store import Store
from briefloop.runtime_bridge import RuntimeBridge
from briefloop.harness import HarnessManager
from briefloop.bridge_harness import BridgeHarness
from briefloop.chat_dispatch import ChatDispatcher
from test_harness import RPC, until
from test_bridge_harness import BridgeFixture


def test_bridge_send_deadline_includes_actual_pipe_write(tmp_path,monkeypatch):
    import briefloop.runtime_bridge as module
    original=module.OwnedProcess
    monkeypatch.setattr(module,'OwnedProcess',lambda args,**kw:original([sys.executable,'-c','import time;time.sleep(60)'],**kw))
    b=RuntimeBridge(node_binary=sys.executable);done=threading.Event();errors=[]
    def call():
        try:b.call('blocked',{'payload':'x'*2_000_000},timeout=.2)
        except Exception as e:errors.append(e)
        finally:done.set()
    worker=threading.Thread(target=call,daemon=True);worker.start()
    try:
        assert done.wait(1.5),'blocked pipe bypassed the request timeout'
        assert errors and isinstance(errors[0],(TimeoutError,RuntimeError))
        assert b._lock.acquire(blocking=False),'state lock held during pipe write'
        b._lock.release()
    finally:
        if b.process is not None:b.process.close_tree(timeout=.1)
        worker.join(2);b.close()


def test_blocked_codex_turn_does_not_lock_other_host_or_cancel(tmp_path):
    entered=threading.Event();release=threading.Event()
    class SlowRPC(RPC):
        def request(self,method,params):
            if method=='turn/start':entered.set();release.wait(3)
            return super().request(method,params)
    store=Store(tmp_path);codex=HarnessManager(store,SlowRPC);buddy=BridgeHarness(store,BridgeFixture(),'codebuddy')
    ChatDispatcher({'codex':codex,'codebuddy':buddy})
    sid=codex.create_session()['id'];other=buddy.create_session(runtime={'model':'default'})['id']
    codex.send(sid,'blocked',message_id='blocked')
    assert entered.wait(2)
    result=threading.Event()
    worker=threading.Thread(target=lambda:(buddy.send(other,'still usable'),result.set()),daemon=True);worker.start()
    try:
        assert result.wait(.7),'one host call blocked another host admission'
        before=time.monotonic();codex.cancel(sid);assert time.monotonic()-before<.5
    finally:
        release.set();worker.join(3);until(lambda:sid not in codex._busy);codex.close()
    assert sum(method=='turn/start' for method,_ in codex.client.calls)==1
    assert any(method=='turn/interrupt' for method,_ in codex.client.calls)


def test_codex_response_can_be_preceded_by_terminal_notification(tmp_path):
    class EarlyRPC(RPC):
        def request(self,method,params):
            result=super().request(method,params)
            if method=='turn/start':
                manager.handle_notification({'method':'turn/completed','params':{'threadId':params['threadId'],'turn':{'id':result['turn']['id'],'status':'completed'}}})
            return result
    manager=HarnessManager(Store(tmp_path),EarlyRPC)
    ChatDispatcher({'codex':manager})
    sid=manager.create_session()['id'];manager.send(sid,'complete immediately',message_id='early')
    until(lambda:sid not in manager._busy)
    snapshot=manager.snapshot(sid)
    assert snapshot['session']['status']=='idle'
    assert next(m for m in snapshot['messages'] if m['id']=='early')['status']=='completed'
    manager.close()


def test_opencode_owned_process_exit_is_terminal_even_without_timeout(tmp_path):
    from briefloop.opencode_harness import OpencodeHarness
    from test_opencode_harness import FakeClient
    from briefloop.platform_support import OwnedProcess
    from briefloop.backends.opencode_server import OpencodeError
    store=Store(tmp_path);store.set_meta('settings',{**store.settings(),'timeout_minutes':0})
    process=OwnedProcess([sys.executable,'-c','import time;time.sleep(60)'])
    class DeadClient(FakeClient):
        def __init__(self,*args):super().__init__(*args);self.process=process;self.mode='running';self.reads=0
        def messages(self,*args,**kwargs):
            self.reads+=1
            if self.reads==1:
                value=super().messages(*args,**kwargs)
                process.terminate();process.wait(timeout=2)
                return value
            raise OpencodeError('connection refused')
    manager=OpencodeHarness(store,DeadClient)
    sid=manager.create_session()['id'];manager.send(sid,'work',message_id='dead')
    try:
        until(lambda:manager.chat.session(sid)['status']=='failed')
        snapshot=manager.snapshot(sid)
        assert any(m['role']=='assistant' and 'hello done' in m['text'] for m in snapshot['messages'])
        assert len(manager.client.prompts)==1
        count=len(snapshot['events']);time.sleep(.3)
        assert len(manager.snapshot(sid)['events'])==count
    finally:process.close_tree(timeout=.2);manager.close()


def test_owned_group_eperm_requires_reap_and_successful_reprobe(monkeypatch):
    import briefloop.platform_support as module
    process=object.__new__(module.OwnedProcess);process.pid=12345
    calls=[]
    def killpg(pid,sig):
        calls.append((pid,sig))
        if len(calls)==1:raise PermissionError('zombie race')
        raise ProcessLookupError('reaped and no descendants')
    monkeypatch.setattr(module.os,'killpg',killpg)
    waits=[];process.wait=lambda timeout:waits.append(timeout)
    with pytest.raises(ProcessLookupError):process._signal_group(0,time.monotonic()+1)
    assert calls==[(12345,0),(12345,0)] and len(waits)==1
    monkeypatch.setattr(module.os,'killpg',lambda *args:(_ for _ in ()).throw(PermissionError('live inaccessible group')))
    with pytest.raises(PermissionError,match='live inaccessible'):process._signal_group(0,time.monotonic()+1)
    process._child_created=False


def test_app_server_write_and_close_are_bounded(tmp_path,monkeypatch):
    import briefloop.app_server as module
    from briefloop import host_bins
    original=module.OwnedProcess
    program='import sys,json,time;r=json.loads(sys.stdin.readline());print(json.dumps({"id":r["id"],"result":{}}),flush=True);time.sleep(60)'
    monkeypatch.setattr(host_bins,'find',lambda name:sys.executable)
    monkeypatch.setattr(module,'OwnedProcess',lambda args,**kw:original([sys.executable,'-c',program],**kw))
    client=module.AppServerClient(tmp_path/'rpc');done=threading.Event();errors=[]
    def call():
        try:client.request('blocked',{'payload':'x'*2_000_000},timeout=.2)
        except Exception as exc:errors.append(exc)
        finally:done.set()
    worker=threading.Thread(target=call,daemon=True);worker.start()
    try:
        assert done.wait(1.5)
        assert errors and isinstance(errors[0],(TimeoutError,RuntimeError))
        assert client._lock.acquire(blocking=False);client._lock.release()
        assert client.process.poll() is not None
        before=time.monotonic();client.close();assert time.monotonic()-before<1.5
    finally:client.process.close_tree(timeout=.2);worker.join(2);client.close()


def test_blocked_opencode_prompt_does_not_block_other_runtime(tmp_path):
    from briefloop.opencode_harness import OpencodeHarness
    from test_opencode_harness import FakeClient
    entered=threading.Event();release=threading.Event()
    class SlowClient(FakeClient):
        def prompt_async(self,*args,**kwargs):
            entered.set();release.wait(3);super().prompt_async(*args,**kwargs)
    store=Store(tmp_path);host=OpencodeHarness(store,SlowClient);buddy=BridgeHarness(store,BridgeFixture(),'codebuddy')
    ChatDispatcher({'opencode':host,'codebuddy':buddy})
    sid=host.create_session()['id'];other=buddy.create_session(runtime={'model':'default'})['id']
    host.send(sid,'blocked',message_id='one');assert entered.wait(2)
    done=threading.Event()
    worker=threading.Thread(target=lambda:(buddy.send(other,'usable'),done.set()),daemon=True);worker.start()
    try:
        assert done.wait(.7)
        before=time.monotonic();host.cancel(sid);assert time.monotonic()-before<.5
        host.send(sid,'after cancel',message_id='two')
        assert sid in host._cancel_requested
    finally:release.set();worker.join(3)
    until(lambda:sid not in host._busy)
    assert len(host.client.prompts)==1 and host.client.aborts==['ses_fake']
    assert next(m for m in host.snapshot(sid)['messages'] if m['id']=='two')['status']=='queued'
    host.close()
