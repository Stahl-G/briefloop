import hashlib
import json
from pathlib import Path
from briefloop import sources
from briefloop.store import Store


def test_source_metadata_round_trip_with_cp936_default(tmp_path,monkeypatch):
    import io
    from PIL import Image
    from briefloop.media import source_attachment,source_files
    # Exercise real CP936 I/O even on UTF-8 CI hosts. Explicit file encodings
    # still take precedence, exactly as on a Chinese Windows installation.
    original_open=Path.open
    def cp936_open(self,mode='r',buffering=-1,encoding=None,errors=None,newline=None):
        if 'b' not in mode and encoding in (None,'locale'):
            encoding='cp936'
        return original_open(self,mode,buffering,encoding,errors,newline)
    monkeypatch.setattr(Path,'open',cp936_open)
    store=Store(tmp_path)
    image=io.BytesIO();Image.new('RGB',(2,2),'white').save(image,format='PNG')
    uploaded=sources.upload(store,'中文图像.png',image.getvalue())
    attachment=source_attachment(store,uploaded['id'])
    assert attachment['image_path'] and attachment['width']==2
    raw='<html><title>中文材料</title><body>材料正文</body></html>'.encode('utf-8')
    monkeypatch.setattr(sources,'_fetch_bytes',lambda url,**_:(raw,'text/html; charset=utf-8','utf-8'))
    fetched=sources.fetch(store,'https://example.test/report')
    _,metadata,original=source_files(store,fetched['id'])
    assert metadata['title']=='中文材料' and original.read_bytes()==raw


def test_web_snapshot_preserves_response_and_failed_extraction(tmp_path,monkeypatch):
    store=Store(tmp_path)
    raw=b'<html><table><tr><th>Company</th><th>USD</th><th>MW</th></tr><tr><td>A</td><td>120</td><td>45</td></tr></table></html>'
    monkeypatch.setattr(sources,'_fetch_bytes',lambda url,**_:(raw,'text/html; charset=utf-8','utf-8'))
    record=sources.fetch(store,'https://example.test/report')
    assert record['status']=='ready'
    text=store.source_text(record['id'])
    assert 'A\t120\t45' in text
    metadata=json.loads((store.root/'sources'/(record['id']+'.provenance.json')).read_text(encoding='utf-8'))
    assert (store.root/metadata['original_path']).read_bytes()==raw
    assert metadata['raw_sha256']==hashlib.sha256(raw).hexdigest()
    assert metadata['text_sha256']==record['hash']
    assert metadata['url']==record['url']
    assert metadata['extractor']=='briefloop.sources.TextHTML (utf-8)'
    blank=b'<html><script>not body text</script></html>'
    monkeypatch.setattr(sources,'_fetch_bytes',lambda url,**_:(blank,'text/html','utf-8'))
    failed=sources.fetch(store,'https://example.test/blank')
    assert failed['status']=='failed' and failed['error']
    failure_meta=json.loads((store.root/'sources'/(failed['id']+'.provenance.json')).read_text(encoding='utf-8'))
    assert failure_meta['extraction_status']=='failed'
    assert (store.root/failure_meta['original_path']).read_bytes()==blank
    assert store.source_text(failed['id'])==''

def test_corrupt_docx_becomes_a_failed_source(tmp_path):
    import io,zipfile
    buffer=io.BytesIO()
    with zipfile.ZipFile(buffer,'w') as archive:archive.writestr('other.xml','x')
    store=Store(tmp_path)
    record=sources.upload(store,'broken.docx',buffer.getvalue())
    assert record['status']=='failed' and 'DOCX' in record['error']
    assert store.source_text(record['id'])==''


