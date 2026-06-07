"""Multi-Agent Brief Workflow."""

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("multi-agent-brief-workflow")
except PackageNotFoundError:
    __version__ = "0.0.0.dev0"
