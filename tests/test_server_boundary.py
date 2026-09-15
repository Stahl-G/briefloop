import http.client
import json
import threading

from briefloop.server import make_server
from briefloop.sources import upload


def test_foreign_host_cannot_read_workspace_or_use_session_token(tmp_path):
    server = make_server(tmp_path / 'workspace', port=0, paused=True)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    authority = f'127.0.0.1:{server.server_port}'

    def request(path, *, host=authority, body=None, token=None):
        connection = http.client.HTTPConnection('127.0.0.1', server.server_port)
        headers = {'Host': host}
        if token:
            headers.update({'X-BriefLoop-Token': token, 'Origin': 'http://' + authority})
        connection.request('POST' if body is not None else 'GET', path,
                           body=json.dumps(body) if body is not None else None,
                           headers=headers)
        response = connection.getresponse()
        result = response.status, response.read()
        connection.close()
        return result

    try:
        source = upload(server.store, 'private-synthetic.txt', b'Synthetic private report source.')
        original = '/api/source-original?id=' + source['id']
        assert request(original) == (200, b'Synthetic private report source.')
        status, data = request('/api/session')
        assert status == 200
        token = json.loads(data)['token']
        for path in (original, '/api/state', '/api/session'):
            status, data = request(path, host=f'untrusted.example:{server.server_port}')
            assert status == 403, (path, status, data)
            assert b'Synthetic private report source.' not in data
            assert token.encode() not in data
        # Even possession of the token does not admit a foreign authority.
        assert request('/api/settings', host=f'untrusted.example:{server.server_port}',
                       body={'auto_learn': False}, token=token)[0] == 403
        assert request('/api/settings', body={'auto_learn': False}, token=token)[0] == 200
        assert request('/api/service-stop',body={'pid':0,'workspace_id':'wrong'},token=token)[0]!=200
        assert request('/api/state')[0]==200
        assert server.store.settings()['auto_learn'] is False
    finally:
        server.shutdown()
        thread.join()
        server.harness.close()
        server.opencode_harness.close()
        server.server_close()
        server.workspace_lock.close()


def test_private_get_origin_boundary_preserves_navigation_and_native_clients(tmp_path):
    from briefloop.server import _close_service
    server=make_server(tmp_path/'workspace',port=0,paused=True)
    thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    base=f'http://127.0.0.1:{server.server_port}'
    source=upload(server.store,'synthetic.txt',b'Synthetic private canary')
    original='/api/source-original?id='+source['id']
    def get(path,headers=()):
        conn=http.client.HTTPConnection('127.0.0.1',server.server_port)
        conn.putrequest('GET',path)
        for key,value in headers:conn.putheader(key,value)
        conn.endheaders();response=conn.getresponse();result=response.status,response.read();conn.close();return result
    try:
        # All private entry points reject before reading records, rendering or
        # disclosing the handshake. Keep this contract uniform across downloads.
        private=['/api/session','/api/state','/api/task-progress?job=unused',original,'/api/source-image?id=unused','/api/figure?id=unused',
                 '/api/export-file?job=unused','/api/release-file?id=unused','/api/audit-file?job=unused',
                 '/api/download?version=unused','/api/research-notes?version=unused']
        for path in private:
            status,data=get(path,[('Origin','https://untrusted.example')])
            assert status==403 and json.loads(data)['code']=='cross_origin_read_denied'
        for headers in [[('Origin','null')],[('Sec-Fetch-Site','cross-site')],[('Sec-Fetch-Site','same-site')],
                        [('Origin',base),('Origin','https://untrusted.example')],
                        [('Sec-Fetch-Site','same-origin'),('Sec-Fetch-Site','cross-site')]]:
            assert get(original,headers)[0]==403
        for headers in [[],[('Origin',base),('Sec-Fetch-Site','same-origin')],[('Sec-Fetch-Site','none')]]:
            assert get(original,headers)==(200,b'Synthetic private canary')
        for path in ['/','/index.html','/app.js','/style.css']:
            assert get(path,[('Origin','https://untrusted.example'),('Sec-Fetch-Site','cross-site')])[0]==200
    finally:server.shutdown();thread.join();_close_service(server)


