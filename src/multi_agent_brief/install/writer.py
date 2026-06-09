"""Shared safe-write primitives for installer commands."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


class InstallWriteError(RuntimeError):
    """Raised when planned installer writes are unsafe or refused."""


@dataclass(frozen=True)
class PlannedWrite:
    """A fully rendered file write planned by an installer."""

    destination: Path
    content: str


def apply_planned_writes(
    *,
    writes: list[PlannedWrite],
    root: Path,
    force: bool = False,
    dry_run: bool = False,
    generated_markers: tuple[str, ...] = (),
) -> list[Path]:
    """Validate and optionally apply planned writes under root."""
    root = root.expanduser().resolve()
    _validate_destinations(root=root, writes=writes)
    _check_overwrites(
        writes=writes,
        force=force,
        generated_markers=generated_markers,
    )

    written: list[Path] = []
    if dry_run:
        return [write.destination for write in writes]

    for write in writes:
        write.destination.parent.mkdir(parents=True, exist_ok=True)
        write.destination.write_text(write.content, encoding="utf-8")
        written.append(write.destination)
    return written


def _validate_destinations(*, root: Path, writes: list[PlannedWrite]) -> None:
    for write in writes:
        resolved = write.destination.resolve()
        try:
            resolved.relative_to(root)
        except ValueError as exc:
            raise InstallWriteError(
                f"Installer write resolves outside target root: {write.destination}"
            ) from exc
        if write.destination.is_symlink():
            raise InstallWriteError(
                f"Refusing to overwrite symlink during install: {write.destination}"
            )


def _check_overwrites(
    *,
    writes: list[PlannedWrite],
    force: bool,
    generated_markers: tuple[str, ...],
) -> None:
    for write in writes:
        dst = write.destination
        if not dst.exists():
            continue
        existing_text = _read_existing_text(dst)
        if force:
            continue
        if existing_text is not None and existing_text == write.content:
            continue
        if existing_text is not None and _text_has_generated_marker(
            existing_text,
            generated_markers,
        ):
            continue
        raise InstallWriteError(
            f"Refusing to overwrite existing non-MABW file without --force: {dst}"
        )


def _read_existing_text(path: Path) -> str | None:
    try:
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None


def _text_has_generated_marker(text: str, markers: tuple[str, ...]) -> bool:
    return any(marker and marker in text for marker in markers)

