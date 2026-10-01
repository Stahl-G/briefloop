"""Native behavioral checks; synthetic children never invoke a model."""
import json
import os
from pathlib import Path
import subprocess
import sys
import time

import pytest

from briefloop.platform_support import OwnedProcess, WorkspaceLock, cli_command, process_alive

pytestmark = pytest.mark.skipif(os.name != 'nt', reason='Windows native behavior')


def test_filesystem_path_reads_existing_files_and_preserves_windows_path_forms(tmp_path, monkeypatch):
    from briefloop.platform_support import filesystem_path
    from briefloop.native_roles import _atomic
    monkeypatch.chdir(tmp_path)
    original = Path('中文 existing.json')
    original.write_text('legacy content', encoding='utf-8')
    extended = filesystem_path(original)
    assert str(extended) == '\\\\?\\' + str(original.absolute())
    assert extended.read_text(encoding='utf-8') == 'legacy content'
    assert filesystem_path(extended) == extended
    assert str(filesystem_path(r'\\server\share\writer')) == r'\\?\UNC\server\share\writer'
    _atomic(extended, 'replacement')
    assert original.read_text(encoding='utf-8') == 'replacement'
    assert not original.with_name(original.name + '.tmp').exists()


def test_lock_is_exclusive_before_database_initialization(tmp_path):
    root = tmp_path / '中文 workspace'
    lock = WorkspaceLock(root)
    try:
        result = subprocess.run([sys.executable, '-X', 'utf8', '-c',
            'from briefloop.server import make_server; import sys; make_server(sys.argv[1], 0)', str(root)],
            capture_output=True, text=True, encoding='utf-8')
        assert result.returncode != 0
        assert '工作区锁' in result.stderr
        assert not (root / 'briefloop.db').exists()
    finally:
        lock.close()
    WorkspaceLock(root).close()


def test_owned_tree_cleanup_preserves_unrelated_process(tmp_path):
    independent = subprocess.Popen([sys.executable, '-c', 'import time;time.sleep(90)'])
    script = "import subprocess,sys,time; p=subprocess.Popen([sys.executable,'-c','import time;time.sleep(90)']); print(p.pid,flush=True); time.sleep(90)"
    owned = OwnedProcess([sys.executable, '-c', script], stdout=subprocess.PIPE, text=True)
    try:
        child_pid = int(owned.stdout.readline())
        assert process_alive(owned.pid) and process_alive(child_pid)
        owned.close_tree()
        deadline = time.monotonic() + 5
        while process_alive(child_pid) and time.monotonic() < deadline:
            time.sleep(.05)
        assert not process_alive(child_pid)
        assert independent.poll() is None
    finally:
        owned.close_tree()
        independent.terminate()
        independent.wait()


def test_npm_command_preserves_unicode_spaces_and_shell_metacharacters(tmp_path):
    import shutil
    if not shutil.which('node'):
        pytest.skip('Node is required for npm shims')
    folder = tmp_path / '中文 npm space'; folder.mkdir()
    shim = folder / 'demo.cmd'; shim.write_text('@echo off', encoding='utf-8')
    shim.with_suffix('').write_text('exec node "$basedir/entry.js" "$@"', encoding='utf-8')
    (folder / 'entry.js').write_text('console.log(JSON.stringify(process.argv.slice(2)))', encoding='utf-8')
    args = ['中文 材料', '&echo bad', '%PATH%', 'a"b', 'x|y']
    process = OwnedProcess([str(shim), *args], stdout=subprocess.PIPE, text=True)
    try:
        stdout, _ = process.communicate(timeout=5)
        assert process.returncode == 0
        assert json.loads(stdout) == args
    finally:
        process.close_tree()
    shim.with_suffix('').unlink()
    with pytest.raises(ValueError):
        cli_command([str(shim)])


