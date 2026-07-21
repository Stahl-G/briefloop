"""Deterministic procedural-isolation preflight for an A2 experiment.

This module does not create an OS sandbox and grants no runtime authority.  It
only checks that the declared A2 workspace and allowed inputs are confined to
the selected project root while declared prior-condition and scoring material
is outside that root and its explicitly enumerable parents.
"""

from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
from typing import Iterable


SCHEMA_VERSION = "briefloop.experimental.a2_procedural_isolation_preflight.v1"
ISOLATION_STRENGTH = "procedural_isolation"


@dataclass(frozen=True, slots=True)
class A2ForbiddenPaths:
    """Paths that must not overlap the A2 agent's procedurally exposed roots."""

    risk_ledger: Path
    a0_run: Path
    a1_run: Path
    scoring_paths: tuple[Path, ...]

    def labelled_paths(self) -> tuple[tuple[str, Path], ...]:
        labelled: list[tuple[str, Path]] = [
            ("risk_ledger", self.risk_ledger),
            ("a0_run", self.a0_run),
            ("a1_run", self.a1_run),
        ]
        labelled.extend(
            (f"scoring_path_{index}", path)
            for index, path in enumerate(self.scoring_paths, start=1)
        )
        return tuple(labelled)


@dataclass(frozen=True, slots=True)
class A2IsolationFinding:
    reason_code: str
    subject: str
    path: str
    exposed_root: str | None = None

    def to_dict(self) -> dict[str, object]:
        payload: dict[str, object] = {
            "reason_code": self.reason_code,
            "subject": self.subject,
            "path": self.path,
        }
        if self.exposed_root is not None:
            payload["exposed_root"] = self.exposed_root
        return payload


@dataclass(frozen=True, slots=True)
class A2IsolationPreflight:
    decision: str
    project_root: str
    workspace: str
    allowed_inputs: tuple[str, ...]
    exposed_roots: tuple[str, ...]
    forbidden_paths: tuple[tuple[str, str], ...]
    findings: tuple[A2IsolationFinding, ...]

    @property
    def ok(self) -> bool:
        return self.decision == "allowed"

    @property
    def reason_codes(self) -> tuple[str, ...]:
        return tuple(dict.fromkeys(finding.reason_code for finding in self.findings))

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": SCHEMA_VERSION,
            "experimental": True,
            "ok": self.ok,
            "decision": self.decision,
            "isolation_strength": ISOLATION_STRENGTH,
            "os_sandbox_enforced": False,
            "runtime_authority": False,
            "provider_calls": 0,
            "project_root": self.project_root,
            "workspace": self.workspace,
            "allowed_inputs": list(self.allowed_inputs),
            "exposed_roots": list(self.exposed_roots),
            "forbidden_paths": [
                {"kind": kind, "path": path} for kind, path in self.forbidden_paths
            ],
            "reason_codes": list(self.reason_codes),
            "findings": [finding.to_dict() for finding in self.findings],
            "boundary": (
                "This result records a deterministic directory-layout check only. "
                "It does not prevent Codex or another local process from reading "
                "arbitrary filesystem paths."
            ),
        }


@dataclass(frozen=True, slots=True)
class _NormalizedPath:
    lexical: Path
    resolved: Path


def _normalize(path: str | Path) -> _NormalizedPath:
    lexical = Path(os.path.abspath(os.fspath(path)))
    return _NormalizedPath(lexical=lexical, resolved=lexical.resolve(strict=False))


def _contained(candidate: _NormalizedPath, root: _NormalizedPath) -> bool:
    return candidate.lexical.is_relative_to(root.lexical) and candidate.resolved.is_relative_to(
        root.resolved
    )


def _overlaps(first: _NormalizedPath, second: _NormalizedPath) -> bool:
    lexical_overlap = first.lexical.is_relative_to(
        second.lexical
    ) or second.lexical.is_relative_to(first.lexical)
    resolved_overlap = first.resolved.is_relative_to(
        second.resolved
    ) or second.resolved.is_relative_to(first.resolved)
    return lexical_overlap or resolved_overlap


