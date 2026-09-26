"""Desktop lifecycle checks against a temporary loopback service; no models."""
import http.client
import json
import os
import socket
import threading
import time
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


def test_service_stop_cancels_inflight_mcp_before_natural_response(service, tmp_path, monkeypatch):
    import sys
    import time
    from pathlib import Path
    from concurrent.futures import ThreadPoolExecutor
    server, request = service
    directory = tmp_path/'mcp'; directory.mkdir()
    (directory/'document.txt').write_text('Synthetic source')
    connector = server.connectors.save({'name':'Delayed source','transport':'stdio',
        'command':sys.executable,'args':[str(Path(__file__).resolve().parents[1]/'probes/mcp_m0/server.py'),
        '--transport','stdio','--directory',str(directory)],'timeout_seconds':120,'max_response_bytes':65536})['id']
    assert server.connectors.enable(connector)['state']=='connected'
    run = server.store.create_run({'title':'Synthetic','objective':'Read selected data'}, [],
                                  connector_selection_validated=True)
    job = server.store.enqueue('generate', {'run_id':run['id']})
    server.connector_tasks.bind(job['id'], [{'connector_id':connector,'tools':['slow_read']}],
                                max_calls=1,max_total_bytes=65536)
    token = server.connector_tasks.access(job['id'])['access_token']
    def call():
        connection = http.client.HTTPConnection('127.0.0.1',server.server_port,timeout=10)
        try:
            connection.request('POST','/api/connectors/task-tool',json.dumps({'action':'call',
                'connector_id':connector,'name':'slow_read','arguments':{'token':'cancel-probe'},
                'request_id':'stop-probe'}),{'Authorization':'Bearer '+token})
            response=connection.getresponse()
            return json.loads(response.read())
        finally:connection.close()
    shutdown=threading.Event()
    with monkeypatch.context() as patch, ThreadPoolExecutor(1) as pool:
        patch.setattr(server,'shutdown',shutdown.set)
        future=pool.submit(call)
        log=directory/'server.jsonl'; deadline=time.monotonic()+8
        while not log.exists() or 'slow_started' not in log.read_text():
            assert time.monotonic()<deadline, 'MCP fixture did not start'
            time.sleep(.02)
        assert request('/api/service-stop',{'pid':os.getpid(),'workspace_id':server.store.meta('workspace_id'),
                                            'busy_action':'cancel'})[0]==200
        assert shutdown.wait(5), 'Stop waited for the 20-second natural MCP response'
        result=future.result(timeout=2)
    assert result.get('status')!='admitted'
    assert server.store.one('jobs',job['id'])['status']=='cancelled'
    assert 'slow_completed' not in log.read_text()
    assert not server.store.source_ids(run['id'])


def test_owned_service_stops_on_stdin_eof_but_standalone_does_not(tmp_path):
    import subprocess
    import sys
    import time
    from briefloop.platform_support import WorkspaceLock
    root=tmp_path/'owned'
    log_path=tmp_path/'service.log'
    env={**os.environ,'BRIEFLOOP_DESKTOP_OWNER_PIPE':'1','BRIEFLOOP_LAUNCH_ID':'synthetic-owner'}
    def launch(owned):
        child_env=dict(env)
        if not owned:child_env.pop('BRIEFLOOP_DESKTOP_OWNER_PIPE')
        executable=sys.executable
        if os.name=='nt':
            # Match the desktop launcher: bypass the venv redirector while
            # retaining its environment, so Popen owns the actual service PID.
            executable=sys._base_executable
            child_env['__PYVENV_LAUNCHER__']=sys.executable
        # Set UTF-8 on this child too; the parent's -X flag is not inherited and
        # a CLI UTF-8 re-exec would otherwise create another intermediary PID.
        with log_path.open('ab') as log:
            return subprocess.Popen([executable,'-I','-X','utf8','-u','-m','briefloop','serve','--workspace',str(root),
                                     '--port','0','--paused'],env=child_env,stdin=subprocess.PIPE,
                                    stdout=log,stderr=log,
                                    creationflags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0)
    def ready(process):
        deadline=time.monotonic()+10
        marker=root/'server.json'
        while not marker.exists() or json.loads(marker.read_text()).get('pid')!=process.pid:
            assert process.poll() is None and time.monotonic()<deadline, log_path.read_text(encoding='utf-8')
            time.sleep(.03)
    owned=launch(True)
    try:
        ready(owned);owned.stdin.close();assert owned.wait(timeout=8)==0
        lock=WorkspaceLock(root);lock.close()
        standalone=launch(False)
        try:
            ready(standalone);standalone.stdin.close()
            with pytest.raises(subprocess.TimeoutExpired):standalone.wait(timeout=.3)
        finally:
            standalone.terminate();standalone.wait(timeout=8)
    finally:
        if owned.poll() is None:owned.kill();owned.wait(timeout=5)


