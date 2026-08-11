"""Pure shared workspace-member and nested-workspace hygiene rules."""

from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
import stat
from typing import Literal


HygieneSurface = Literal["delivery", "audit", "archive", "bundle"]
_JUNK_NAMES = frozenset({".DS_Store", "Thumbs.db", "desktop.ini"})
_JUNK_SUFFIXES = (".tmp", ".temp", ".swp", ".swo")
_BOOTSTRAP_FILES = frozenset({"config.yaml", "sources.yaml", "user.md"})


@dataclass(frozen=True)
class WorkspaceMemberDecision:
    status: Literal["include", "exclude"]
    reason_code: str | None
    relative_path: str | None


class NestedWorkspaceTargetError(RuntimeError):
    """Raised before init writes beneath an existing BriefLoop workspace."""

    code = "workspace_target_nested"

    def __init__(self) -> None:
        super().__init__(self.code)


def is_briefloop_workspace_root(path: Path) -> bool:
    """Recognize Store authority or the complete strict pre-Store marker set."""

    try:
        database = path / "briefloop.db"
        if database.exists():
            return database.is_file() and not database.is_symlink()
        return (
            all(
                (path / name).is_file() and not (path / name).is_symlink()
                for name in _BOOTSTRAP_FILES
            )
            and (path / "input").is_dir()
            and not (path / "input").is_symlink()
        )
    except OSError:
        return False


def nested_workspace_ancestor(target: str | Path) -> Path | None:
    """Return a lexical or canonical parent workspace, excluding target itself."""

    lexical, canonical = _workspace_target_paths(target)
    return _nested_workspace_ancestor(lexical, canonical)


def _nested_workspace_ancestor(lexical: Path, canonical: Path) -> Path | None:
    seen: set[Path] = set()
    for parent in (*lexical.parents, *canonical.parents):
        if parent in seen:
            continue
        seen.add(parent)
        if is_briefloop_workspace_root(parent):
            return parent
    return None


def canonical_workspace_target(target: str | Path) -> Path:
    """Bind routing input to one canonical non-nested workspace target."""

    lexical, canonical = _workspace_target_paths(target)
    if _nested_workspace_ancestor(lexical, canonical) is not None:
        raise NestedWorkspaceTargetError
    return canonical


def _workspace_target_paths(target: str | Path) -> tuple[Path, Path]:
    raw = Path(target).expanduser()
    if not raw.is_absolute():
        raw = Path.cwd() / raw
    lexical = Path(os.path.abspath(raw))
    canonical = lexical.resolve(strict=False)
    return lexical, canonical


def classify_workspace_member(
    workspace: str | Path,
    candidate: str | Path,
    *,
    surface: HygieneSurface,
    allowed_hidden_roots: frozenset[str] = frozenset(),
) -> WorkspaceMemberDecision:
    """Classify one existing candidate without following symlinks."""

    del surface  # The shared rules are intentionally identical for all bundles.
    root = Path(workspace).expanduser().resolve(strict=True)
    raw = Path(candidate).expanduser()
    lexical = raw if raw.is_absolute() else root / raw
    lexical = Path(os.path.abspath(lexical))
    try:
        relative = lexical.relative_to(root)
    except ValueError:
        return WorkspaceMemberDecision(
            "exclude", "workspace_member_escapes_root", None
        )
    if not relative.parts:
        return WorkspaceMemberDecision("exclude", "workspace_member_not_regular", ".")
    for index, part in enumerate(relative.parts):
        lower = part.lower()
        if (
            part in _JUNK_NAMES
            or part.startswith("~$")
            or part.startswith(".~lock.")
            or part.endswith(("~", "#"))
            or lower in {item.lower() for item in _JUNK_NAMES}
            or lower.endswith(_JUNK_SUFFIXES)
        ):
            return WorkspaceMemberDecision(
                "exclude",
                "workspace_member_packaging_residue",
                relative.as_posix(),
            )
        if part.startswith(".briefloop-pub-probe-"):
            return WorkspaceMemberDecision(
                "exclude",
                "workspace_member_publication_probe",
                relative.as_posix(),
            )
        if part == "__MACOSX":
            return WorkspaceMemberDecision(
                "exclude",
                "workspace_member_platform_metadata",
                relative.as_posix(),
            )
        if part.startswith(".") and (
            index != 0 or part not in allowed_hidden_roots
        ):
            return WorkspaceMemberDecision(
                "exclude",
                "workspace_member_hidden",
                relative.as_posix(),
            )
    current = root
    for index, part in enumerate(relative.parts):
        current = current / part
        try:
            info = current.lstat()
        except OSError:
            return WorkspaceMemberDecision(
                "exclude",
                "workspace_member_unreadable",
                relative.as_posix(),
            )
        if stat.S_ISLNK(info.st_mode):
            return WorkspaceMemberDecision(
                "exclude",
                "workspace_member_symlink",
                relative.as_posix(),
            )
        if index < len(relative.parts) - 1:
            if not stat.S_ISDIR(info.st_mode):
                return WorkspaceMemberDecision(
                    "exclude",
                    "workspace_member_non_directory_parent",
                    relative.as_posix(),
                )
            if current != root and is_briefloop_workspace_root(current):
                return WorkspaceMemberDecision(
                    "exclude",
                    "workspace_member_nested_workspace",
                    relative.as_posix(),
                )
        elif not stat.S_ISREG(info.st_mode):
            return WorkspaceMemberDecision(
                "exclude",
                "workspace_member_not_regular",
                relative.as_posix(),
            )
    return WorkspaceMemberDecision("include", None, relative.as_posix())


__all__ = [
    "NestedWorkspaceTargetError",
    "WorkspaceMemberDecision",
    "canonical_workspace_target",
    "classify_workspace_member",
    "is_briefloop_workspace_root",
    "nested_workspace_ancestor",
]