def test_run_url_reuse_and_bounded_source_reader(tmp_path,monkeypatch,capsys):
    from briefloop.cli import main
    from briefloop.scout_tools import read_source
    store=Store(tmp_path);calls=[]
    def response(url,**_):
        calls.append(url)
        if url.endswith('/retry') and calls.count(url)==1:return b'<html></html>','text/html','utf-8'
        return b'first\nsecond long line\nthird\nfourth\n','text/plain','utf-8'
    monkeypatch.setattr(sources,'_fetch_bytes',response)
    req={'title':'test','objective':'research','allow_web':True}
    run=store.create_run(req,[])['id']
    first=sources.fetch_for_run(store,run,'https://example.test/report')
    metadata=(store.root/'sources'/(first['id']+'.provenance.json')).read_text(encoding='utf-8')
    reused=sources.fetch_for_run(store,run,'https://example.test/report')
    assert reused['id']==first['id'] and reused['reused'] is True
    assert calls.count('https://example.test/report')==1
    assert (store.root/'sources'/(first['id']+'.provenance.json')).read_text(encoding='utf-8')==metadata
    other=store.create_run(req,[])['id']
    assert sources.fetch_for_run(store,other,'https://example.test/report')['id']!=first['id']
    assert calls.count('https://example.test/report')==2
    failure=sources.fetch_for_run(store,run,'https://example.test/retry')
    repaired=sources.fetch_for_run(store,run,'https://example.test/retry')
    assert failure['status']=='failed' and repaired['status']=='ready'
    assert store.one('sources',failure['id'])['status']=='failed'
    assert read_source(store,first['id'])==store.source_text(first['id'])
    # This is an in-process command behavior test; Windows console bootstrap
    # has separate subprocess coverage and must not re-exec the pytest runner.
    monkeypatch.setattr('briefloop.platform_support.ensure_utf8',lambda:None)
    monkeypatch.setattr('sys.argv',['briefloop','tool','--workspace',str(store.root),'read-source','--id',first['id'],'--start-line','2','--end-line','3','--max-chars','6'])
    main();output=capsys.readouterr().out
    assert '共 4 行' in output and '第 2–2 行' in output
    assert '2: second' in output and '末行仅显示部分字符' in output
    assert 'third' not in output


def test_html_title_is_used_as_a_readable_source_label():
    from briefloop.sources import html_title
    assert html_title(b'<html><head><title>  Hello   World </title></head>', 'text/html; charset=utf-8') == 'Hello World'
    assert html_title(b'<html><head></head></html>', 'text/html') == ''
    assert html_title(b'<title>x</title>', 'application/pdf') == ''
    assert html_title(b'<title>Just a moment...</title>', 'text/html') == ''
    assert html_title(('<title>'+'长'*300+'</title>').encode(), 'text/html') == '长'*200


def test_snapshot_run_includes_attached_sources_in_count(tmp_path):
    from briefloop.store import Store
    store=Store(tmp_path/'ws')
    run=store.create_run({'title':'r','objective':'o','allow_web':True},[])
    a=store.add_source('a','text');b=store.add_source('b','text')
    store.attach_source(run['id'],a['id']);store.attach_source(run['id'],b['id'])
    row=next(r for r in store.snapshot()['runs'] if r['id']==run['id'])
    assert row['source_count']==2 and set(row['all_source_ids'])=={a['id'],b['id']}


