"""Bounded local workspace discovery and verified service reuse.

Opening a workspace starts its existing local service lifecycle; this module
never enqueues a job or sends an agent message. It may stop a workspace's own
service on explicit user request, after verifying the pid over HTTP.
"""
from contextlib import closing
from pathlib import Path
import http.client
import json
import os
import signal
import sqlite3
import subprocess
import sys
import tempfile
import time
from urllib.parse import urlsplit
from .store import Store, dump

REGISTRY = '.briefloop-workspaces.json'
# Must exceed the service shutdown budget: Worker.close() joins three threads
# with a 12s timeout each (~36s) before the harnesses and lock are released.
STOP_GRACE_SECONDS = 60
STOP_KILL_GRACE_SECONDS = 5
_LOOPBACK = ('127.0.0.1', 'localhost', '::1')


def _recent(parent):
    try:
        value = json.loads((parent / REGISTRY).read_text())
        return [Path(p).expanduser().resolve() for p in value.get('recent', [])[:20]
                if isinstance(p, str) and Path(p).expanduser().is_absolute()]
    except (OSError, ValueError, TypeError, AttributeError):
        return []


def _workspace_id(root):
    """Listing is read-only; it must not initialize every discovered directory."""
    try:
        with closing(sqlite3.connect((root / 'briefloop.db').as_uri() + '?mode=ro', uri=True)) as connection:
            row = connection.execute("SELECT value FROM meta WHERE key='workspace_id'").fetchone()
            return json.loads(row[0]) if row else None
    except (OSError, ValueError, sqlite3.Error):
        return None


def _validated_info(root, workspace_id):
    """Read and validate server.json once so callers share one validated pid/url."""
    try:
        info = json.loads((root / 'server.json').read_text())
        pid = info['pid']; url = info['url'].rstrip('/')
        parsed = urlsplit(url)
        if (not isinstance(pid, int) or isinstance(pid, bool) or pid <= 0
                or parsed.scheme != 'http' or parsed.hostname not in _LOOPBACK
                or not parsed.port or parsed.username or parsed.password
                or parsed.path or parsed.query or parsed.fragment):
            return None
        if info.get('workspace_id') not in (None, workspace_id):
            return None
        return {'pid': pid, 'url': url}
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        return None


def _alive(pid):
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except (OverflowError, ValueError):
        return False
    except OSError:
        # PermissionError still means the process exists.
        return True


def _running(root, workspace_id):
    """Liveness check for listing; confirm the pid still serves this workspace."""
    info = _validated_info(root, workspace_id)
    if not info or not _alive(info['pid']):
        return False
    try:
        return _read_api(info['url'], '/api/runtime').get('server_pid') == info['pid']
    except (OSError, ValueError, KeyError, TypeError, AttributeError, http.client.HTTPException):
        return False


def _entry(root, current):
    workspace_id = _workspace_id(root)
    return {'name': root.name, 'path': str(root), 'workspace_id': workspace_id,
            'current': root == current, 'running': _running(root, workspace_id)}


def list_workspaces(store):
    current = store.root.resolve()
    candidates = [current, *_recent(current.parent)]
    try:
        candidates.extend(sorted((p.resolve() for p in current.parent.iterdir()
                                  if p.is_dir() and (p / 'briefloop.db').is_file()), key=lambda p: p.name.lower()))
    except OSError:
        pass
    seen = set()
    entries = []
    for root in candidates:
        if root in seen or not root.is_dir() or not (root / 'briefloop.db').is_file():
            continue
        seen.add(root)
        entries.append(_entry(root, current))
    return {'current': _entry(current, current), 'workspaces': entries}


def _remember(current, target):
    # Register both ends so navigation back works even across parent directories.
    for parent in {current.parent, target.parent}:
        temporary = None
        try:
            paths = list(dict.fromkeys([target, current, *_recent(parent)]))[:20]
            with tempfile.NamedTemporaryFile('w', encoding='utf-8', dir=parent,
                                             prefix=REGISTRY + '.', delete=False) as output:
                temporary = Path(output.name)
                output.write(dump({'recent': [str(p) for p in paths]}))
            temporary.replace(parent / REGISTRY)
        except OSError:
            # A read-only parent must not prevent opening a writable workspace.
            if temporary:
                temporary.unlink(missing_ok=True)


def _read_api(url, path):
    parsed = urlsplit(url)
    connection = http.client.HTTPConnection(parsed.hostname, parsed.port, timeout=1.5)
    try:
        connection.request('GET', path)
        response = connection.getresponse()
        if response.status != 200:
            raise OSError('Workspace API did not respond successfully')
        body = response.read(4 * 1024 * 1024 + 1)
        if len(body) > 4 * 1024 * 1024:
            raise ValueError('Workspace API response is too large')
        return json.loads(body)
    finally:
        connection.close()