def test_shutdown_finishes_admitted_save_before_cancelling_jobs(service, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    server,request=service;store=server.store
    source=store.add_source('Synthetic','Original')
    run=store.create_run({'title':'Save','objective':'Synthetic'},[source['id']])
    brief=store.publish(run['id'],{'title':'Save','markdown':'Original'})
    job=store.enqueue('review',{'version_id':brief['id']})
    entered=threading.Event();release=threading.Event();shutdown=threading.Event()
    original=store.revise
    def delayed_save(*args,**kwargs):
        entered.set()
        assert release.wait(5)
        return original(*args,**kwargs)
    with monkeypatch.context() as patch, ThreadPoolExecutor(1) as pool:
        patch.setattr(store,'revise',delayed_save)
        patch.setattr(server,'shutdown',shutdown.set)
        future=pool.submit(request,'/api/save',{'base_version':brief['id'],'markdown':'Saved before exit'})
        try:
            assert entered.wait(2)
            assert request('/api/service-stop',{'pid':os.getpid(),'workspace_id':store.meta('workspace_id'),
                                                'busy_action':'cancel'})[0]==200
            assert not shutdown.is_set() and store.one('jobs',job['id'])['status']=='queued'
        finally:release.set()
        code,saved=future.result(timeout=3)
        assert code==200 and saved['markdown']=='Saved before exit'
        assert shutdown.wait(3)
    assert store.one('briefs',saved['id'])['markdown']=='Saved before exit'
    assert store.one('jobs',job['id'])['status']=='cancelled'


def test_shutdown_interrupts_partial_bodies_without_committing_them(service, monkeypatch):
    server,request=service;store=server.store
    token=request('/api/session')[1]['token']
    previous=store.settings()['max_reports']
    job=store.enqueue('review',{})
    stopped=threading.Event();shutdown=server.shutdown
    def observe_shutdown():
        shutdown();stopped.set()
    monkeypatch.setattr(server,'shutdown',observe_shutdown)
    connections=[]
    try:
        # Even a syntactically complete JSON prefix must not be applied when
        # the declared request body has not finished arriving.
        for path,body in [('/api/upload-file?name=partial.txt',b'x'),
                          ('/api/settings',json.dumps({'max_reports':2 if previous!=2 else 3}).encode())]:
            connection=socket.create_connection(('127.0.0.1',server.server_port),timeout=5)
            connections.append(connection)
            connection.sendall((f'POST {path} HTTP/1.1\r\nHost: 127.0.0.1:{server.server_port}\r\n'
                                f'X-BriefLoop-Token: {token}\r\nContent-Length: {len(body)+2}\r\n\r\n').encode()+body)
        deadline=time.monotonic()+3
        while server._active_posts!=2 and time.monotonic()<deadline:time.sleep(.01)
        assert server._active_posts==2
        assert request('/api/service-stop',{'pid':os.getpid(),'workspace_id':store.meta('workspace_id'),
                                            'busy_action':'cancel'})==(200,{'stopping':True})
        assert stopped.wait(3),'Stopping still waits for the clients to finish their bodies'
        for connection in connections:
            response=http.client.HTTPResponse(connection);response.begin()
            assert response.status==503 and json.loads(response.read())['code']=='service_draining'
        assert server._active_posts==0
        assert not store.rows('SELECT id FROM sources')
        assert store.settings()['max_reports']==previous
        assert store.one('jobs',job['id'])['status']=='cancelled'
    finally:
        for connection in connections:connection.close()


def test_body_idle_timeout_allows_progressing_uploads(service, monkeypatch):
    server,request=service
    server.worker.start()
    monkeypatch.setattr(server.RequestHandlerClass,'timeout',.5)
    token=request('/api/session')[1]['token']
    address=('127.0.0.1',server.server_port)
    def headers(path,size):
        return (f'POST {path} HTTP/1.1\r\nHost: 127.0.0.1:{server.server_port}\r\n'
                f'X-BriefLoop-Token: {token}\r\nContent-Length: {size}\r\n\r\n').encode()
    with socket.create_connection(address,timeout=5) as connection:
        connection.sendall(headers('/api/upload-file?name=stalled.txt',3)+b'x')
        response=http.client.HTTPResponse(connection);response.begin()
        assert response.status==408 and json.loads(response.read())['code']=='request_timeout'
    assert not server.store.rows('SELECT id FROM sources')
    previous=server.store.settings()['max_reports']
    body=json.dumps({'max_reports':2 if previous!=2 else 3}).encode()
    with socket.create_connection(address,timeout=5) as connection:
        connection.sendall(headers('/api/settings',len(body)+1)+body)
        connection.shutdown(socket.SHUT_WR)
        response=http.client.HTTPResponse(connection);response.begin()
        assert response.status==400 and json.loads(response.read())['code']=='incomplete_request'
    assert server.store.settings()['max_reports']==previous
    # Total upload time exceeds the idle limit, but each chunk makes progress.
    chunk=b'Synthetic evidence\n'*4096
    with socket.create_connection(address,timeout=5) as connection:
        connection.sendall(headers('/api/upload-file?name=complete.txt',len(chunk)*5))
        for index in range(5):
            if index:time.sleep(.2)
            connection.sendall(chunk)
        response=http.client.HTTPResponse(connection);response.begin()
        source=json.loads(response.read())
        assert response.status==202 and source['status']=='queued'
    deadline=time.monotonic()+5
    while time.monotonic()<deadline and server.store.one('sources',source['id'])['status'] not in ('ready','failed'):time.sleep(.05)
    assert server.store.one('sources',source['id'])['status']=='ready'
    assert server.store.source_text(source['id'])==(chunk*5).decode()
    assert request('/api/settings',{'max_reports':3})[0]==200
    server.worker.close()


def test_corrupt_connector_config_preserves_workspace_and_recovers_after_repair(tmp_path):
    from briefloop.store import Store
    from briefloop.connectors import ConnectorService, ConnectorError
    from briefloop.connectors.config import atomic_json
    root=tmp_path/'damaged'; store=Store(root)
    source=store.add_source('Keep','Original evidence')
    run=store.create_run({'title':'Keep','objective':'Read'},[source['id']])
    brief=store.publish(run['id'],{'title':'Keep','markdown':'Saved report'})
    original=ConnectorService(root)
    original.save({'name':'Saved connection','transport':'http','url':'http://127.0.0.1:9/mcp'})
    original.close()
    path=root/'.connectors/connections.json';valid=path.read_bytes()
    path.write_bytes(b'{"broken":')
    server=make_server(root,port=0,paused=True)
    try:
        assert server.store.one('briefs',brief['id'])['markdown']=='Saved report'
        with pytest.raises(ConnectorError,match='原文件已保留'):
            server.connectors.list()
        with pytest.raises(ConnectorError):
            server.connectors.save({'name':'Must not overwrite','transport':'http','url':'http://127.0.0.1:9/mcp'})
        assert path.read_bytes()==b'{"broken":'
        atomic_json(path,json.loads(valid))
        assert server.connectors.list()[0]['name']=='Saved connection'
    finally:
        server.harness.close();server.opencode_harness.close();server.runtime_bridge.close()
        server.server_close();server.workspace_lock.close()
