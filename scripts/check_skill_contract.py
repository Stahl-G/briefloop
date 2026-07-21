#!/usr/bin/env python3
"""Lightweight drift checks for the repo-local BriefLoop operator skill."""

from __future__ import annotations

import re
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
CANONICAL = ROOT / ".agents" / "skills" / "briefloop"
SKILL = CANONICAL / "SKILL.md"
CLAUDE_WRAPPER = ROOT / ".claude" / "skills" / "briefloop" / "SKILL.md"
HERMES_PLUGIN_PROJECTION = ROOT / "integrations" / "hermes-plugin" / "mabw" / "skills" / "briefloop"
VERSION_MATRIX = CANONICAL / "references" / "version-matrix.md"
PUBLIC_CLAIMS = CANONICAL / "references" / "public-claims.md"


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _error(message: str) -> str:
    return f"[skill-contract] {message}"


def _relative_files(root: Path) -> list[Path]:
    if not root.exists():
        return []
    return sorted(path.relative_to(root) for path in root.rglob("*") if path.is_file())


def _check_projection(source: Path, target: Path, *, label: str) -> list[str]:
    if not target.exists():
        return [_error(f"{label} projection is missing: {target.relative_to(ROOT)}")]
    errors: list[str] = []
    source_files = set(_relative_files(source))
    target_files = set(_relative_files(target))
    for rel_path in sorted(source_files - target_files):
        errors.append(_error(f"{label} projection missing file: {target.relative_to(ROOT) / rel_path}"))
    for rel_path in sorted(target_files - source_files):
        errors.append(_error(f"{label} projection has extra file: {target.relative_to(ROOT) / rel_path}"))
    for rel_path in sorted(source_files & target_files):
        if (source / rel_path).read_bytes() != (target / rel_path).read_bytes():
            errors.append(_error(f"{label} projection differs from canonical: {target.relative_to(ROOT) / rel_path}"))
    return errors


def main() -> int:
    errors: list[str] = []

    if not SKILL.exists():
        errors.append(_error("canonical .agents/skills/briefloop/SKILL.md is missing"))
    if not CLAUDE_WRAPPER.exists():
        errors.append(_error("Claude briefloop skill wrapper is missing"))
    if not HERMES_PLUGIN_PROJECTION.exists():
        errors.append(_error("Hermes plugin briefloop skill projection is missing"))
    if not VERSION_MATRIX.exists():
        errors.append(_error("version-matrix.md is missing"))

    if errors:
        for error in errors:
            print(error, file=sys.stderr)
        return 1

    skill_text = _read(SKILL)
    wrapper_text = _read(CLAUDE_WRAPPER)
    matrix_text = _read(VERSION_MATRIX)
    public_claims_text = _read(PUBLIC_CLAIMS) if PUBLIC_CLAIMS.exists() else ""

    references = sorted(set(re.findall(r"references/[a-z0-9-]+\.md", skill_text)))
    for reference in references:
        if not (CANONICAL / reference).exists():
            errors.append(_error(f"missing referenced file: {reference}"))

    if ".agents/skills/briefloop/SKILL.md" not in wrapper_text:
        errors.append(_error("Claude wrapper does not point to canonical skill"))
    if "future 090 readiness" in wrapper_text:
        errors.append(_error("Claude wrapper routes operators to future 090 readiness framing"))
    if "archived MABW-080 / BriefLoop-090 experiment tooling" not in wrapper_text:
        errors.append(_error("Claude wrapper does not describe MABW-080 / BriefLoop-090 as archived tooling"))
    errors.extend(_check_projection(CANONICAL, HERMES_PLUGIN_PROJECTION, label="Hermes plugin briefloop skill"))

    version = (ROOT / "VERSION").read_text(encoding="utf-8").strip()
    expected_version = f"v{version}"
    if expected_version not in matrix_text:
        errors.append(_error(f"version matrix does not mention current VERSION {expected_version}"))

    if "Planned / Not Yet Authoritative" not in matrix_text:
        errors.append(_error("version matrix does not separate planned controls"))
    if "MABW-080 / BriefLoop-090 experiment operations" in matrix_text:
        errors.append(_error("version matrix lists BriefLoop-090 as a current experiment operation surface"))
    if (
        "BriefLoop-090 is an archived experiment/readiness label" not in matrix_text
        or "not a current CLI namespace" not in matrix_text
    ):
        errors.append(_error("version matrix does not explain BriefLoop-090 is archived and not a current CLI namespace"))

    forbidden_positive_claims = [
        "BriefLoop proves truth.",
        "BriefLoop eliminates hallucinations.",
        "BriefLoop makes reports automatically ready to send.",
        "Improvement Memory improves output quality as a general fact.",
    ]
    for claim in forbidden_positive_claims:
        if claim in public_claims_text and f"- {claim}" not in public_claims_text:
            errors.append(_error(f"public claims may assert forbidden claim: {claim}"))

    implemented_overclaims = [
        "Atomic Claim Graph is implemented",
        "Evidence Span Registry is implemented",
        "Claim-Support Matrix is implemented",
    ]
    joined = "\n".join([skill_text, matrix_text, public_claims_text])
    for phrase in implemented_overclaims:
        if phrase in joined:
            errors.append(_error(f"planned control described as implemented: {phrase}"))

    if errors:
        for error in errors:
            print(error, file=sys.stderr)
        return 1
    print("[skill-contract] ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