def test_process_host_death_reaps_only_its_execution(tmp_path):
    from briefloop import process_host
    script = "import subprocess,sys,time; p=subprocess.Popen([sys.executable,'-c','import time;time.sleep(90)']); print(p.pid,flush=True); time.sleep(90)"
    helper = subprocess.Popen([sys.executable, '-X', 'utf8', process_host.__file__, sys.executable, '-c', script],
                              stdout=subprocess.PIPE, text=True, encoding='utf-8')
    try:
        descendant = int(helper.stdout.readline())
        helper.terminate(); helper.wait(timeout=5)
        deadline = time.monotonic() + 5
        while process_alive(descendant) and time.monotonic() < deadline:
            time.sleep(.05)
        assert not process_alive(descendant)
    finally:
        if helper.poll() is None:
            helper.terminate(); helper.wait()


@pytest.mark.parametrize('long_workspace', [False, True])
def test_word_locked_destination_keeps_old_file_and_saved_revision(tmp_path, long_workspace):
    import ctypes as c
    from ctypes import wintypes as w
    import threading
    from briefloop.store import Store
    from briefloop.export_jobs import enqueue_export, generate_word, output_path
    from briefloop.platform_support import filesystem_path
    root=tmp_path/'中文 report'
    if long_workspace:
        root=tmp_path/('中文 report-'+'w'*(220-len(str(tmp_path))-1-len('中文 report-')))
    store=Store(root)
    source=store.add_source('合成来源','本周两项交付。')
    run=store.create_run({'title':'合成验收','objective':'核对保存的内容'},[source['id']])
    brief=store.publish(run['id'],{'title':'合成验收','markdown':'# 合成验收\n\n本周两项交付。'},author='example')
    job=enqueue_export(store,brief['id'])
    target=output_path(store,job)
    filesystem_path(target.parent).mkdir(parents=True)
    filesystem_path(target).write_bytes(b'previous document must survive')
    from briefloop.windows_process import api, close_handle
    handle=api('CreateFileW',[w.LPCWSTR,w.DWORD,w.DWORD,c.c_void_p,w.DWORD,w.DWORD,w.HANDLE],w.HANDLE)(
        str(filesystem_path(target)),0x80000000,1,None,3,0,None)
    assert handle!=c.c_void_p(-1).value
    try:
        result=generate_word(store,job,threading.Event())
        store.update_job(job['id'],'complete',result=result)
        assert filesystem_path(target).read_bytes()==b'previous document must survive'
        from docx import Document
        saved=output_path(store,store.one('jobs',job['id']))
        assert saved!=target
        from io import BytesIO
        assert Document(BytesIO(filesystem_path(saved).read_bytes())).paragraphs
        assert saved.is_relative_to(store.root) and not str(saved).startswith('\\\\?\\')
        if long_workspace:assert len(str(saved))>260
        assert store.one('briefs',brief['id'])['author']=='example'
        assert enqueue_export(store,brief['id'])['id']==job['id']
    finally:
        close_handle(handle)


def test_long_word_artifact_is_cached_queryable_and_downloadable(tmp_path):
    import hashlib
    import http.client
    import threading
    from briefloop import export_jobs
    from briefloop.external_requests import dispatch
    from briefloop.platform_support import filesystem_path
    from briefloop.server import make_server
    from briefloop.store import Store
    root=tmp_path/('中文工作区-'+'w'*(220-len(str(tmp_path))-1-len('中文工作区-')))
    store=Store(root)
    source=store.add_source('合成来源','本周两项交付。')
    run=store.create_run({'title':'合成验收','objective':'核对保存的内容'},[source['id']])
    brief=store.publish(run['id'],{'title':'合成验收','markdown':'# 合成验收\n\n本周两项交付。'},author='example')
    job=export_jobs.enqueue_export(store,brief['id'])
    result=export_jobs.generate_word(store,job,threading.Event())
    store.update_job(job['id'],'complete',result=result)
    path=export_jobs.output_path(store,store.one('jobs',job['id']))
    assert len(str(path))>260 and path.is_relative_to(root)
    blob=filesystem_path(path).read_bytes()
    assert export_jobs.enqueue_export(store,brief['id'])['id']==job['id']
    query={'workspace_id':store.meta('workspace_id'),'action':'query','job_id':job['id']}
    assert dispatch(store,query)['artifact_available'] is True
    server=make_server(root,port=0,paused=True)
    thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    connection=http.client.HTTPConnection('127.0.0.1',server.server_port,timeout=10)
    try:
        connection.request('GET','/api/export-file?job='+job['id'])
        response=connection.getresponse()
        assert response.status==200
        assert response.read()==blob and hashlib.sha256(blob).hexdigest()==result['sha256']
        filesystem_path(path).write_bytes(b'damaged artifact')
        connection.request('GET','/api/export-file?job='+job['id'])
        response=connection.getresponse();response.read()
        assert response.status==400
        assert dispatch(store,query)['artifact_available'] is False
        assert export_jobs.enqueue_export(store,brief['id'])['id']!=job['id']
        assert store.one('briefs',brief['id'])['hash']==brief['hash']
    finally:
        connection.close();server.shutdown();thread.join();server.server_close()


