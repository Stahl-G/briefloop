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


def test_word_locked_destination_keeps_old_file_and_saved_revision(tmp_path):
    import ctypes as c
    from ctypes import wintypes as w
    import threading
    from briefloop.demo import create_demo
    from briefloop.store import Store
    from briefloop.export_jobs import enqueue_export, generate_word, output_path
    store=Store(tmp_path/'中文 report')
    demo=create_demo(store)
    job=enqueue_export(store,demo['version_id'])
    target=output_path(store,job);target.parent.mkdir(parents=True)
    target.write_bytes(b'previous document must survive')
    from briefloop.windows_process import api, close_handle
    handle=api('CreateFileW',[w.LPCWSTR,w.DWORD,w.DWORD,c.c_void_p,w.DWORD,w.DWORD,w.HANDLE],w.HANDLE)(
        str(target),0x80000000,1,None,3,0,None)
    assert handle!=c.c_void_p(-1).value
    try:
        result=generate_word(store,job,threading.Event())
        store.update_job(job['id'],'complete',result=result)
        assert target.read_bytes()==b'previous document must survive'
        from docx import Document
        saved=output_path(store,store.one('jobs',job['id']))
        assert saved!=target
        assert Document(saved).paragraphs
        assert store.one('briefs',demo['version_id'])['author']=='example'
        assert enqueue_export(store,demo['version_id'])['id']==job['id']
    finally:
        close_handle(handle)


def test_console_start_reports_actual_service_identity_and_shuts_down(tmp_path):
    from briefloop.workspaces import _request_shutdown, _read_api
    root=tmp_path/'中文 fresh workspace'
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
        assert len(_read_api(info['url'],'/api/state')['templates'])==36
    finally:
        if marker.exists():
            info=json.loads(marker.read_text(encoding='utf-8'))
            _request_shutdown(info['url'],info['pid'],info['workspace_id'])
            deadline=time.monotonic()+55
            while process_alive(info['pid']) and time.monotonic()<deadline:time.sleep(.1)
            assert not process_alive(info['pid'])
