"""Execute the commands agents receive, from a separate Unicode workspace."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

import pytest

from briefloop import agent_commands


def _shell(kind):
    if os.name == 'nt' and kind == 'powershell':
        shell = shutil.which('pwsh') or shutil.which('powershell')
        if not shell:
            pytest.skip('PowerShell unavailable')
        return [shell, '-NoProfile', '-NonInteractive', '-Command']
    if os.name == 'nt':
        # Do not accidentally select the unrelated Windows WSL bash launcher.
        candidates = [Path(os.environ.get('ProgramFiles', 'C:/Program Files')) / 'Git/bin/bash.exe',
                      Path(os.environ.get('LOCALAPPDATA', '')) / 'Programs/Git/bin/bash.exe']
        shell = next((str(path) for path in candidates if path.is_file()), None)
    else:
        shell = shutil.which('bash') or shutil.which('sh')
    if not shell:
        pytest.skip('Bash unavailable')
    return [shell, '-c']


def _execute(command, shell, cwd):
    env = {key: value for key, value in os.environ.items() if key not in ('PYTHONPATH', 'PYTHONHOME')}
    env['PYTHONDONTWRITEBYTECODE'] = '1'
    result = subprocess.run([*shell, command], cwd=cwd, env=env,
                            capture_output=True, encoding='utf-8', timeout=30)
    assert result.returncode == 0, result.stderr + result.stdout
    return result.stdout


SHELL_CASES = [('codex', 'powershell'), ('opencode', 'powershell'), ('opencode', 'bash')]


@pytest.mark.parametrize('backend,kind', SHELL_CASES)
def test_agent_tool_uses_server_package_outside_repository(tmp_path, monkeypatch, backend, kind):
    shell = _shell(kind)
    monkeypatch.setenv('SHELL', shell[0])
    workspace = tmp_path / "中文 workspace's $literal"
    workspace.mkdir()
    # A module in the agent's cwd must not replace the server's implementation.
    (workspace / 'briefloop.py').write_text("raise RuntimeError('wrong checkout')", encoding='utf-8')
    request = workspace / "请求's $literal.json"
    request.write_text(json.dumps({'action': 'capabilities'}), encoding='utf-8')
    command = (agent_commands.tool_command(workspace, backend=backend)
               + ' workspace-action --request ' + agent_commands.quote_path(request, backend))
    output = json.loads(_execute(command, shell, workspace))
    assert 'export_word' in output['actions']
    assert '当前执行此命令' in output['authority']


@pytest.mark.parametrize('backend,kind', SHELL_CASES)
def test_wikiskill_agent_entrypoint_outside_repository(tmp_path, monkeypatch, backend, kind):
    shell = _shell(kind)
    monkeypatch.setenv('SHELL', shell[0])
    (tmp_path / 'wikiskill.py').write_text("raise RuntimeError('wrong checkout')", encoding='utf-8')
    command = agent_commands.agent_command('wikiskill', ['--version'], backend=backend)
    assert _execute(command, shell, tmp_path).startswith('WikiSkill ')


def test_entrypoint_works_without_an_installed_briefloop(tmp_path):
    """Probe resolution with a tiny package and -S, excluding pip installations."""
    package = tmp_path / 'installation' / 'briefloop'
    package.mkdir(parents=True)
    entrypoint = package / '_entrypoint.py'
    shutil.copyfile(Path(agent_commands.__file__).with_name('_entrypoint.py'), entrypoint)
    (package / '__init__.py').write_text('', encoding='utf-8')
    (package / '__main__.py').write_text('print("正确工具")', encoding='utf-8')
    cwd = tmp_path / 'other workspace'
    cwd.mkdir()
    result = subprocess.run([sys.executable, '-X', 'utf8', '-S', str(entrypoint), 'briefloop'],
                            cwd=cwd, capture_output=True, encoding='utf-8', timeout=10)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == '正确工具'


def test_entrypoint_rejects_unexpected_modules():
    with pytest.raises(ValueError, match='Unsupported'):
        agent_commands.agent_command('arbitrary_module')


@pytest.mark.parametrize('backend,kind', SHELL_CASES)
def test_joined_scout_json_is_utf8_independent_of_shell(tmp_path, monkeypatch, backend, kind):
    shell = _shell(kind)
    monkeypatch.setenv('SHELL', shell[0])
    workspace = tmp_path / "资料与输出's"
    workspace.mkdir()
    scout = workspace / 'scout.json'
    scout.write_text(json.dumps({'sources': [], 'search_summary': '中文材料核对'}, ensure_ascii=False),
                     encoding='utf-8')
    joined = workspace / '合并结果.json'
    command = (agent_commands.tool_command(workspace, backend=backend)
               + ' join-scouts --files ' + agent_commands.quote_path(scout, backend)
               + ' --output ' + agent_commands.quote_path(joined, backend))
    output = _execute(command, shell, workspace)
    result = json.loads(joined.read_bytes().decode('utf-8'))
    assert result['search_summary'] == '中文材料核对'
    assert json.loads(output) == result
    assert not joined.with_name(joined.name + '.tmp').exists()


@pytest.mark.skipif(os.name != 'nt', reason='Windows OpenCode shell selection')
def test_opencode_falls_back_to_detected_powershell(monkeypatch):
    monkeypatch.delenv('SHELL', raising=False)
    selected = agent_commands.opencode_shell()
    assert Path(selected).stem.lower() in ('pwsh', 'powershell')
    assert agent_commands.agent_command('briefloop', backend='opencode').startswith('& ')


@pytest.mark.skipif(os.name != 'nt', reason='Windows OpenCode shell selection')
@pytest.mark.parametrize('configured', ['missing-custom-shell.exe', 'cmd.exe'])
def test_opencode_rejects_unknown_or_missing_configured_shell(monkeypatch, configured):
    monkeypatch.setenv('SHELL', configured)
    with pytest.raises(ValueError, match='SHELL'):
        agent_commands.opencode_shell()


@pytest.mark.skipif(os.name != 'nt', reason='Windows OpenCode shell selection')
@pytest.mark.parametrize('kind', ['powershell', 'bash'])
def test_opencode_owned_server_gets_same_shell_as_prompt(tmp_path, monkeypatch, kind):
    from briefloop.backends import opencode_server
    from briefloop import host_bins
    shell = _shell(kind)
    monkeypatch.setenv('SHELL', shell[0])
    selected = agent_commands.opencode_shell()
    captured = {}
    class Process:
        def __init__(self, args, **kwargs):
            captured.update(kwargs)
        def close_tree(self, **kwargs):
            pass
    monkeypatch.setattr(host_bins, 'find', lambda name: 'test-opencode.exe')
    monkeypatch.setattr(opencode_server, 'OwnedProcess', Process)
    monkeypatch.setattr(opencode_server.OpencodeServerClient, '_wait_ready', lambda self: '1.18.30')
    client = opencode_server.OpencodeServerClient(tmp_path, port=4096)
    try:
        assert captured['env']['SHELL'] == selected == client.shell
        command = agent_commands.agent_command('briefloop', ['--version'], backend='opencode')
        assert _execute(command, shell, tmp_path).startswith('BriefLoop ')
    finally:
        client.close()