def test_unicode_upload_and_original_survive_workspace_reopen(tmp_path):
    from briefloop.sources import upload
    from briefloop.store import Store

    root = tmp_path / '中文 空格 workspace 😀'
    name = '季度材料 中文 😀.txt'
    content = '青禾团队 English\n¥1,234.50；μm；㎡；—\n'
    store = Store(root)
    source = upload(store, name, content.encode('utf-8'))
    assert source['status'] == 'ready'

    reopened = Store(root)
    assert reopened.one('sources', source['id'])['name'] == name
    assert reopened.source_text(source['id']) == content
    provenance = json.loads((root / 'sources' / (source['id'] + '.provenance.json')).read_text(encoding='utf-8'))
    assert (root / provenance['original_path']).read_bytes() == content.encode('utf-8')
    index = root / 'wiki' / 'index.md'
    index.parent.mkdir(parents=True, exist_ok=True)
    index.write_text('中文 Wiki；μm；😀', encoding='utf-8')
    assert reopened.snapshot()['wiki'] == '中文 Wiki；μm；😀'


def test_long_source_uploads_extract_reopen_and_preserve_integrity_guards(tmp_path):
    import hashlib
    from io import BytesIO
    import _winapi
    import threading
    from PIL import Image
    from pypdf import PdfWriter
    from briefloop import media, sources
    from briefloop.platform_support import filesystem_path
    from briefloop.source_extraction import extract_job
    from briefloop.source_ingestion import receive_upload
    from briefloop.store import Store, SourceTooLarge
    root=tmp_path/('中文来源-'+'w'*(224-len(str(tmp_path))-1-len('中文来源-')))
    store=Store(root)
    text='仅用于合成验收。\n中文 😀；UTF-8 second line.\n'
    direct=sources.upload(store,'中文原件.txt',text.encode('utf-8'))
    assert Store(root).source_text(direct['id'])==text
    image=BytesIO();Image.new('RGB',(8,4),(90,140,180)).save(image,format='PNG')
    pdf=BytesIO();writer=PdfWriter();writer.add_blank_page(width=200,height=100);writer.write(pdf)
    originals=[]
    for name,data in (('中文材料.txt',text.encode('utf-8')),('图表.png',image.getvalue()),('扫描.pdf',pdf.getvalue())):
        source=receive_upload(store,name,len(data),lambda sink:sink.write(data))
        assert source['status']=='queued'
        jid=source['extraction_job_id'];store.update_job(jid,'running')
        assert extract_job(store,store.one('jobs',jid),threading.Event())=={'source_id':source['id']}
        reopened=Store(root)
        record,metadata,original=media.source_files(reopened,source['id'])
        assert record['status']=='ready' and store.one('jobs',jid)['status']=='complete'
        assert len(str(original))>260 and filesystem_path(original).read_bytes()==data
        assert hashlib.sha256(data).hexdigest()==metadata['raw_sha256']
        assert not str(original).startswith('\\\\?\\') and not metadata['original_path'].startswith('\\\\?\\')
        assert len(str(root/record['path']))>260
        attachment=media.source_attachment(reopened,source['id'])
        assert attachment['status']=='ready'
        if name.endswith('.txt'):
            assert reopened.source_text(source['id'],max_bytes=1000)==text
            with pytest.raises(SourceTooLarge):reopened.source_text(source['id'],max_bytes=1)
        elif name.endswith('.png'):
            assert attachment['needs_visual'] and len(attachment['image_path'])>260
            assert Image.open(filesystem_path(attachment['image_path'])).size==(8,4)
        else:
            page=media.render_source_pages(reopened,source['id'],[1])['pages'][0]
            assert len(page['path'])>260 and filesystem_path(page['path']).is_file()
            assert media.rendered_page_path(reopened,source['id'],1)==Path(page['path'])
        originals.append((record,metadata,original,data))
    record,metadata,original,data=originals[0]
    filesystem_path(original).write_bytes(b'changed original')
    with pytest.raises(ValueError,match='哈希不匹配'):media.source_files(store,record['id'])
    filesystem_path(original).write_bytes(data)
    sidecar=root/'sources'/(record['id']+'.provenance.json')
    filesystem_path(sidecar).write_text(json.dumps({**metadata,'original_path':'sources/../outside.txt'}),encoding='utf-8')
    with pytest.raises(ValueError,match='越界'):media.source_files(store,record['id'])
    target=tmp_path/'unrelated';target.mkdir();marker=target/'keep.txt';marker.write_bytes(b'keep unrelated bytes')
    junction=root/'sources'/'escape';_winapi.CreateJunction(str(target),str(junction))
    try:
        with pytest.raises(ValueError,match='越界'):media.safe_source_path(store,'sources/escape/keep.txt')
        assert marker.read_bytes()==b'keep unrelated bytes'
    finally:filesystem_path(junction).rmdir()