def test_upload_limits_are_disclosed_and_enforced_before_source_creation(tmp_path,monkeypatch):
    import base64
    from briefloop import server as module
    monkeypatch.setattr(module,'MAX_UPLOAD_BYTES',3)
    server=make_server(tmp_path/'workspace',port=0,paused=True)
    thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    def request(body=None,length=None):
        conn=http.client.HTTPConnection('127.0.0.1',server.server_port)
        headers={} if body is None else {'X-BriefLoop-Token':token}
        if length is not None:headers['Content-Length']=str(length)
        conn.request('GET' if body is None else 'POST','/api/session' if body is None else '/api/upload',body,headers)
        r=conn.getresponse();value=(r.status,json.loads(r.read()));conn.close();return value
    try:
        status,session=request();token=session['token']
        assert session['upload_limits']=={'max_file_bytes':3,'max_request_bytes':module.MAX_REQUEST_BYTES,'max_pdf_bytes':module.MAX_PDF_UPLOAD_BYTES}
        status,error=request(json.dumps({'name':'large.txt','data':base64.b64encode(b'abcd').decode()}))
        assert status==400 and 'large.txt' in error['error']
        assert not server.store.rows('SELECT * FROM sources')
        assert request(json.dumps({'name':'ok.txt','data':base64.b64encode(b'abc').decode()}))[0]==200
        status,error=request('',length=module.MAX_REQUEST_BYTES)
        assert status==413 and error['code']=='request_too_large'
    finally:server.shutdown();thread.join();module._close_service(server)


def test_raw_source_upload_allows_larger_pdfs_and_rejects_before_creating_sources(tmp_path,monkeypatch):
    import io,socket
    from urllib.parse import quote
    from pypdf import PdfWriter
    from briefloop import server as module
    writer=PdfWriter();writer.add_blank_page(width=200,height=100);pdf=io.BytesIO();writer.write(pdf);pdf=pdf.getvalue()
    monkeypatch.setattr(module,'MAX_UPLOAD_BYTES',3)
    monkeypatch.setattr(module,'MAX_PDF_UPLOAD_BYTES',len(pdf))
    server=make_server(tmp_path/'workspace',port=0,paused=True)
    thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    def post(name,body,headers=None):
        conn=http.client.HTTPConnection('127.0.0.1',server.server_port)
        conn.request('POST','/api/upload-file?name='+quote(name),body,{'X-BriefLoop-Token':token,'Content-Type':'application/octet-stream',**(headers or {})})
        r=conn.getresponse();value=(r.status,json.loads(r.read()));conn.close();return value
    try:
        conn=http.client.HTTPConnection('127.0.0.1',server.server_port);conn.request('GET','/api/session')
        token=json.loads(conn.getresponse().read())['token'];conn.close()
        assert post('big.txt',b'abcd')[0]==413
        assert post('big.pdf',pdf+b'x')[0]==413
        assert post('',b'abc')[0]==400
        assert post('ok.pdf',pdf,{'X-BriefLoop-Token':'stale'})[0]==403
        # A body shorter than its declared length is not admitted as a source.
        raw=socket.create_connection(('127.0.0.1',server.server_port))
        raw.sendall(f'POST /api/upload-file?name=cut.pdf HTTP/1.1\r\nHost: 127.0.0.1:{server.server_port}\r\nX-BriefLoop-Token: {token}\r\nContent-Length: {len(pdf)}\r\n\r\n'.encode()+pdf[:10])
        raw.shutdown(socket.SHUT_WR);assert b' 400 ' in raw.recv(4096).split(b'\r\n',1)[0]+b' ';raw.close()
        assert not server.store.rows('SELECT * FROM sources')
        status,source=post('../年报.pdf',pdf)
        assert status==200 and source['name']=='年报.pdf' and source['status']=='ready'
        assert post('ok.txt',b'abc')[1]['status']=='ready'
    finally:server.shutdown();thread.join();module._close_service(server)
