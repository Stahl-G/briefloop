#!/usr/bin/env python3
"""Guard the current SQLite-only Codex operating protocol and its projections."""

from __future__ import annotations

import argparse
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
CANONICAL = ROOT / ".agents" / "skills" / "briefloop"
HERMES_PLUGIN_PROJECTION = (
    ROOT / "integrations" / "hermes-plugin" / "mabw" / "skills" / "briefloop"
)
PACKAGED_CODEX = (
    ROOT / "src" / "multi_agent_brief" / "runtime_kits" / "codex" / "skills" / "briefloop"
)


REQUIRED_REFERENCE_PHRASES: dict[str, list[str]] = {
    "SKILL.md": [
        "SQLite-only and Codex-only",
        "CoreRunNextAction",
        "delegate",
        "deterministic",
        "human_decision",
        "blocked",
        "complete",
        "RoleTaskEnvelope",
        "runtime_action_stale",
        "package_ready",
        "effect_kind=delivered",
    ],
    "references/codex-controlstore-v2.md": [
        "runtime_action.json",
        "runtime invocation-start",
        "delegate_exact_role",
        "allowed_output_filenames",
        "runtime invocation-accept",
        "runtime invocation-fail",
        "runtime apply",
        "--human-request",
        "invocation_accept_or_fail",
        "runtime_action_stale",
        "package_ready",
        "effect_kind=delivered",
        "Never read them back for legality",
    ],
    "references/version-matrix.md": [
        "briefloop-codex-skill-v0.3.0",
        "Prior release line: `v0.13.0`",
        "Prepared release line: `v0.14.0`",
        "Codex is the only active fresh runtime",
        "Strict Pydantic requests are the only write boundary",
        "Experimental",
        "NOT MEASURED",
        "eval-cases",
        "experiments 080",
    ],
    "CHANGELOG.md": [
        "briefloop-codex-skill-v0.3.0",
        "SQLite-only Codex runtime state machine",
        "package_ready",
        "delivered",
    ],
}

PACKAGED_REQUIRED_PHRASES: dict[str, list[str]] = {
    "SKILL.md": [
        "Use when operating this workspace",
        "CoreRunNextAction",
        "runtime invocation-start",
        "runtime apply",
        "human_decision",
        "blocked",
        "complete",
        "package_ready",
        "delivered",
        "Never fall back",
    ],
    "references/controlstore-v2.md": REQUIRED_REFERENCE_PHRASES[
        "references/codex-controlstore-v2.md"
    ],
}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", action="store_true", help="Emit machine-readable JSON.")
    args = parser.parse_args()

    checks: list[dict[str, str]] = []
    _check_required_phrases(checks)
    _check_packaged_phrases(checks)
    _check_projection_parity(checks)

    ok = all(item["status"] == "pass" for item in checks)
    payload = {
        "ok": ok,
        "schema_version": "briefloop.skill_freshness_check.v1",
        "runtime_effect": "readiness_check_only",
        "checks": checks,
    }
    if args.json:
        print(json.dumps(payload, indent=2, sort_keys=True))
    else:
        _print_human(payload)
    return 0 if ok else 1


def _check_phrase_set(
    checks: list[dict[str, str]],
    *,
    root: Path,
    prefix: str,
    requirements: dict[str, list[str]],
) -> None:
    for rel_path, phrases in requirements.items():
        path = root / rel_path
        if not path.exists():
            _append_check(checks, f"{prefix}.{rel_path}", False, "missing file")
            continue
        text = path.read_text(encoding="utf-8")
        missing = [phrase for phrase in phrases if phrase not in text]
        _append_check(
            checks,
            f"{prefix}.{rel_path}.freshness",
            not missing,
            f"missing={missing}",
        )


def _check_required_phrases(checks: list[dict[str, str]]) -> None:
    _check_phrase_set(
        checks,
        root=CANONICAL,
        prefix="canonical",
        requirements=REQUIRED_REFERENCE_PHRASES,
    )


def _check_packaged_phrases(checks: list[dict[str, str]]) -> None:
    _check_phrase_set(
        checks,
        root=PACKAGED_CODEX,
        prefix="packaged_codex",
        requirements=PACKAGED_REQUIRED_PHRASES,
    )


def _check_projection_parity(checks: list[dict[str, str]]) -> None:
    errors = _projection_errors(CANONICAL, HERMES_PLUGIN_PROJECTION)
    _append_check(
        checks,
        "hermes_plugin.briefloop_skill_projection",
        not errors,
        "; ".join(errors) if errors else "canonical and plugin projection match",
    )


def _projection_errors(source: Path, target: Path) -> list[str]:
    if not target.exists():
        return [f"missing projection directory: {_display_path(target)}"]
    errors: list[str] = []
    source_files = set(_relative_files(source))
    target_files = set(_relative_files(target))
    for rel_path in sorted(source_files - target_files):
        errors.append(f"missing file: {_display_path(target / rel_path)}")
    for rel_path in sorted(target_files - source_files):
        errors.append(f"extra file: {_display_path(target / rel_path)}")
    for rel_path in sorted(source_files & target_files):
        if (source / rel_path).read_bytes() != (target / rel_path).read_bytes():
            errors.append(f"differs: {_display_path(target / rel_path)}")
    return errors


def _display_path(path: Path) -> str:
    try:
        return str(path.relative_to(ROOT))
    except ValueError:
        return str(path)


def _relative_files(root: Path) -> list[Path]:
    if not root.exists():
        return []
    return sorted(path.relative_to(root) for path in root.rglob("*") if path.is_file())


def _append_check(checks: list[dict[str, str]], check_id: str, ok: bool, detail: str) -> None:
    checks.append({
        "id": check_id,
        "status": "pass" if ok else "fail",
        "detail": detail,
    })


def _print_human(payload: dict[str, object]) -> None:
    print("BriefLoop Skill Freshness Check")
    print("=" * 40)
    for item in payload["checks"]:  # type: ignore[index]
        status = "OK" if item["status"] == "pass" else "FAIL"
        print(f"  [{status}] {item['id']}: {item['detail']}")
    print()
    print("ALL CHECKS PASSED." if payload["ok"] else "FAILED.")


if __name__ == "__main__":
    raise SystemExit(main())