def test_long_source_disconnect_recovery_retry_and_cancel_keep_originals(tmp_path,monkeypatch):
    import hashlib
    from briefloop import media
    from briefloop.platform_support import filesystem_path
    from briefloop.source_ingestion import receive_upload,recover_uploads,retry_upload
    from briefloop.store import Store
    root=tmp_path/('中文恢复-'+'w'*(224-len(str(tmp_path))-1-len('中文恢复-')))
    store=Store(root)
    def disconnected(sink):sink.write(b'partial');raise ConnectionError('synthetic disconnect')
    with pytest.raises(ConnectionError):receive_upload(store,'中断.txt',100,disconnected)
    assert not store.rows('SELECT * FROM sources')
    assert not list(filesystem_path(root/'sources').glob('*.upload.*'))
    data='完整合成原件。\n'.encode('utf-8')
    def interrupted(*args):raise RuntimeError('synthetic admission interruption')
    with monkeypatch.context() as patch:
        patch.setattr(store,'accept_source_upload',interrupted)
        with pytest.raises(RuntimeError):receive_upload(store,'恢复.txt',len(data),lambda sink:sink.write(data))
    assert len(list(filesystem_path(root/'sources').glob('*.upload.json')))==1
    recover_uploads(store);recover_uploads(store)
    source=store.rows('SELECT * FROM sources')[0];job=store.rows("SELECT * FROM jobs WHERE kind='source_extract'")[0]
    assert len(store.rows('SELECT * FROM sources'))==1 and not list(filesystem_path(root/'sources').glob('*.upload.*'))
    assert store.finish_source_extraction(source['id'],job['id'],'',None,status='cancelled',error='Synthetic cancellation')
    assert not store.finish_source_extraction(source['id'],job['id'],'late text',{},status='ready')
    assert store.source_text(source['id'])==''
    retried=retry_upload(store,source['id'])
    assert retried['id']!=source['id'] and store.one('sources',source['id'])['status']=='cancelled'
    for sid in (source['id'],retried['id']):
        _,metadata,original=media.source_files(Store(root),sid)
        assert filesystem_path(original).read_bytes()==data and metadata['raw_sha256']==hashlib.sha256(data).hexdigest()
    assert len(list(filesystem_path(root/'sources').glob('*.original.txt')))==2


