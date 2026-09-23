"""Atomic private files for locally saved search-provider keys."""
import os
from pathlib import Path
import tempfile


def write_key(path, key):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    descriptor, temporary = tempfile.mkstemp(prefix='.search-key-', dir=path.parent)
    try:
        # Own the descriptor before setting permissions so a failure closes it
        # before cleanup; Windows cannot unlink an open temporary file.
        with os.fdopen(descriptor, 'w', encoding='utf-8') as stream:
            if os.name == 'nt':
                from .connectors.windows_acl import protect_private
                protect_private(Path(temporary))
            else:
                os.fchmod(stream.fileno(), 0o600)
            stream.write(key)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
