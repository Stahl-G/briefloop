"""Platform capability checks for retained-directory projection publication."""

from __future__ import annotations

import inspect
import os


def supports_retained_directory_publication() -> bool:
    """Return whether the complete retained-relative writer is available."""

    required_dir_fd = (os.open, os.stat, os.mkdir, os.unlink)
    try:
        replace_parameters = inspect.signature(os.replace).parameters
    except (TypeError, ValueError):
        return False
    return (
        all(function in os.supports_dir_fd for function in required_dir_fd)
        and os.stat in os.supports_follow_symlinks
        and {"src_dir_fd", "dst_dir_fd"} <= set(replace_parameters)
        and bool(getattr(os, "O_DIRECTORY", 0))
        and bool(getattr(os, "O_NOFOLLOW", 0))
        and callable(getattr(os, "fstat", None))
    )


__all__ = ["supports_retained_directory_publication"]
