#!/usr/bin/env python3
"""Guard the current single-session SQLite/Codex operator protocol."""

from __future__ import annotations

import argparse
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
CANONICAL = ROOT / ".agents" / "skills" / "briefloop"
PACKAGED_CODEX = (
    ROOT
    / "src"
    / "multi_agent_brief"
    / "runtime_kits"
    / "codex"
    / "skills"
    / "briefloop"
)

REQUIRED_REFERENCE_PHRASES: dict[str, list[str]] = {
    "SKILL.md": [
        "Do not create an agent swarm",
        "CoreRunNextAction",
        "runtime continue",
        "role_work_required",
        "needs_human",
        "needs_attention",
        "finalized_local",
        "Solar Stock Periodic",
        "AI Second Opinion",
        "local_derivation_failed",
        "FrozenGuidanceContext",
    ],
    "references/codex-controlstore-v2.md": [
        "runtime_action.json",
        "runtime invocation-start",
        "RoleTaskEnvelope",
        "allowed_output_filenames",
        "runtime invocation-accept",
        "runtime invocation-fail",
        "runtime apply",
        "--human-request",
        "runtime_action_stale",
        "effect_kind=package_ready",
        "effect_kind=delivered",
        "local_derivation_failed",
        "true `outcome_unknown`",
        "20 atomic",
        "There is no one-wide-query or top-five fallback",
        "Never read them back for legality",
    ],
    "evals/evals.json": [
        '"skill_name": "briefloop"',
        "role_work_required",
        "source_acquisition_recovery_decision_required",
        "local_derivation_failed",
        "solar-stock-periodic",
    ],
}

PACKAGED_REQUIRED_PHRASES: dict[str, list[str]] = {
    "SKILL.md": REQUIRED_REFERENCE_PHRASES["SKILL.md"],
    "references/controlstore-v2.md": REQUIRED_REFERENCE_PHRASES[
        "references/codex-controlstore-v2.md"
    ],
}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--json", action="store_true", help="Emit machine-readable JSON."
    )
    args = parser.parse_args()

    checks: list[dict[str, str]] = []
    _check_required_phrases(checks)
    _check_packaged_phrases(checks)
    ok = all(item["status"] == "pass" for item in checks)
    payload = {
        "ok": ok,
        "schema_version": "briefloop.skill_freshness_check.v2",
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


def _append_check(
    checks: list[dict[str, str]], check_id: str, ok: bool, detail: str
) -> None:
    checks.append(
        {
            "id": check_id,
            "status": "pass" if ok else "fail",
            "detail": detail,
        }
    )


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
