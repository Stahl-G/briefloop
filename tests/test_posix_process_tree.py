"""Actual process-group ownership checks on Linux and macOS."""
import json
import os
import signal
import subprocess
import sys
import time

import pytest

from briefloop.platform_support import OwnedProcess


pytestmark = pytest.mark.skipif(os.name != 'posix', reason='POSIX process groups')


def _running(pid):
    # Orphaned children can briefly remain zombies before init reaps them.
    # A zombie cannot execute and is not an unclosed running descendant.
    result = subprocess.run(['ps', '-o', 'stat=', '-p', str(pid)],
                            capture_output=True, text=True, check=False)
    return bool(result.stdout.strip()) and not result.stdout.strip().startswith('Z')


@pytest.mark.parametrize('kill_owner', [True, False])
def test_owner_watch_reclaims_host_and_descendants(tmp_path, kill_owner):
    """SIGKILL requires kernel pipe EOF, not a Python atexit callback."""
    ready = tmp_path / 'ready.json'
    host_script = '''
import json, os, pathlib, signal, subprocess, sys, time
signal.signal(signal.SIGTERM, signal.SIG_IGN)
child = subprocess.Popen([sys.executable, '-c',
    'import signal,time;signal.signal(signal.SIGTERM,signal.SIG_IGN);time.sleep(90)'])
p = pathlib.Path(sys.argv[1]); q = p.with_suffix('.tmp')
q.write_text(json.dumps({'host':os.getpid(), 'child':child.pid})); q.replace(p)
time.sleep(90)
'''
    owner_script = '''
import signal,sys
from briefloop.platform_support import OwnedProcess
p = OwnedProcess([sys.executable, '-c', sys.argv[1], sys.argv[2]], parent_death=True)
signal.pause()
'''
    owner = subprocess.Popen([sys.executable, '-c', owner_script, host_script, str(ready)],
                             start_new_session=True)
    unrelated = subprocess.Popen([sys.executable, '-c', 'import time;time.sleep(90)'],
                                 start_new_session=True)
    state = None
    try:
        deadline = time.monotonic() + 10
        while not ready.exists() and time.monotonic() < deadline:
            assert owner.poll() is None
            time.sleep(.02)
        state = json.loads(ready.read_text())
        assert os.getpgid(state['child']) == state['host']
        if kill_owner:
            owner.kill(); owner.wait(timeout=5)
        else:
            os.kill(state['host'], signal.SIGKILL)
        deadline = time.monotonic() + 8
        while any(_running(pid) for pid in state.values()) and time.monotonic() < deadline:
            time.sleep(.05)
        assert not any(_running(pid) for pid in state.values())
        assert unrelated.poll() is None
    finally:
        if owner.poll() is None:
            owner.kill(); owner.wait(timeout=5)
        if state:
            try: os.killpg(state['host'], signal.SIGKILL)
            except ProcessLookupError: pass
        unrelated.terminate(); unrelated.wait(timeout=5)


def test_owner_watch_preserves_stdio_and_exit_status():
    host = OwnedProcess([sys.executable, '-c',
        'import sys;print(sys.stdin.read());sys.exit(7)'], parent_death=True,
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)
    try:
        output, _ = host.communicate('hello', timeout=5)
        assert output == 'hello\n'
        assert host.returncode == 7
    finally:
        host.close_tree(timeout=2)


def test_group_probe_waits_out_a_zombie_watcher_it_cannot_reap(monkeypatch):
    """Darwin: the watcher SIGKILLs itself and is launchd's zombie for a moment."""
    import briefloop.platform_support as platform_support
    host = OwnedProcess([sys.executable, '-c', 'pass'])
    host.wait(timeout=5)
    replies = [PermissionError(1, 'Operation not permitted')] * 3 + [ProcessLookupError()]
    calls = []
    def killpg(pid, sig):
        calls.append((pid, sig))
        raise replies[min(len(calls) - 1, len(replies) - 1)]
    monkeypatch.setattr(platform_support.os, 'killpg', killpg)
    host.close_tree(timeout=2)
    assert calls[-1] == (host.pid, 0) and len(calls) >= 4
    assert all(pid == host.pid for pid, _ in calls)


def test_group_that_keeps_denying_access_is_still_an_error(monkeypatch):
    import briefloop.platform_support as platform_support
    host = OwnedProcess([sys.executable, '-c', 'pass'])
    host.wait(timeout=5)
    def killpg(pid, sig):
        raise PermissionError(1, 'Operation not permitted')
    monkeypatch.setattr(platform_support.os, 'killpg', killpg)
    started = time.monotonic()
    with pytest.raises(PermissionError):
        host.close_tree(timeout=.1)
    assert time.monotonic() - started < 2


@pytest.mark.parametrize('leader_exits_first', [False, True])
def test_close_tree_kills_term_ignoring_child_after_leader_exit(tmp_path, leader_exits_first):
    ready = tmp_path / 'child-ready.json'
    child_script = (
        'import json,os,pathlib,signal,sys,time; '
        'signal.signal(signal.SIGTERM,signal.SIG_IGN); '
        'ready=pathlib.Path(sys.argv[1]); '
        'tmp=ready.with_suffix(".tmp"); '
        'tmp.write_text(json.dumps({"pid":os.getpid()})); tmp.replace(ready); '
        'time.sleep(90)'
    )
    parent_script = (
        'import pathlib,signal,subprocess,sys,time; '
        'signal.signal(signal.SIGTERM,lambda *args:sys.exit(0)); '
        'subprocess.Popen([sys.executable,"-c",sys.argv[1],sys.argv[2]]); '
        'time.sleep(90)'
    )
    unrelated = subprocess.Popen([sys.executable, '-c', 'import time;time.sleep(90)'],
                                 start_new_session=True)
    owned = None
    try:
        owned = OwnedProcess([sys.executable, '-c', parent_script, child_script, str(ready)])
        deadline = time.monotonic() + 10
        while not ready.exists() and time.monotonic() < deadline:
            assert owned.poll() is None
            time.sleep(.02)
        assert ready.exists(), 'Descendant did not finish installing its signal handler'
        child = json.loads(ready.read_text())['pid']
        assert os.getpgid(child) == owned.pid
        if leader_exits_first:
            owned.terminate()
            assert owned.wait(timeout=5) == 0
        owned.close_tree(timeout=.3)
        assert owned.returncode == 0
        deadline = time.monotonic() + 5
        while _running(child) and time.monotonic() < deadline:
            time.sleep(.02)
        assert not _running(child), 'SIGTERM-ignoring descendant survived close_tree'
        assert unrelated.poll() is None
        owned.close_tree(timeout=.3)
        assert unrelated.poll() is None
    finally:
        # Clean up only the session/group created above, even on a failed check.
        if owned is not None:
            try:
                os.killpg(owned.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            owned.wait(timeout=5)
        unrelated.terminate()
        unrelated.wait(timeout=5)
