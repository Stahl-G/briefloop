"""Idle transport retirement must preserve in-flight RPCs and execution observers."""
import shutil
import threading
import time
import pytest
from briefloop.runtime_bridge import RuntimeBridge


def eventually(check, timeout=3):
    until = time.monotonic() + timeout
    while time.monotonic() < until:
        if check():
            return
        time.sleep(.01)
    assert check()


@pytest.fixture
def bridge(tmp_path, monkeypatch):
    node = shutil.which('node')
    if not node:
        pytest.skip('Node required for bridge lifecycle checks')
    (tmp_path/'static').mkdir()
    (tmp_path/'static/runtime-bridge.mjs').write_text('''
import {createInterface} from 'node:readline';
createInterface({input:process.stdin}).on('line',line=>{
 const m=JSON.parse(line),p=m.params;
 setTimeout(()=>{
  console.log(JSON.stringify({id:m.id,result:{pid:process.pid,session_id:p.session_id}}));
  if(m.method==='finish')console.log(JSON.stringify({method:'event',params:{kind:'end',execution_id:p.execution_id}}));
 },p.delay||0);
});
''')
    monkeypatch.setattr('briefloop.runtime_bridge.files', lambda _: tmp_path)
    b=RuntimeBridge(node_binary=node)
    b.IDLE_SECONDS=.08
    yield b
    b.close()


def test_idle_reclaims_and_restarts_without_losing_supplied_session(bridge):
    first=bridge.call('ping')
    process=bridge.process
    eventually(lambda: process.poll() is not None)
    assert bridge.process is None
    second=bridge.call('ping', {'session_id':'persisted-host-session'})
    assert second['pid'] != first['pid']
    assert second['session_id']=='persisted-host-session'


def test_pending_rpc_outlives_idle_grace(bridge):
    result=[]
    task=threading.Thread(target=lambda:result.append(bridge.call('ping',{'delay':250})))
    task.start()
    eventually(lambda: bool(bridge._pending))
    process=bridge.process
    time.sleep(.15)
    assert process.poll() is None
    task.join(2)
    assert result
    eventually(lambda: process.poll() is not None)


def test_active_execution_and_unconsumed_observer_hold_transport(bridge):
    bridge.call('start',{'execution_id':'turn'})
    process=bridge.process
    time.sleep(.15)
    assert process.poll() is None
    sink=bridge.subscribe('turn')
    bridge.call('finish',{'execution_id':'turn'})
    assert sink.get(timeout=1)['kind']=='end'
    time.sleep(.15)
    assert process.poll() is None
    bridge.unsubscribe('turn')
    eventually(lambda: process.poll() is not None)
