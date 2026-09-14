"""The HTTP host must not inherit the desktop ownership input pipe."""
import os
from pathlib import Path
import subprocess
import sys


def test_http_host_receives_eof_while_owner_pipe_stays_open(tmp_path):
    script = r'''
import subprocess, sys
from briefloop import host_bins, agent_commands
from briefloop.backends import opencode_server as module
original = module.OwnedProcess
host_bins.find = lambda name: sys.executable
agent_commands.opencode_shell = lambda: None
def launch(args, **kwargs):
    kwargs['stdout'] = subprocess.PIPE
    return original([sys.executable, '-c',
        'import sys; print("EOF" if not sys.stdin.buffer.read(1) else "SHARED", flush=True)'], **kwargs)
module.OwnedProcess = launch
def ready(self):
    output, _ = self.process.communicate(timeout=3)
    assert output.strip() == b'EOF', output
    return '1.test'
module.OpencodeServerClient._wait_ready = ready
client = module.OpencodeServerClient(sys.argv[1])
client.close()
print('READY', flush=True)
'''
    root = Path(__file__).resolve().parents[1]
    process = subprocess.Popen(
        [sys.executable, '-c', script, str(tmp_path)],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        env={**os.environ, 'PYTHONPATH': str(root / 'src')},
    )
    try:
        # communicate() would close the owner pipe and hide the regression.
        process.wait(timeout=12)
        assert process.returncode == 0, process.stderr.read().decode(errors='replace')
        assert process.stdout.read().strip() == b'READY'
    finally:
        if process.poll() is None:
            process.kill()
            process.wait(timeout=5)
        process.stdin.close()
        process.stdout.close()
        process.stderr.close()