def _active_server(root, workspace_id):
    info = _validated_info(root, workspace_id)
    if not info:
        return None
    pid = info['pid']; url = info['url']
    try:
        if not _alive(pid):
            return None
        actual = _read_api(url, '/api/runtime')
        if actual.get('server_pid') != pid:
            return None
        try:
            current = _read_api(url, '/api/workspaces')['current']
            same = Path(current['path']).resolve() == root and current.get('workspace_id') == workspace_id
        except (OSError, ValueError, KeyError, TypeError, http.client.HTTPException):
            # Older services expose the durable identity through /api/state.
            same = _read_api(url, '/api/state').get('workspace_id') == workspace_id
        if same:
            return {'url': url, 'path': str(root), 'workspace_id': workspace_id, 'reused': True,
                    'paused': bool(actual.get('paused', False)), 'pid': pid}
    except (OSError, ValueError, KeyError, TypeError, AttributeError, http.client.HTTPException):
        pass
    return None


def open_workspace(store, path, create=False):
    if not isinstance(path, str) or not path.strip():
        raise ValueError('请输入工作区目录')
    root = Path(path.strip()).expanduser()
    if not root.is_absolute():
        root = store.root.parent / root
    root = root.resolve()
    if not root.exists():
        if not create:
            raise ValueError('工作区目录不存在；创建新工作区时请启用创建')
        root.mkdir(parents=True, exist_ok=True)
    if not root.is_dir():
        raise ValueError('工作区必须是目录')
    target = Store(root)  # Existing empty directories may be initialized explicitly.
    workspace_id = target.meta('workspace_id')
    active = _active_server(root, workspace_id)
    if active:
        _remember(store.root, root)
        return active
    command = [sys.executable, '-m', 'briefloop', 'start', '--workspace', str(root), '--port', '0', '--paused']
    detail = ''
    try:
        launched = subprocess.run(command, capture_output=True, text=True, timeout=15)
        detail = (launched.stderr or launched.stdout or '')[-600:]
    except subprocess.TimeoutExpired:
        # The detached child may still become ready; do not kill that service.
        detail = '启动入口等待超时'
    deadline = time.monotonic() + 3
    while True:
        active = _active_server(root, workspace_id)
        if active:
            _remember(store.root, root)
            return {**active, 'reused': False}
        if time.monotonic() >= deadline:
            break
        time.sleep(.1)
    raise RuntimeError('工作区服务尚未就绪，已保留原服务与任务。日志：' + str(root / 'server.log')
                       + ('；' + detail if detail else ''))


def stop_workspace(store, path):
    """Stop one workspace's own local service after verifying it over HTTP.

    The current workspace is refused: stopping the service that serves this
    request would drop the page. Switch away first, then stop it.
    """
    if not isinstance(path, str) or not path.strip():
        raise ValueError('请输入工作区目录')
    root = Path(path.strip()).expanduser()
    if not root.is_absolute():
        root = store.root.parent / root
    root = root.resolve()
    if root == store.root.resolve():
        raise ValueError('不能停止当前正在使用的工作区；请先切换到其他工作区')
    workspace_id = _workspace_id(root)
    active = _active_server(root, workspace_id)
    if not active:
        return {'stopped': False, 'path': str(root), 'message': '该工作区没有在运行的服务。'}
    pid = active['pid']  # Validated once by _active_server; no second server.json read.
    try:
        os.kill(pid, signal.SIGTERM)
    except ProcessLookupError:
        pass
    except OSError:
        return {'stopped': False, 'path': str(root), 'pid': pid,
                'message': f'无法向服务进程 {pid} 发送停止信号，已保留服务状态文件。'}
    deadline = time.monotonic() + STOP_GRACE_SECONDS
    while time.monotonic() < deadline and _alive(pid):
        time.sleep(.1)
    forced = False
    if _alive(pid):
        forced = True
        try:
            os.kill(pid, signal.SIGKILL)
        except OSError:
            pass
        kill_deadline = time.monotonic() + STOP_KILL_GRACE_SECONDS
        while time.monotonic() < kill_deadline and _alive(pid):
            time.sleep(.1)
    if _alive(pid):
        # The process still holds the workspace lock; keep the markers so the
        # service stays discoverable instead of looking stopped while alive.
        return {'stopped': False, 'path': str(root), 'pid': pid,
                'message': f'服务进程 {pid} 仍在运行，已保留 server.json 以便稍后重试或手动结束该进程。'}
    for name in ('server.json', 'server.pid'):
        try:
            (root / name).unlink()
        except OSError:
            pass
    message = '已停止该工作区服务；数据、来源和任务记录都保留，可随时重新打开。'
    if forced:
        message = '服务进程未在等待时间内退出，已强制结束；数据、来源和任务记录都保留，可随时重新打开。'
    return {'stopped': True, 'path': str(root), 'message': message}
