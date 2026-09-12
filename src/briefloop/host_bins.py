"""Locate host CLI binaries the way a login shell would.

A service started from launchd, cron or a container often does not inherit the
user's shell PATH. A CLI that is installed and authenticated then looks "not
installed", and the feature it powers disappears without any visible reason.
Search PATH first, then the well-known install directories each host uses.
"""
from pathlib import Path
import os
import shutil

EXTRA_DIRS = (
    '/opt/homebrew/bin',
    '/usr/local/bin',
    '~/.opencode/bin',
    '~/.local/bin',
    '~/.bun/bin',
    '~/.cargo/bin',
    '~/.npm-global/bin',
    '~/.dsh/bin',
)


SEARCH_HINT = '已查找 PATH、~/.opencode/bin、/opt/homebrew/bin 等常见安装目录'


def find(name, *, extra=()):
    """Absolute path of `name`, or None. PATH wins; known dirs are the fallback."""
    if not name:
        return None
    found = shutil.which(name)
    if found:
        return found
    windows_dirs = [str(Path(os.environ['APPDATA']) / 'npm')] if os.name == 'nt' and os.environ.get('APPDATA') else []
    for directory in (*extra, *windows_dirs, *EXTRA_DIRS):
        found = shutil.which(name, path=str(Path(directory).expanduser()))
        if found:
            return str(Path(found).resolve())
    return None