@pytest.mark.parametrize('long_workspace', [False, True])
def test_console_start_reports_actual_service_identity_and_shuts_down(tmp_path, long_workspace):
    from briefloop.workspaces import _request_shutdown, _read_api
    from briefloop.templates import BUILTIN_TEMPLATES
    root=tmp_path/'中文 fresh workspace'
    if long_workspace:root=tmp_path/('中文 fresh-'+'w'*(224-len(str(tmp_path))-1-len('中文 fresh-')))
    marker=root/'server.json'
    entry=Path(sys.executable).with_name('briefloop.exe')
    assert entry.is_file(), 'Install briefloop before running the native startup check'
    try:
        result=subprocess.run([str(entry),'start','--workspace',str(root),'--port','0','--paused'],
                              cwd=tmp_path,capture_output=True,text=True,encoding='utf-8',timeout=60,
                              env={**os.environ,'PYTHONUTF8':'0'})
        assert result.returncode==0,(result.stdout,result.stderr)
        info=json.loads(marker.read_text(encoding='utf-8'))
        assert len(info['launch_id'])==32
        assert int((root/'server.pid').read_text())==info['pid']
        assert _read_api(info['url'],'/api/runtime')['server_pid']==info['pid']
        templates=_read_api(info['url'],'/api/state')['templates']
        expected_names={label for _,_,label in BUILTIN_TEMPLATES}
        assert len(templates)==len(BUILTIN_TEMPLATES)
        assert {row['name'] for row in templates}==expected_names
        english_names={label for filename,_,label in BUILTIN_TEMPLATES if '-en-' in filename}
        assert english_names
        assert {row['name'] for row in templates if row['language_hint']=='en'}==english_names
        if long_workspace:
            import hashlib
            from io import BytesIO
            from docx import Document
            from briefloop.document_model import brief_document
            from briefloop.platform_support import filesystem_path
            from briefloop.store import Store
            from briefloop.templates import export_template, rebuild_template_version
            store=Store(root)
            selected=next(row for row in templates if row['name']=='通用报告·品牌黛蓝')
            original=root/'templates'/selected['id']/'original.docx'
            original_bytes=filesystem_path(original).read_bytes()
            assert len(str(original))>260
            assert hashlib.sha256(original_bytes).hexdigest()==selected['source_hash']
            rebuilt=rebuild_template_version(store,selected['id'])
            source=store.add_source('Synthetic material','Two deliveries this week.')
            run=store.create_run({'title':'长路径合成验收','objective':'确定性模板导出','template_id':rebuilt['id']},[source['id']])
            brief=store.publish(run['id'],{'title':'长路径合成验收','markdown':'# 长路径合成验收\n\nTwo deliveries this week.'},author='example')
            blob=export_template(store,brief,brief_document(brief),{})
            assert 'Two deliveries this week.' in '\n'.join(p.text for p in Document(BytesIO(blob)).paragraphs)
            assert filesystem_path(original).read_bytes()==original_bytes
            assert store.one('briefs',brief['id'])['author']=='example'
            prepared=root/'templates'/rebuilt['id']/'prepared.docx'
            filesystem_path(prepared).write_bytes(b'tampered synthetic template')
            with pytest.raises(ValueError,match='底稿已变化'):
                export_template(store,brief,brief_document(brief),{})
            filesystem_path(original).write_bytes(b'tampered synthetic original')
            with pytest.raises(ValueError,match='原件已变化'):
                rebuild_template_version(store,selected['id'])
        # A valid session token alone must not stop a different service identity.
        for pid, workspace_id in ((0, info['workspace_id']), (info['pid'], 'wrong-workspace')):
            with pytest.raises(OSError, match='拒绝停止'):
                _request_shutdown(info['url'], pid, workspace_id)
            assert process_alive(info['pid'])
            assert _read_api(info['url'], '/api/runtime')['server_pid'] == info['pid']
    finally:
        if marker.exists():
            info=json.loads(marker.read_text(encoding='utf-8'))
            _request_shutdown(info['url'],info['pid'],info['workspace_id'])
            deadline=time.monotonic()+55
            while process_alive(info['pid']) and time.monotonic()<deadline:time.sleep(.1)
            assert not process_alive(info['pid'])
