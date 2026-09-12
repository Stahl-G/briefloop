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
