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
