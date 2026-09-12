"""Commands shown to agent shells, pinned to this server's Python/package tree."""
import os
from pathlib import Path
import shlex
import shutil
import sys


def opencode_shell():
    """Select the Windows shell shared by OpenCode serve and task commands.

    OpenCode 1.18.30's tool is named `bash` but uses SHELL, then prefers pwsh
    and powershell before Git Bash. Pin the selected executable in the owned
    server environment instead of relying on the tool name or host defaults.
    """
    if os.name != 'nt':
        return None
    configured = os.environ.get('SHELL')
    if configured:
        selected = shutil.which(configured)
        if not selected:
            raise ValueError('配置的 OpenCode SHELL 不存在：' + configured)
        if Path(selected).stem.lower() not in ('pwsh', 'powershell', 'bash', 'sh', 'zsh'):
            raise ValueError('尚未支持此 OpenCode SHELL 的工具命令：' + selected)
        return selected
    for name in ('pwsh', 'powershell'):
        selected = shutil.which(name)
        if selected:
            return selected
    git_bash = os.environ.get('OPENCODE_GIT_BASH_PATH')
    if not git_bash:
        git = shutil.which('git')
        if git:
            git_bash = str(Path(git).parent.parent / 'bin' / 'bash.exe')
    if git_bash and Path(git_bash).is_file():
        return git_bash
    raise ValueError('OpenCode 工具需要可用的 PowerShell 或 Git Bash；请配置 SHELL')


def _powershell(backend):
    if os.name != 'nt':
        return False
    if backend == 'opencode':
        return Path(opencode_shell()).stem.lower() in ('pwsh', 'powershell')
    return backend == 'codex'


def quote_path(path, backend='codex'):
    text = str(path)
    if os.name == 'nt':
        text = text.replace('\\', '/')
    return _quote(text, backend)


def _quote(value, backend):
    text = str(value)
    if _powershell(backend):
        return "'" + text.replace("'", "''") + "'"
    return shlex.quote(text)


def agent_command(module, arguments=(), *, backend='codex'):
    if module not in ('briefloop', 'wikiskill'):
        raise ValueError('Unsupported agent tool module: ' + str(module))
    parts = [quote_path(sys.executable, backend), '-X', 'utf8',
             quote_path(Path(__file__).with_name('_entrypoint.py').resolve(), backend), module]
    parts.extend(_quote(value, backend) for value in arguments)
    return ('& ' if _powershell(backend) else '') + ' '.join(parts)


def tool_command(workspace, *, backend='codex'):
    command = agent_command('briefloop', ('tool', '--workspace'), backend=backend)
    return command + ' ' + quote_path(workspace, backend)