def test_source_fetch_refuses_this_machine_and_private_networks(tmp_path,monkeypatch):
    import socket
    store=Store(tmp_path)
    def no_spawn(*args,**kwargs):raise AssertionError('no request may be sent')
    monkeypatch.setattr(sources.subprocess,'run',no_spawn)
    monkeypatch.setattr(sources,'find_host_bin',lambda name:'curl')
    answers={'localhost':['127.0.0.1','::1'],'rebind.example':['93.184.215.14','127.0.0.1']}
    def resolve(host,port,*args,**kwargs):
        return [(socket.AF_INET6 if ':' in a else socket.AF_INET,socket.SOCK_STREAM,6,'',(a,port)) for a in answers.get(host,[host])]
    monkeypatch.setattr(sources.socket,'getaddrinfo',resolve)
    for url in ('http://127.0.0.1:8765/api/state','http://localhost:8765/api/session','http://[::1]/','http://[::ffff:127.0.0.1]/',
                'http://10.0.0.8/','http://192.168.1.1/','http://169.254.169.254/latest/meta-data','http://0.0.0.0/',
                'http://2130706433/','http://0177.0.0.1/','http://127.1/','http://rebind.example/'):
        record=sources.fetch(store,url)
        assert record['status']=='failed' and '拒绝读取' in record['error'],url
    # Proxy fake-IP DNS still reaches public sites; a user's own intranet page may
    # use private networks, but never this machine or cloud metadata.
    assert sources._public_target('https://198.18.0.7/')[2]==['198.18.0.7']
    assert sources._public_target('http://192.168.1.20/wiki',allow_private=True)[2]==['192.168.1.20']
    for url in ('http://127.0.0.1:8765/api/state','http://169.254.169.254/','http://rebind.example/'):
        try:sources._public_target(url,allow_private=True)
        except ValueError:continue
        raise AssertionError(url)


def test_source_fetch_pins_checked_address_and_checks_every_redirect(tmp_path,monkeypatch):
    import socket
    import urllib.request
    commands=[]
    def fake_curl(command,**kwargs):
        commands.append(command)
        Path(command[command.index('-o')+1]).write_bytes(b'moved')
        class Result:returncode=0;stderr='';stdout='302\nhttp://127.0.0.1:8765/api/state\ntext/html'
        return Result()
    monkeypatch.setattr(sources.subprocess,'run',fake_curl)
    monkeypatch.setattr(sources,'find_host_bin',lambda name:'curl')
    monkeypatch.setattr(sources.socket,'getaddrinfo',lambda host,port,*a,**k:[(socket.AF_INET,socket.SOCK_STREAM,6,'',('93.184.215.14' if host=='example.com' else host,port))])
    try:sources._fetch_bytes('https://example.com/report')
    except ValueError as exc:assert '拒绝读取' in str(exc)
    else:raise AssertionError('redirect to this machine must be refused')
    [command]=commands  # the redirect target is refused before a second request
    assert '--location' not in command and command[command.index('--resolve')+1]=='example.com:443:93.184.215.14'
    handler=sources._PublicRedirects()
    request=urllib.request.Request('https://example.com/report')
    try:handler.redirect_request(request,None,302,'Found',{},'http://127.0.0.1:8765/api/state')
    except ValueError:pass
    else:raise AssertionError('fallback reader must refuse the same redirect')


def test_fallback_reader_connects_only_to_the_address_it_checked(monkeypatch):
    import socket
    answers=iter(['93.184.215.14','127.0.0.1'])
    lookups=[];connections=[]
    def resolve(host,port,*args,**kwargs):
        address=next(answers,'127.0.0.1');lookups.append((host,address))
        return [(socket.AF_INET,socket.SOCK_STREAM,6,'',(address,port))]
    def connect(address,*args,**kwargs):
        connections.append(address);raise OSError('synthetic: no network in tests')
    monkeypatch.setattr(sources,'find_host_bin',lambda name:None)
    monkeypatch.setattr(sources.urllib.request,'getproxies',lambda:{})
    monkeypatch.setenv('no_proxy','*')
    monkeypatch.setattr(sources.socket,'getaddrinfo',resolve)
    monkeypatch.setattr(sources.socket,'create_connection',connect)
    # First answer is public, the second (at connect time) is loopback: nothing is connected.
    try:sources._fetch_bytes('http://rebind.example/report')
    except ValueError as exc:assert '拒绝读取' in str(exc)
    else:raise AssertionError('rebinding must be refused')
    assert [address for _,address in lookups]==['93.184.215.14','127.0.0.1'] and connections==[]
    answers=iter(['93.184.215.14','93.184.215.14'])
    try:sources._fetch_bytes('https://stable.example/report')
    except OSError:pass
    assert connections==[('93.184.215.14',443)]
