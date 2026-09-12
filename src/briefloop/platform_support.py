"""Local OS primitives. Process ownership is cleanup, never a sandbox."""
from pathlib import Path
import os
import signal
import subprocess
import sys


class WorkspaceLock:
    """Acquire before opening SQLite; keep the same inode until service exit."""
    def __init__(self, workspace):
        root = Path(workspace).expanduser().resolve()
        root.mkdir(parents=True, exist_ok=True)
        self.handle = (root / '.server.lock').open('a+b')
        try:
            if os.name == 'nt':
                import msvcrt
                self.handle.seek(0)
                msvcrt.locking(self.handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(self.handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            self.handle.close()
            raise RuntimeError('这个工作区已有本地服务在运行，或无法取得工作区锁') from exc

    def close(self):
        if not self.handle.closed:
            if os.name == 'nt':
                import msvcrt
                self.handle.seek(0)
                msvcrt.locking(self.handle.fileno(), msvcrt.LK_UNLCK, 1)
            self.handle.close()


def ensure_utf8():
    """Console scripts and python -m use the same UTF-8 mode on Windows."""
    if os.name == 'nt' and not sys.flags.utf8_mode:
        raise SystemExit(subprocess.call([sys.executable, '-X', 'utf8', *sys.orig_argv[1:]],
                                        env={**os.environ, 'PYTHONUTF8': '1'}))


def cli_command(arguments):
    """Resolve npm shims without cmd.exe or shell interpolation."""
    args = [str(a) for a in arguments]
    if os.name != 'nt' or Path(args[0]).suffix.lower() not in ('.cmd', '.bat'):
        return args
    # npm emits a sibling POSIX shim containing the exact entrypoint. Newer
    # native packages (including OpenCode) invoke an exe without Node.
    import re
    shim = Path(args[0])
    sibling = shim.with_suffix('')
    text = sibling.read_text(encoding='utf-8') if sibling.is_file() else ''
    native = re.search(r'^exec\s+"\$basedir/([^"\r\n]+\.exe)"\s+"\$@"\s*$', text, re.MULTILINE | re.IGNORECASE)
    if native:
        entry = (shim.parent / native.group(1)).resolve()
        if not entry.is_file():
            raise FileNotFoundError(entry)
        return [str(entry), *args[1:]]
    match = re.search(r'"\$basedir/([^"\r\n]+)"\s+"\$@"', text)
    if not match:
        raise ValueError('无法安全解析 CLI shim，请配置原生 exe 或标准 npm CLI：' + str(shim))
    entry = (shim.parent / match.group(1)).resolve()
    if not entry.is_file():
        raise FileNotFoundError(entry)
    from .host_bins import find
    node = find('node')
    if not node:
        raise FileNotFoundError('npm CLI 需要 Node.js')
    return [node, str(entry), *args[1:]]


def process_alive(pid):
    if not isinstance(pid, int) or isinstance(pid, bool) or pid <= 0 or pid > 0xffffffff:
        return False
    if os.name == 'nt':
        from .windows_process import alive
        return alive(pid)
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except (OverflowError, ValueError):
        return False
    except OSError:
        return True


class OwnedProcess(subprocess.Popen):
    """One owned process tree. No lookup by executable name or arbitrary PID."""
    def __init__(self, args, **kwargs):
        self._job = None
        self._tree_closed = False
        if kwargs.get('text') or kwargs.get('universal_newlines'):
            kwargs.setdefault('encoding', 'utf-8')
        kwargs['env'] = {**(kwargs.get('env') or os.environ), 'PYTHONUTF8': '1'}
        if os.name == 'nt':
            from .windows_process import Job
            self._job = Job()
            kwargs.pop('start_new_session', None)
            kwargs['creationflags'] = kwargs.get('creationflags', 0) | 0x00000004 | subprocess.CREATE_NO_WINDOW
        else:
            kwargs['start_new_session'] = True
        try:
            super().__init__(cli_command(args), **kwargs)
            if self._job:
                self._job.assign_and_resume(self)
        except BaseException:
            if getattr(self, '_child_created', False):
                super().kill()
                self.wait()
            if self._job:
                self._job.close()
            raise

    def close_tree(self, timeout=5):
        if self._tree_closed:return
        if self._job:
            self._job.close()
            self.wait(timeout=timeout)
        else:
            try:
                os.killpg(self.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
            try:
                self.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                os.killpg(self.pid, signal.SIGKILL)
                self.wait(timeout=timeout)
        self._tree_closed = True
