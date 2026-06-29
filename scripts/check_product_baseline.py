#!/usr/bin/env python3
"""Check the v0.11 product-baseline readiness surface.

This is a pre-release readiness guard. It verifies product-facing CLI entries,
ReportPack defaults, packaged config parity, public documentation boundaries,
and reference-run surface presence. It does not promote any experimental
surface to stable support status and does not run a BriefLoop workspace.
"""

from __future__ import annotations

import argparse
import contextlib
import io
import json
import sys
import tempfile
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from multi_agent_brief.cli.main import build_parser, main as cli_main  # noqa: E402
from multi_agent_brief.product.report_pack_aliases import (  # noqa: E402
    RECOMMENDED_REPORT_PACK_ENTRIES,
    aliases_for_report_pack,
)

BASELINE_TARGET = "v0.11.0"
EXPECTED_PRODUCT_ENTRIES = {
    "industry-weekly": "market_weekly",
    "management-monthly": "management_monthly",
    "document-review": "evidence_extract",
    "solar-periodic": "solar_industry_periodic",
}
EXPECTED_LEGACY_ENTRIES = {
    "market-weekly": "market_weekly",
    "evidence-extract": "evidence_extract",
    "solar-industry-periodic": "solar_industry_periodic",
}
STABLE_SCENARIO_PACKS = {
    "market_weekly",
    "management_monthly",
    "evidence_extract",
}
REQUIRED_CONTROL_SPINE = {
    "claim_ledger",
    "artifact_registry",
    "quality_gates",
    "event_log",
    "archive",
    "source_appendix",
    "support_records",
    "human_delivery_approval",
    "frozen_artifact_integrity",
}
REQUIRED_DOC_BOUNDARY_PHRASES = {
    "README_en.md": [
        "does not parse PDFs automatically",
        "prove semantic truth",
        "approve delivery",
        "create public release authority",
    ],
    "docs/roadmap.md": [
        "v0.11.0 — Stable Weekly/Monthly Brief Product",
        "no force-deliver path",
        "no automatic public release or external publication command",
        "local studio preview after the cli product path works",
    ],
    "docs/support-matrix.md": [
        "Supported",
        "Experimental",
        "force-deliver",
        "Quality Panel projection",
    ],
}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", action="store_true", help="Emit machine-readable JSON.")
    args = parser.parse_args()

    checks: list[dict[str, str]] = []
    _check_report_pack_configs(checks)
    _check_product_entries(checks)
    _check_packs_cli_surface(checks)
    _check_workspace_creation(checks)
    _check_cli_and_docs_boundaries(checks)
    _check_reference_run_surface(checks)

    ok = all(item["status"] == "pass" for item in checks)
    payload = {
        "ok": ok,
        "schema_version": "briefloop.product_baseline_check.v1",
        "baseline_target": BASELINE_TARGET,
        "runtime_effect": "readiness_check_only",
        "checks": checks,
        "non_goals": [
            "version_bump",
            "support_status_promotion",
            "stage_execution",
            "gate_execution",
            "delivery_approval",
            "semantic_truth_proof",
            "release_authority",
        ],
    }
    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
    else:
        print("Product Baseline Readiness Check")
        print("=" * 40)
        for item in checks:
            print(f"  [{item['status'].upper()}] {item['id']}: {item['detail']}")
        print()
        print("ALL CHECKS PASSED." if ok else "FAILED.")
    return 0 if ok else 1


def _check_report_pack_configs(checks: list[dict[str, str]]) -> None:
    root_dir = ROOT / "configs" / "report_packs"
    package_dir = ROOT / "src" / "multi_agent_brief" / "configs" / "report_packs"
    root_packs = _load_yaml_dir(root_dir)
    package_packs = _load_yaml_dir(package_dir)

    expected_ids = set(RECOMMENDED_REPORT_PACK_ENTRIES)
    _append_check(
        checks,
        "report_pack_ids",
        set(root_packs) == expected_ids,
        f"root packs={sorted(root_packs)} expected={sorted(expected_ids)}",
    )
    _append_check(
        checks,
        "report_pack_package_parity",
        root_packs == package_packs,
        "root report_packs match packaged copies",
    )

    stable_missing = sorted(STABLE_SCENARIO_PACKS - set(root_packs))
    _append_check(
        checks,
        "stable_scenario_packs",
        not stable_missing,
        f"stable scenarios={sorted(STABLE_SCENARIO_PACKS)} missing={stable_missing}",
    )

    for pack_id in sorted(STABLE_SCENARIO_PACKS):
        pack = root_packs.get(pack_id) or {}
        spec = pack.get("default_report_spec") if isinstance(pack.get("default_report_spec"), dict) else {}
        outputs = set(spec.get("outputs") or [])
        control_spine = spec.get("control_spine") if isinstance(spec.get("control_spine"), dict) else {}
        spine_missing = sorted(key for key in REQUIRED_CONTROL_SPINE if control_spine.get(key) is not True)
        source_policy = spec.get("source_policy") if isinstance(spec.get("source_policy"), dict) else {}
        _append_check(
            checks,
            f"{pack_id}.markdown_docx_outputs",
            {"markdown", "docx"} <= outputs,
            f"outputs={sorted(outputs)}",
        )
        _append_check(
            checks,
            f"{pack_id}.control_spine",
            not spine_missing,
            f"required control spine missing={spine_missing}",
        )
        _append_check(
            checks,
            f"{pack_id}.no_hidden_crawling",
            source_policy.get("hidden_autonomous_crawling") is False,
            "hidden_autonomous_crawling=false",
        )


