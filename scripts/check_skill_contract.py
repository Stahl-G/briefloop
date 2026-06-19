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
VERSION_MATRIX = CANONICAL / "references" / "version-matrix.md"
PUBLIC_CLAIMS = CANONICAL / "references" / "public-claims.md"
EXPERIMENT_REF = CANONICAL / "references" / "experiment-080-090.md"


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _error(message: str) -> str:
    return f"[skill-contract] {message}"


def main() -> int:
    errors: list[str] = []

    if not SKILL.exists():
        errors.append(_error("canonical .agents/skills/briefloop/SKILL.md is missing"))
    if not CLAUDE_WRAPPER.exists():
        errors.append(_error("Claude briefloop skill wrapper is missing"))
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
    experiment_text = _read(EXPERIMENT_REF) if EXPERIMENT_REF.exists() else ""

    references = sorted(set(re.findall(r"references/[a-z0-9-]+\.md", skill_text)))
    for reference in references:
        if not (CANONICAL / reference).exists():
            errors.append(_error(f"missing referenced file: {reference}"))

    if ".agents/skills/briefloop/SKILL.md" not in wrapper_text:
        errors.append(_error("Claude wrapper does not point to canonical skill"))

    version = (ROOT / "VERSION").read_text(encoding="utf-8").strip()
    expected_version = f"v{version}"
    if expected_version not in matrix_text:
        errors.append(_error(f"version matrix does not mention current VERSION {expected_version}"))

    for target in ("auditable_brief", "delivery_brief"):
        if target not in experiment_text:
            errors.append(_error(f"experiment reference does not mention {target}"))

    if "Planned / Not Yet Authoritative" not in matrix_text:
        errors.append(_error("version matrix does not separate planned controls"))

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
    joined = "\n".join([skill_text, matrix_text, experiment_text, public_claims_text])
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