def _unique_paths(paths: Iterable[_NormalizedPath]) -> tuple[_NormalizedPath, ...]:
    unique: dict[tuple[str, str], _NormalizedPath] = {}
    for path in paths:
        unique[(str(path.lexical), str(path.resolved))] = path
    return tuple(unique.values())


def preflight_a2_procedural_isolation(
    *,
    project_root: str | Path,
    workspace: str | Path,
    allowed_inputs: Iterable[str | Path],
    forbidden: A2ForbiddenPaths,
    enumerable_parents: Iterable[str | Path] = (),
) -> A2IsolationPreflight:
    """Check an A2 directory layout without invoking a provider or runtime.

    The project root and its immediate parent are always treated as enumerable.
    Callers may add other roots exposed by their launcher.  All four forbidden
    categories are mandatory; an empty scoring declaration fails closed.
    """

    root = _normalize(project_root)
    workspace_path = _normalize(workspace)
    inputs = tuple(_normalize(path) for path in allowed_inputs)
    exposed = _unique_paths(
        (
            root,
            _normalize(root.lexical.parent),
            *(_normalize(path) for path in enumerable_parents),
        )
    )
    forbidden_labelled = forbidden.labelled_paths()
    normalized_forbidden = tuple(
        (kind, _normalize(path)) for kind, path in forbidden_labelled
    )
    findings: list[A2IsolationFinding] = []

    if not root.lexical.exists():
        findings.append(
            A2IsolationFinding("project_root_missing", "project_root", str(root.lexical))
        )
    elif not root.lexical.is_dir():
        findings.append(
            A2IsolationFinding(
                "project_root_not_directory", "project_root", str(root.lexical)
            )
        )

    if not workspace_path.lexical.exists():
        findings.append(
            A2IsolationFinding(
                "workspace_missing", "workspace", str(workspace_path.lexical)
            )
        )
    elif not workspace_path.lexical.is_dir():
        findings.append(
            A2IsolationFinding(
                "workspace_not_directory", "workspace", str(workspace_path.lexical)
            )
        )
    if not _contained(workspace_path, root):
        findings.append(
            A2IsolationFinding(
                "workspace_outside_project_root",
                "workspace",
                str(workspace_path.lexical),
                str(root.lexical),
            )
        )

    if not inputs:
        findings.append(
            A2IsolationFinding(
                "allowed_inputs_missing", "allowed_inputs", str(root.lexical)
            )
        )
    for index, path in enumerate(inputs, start=1):
        subject = f"allowed_input_{index}"
        if not path.lexical.exists():
            findings.append(
                A2IsolationFinding("allowed_input_missing", subject, str(path.lexical))
            )
        if not _contained(path, root):
            findings.append(
                A2IsolationFinding(
                    "allowed_input_outside_project_root",
                    subject,
                    str(path.lexical),
                    str(root.lexical),
                )
            )

    if not forbidden.scoring_paths:
        findings.append(
            A2IsolationFinding(
                "scoring_paths_declaration_missing",
                "scoring_paths",
                str(root.lexical),
            )
        )

    for kind, path in normalized_forbidden:
        for exposed_root in exposed:
            if _overlaps(path, exposed_root):
                findings.append(
                    A2IsolationFinding(
                        "forbidden_path_exposed",
                        kind,
                        str(path.lexical),
                        str(exposed_root.lexical),
                    )
                )
                break

    return A2IsolationPreflight(
        decision="blocked" if findings else "allowed",
        project_root=str(root.lexical),
        workspace=str(workspace_path.lexical),
        allowed_inputs=tuple(str(path.lexical) for path in inputs),
        exposed_roots=tuple(str(path.lexical) for path in exposed),
        forbidden_paths=tuple(
            (kind, str(path.lexical)) for kind, path in normalized_forbidden
        ),
        findings=tuple(findings),
    )


__all__ = [
    "A2ForbiddenPaths",
    "A2IsolationFinding",
    "A2IsolationPreflight",
    "ISOLATION_STRENGTH",
    "SCHEMA_VERSION",
    "preflight_a2_procedural_isolation",
]