def _check_product_entries(checks: list[dict[str, str]]) -> None:
    for entry, pack_id in {**EXPECTED_PRODUCT_ENTRIES, **EXPECTED_LEGACY_ENTRIES}.items():
        aliases = aliases_for_report_pack(pack_id)
        _append_check(
            checks,
            f"entry.{entry}",
            entry in aliases,
            f"{entry} resolves to {pack_id}; aliases={aliases}",
        )


def _check_packs_cli_surface(checks: list[dict[str, str]]) -> None:
    list_code, list_payload = _run_cli_json(["packs", "list", "--json"])
    packs = list_payload.get("packs") if isinstance(list_payload.get("packs"), list) else []
    packs_by_id = {
        item.get("pack_id"): item
        for item in packs
        if isinstance(item, dict) and isinstance(item.get("pack_id"), str)
    }
    _append_check(
        checks,
        "packs_list_cli.ok",
        list_code == 0 and list_payload.get("ok") is True,
        f"exit={list_code} ok={list_payload.get('ok')}",
    )
    _append_check(
        checks,
        "packs_list_cli.internal_pack_ids",
        set(packs_by_id) == set(RECOMMENDED_REPORT_PACK_ENTRIES),
        f"pack_ids={sorted(packs_by_id)} expected={sorted(RECOMMENDED_REPORT_PACK_ENTRIES)}",
    )
    missing_entries = []
    missing_aliases = []
    for product_entry, pack_id in EXPECTED_PRODUCT_ENTRIES.items():
        item = packs_by_id.get(pack_id) or {}
        aliases = item.get("aliases") if isinstance(item.get("aliases"), list) else []
        if item.get("recommended_entry") != product_entry:
            missing_entries.append(f"{pack_id}->{product_entry}")
        if product_entry not in aliases:
            missing_aliases.append(f"{pack_id}:{product_entry}")
    for legacy_entry, pack_id in EXPECTED_LEGACY_ENTRIES.items():
        item = packs_by_id.get(pack_id) or {}
        aliases = item.get("aliases") if isinstance(item.get("aliases"), list) else []
        if legacy_entry not in aliases:
            missing_aliases.append(f"{pack_id}:{legacy_entry}")
    _append_check(
        checks,
        "packs_list_cli.product_entries",
        not missing_entries,
        f"recommended entry mismatches={missing_entries}",
    )
    _append_check(
        checks,
        "packs_list_cli.aliases",
        not missing_aliases,
        f"missing aliases={missing_aliases}",
    )

    unknown_code, unknown_payload = _run_cli_json(["packs", "show", "unknown-pack", "--json"])
    recommended = unknown_payload.get("recommended_entries")
    internal = unknown_payload.get("internal_pack_ids")
    _append_check(
        checks,
        "packs_unknown_cli.error",
        unknown_code == 1
        and unknown_payload.get("ok") is False
        and "unknown report pack" in str(unknown_payload.get("error") or ""),
        f"exit={unknown_code} ok={unknown_payload.get('ok')} error={unknown_payload.get('error')}",
    )
    _append_check(
        checks,
        "packs_unknown_cli.product_entries",
        recommended == sorted(EXPECTED_PRODUCT_ENTRIES),
        f"recommended_entries={recommended} expected={sorted(EXPECTED_PRODUCT_ENTRIES)}",
    )
    _append_check(
        checks,
        "packs_unknown_cli.internal_pack_ids",
        internal == sorted(RECOMMENDED_REPORT_PACK_ENTRIES),
        f"internal_pack_ids={internal} expected={sorted(RECOMMENDED_REPORT_PACK_ENTRIES)}",
    )


