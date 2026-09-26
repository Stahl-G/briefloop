"""Read-only, installation-bound version probe for capability admission."""
import os
from pathlib import Path
import re
import subprocess
import threading
import time

from .host_bins import find
from .platform_support import cli_command

_lock = threading.Lock()
_cache = {}
_SUCCESS_TTL = 300.0
_FAILURE_TTL = 15.0


def installed_major():
    """Unknown is never treated as v1; unchanged installs reuse bounded probes.

    Resolving the entry point and checking its stat also invalidates the cache
    when a symlink target changes or an upgrade replaces the executable. The
    TTL also catches npm launchers whose internal binary changes independently.
    """
    binary = find('opencode')
    if not binary:
        return None
    try:
        path = Path(binary).resolve(strict=True)
        stat = path.stat()
    except OSError:
        return None
    identity = (str(path), stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns)
    with _lock:
        cached = _cache.get(identity)
        if cached is not None and time.monotonic() < cached[0]:
            return cached[1]
        env = dict(os.environ)
        env.pop('ELECTRON_RUN_AS_NODE', None)
        try:
            result = subprocess.run(cli_command([binary, '--version']), stdin=subprocess.DEVNULL,
                                    capture_output=True, text=True, encoding='utf-8',
                                    timeout=3, check=True, env=env)
            match = re.fullmatch(r'(?:opencode\s+)?v?(\d+)\.\d+\.\d+(?:[-+][\w.-]+)?',
                                 result.stdout.strip(), flags=re.I)
            major = int(match[1]) if match else None
        except (OSError, UnicodeError, subprocess.SubprocessError):
            major = None
        # Briefly cache failures: state polling must not spawn retry probes,
        # but a transient timeout must not disable review until app restart.
        if len(_cache) >= 8:
            _cache.clear()
        _cache[identity] = (time.monotonic() + (_FAILURE_TTL if major is None else _SUCCESS_TTL), major)
        return major
