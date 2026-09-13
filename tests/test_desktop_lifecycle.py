"""Desktop lifecycle checks against a temporary loopback service; no models."""
import http.client
import json
import os
import threading
from types import SimpleNamespace

import pytest

from briefloop.server import make_server, _close_service


@pytest.fixture
def service(tmp_path):
    server=make_server(tmp_path/'workspace',port=0,paused=True)
    thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    def request(path,body=None):
        connection=http.client.HTTPConnection('127.0.0.1',server.server_port,timeout=5)
        headers={'Content-Type':'application/json'}
        if body is not None:
            headers['X-BriefLoop-Token']=request('/api/session')[1]['token']
        connection.request('POST' if body is not None else 'GET',path,
                           json.dumps(body) if body is not None else None,headers)
        response=connection.getresponse();result=response.status,json.loads(response.read())
        connection.close();return result
    try:yield server,request
    finally:
        server.shutdown();thread.join(timeout=5)
        server.harness.close();server.opencode_harness.close();server.runtime_bridge.close()
        server.server_close();server.workspace_lock.close()


def test_service_status_counts_old_jobs_and_busy_conversations_without_truncation(service):
    server,request=service;store=server.store;chat=server.harness.chat
    old=store.enqueue('learn',{})
    review=store.enqueue('review',{})
    store.update_job(review['id'],'running')
    for _ in range(35):
        job=store.enqueue('review',{});store.update_job(job['id'],'complete')
    queued=chat.create('Queued conversation',{'backend':'opencode'},store.root)
    chat.message(queued['id'],'synthetic queued message')
    question=chat.create('Pending question',{'backend':'codex'},store.root)
    chat.add_request(question['id'],1,{'question':'synthetic'})
    internal=chat.create('Internal session',{'backend':'claude'},store.root)
    chat.event(internal['id'],'session/internal',{})
    chat.update(internal['id'],status='starting')
    code,status=request('/api/service-status')
    assert code==200 and status['busy'] and not status['draining']
    assert status['pid']==os.getpid() and status['workspace_id']==store.meta('workspace_id')
    assert {job['id'] for job in status['jobs']}=={old['id'],review['id']}
    assert {row['id'] for row in status['sessions']}=={queued['id'],question['id'],internal['id']}
    identity={'pid':status['pid'],'workspace_id':status['workspace_id']}
    assert request('/api/service-stop',{**identity,'pid':0})[0]!=200
    code,result=request('/api/service-stop',identity)
    assert code==409 and result['code']=='service_busy' and not result['draining']
    assert store.one('jobs',old['id'])['status']=='queued'
    assert not server.worker.stopping.is_set()


def test_explicit_cancel_drains_and_preserves_completed_history(service,monkeypatch):
    server,request=service;store=server.store;chat=server.harness.chat
    queued=store.enqueue('learn',{})
    running=store.enqueue('review',{});store.update_job(running['id'],'running')
    complete=store.enqueue('review',{});store.update_job(complete['id'],'complete',result={'saved':True})
    old_complete=store.one('jobs',complete['id'])
    session=chat.create('Queued conversation',{'backend':'codex'},store.root)
    message=chat.message(session['id'],'synthetic queued message')
    shutdown=threading.Event()
    with monkeypatch.context() as patch:
        patch.setattr(server,'shutdown',shutdown.set)
        identity={'pid':os.getpid(),'workspace_id':store.meta('workspace_id')}
        assert request('/api/service-stop',{**identity,'busy_action':'cancel'})==(200,{'stopping':True})
        assert shutdown.wait(3)
        assert request('/api/service-status')[1]['draining']
        assert request('/api/learn',{})[0]==503
        # Necessary cancellation remains admitted while new work is rejected.
        assert request('/api/stop',{'job_id':queued['id']})[0]==200
        assert request('/api/harness/cancel',{'session_id':session['id']})[0]==200
        assert request('/api/service-stop',identity)==(200,{'stopping':True})
    assert store.one('jobs',queued['id'])['status']=='cancelled'
    assert store.one('jobs',running['id'])['status']=='cancelled'
    assert store.one('jobs',complete['id'])==old_complete
    assert next(row for row in chat.snapshot(session['id'])['messages'] if row['id']==message['id'])['status']=='cancelled'


def test_cleanup_attempts_every_owner_and_releases_socket_and_lock_after_failures():
    seen=[]
    def closer(name,fail=False):
        def close():
            seen.append(name)
            if fail:raise RuntimeError('synthetic private diagnostic')
        return SimpleNamespace(close=close)
    server=SimpleNamespace(bridge_harnesses={'synthetic':closer('bridge',True)},
                           worker=closer('worker',True),harness=closer('harness'),
                           opencode_harness=closer('opencode'),runtime_bridge=closer('runtime_bridge'),
                           server_close=closer('socket').close,workspace_lock=closer('lock'))
    errors=_close_service(server)
    assert seen==['bridge','worker','harness','opencode','runtime_bridge','socket','lock']
    assert errors==[{'component':'bridge:synthetic','error_type':'RuntimeError'},
                    {'component':'worker','error_type':'RuntimeError'}]
    assert 'synthetic private diagnostic' not in json.dumps(errors)


def test_cancel_failure_is_recorded_and_running_job_remains_recoverable(service,monkeypatch):
    server,request=service;store=server.store
    job=store.enqueue('review',{});store.update_job(job['id'],'running')
    shutdown=threading.Event()
    def fail_cancel(_):raise RuntimeError('PRIVATE synthetic transport failure')
    with monkeypatch.context() as patch:
        patch.setattr(server,'shutdown',shutdown.set)
        patch.setattr(server.worker,'stop_job',fail_cancel)
        code,_=request('/api/service-stop',{'pid':os.getpid(),'workspace_id':store.meta('workspace_id'),
                                           'busy_action':'cancel'})
        assert code==200 and shutdown.wait(3)
    assert server.shutdown_errors==[{'component':'job_cancel','error_type':'RuntimeError'}]
    assert 'PRIVATE' not in json.dumps(server.shutdown_errors)
    assert store.one('jobs',job['id'])['status']=='running'
    # The existing startup recovery preserves this unfinished attempt. The stop
    # event remains set, so these threads exit without claiming any model work.
    server.worker.start()
    server.worker.close()
    assert store.one('jobs',job['id'])['status']=='interrupted'
    assert store.one('jobs',job['id'])['result'] is None
