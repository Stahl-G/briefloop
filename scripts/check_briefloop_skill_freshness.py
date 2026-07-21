#!/usr/bin/env python3
"""BriefLoop skill freshness guard.

This is intentionally separate from check_skill_contract.py. The contract check
guards structure and projection parity; this guard locks recent control-surface
semantics that must stay visible to the BriefLoop operator skill.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
CANONICAL = ROOT / ".agents" / "skills" / "briefloop"
HERMES_PLUGIN_PROJECTION = ROOT / "integrations" / "hermes-plugin" / "mabw" / "skills" / "briefloop"


REQUIRED_REFERENCE_PHRASES: dict[str, list[str]] = {
    "references/version-matrix.md": [
        "briefloop-operator-skill-v0.2.0",
        "v1.0 RC Landed Surfaces",
        "Pending Before v1.0",
        "single delivery-truth record",
        "repair supersede-stage",
        "coverage_omission",
        "quality summarize",
        "quality_panel.json",
        "quality_summary.md",
        "quality_panel.html",
        "quality_panel_closeout",
        "approval init",
        "approval record",
        "release check",
        "release_readiness_report.json",
        "branding_context",
        "Trajectory Regulation",
        "Materiality Selection",
        "Reader Template Conformance",
        "Citation Profile Split",
        "reader_contract",
        "reader_contract.citation_profile",
        "executive",
        "analyst",
        "audit",
        "report_bundle_manifest.json",
        "report_template_conformance",
        "reader_block_warnings",
        "screened_candidates.json",
        "materiality_terms",
        "review_materiality_exclusions",
        "workflow_state.json",
        "event_log.jsonl",
        "retry-stage events",
        "request_human_review",
        "block_run",
        "industry-weekly",
        "management-monthly",
        "document-review",
        "solar-periodic",
        "README_en.md",
        "compatibility-pointer shape",
        "v0.11.0 product-baseline readiness",
        "WorkBuddy Skill source bundle",
        ".agents/skills/briefloop-workbuddy/",
        "legacy mirror",
        "integrations/workbuddy/briefloop/",
        "source-clone-only",
        "--runtime operator",
        "workbuddy pack-skill",
        "deterministic local",
        "Skill zip",
        "not a WorkBuddy Marketplace publication",
        "CodeBuddy project Skill adapter",
        ".codebuddy/skills/briefloop/",
        "must not use `context: fork`",
        "used by `--runtime codebuddy` handoff",
        "CodeBuddy project role agents",
        ".codebuddy/agents/briefloop-*.md",
        "must not run `briefloop` or `multi-agent-brief` CLI commands",
        "main CodeBuddy session remains responsible for deterministic transactions",
        "CodeBuddy runtime handoff",
        "`--runtime codebuddy`: experimental handoff",
        "nested_subagents_supported",
        "role_agents_run_cli_transactions",
    ],
    "references/status-and-gates.md": [
        "Completion And Delivery Truth",
        "Store-native status projection",
        "leaves any prior delivery bundle unchanged",
        "Coverage/omission findings",
        "not full-world recall checks",
        "Trajectory Regulation is read-only",
        "Materiality Selection is diagnostic-only",
        "Reader Template Conformance is warning-only",
        "Citation profiles split reader and audit citation surfaces",
        "quality_panel_closeout",
        "excluded from reader-facing delivery bundles",
    ],
    "references/runtime-workspace.md": [
        "briefloop run --workspace <workspace> --runtime codebuddy",
        ".codebuddy/skills/briefloop/",
        ".codebuddy/agents/briefloop-*.md",
        "deterministic CLI transactions to the main session",
    ],
    "references/control-record-map.md": [
        "quality_panel.json",
        "quality_summary.md",
        "quality_panel.html",
        "Materiality Selection is a status / Quality Panel projection",
        "release_readiness_report.json",
        "resolved citation profile",
        "post-finalize closeout",
    ],
    "references/repo-development.md": [
        "v1.0 Pilot Evidence Gate",
        "check_v1_pilot_evidence.py",
        ".codebuddy/agents/briefloop-*.md",
        "CodeBuddy project role agents",
        "source-clone-only",
        "check_product_baseline.py",
        "check_skill_contract.py",
        "check_briefloop_skill_freshness.py",
    ],
    "references/public-claims.md": [
        "RC-Phase Wording",
        "not_satisfied",
    ],
    "references/naming-and-compatibility.md": [
        "README.md` is the canonical English README",
        "README.zh-CN.md` is the canonical Chinese README",
        "README_en.md` is only a short compatibility pointer",
    ],
}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", action="store_true", help="Emit machine-readable JSON.")
    args = parser.parse_args()

    checks: list[dict[str, str]] = []
    _check_required_phrases(checks)
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


def _check_required_phrases(checks: list[dict[str, str]]) -> None:
    for rel_path, phrases in REQUIRED_REFERENCE_PHRASES.items():
        path = CANONICAL / rel_path
        if not path.exists():
            _append_check(checks, f"canonical.{rel_path}", False, "missing file")
            continue
        text = path.read_text(encoding="utf-8")
        missing = [phrase for phrase in phrases if phrase not in text]
        _append_check(
            checks,
            f"canonical.{rel_path}.freshness",
            not missing,
            f"missing={missing}",
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
    if payload["ok"]:
        print("ALL CHECKS PASSED.")
    else:
        print("FAILED.")


if __name__ == "__main__":
    raise SystemExit(main())
