"""Multi-Agent Brief Workflow."""

from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from typing import Optional


def _source_checkout_version() -> Optional[str]:
    """Return repo VERSION when this package is imported from a source checkout."""

    for parent in Path(__file__).resolve().parents:
        version_file = parent / "VERSION"
        if (
            version_file.exists()
            and (parent / "pyproject.toml").exists()
            and (parent / "src" / "multi_agent_brief").exists()
        ):
            text = version_file.read_text(encoding="utf-8").strip()
            return text or None
    return None


def _installed_package_version() -> str:
    try:
        return version("multi-agent-brief-workflow")
    except PackageNotFoundError:
        return "0.0.0.dev0"


__version__ = _source_checkout_version() or _installed_package_version()