def _check_workspace_creation(checks: list[dict[str, str]]) -> None:
    with tempfile.TemporaryDirectory(prefix="briefloop-product-baseline-") as tmp:
        base = Path(tmp)
        for entry, expected_pack in EXPECTED_PRODUCT_ENTRIES.items():
            workspace = base / entry
            stdout = io.StringIO()
            with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(io.StringIO()):
                code = cli_main(["new", entry, str(workspace)])
            spec_path = workspace / "report_spec.yaml"
            spec = _load_yaml_file(spec_path) if spec_path.exists() else {}
            _append_check(
                checks,
                f"new.{entry}",
                code == 0 and spec.get("report_pack") == expected_pack,
                f"exit={code} report_pack={spec.get('report_pack')} expected={expected_pack}",
            )
            _append_check(
                checks,
                f"new.{entry}.local_first_files",
                all((workspace / name).exists() for name in ("config.yaml", "sources.yaml", "user.md"))
                and (workspace / "input" / "sources").is_dir(),
                "workspace skeleton contains config.yaml, sources.yaml, user.md, input/sources",
            )


def _check_cli_and_docs_boundaries(checks: list[dict[str, str]]) -> None:
    help_text = build_parser().format_help()
    _append_check(
        checks,
        "no_force_deliver_cli",
        "force-deliver" not in help_text and "force deliver" not in help_text.lower(),
        "top-level CLI help does not expose force-deliver",
    )
    readme_en = (ROOT / "README_en.md").read_text(encoding="utf-8")
    current_baseline = _extract_current_release_baseline(readme_en)
    _append_check(
        checks,
        "docs.README_en.md.current_release_baseline",
        current_baseline == BASELINE_TARGET,
        f"current_release_baseline={current_baseline or '<missing>'} expected={BASELINE_TARGET}",
    )

    for rel_path, phrases in REQUIRED_DOC_BOUNDARY_PHRASES.items():
        text = (ROOT / rel_path).read_text(encoding="utf-8").lower()
        missing = [phrase for phrase in phrases if phrase.lower() not in text]
        _append_check(
            checks,
            f"docs.{rel_path}",
            not missing,
            f"required boundary phrases missing={missing}",
        )


def _check_reference_run_surface(checks: list[dict[str, str]]) -> None:
    files = sorted((ROOT / "docs" / "reference-runs").glob("*.md"))
    _append_check(
        checks,
        "reference_run_surface_count",
        len(files) >= 5,
        f"reference-run markdown files={len(files)}",
    )
    unsafe = []
    for path in files:
        text = path.read_text(encoding="utf-8").lower()
        if not any(marker in text for marker in ("not proof", "not prove", "not a", "public-safe", "private")):
            unsafe.append(path.name)
    _append_check(
        checks,
        "reference_run_boundary_language",
        not unsafe,
        f"files missing obvious boundary language={unsafe}",
    )


def _load_yaml_dir(path: Path) -> dict[str, dict[str, Any]]:
    packs = {}
    for file_path in sorted(path.glob("*.yaml")):
        payload = _load_yaml_file(file_path)
        pack_id = payload.get("pack_id") if isinstance(payload, dict) else None
        if isinstance(pack_id, str) and pack_id:
            packs[pack_id] = payload
    return packs


def _load_yaml_file(path: Path) -> dict[str, Any]:
    payload = yaml.safe_load(path.read_text(encoding="utf-8"))
    return payload if isinstance(payload, dict) else {}


def _run_cli_json(argv: list[str]) -> tuple[int, dict[str, Any]]:
    stdout = io.StringIO()
    stderr = io.StringIO()
    with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
        code = cli_main(argv)
    try:
        payload = json.loads(stdout.getvalue())
    except json.JSONDecodeError:
        payload = {
            "ok": False,
            "stdout": stdout.getvalue(),
            "stderr": stderr.getvalue(),
        }
    return code, payload if isinstance(payload, dict) else {"ok": False, "payload": payload}


def _extract_current_release_baseline(text: str) -> str:
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.lower().startswith("current release baseline:"):
            return stripped.split(":", 1)[1].strip()
    return ""


def _append_check(checks: list[dict[str, str]], check_id: str, ok: bool, detail: str) -> None:
    checks.append({
        "id": check_id,
        "status": "pass" if ok else "fail",
        "detail": detail,
    })


if __name__ == "__main__":
    raise SystemExit(main())
