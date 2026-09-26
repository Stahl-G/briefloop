"""Service children must not inherit the desktop ownership input pipe."""
import ast
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
module.executable_version = lambda executable, environment=None: '1.18.31'
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


def _run_with_owner_pipe(script, *args):
    root = Path(__file__).resolve().parents[1]
    process = subprocess.Popen(
        [sys.executable, '-c', script, *args],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        env={**os.environ, 'PYTHONPATH': str(root / 'src')},
    )
    try:
        # Keep the owner pipe open for the whole run, as the desktop does.
        process.wait(timeout=30)
        assert process.returncode == 0, process.stderr.read().decode(errors='replace')
        return process.stdout.read().decode().strip()
    finally:
        if process.poll() is None:
            process.kill()
            process.wait(timeout=5)
        process.stdin.close()
        process.stdout.close()
        process.stderr.close()


def test_source_reads_and_runtime_probe_do_not_share_owner_pipe(tmp_path):
    script = r'''
import io, json, socket, subprocess, sys
from pypdf import PdfWriter
from briefloop import host_bins, sources
from briefloop.runtime_bridge import RuntimeBridge

original = subprocess.run
class Probed(Exception):
    pass
def probe(args, **kwargs):
    # Keep the site's stdin choice; replace only the external program.
    try:
        output = original([sys.executable, '-c', 'import sys; print("EOF" if not sys.stdin.buffer.read(1) else "DATA")'],
                          stdin=kwargs.get('stdin'), capture_output=True, timeout=5).stdout.decode().strip()
    except subprocess.TimeoutExpired:
        output = 'SHARED'
    raise Probed(output)
subprocess.run = probe

def observe(call):
    try:
        call()
    except Probed as exc:
        return str(exc)
    return 'NOT_SPAWNED'

results = {}
# Deterministic public DNS: address checks before curl must pass without a network.
socket.getaddrinfo = lambda host, port, *args, **kwargs: [(socket.AF_INET, socket.SOCK_STREAM, 6, '', ('93.184.215.14', port))]
sources.find_host_bin = lambda name: name
results['curl'] = observe(lambda: sources._fetch_bytes('https://example.invalid/'))
pdf = io.BytesIO(); writer = PdfWriter(); writer.add_blank_page(72, 72); writer.write(pdf)
results['pdftotext'] = observe(lambda: sources.extract('source.pdf', pdf.getvalue()))
bridge = RuntimeBridge.__new__(RuntimeBridge)
def unavailable(*args, **kwargs):
    raise RuntimeError('bridge unavailable')
bridge.call = unavailable
host_bins.find = lambda name: sys.executable
results['version_probe'] = observe(bridge.discover)
print(json.dumps(results))
'''
    import json
    results = json.loads(_run_with_owner_pipe(script))
    assert results == {'curl': 'EOF', 'pdftotext': 'EOF', 'version_probe': 'EOF'}


def test_every_service_spawn_chooses_stdin_explicitly():
    """#718/#734: an inherited stdin can be the desktop owner pipe."""
    spawners = {('subprocess', name) for name in ('run', 'Popen', 'call', 'check_call', 'check_output')}
    spawners |= {('asyncio', 'create_subprocess_exec'), ('asyncio', 'create_subprocess_shell')}
    missing = []
    source = Path(__file__).resolve().parents[1] / 'src' / 'briefloop'
    for path in sorted(source.rglob('*.py')):
        for node in ast.walk(ast.parse(path.read_text(encoding='utf-8'))):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            spawn = (isinstance(func, ast.Name) and func.id == 'OwnedProcess') or (
                isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name) and (func.value.id, func.attr) in spawners)
            if spawn and not any(keyword.arg in ('stdin', None) for keyword in node.keywords):
                missing.append(f'{path.relative_to(source)}:{node.lineno}')
    assert not missing, 'pass stdin explicitly (usually subprocess.DEVNULL): ' + ', '.join(missing)
