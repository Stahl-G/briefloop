"""A forced Windows MCP supervisor exit must reap only its owned command tree."""
import json
import os
from pathlib import Path
import subprocess
import sys
import time

import pytest

from briefloop.platform_support import process_alive


@pytest.mark.skipif(os.name != 'nt', reason='Windows MCP Job lifetime')
def test_mcp_supervisor_forced_exit_reaps_descendants_and_preserves_unrelated_process(tmp_path):
    from briefloop.connectors import stdio_supervisor

    root = tmp_path / '中文 MCP workspace'
    root.mkdir()
    marker = root / 'process.json'
    descendant = root / 'child.json'
    script = ('import json,pathlib,subprocess,sys,time; '
              'p=subprocess.Popen([sys.executable,"-c","import time; time.sleep(90)"]); '
              'target=pathlib.Path(sys.argv[1]); temporary=target.with_suffix(".tmp"); '
              'temporary.write_text(json.dumps({"pid":p.pid})); temporary.replace(target); time.sleep(90)')
    unrelated = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(90)'])
    supervisor = subprocess.Popen([sys.executable, '-I', '-X', 'utf8', str(Path(stdio_supervisor.__file__)),
                                   '--marker', str(marker), '--limit', '65536', '--',
                                   sys.executable, '-c', script, str(descendant)],
                                  cwd=root, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    try:
        deadline = time.monotonic() + 10
        while not descendant.exists():
            assert supervisor.poll() is None, supervisor.stderr.read().decode('utf-8')
            assert time.monotonic() < deadline
            time.sleep(.02)
        child = json.loads(descendant.read_text(encoding='utf-8'))['pid']
        command = json.loads(marker.read_text(encoding='utf-8'))['child_pid']
        assert process_alive(command) and process_alive(child)
        supervisor.terminate()
        supervisor.wait(timeout=5)
        deadline = time.monotonic() + 5
        while (process_alive(command) or process_alive(child)) and time.monotonic() < deadline:
            time.sleep(.02)
        assert not process_alive(command) and not process_alive(child)
        assert unrelated.poll() is None
    finally:
        if supervisor.poll() is None:
            supervisor.terminate()
        supervisor.wait(timeout=5)
        supervisor.stdin.close()
        supervisor.stderr.close()
        unrelated.terminate()
        unrelated.wait(timeout=5)
