"""One runtime identity for the CLI and web UI; never mutates an installation."""
import json
import os
from pathlib import Path
import re
import shlex
import subprocess
import sys
import threading
import time
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from . import __version__

PYPI = 'https://pypi.org/pypi/briefloop/json'
DOWNLOADS = 'https://briefloop.ai/downloads.html'
_lock = threading.Lock()
_cache = None


def runtime_info():
    """Report the code actually imported, not another Python's package metadata."""
    package = Path(__file__).resolve().parent
    checkout = package.parent.parent
    kind, build = 'pip', None
    if (checkout / 'pyproject.toml').is_file() and (checkout / '.git').exists():
        kind = 'source'
        try:
            build = subprocess.check_output(['git', '-C', str(checkout), 'describe', '--always', '--abbrev=12', '--dirty', '--exclude=*'],
                                            stdin=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=2).decode().strip()
        except (OSError, subprocess.SubprocessError):
            pass
    # Desktop and its CLI resolve the same active interpreter. Environment flags
    # alone are insufficient: check the manager's durable active pointer.
    prefix = Path(sys.prefix).resolve()
    try:
        active = json.loads((prefix.parent / 'active.json').read_text(encoding='utf-8'))
        if active['environmentId'] == prefix.name and active['version'] == __version__:
            kind, build = 'desktop', active.get('sha256', '')[:12] or None
    except (OSError, ValueError, KeyError, TypeError):
        pass
    if kind == 'pip' and ('pipx' in prefix.parts):
        kind = 'pipx'
    elif kind == 'pip' and '/uv/tools/' in prefix.as_posix():
        kind = 'uv'
    command = None
    if kind == 'pip':
        args = [sys.executable, '-m', 'pip', 'install', '--upgrade', 'briefloop']
        command = subprocess.list2cmdline(args) if os.name == 'nt' else shlex.join(args)
    elif kind == 'pipx':
        command = 'pipx upgrade briefloop'
    elif kind == 'uv':
        command = 'uv tool upgrade briefloop'
    guidance = {
        'desktop': '由桌面 App 管理更新；CLI 使用同一个运行环境，无需另行安装。',
        'source': '当前运行开发源码。更新源码并重启此服务；发布版本号相同也不代表代码相同。',
        'pip': '在启动此服务的终端执行下方命令，完成后重启服务。',
        'pipx': '使用 pipx 更新，完成后重启服务。',
        'uv': '使用 uv 更新，完成后重启服务。',
    }[kind]
    return {'version': __version__, 'installation': kind, 'build': build,
            'update_command': command, 'guidance': guidance, 'download_url': DOWNLOADS}


def check_update(info=None):
    """Explicit read-only stable PyPI check. Cache avoids repeated provider traffic."""
    global _cache
    info = info or runtime_info()
    with _lock:
        if _cache and time.monotonic() - _cache[0] < 300:
            result = dict(_cache[1])
        else:
            try:
                request = Request(PYPI, headers={'Accept': 'application/json', 'User-Agent': 'BriefLoop/' + __version__})
                with urlopen(request, timeout=15) as response:
                    raw = response.read(1024 * 1024 + 1)
                if len(raw) > 1024 * 1024:
                    raise ValueError('response too large')
                data = json.loads(raw)
                versions = [v for v, artifacts in data['releases'].items()
                            if re.fullmatch(r'\d+\.\d+\.\d+', v) and artifacts
                            and any(not a.get('yanked', False) for a in artifacts)]
                latest = max(versions, key=lambda v: tuple(map(int, v.split('.'))))
                result = {'releaseVersion': latest, 'source': 'pypi', 'error': None}
                _cache = (time.monotonic(), result)
            except HTTPError as exc:
                return {**info, 'state': 'error', 'error': {'code': 'http_' + str(exc.code),
                        'message': f'PyPI 返回 HTTP {exc.code}，版本检查未完成。'}, 'retryable': True}
            except (OSError, URLError, ValueError, KeyError, TypeError):
                return {**info, 'state': 'error', 'error': {'code': 'check_failed',
                        'message': '无法读取 PyPI 版本信息，请检查网络后重试。'}, 'retryable': True}
    installed = tuple(map(int, info['version'].split('.')))
    published = tuple(map(int, result['releaseVersion'].split('.')))
    return {**info, **result, 'state': 'available' if published > installed else 'ahead' if installed > published else 'current'}
